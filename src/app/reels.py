"""Reels: a tape's highlight reel as immutable revisions, proposed by the planner and accepted by the person (plan step
5; overview §6, spec §3 EditGraph and Proposal, §6).

- **A revision never changes.** It holds the clips in play order, each a clip occurrence with its own id (spec §3: not
  the source's), its source and its exact half-open range on frames, and everything that made it: the planner's name,
  version, seed and parameters, the results it read (by cache key) and the overrides it obeyed (by id). Rows of both
  are append-only, so the same inputs make the same revision again (`planner.plan` is pure); asking for the same plan
  on the same base returns the stored one.
- **A proposal is a revision not accepted yet**, made on the reel's current revision (its `base`). Accepting is a
  compare-and-swap under the reel's row lock: only a proposal whose base is still the current revision can become it,
  so a stale one is a 409 conflict to plan again, never a silent overwrite (spec §6, the acceptance gate "deterministic
  conflict detection on stale revisions"). The current revision is the last one accepted.
- **The person's word is an override on the reel** (`overrides`, target `reel`): `pin` (this scene is in), `ban` (this
  scene is out), `lock` (this clip stays exactly as it is). A pin or ban records the scene's span, not its number, so it
  still finds its scene after the tape is grouped again; a lock records the clip's range. Every plan reads the ones that
  hold, and what it could not keep is in the revision's `conflicts`, each naming its override.
- **A clip's id is its range's**: a hash of the source's content and the exact start and end, so the same range keeps
  its id from one revision to the next, which is what a lock, a render's cache and the preview name it by.

The clip list is what the preview and the render read (`GET /api/reels/{reel}/revisions/{rev}`); output time is the
clips end to end with no gap (`mediatime.TimeMap.sequence`)."""

import datetime
import hashlib
from fractions import Fraction
from typing import Any, Literal

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from strata_python import mediatime
from strata_python.contracts import ApiModel
from strata_python.db.writes import lock_row
from strata_python.mediatime import Span
from strata_python.tasks import Conflict
from strata_python.workflow import request_key

from app import chapters, describe, overrides, planner, scenes, shots, speech
from app.models import Override, Reel, Revision, Source

PLANNER = "planner"  # the author of a revision the planner made
KINDS: tuple[planner.Kind, ...] = ("pin", "ban", "lock")
MODES: dict[str, str] = {"pin": "require", "ban": "forbid", "lock": "require"}
NAME_CHARS = 200

State = Literal["proposal", "stale", "current", "past", "rejected"]


# --- what the API shows ----------------------------------------------------------------------------------------------


class Clip(ApiModel):
    """A clip occurrence: `start` and `end` are the exact half-open source range (mediatime text, on frames), the
    seconds beside them are for the page; `at_seconds` is where it starts in the reel."""

    id: str = Field(description="the occurrence's own id: the same range keeps it from one revision to the next")
    source_id: int
    start: str
    end: str
    start_seconds: float
    end_seconds: float
    at_seconds: float
    scene: str = Field(description="the start of the scene it was cut from, exact")
    gain_db: float
    score: float
    reasons: list[str]
    locked: bool
    pinned: bool


class Clash(ApiModel):
    """Something the person asked that the plan could not keep; `override` is the constraint, None for the length."""

    override: int | None
    message: str


class Planned(ApiModel):
    name: str
    version: int
    seed: int


class RevisionOut(ApiModel):
    """One revision of a reel. `state`: `proposal` (can be accepted), `stale` (made on a revision that is no longer the
    current one: plan again), `current`, `past` (was current) or `rejected`."""

    id: int
    reel_id: int
    base_id: int | None
    state: State
    author: str
    planner: Planned
    created: datetime.datetime
    accepted: datetime.datetime | None
    accepted_by: str | None
    target_seconds: float
    duration_seconds: float
    rate: str = Field(description="the source's frame rate, exact")
    clips: list[Clip]
    conflicts: list[Clash]


class ConstraintOut(ApiModel):
    """A pin, ban or lock that holds: the span it names (a scene's for a pin or ban, the clip's for a lock)."""

    id: int
    kind: planner.Kind
    start_seconds: float
    end_seconds: float
    clip: str | None = Field(description="the locked clip's id; None for a pin or ban")
    author: str
    created: datetime.datetime


class ReelOut(ApiModel):
    """A reel in the list: its current revision's length and clip count (None before one is accepted)."""

    id: int
    source_id: int
    source_name: str
    name: str
    target_seconds: float
    created: datetime.datetime
    current_id: int | None
    duration_seconds: float | None
    clips: int | None
    proposals: int = Field(description="proposals that can still be accepted")


class ReelDetail(ReelOut):
    current: RevisionOut | None
    open: list[RevisionOut] = Field(description="proposals not accepted or rejected, newest first (stale ones too)")
    constraints: list[ConstraintOut]


# --- reading the tape for the planner --------------------------------------------------------------------------------


def _utc(t: datetime.datetime) -> datetime.datetime:
    return t.replace(tzinfo=datetime.UTC) if t.tzinfo is None else t.astimezone(datetime.UTC)


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def material(db: Session, source: Source) -> tuple[list[planner.Scene], dict[str, Any]]:
    """The source's scenes as the planner reads them, and the cache keys of the results they came from. KeyError
    before the scenes are grouped; descriptions, chapters and speech are used where they exist."""
    cut, grouped = shots.current(db, source), scenes.current(db, source)
    if cut is None or grouped is None or grouped.parameters.get("shots") != cut.cache_key:
        raise KeyError(f"{source.name} has no scenes yet: a reel is cut from them")
    told = describe.current_descriptions(db, source)
    grouped_chapters = chapters.current(db, source)
    chapter_of: dict[int, int] = {}
    if grouped_chapters is not None:
        for k, ch in enumerate(grouped_chapters.payload["chapters"]):
            chapter_of |= dict.fromkeys(ch["scenes"], k)
    said = speech.current(db, source) if speech.has_sound(source) else None
    segments: list[dict[str, Any]] = [] if said is None else said.payload["segments"]
    phrases = [
        planner.Said(Span.of(s["start"], s["end"]), len(str(s["text"]).split()))
        for s in segments
        if not s["doubtful"] and s["end"] > s["start"]
    ]
    out: list[planner.Scene] = []
    used: list[str] = []
    for i, sc in enumerate(grouped.payload["scenes"]):
        span = Span.of(sc["start"], sc["end"])
        d = told.get(sc["start"])
        if d is not None:
            used.append(d.cache_key)
        out.append(
            planner.Scene(
                span=span,
                chapter=chapter_of.get(i),
                description=None if d is None else d.payload["description"],
                said=tuple(p for p in phrases if p.span.overlaps(span)),
            )
        )
    inputs = {
        "scenes": grouped.cache_key,
        "chapters": None if grouped_chapters is None else grouped_chapters.cache_key,
        "transcript": None if said is None else said.cache_key,
        "descriptions": request_key(*used),
    }
    return out, inputs


def _held(db: Session, reel: Reel) -> list[Override]:
    return [o for o in overrides.active(db, "reel", reel.id) if o.kind in KINDS]


def _constraint(o: Override) -> planner.Constraint:
    kind: planner.Kind = "pin" if o.kind == "pin" else "ban" if o.kind == "ban" else "lock"
    return planner.Constraint(o.id, kind, Span.of(o.value["start"], o.value["end"]))


def clip_id(source: Source, span: Span) -> str:
    """The occurrence's id, from the source's content and the exact range: the same range, the same id."""
    h = hashlib.sha256(f"{source.sha256}:{mediatime.text(span.start)}:{mediatime.text(span.end)}".encode())
    return "c" + h.hexdigest()[:12]


def _clip_json(source: Source, c: planner.Clip) -> dict[str, Any]:
    return {
        "id": clip_id(source, c.span),
        "source_id": source.id,
        "start": mediatime.text(c.span.start),
        "end": mediatime.text(c.span.end),
        "scene": mediatime.text(c.scene),
        "gain_db": 0.0,
        "score": c.score,
        "reasons": list(c.reasons),
        "locked": c.locked,
        "pinned": c.pinned,
    }


# --- reels and revisions ---------------------------------------------------------------------------------------------


def _source(db: Session, reel: Reel) -> Source:
    found = db.get(Source, reel.source_id)
    if found is None:  # pragma: no cover - a reel's source is a foreign key
        raise KeyError(f"there is no source {reel.source_id}")
    return found


def length(seconds: float | str | Fraction) -> Fraction:
    """A reel's length from seconds (`120`, `"2:00"`, `"1:02:30"`), exact. ValueError for one not above 0."""
    if isinstance(seconds, str) and ":" in seconds:
        parts = seconds.split(":")
        if len(parts) > 3 or not all(p.strip() for p in parts):
            raise ValueError(f"a length is seconds, m:ss or h:mm:ss, not {seconds!r}")
        t = Fraction(0)
        for p in parts:
            t = t * 60 + mediatime.rational(p.strip())
    else:
        t = mediatime.rational(seconds)
    if t <= 0:
        raise ValueError(f"a reel's length is above 0 seconds, not {seconds}")
    return t


def create(
    db: Session, source: Source, target: Fraction, *, name: str | None = None, author: str = PLANNER
) -> tuple[Reel, Revision]:
    """A new reel of `source` and its first proposal. KeyError before the tape's scenes are grouped (nothing is
    stored then); ValueError for a length that is not above 0 or a name that is empty or too long."""
    if target <= 0:
        raise ValueError(f"a reel's length is above 0 seconds, not {planner.clock(target)}")
    material(db, source)  # refuse before a reel with nothing to cut from is stored
    named = " ".join((name or f"{source.name}, {planner.clock(target)}").split())
    if not named or len(named) > NAME_CHARS:
        raise ValueError(f"a reel's name is 1 to {NAME_CHARS} characters")
    reel = Reel(source_id=source.id, name=named, target=mediatime.text(target))
    db.add(reel)
    db.flush()
    return reel, propose(db, reel, author=author)


def get(db: Session, reel_id: int) -> Reel:
    found = db.get(Reel, reel_id)
    if found is None:
        raise KeyError(f"there is no reel {reel_id}")
    return found


def all_reels(db: Session, source: Source | None = None) -> list[Reel]:
    q = select(Reel).order_by(Reel.id)
    if source is not None:
        q = q.where(Reel.source_id == source.id)
    return list(db.scalars(q))


def revision(db: Session, reel: Reel, revision_id: int) -> Revision:
    found = db.get(Revision, revision_id)
    if found is None or found.reel_id != reel.id:
        raise KeyError(f"reel {reel.id} has no revision {revision_id}")
    return found


def current(db: Session, reel: Reel) -> Revision | None:
    """The reel's current revision: the last one accepted (a later accept is always of a later row, since its base is
    the one before), or None before any is."""
    q = select(Revision).where(Revision.reel_id == reel.id, Revision.accepted.is_not(None))
    return db.scalars(q.order_by(Revision.id.desc()).limit(1)).first()


def open_proposals(db: Session, reel: Reel) -> list[Revision]:
    """Proposals neither accepted nor rejected, newest first (stale ones included: `state_of` tells them apart)."""
    q = select(Revision).where(Revision.reel_id == reel.id, Revision.accepted.is_(None), Revision.rejected.is_(None))
    return list(db.scalars(q.order_by(Revision.id.desc())))


def propose(
    db: Session,
    reel: Reel,
    *,
    seed: int = 0,
    target: Fraction | None = None,
    author: str = PLANNER,
) -> Revision:
    """A new proposal on the reel's current revision, planned from what is found about the tape now and the
    constraints that hold (a new `target` becomes the reel's length). The same plan asked again on the same base is
    the stored proposal. KeyError before the scenes are grouped."""
    if target is not None:
        if target <= 0:
            raise ValueError(f"a reel's length is above 0 seconds, not {planner.clock(target)}")
        reel.target = mediatime.text(target)
    goal = mediatime.rational(reel.target)
    source = _source(db, reel)
    found, inputs = material(db, source)
    held = _held(db, reel)
    base = current(db, reel)
    made_by = {"name": planner.NAME, "version": planner.VERSION, "seed": seed, "parameters": planner.PARAMETERS}
    used = {"results": inputs, "overrides": [o.id for o in held]}
    key = request_key("revision", reel.id, None if base is None else base.id, made_by, reel.target, used)
    again = db.scalars(select(Revision).where(Revision.key == key, Revision.rejected.is_(None))).first()
    if again is not None:
        db.commit()  # the reel's new length, if any
        return again
    plan = planner.plan(found, goal, shots.rate_of(source), [_constraint(o) for o in held], seed=seed)
    rev = Revision(
        reel_id=reel.id,
        base_id=None if base is None else base.id,
        author=author,
        planner=made_by,
        inputs=used,
        clips=[_clip_json(source, c) for c in plan.clips],
        conflicts=[{"override": c.override, "message": c.message} for c in plan.conflicts],
        target=reel.target,
        duration=mediatime.text(plan.duration),
        key=key,
    )
    db.add(rev)
    db.commit()
    return rev


def accept(db: Session, reel_id: int, revision_id: int, *, author: str) -> Revision:
    """Make a proposal the reel's current revision, if its base still is (compare-and-swap under the reel's row lock).
    Conflict (409) when the reel has moved on since it was made; ValueError for a rejected one; accepting the current
    revision again changes nothing."""
    reel = lock_row(db, Reel, reel_id)
    rev = revision(db, reel, revision_id)
    if rev.accepted is not None:
        db.rollback()
        return rev
    if rev.rejected is not None:
        db.rollback()
        raise ValueError(f"revision {rev.id} was rejected: plan the reel again")
    head = current(db, reel)
    if rev.base_id != (None if head is None else head.id):
        db.rollback()
        now = "none" if head is None else f"revision {head.id}"
        raise Conflict(
            f"The reel has changed since this proposal was made (its current revision is {now}): plan it again.",
            payload={"current": None if head is None else head.id},
        )
    rev.accepted, rev.accepted_by = _now(), author
    db.commit()
    return rev


def reject(db: Session, reel: Reel, revision_id: int) -> Revision:
    """Set a proposal aside. ValueError for one that was accepted."""
    rev = revision(db, reel, revision_id)
    if rev.accepted is not None:
        raise ValueError(f"revision {rev.id} was accepted: it stays in the reel's history")
    if rev.rejected is None:
        rev.rejected = _now()
        db.commit()
    return rev


def state_of(rev: Revision, head: Revision | None) -> State:
    if rev.rejected is not None:
        return "rejected"
    if rev.accepted is not None:
        return "current" if head is not None and head.id == rev.id else "past"
    return "proposal" if rev.base_id == (None if head is None else head.id) else "stale"


# --- the person's pins, bans and locks -------------------------------------------------------------------------------


def constrain(
    db: Session, reel: Reel, kind: str, *, scene: int | None = None, clip: str | None = None, author: str
) -> Override:
    """Pin or ban scene `scene` (from 0, the tape's scenes now) or lock clip `clip` (an id from one of the reel's
    revisions). Saying the same again changes nothing; pinning a banned scene (or banning a pinned one) takes the
    other back. ValueError for another kind, a scene or clip there is not, or the wrong one of them given."""
    if kind not in KINDS:
        raise ValueError(f"a constraint is one of {', '.join(KINDS)}, not {kind!r}")
    source = _source(db, reel)
    held = _held(db, reel)
    if kind == "lock":
        if clip is None or scene is not None:
            raise ValueError("a lock names a clip")
        found = _find_clip(db, reel, clip)
        value: dict[str, Any] = {"clip": clip, "source": source.id, "start": found["start"], "end": found["end"]}
        same = [o for o in held if o.kind == "lock" and o.value.get("clip") == clip]
    else:
        if scene is None or clip is not None:
            raise ValueError(f"a {kind} names a scene")
        found_scenes, _ = material(db, source)
        if not 0 <= scene < len(found_scenes):
            raise ValueError(f"the tape has scenes 1 to {len(found_scenes)}, not scene {scene + 1}")
        span = found_scenes[scene].span
        value = {"source": source.id, "start": mediatime.text(span.start), "end": mediatime.text(span.end)}
        on_it = [o for o in held if o.kind in ("pin", "ban") and [o.value["start"], o.value["end"]] == span.to_json()]
        same = [o for o in on_it if o.kind == kind]
        for o in on_it:
            if o.kind != kind:
                overrides.withdraw(db, o)
    if same:
        return same[0]
    return overrides.add(db, "reel", reel.id, kind, value, mode=MODES[kind], author=author)


def unconstrain(db: Session, reel: Reel, override_id: int) -> None:
    """Take a pin, ban or lock back. KeyError for one that does not hold on this reel."""
    found = next((o for o in _held(db, reel) if o.id == override_id), None)
    if found is None:
        raise KeyError(f"reel {reel.id} has no pin, ban or lock {override_id}")
    overrides.withdraw(db, found)


def _find_clip(db: Session, reel: Reel, clip: str) -> dict[str, Any]:
    q = select(Revision).where(Revision.reel_id == reel.id).order_by(Revision.id.desc())
    for rev in db.scalars(q):
        for c in rev.clips:
            if c["id"] == clip:
                return c
    raise ValueError(f"reel {reel.id} has no clip {clip}")


# --- as the API shows them -------------------------------------------------------------------------------------------


def _seconds(t: str) -> float:
    return float(mediatime.rational(t))


def revision_out(db: Session, rev: Revision, head: Revision | None = None, *, rate: str | None = None) -> RevisionOut:
    reel = get(db, rev.reel_id)
    if head is None:
        head = current(db, reel)
    at = Fraction(0)
    clips: list[Clip] = []
    for c in rev.clips:
        start, end = mediatime.rational(c["start"]), mediatime.rational(c["end"])
        clips.append(
            Clip(
                **{
                    k: c[k]
                    for k in ("id", "source_id", "start", "end", "scene", "gain_db", "score", "locked", "pinned")
                },
                reasons=list(c["reasons"]),
                start_seconds=float(start),
                end_seconds=float(end),
                at_seconds=float(at),
            )
        )
        at += end - start
    return RevisionOut(
        id=rev.id,
        reel_id=rev.reel_id,
        base_id=rev.base_id,
        state=state_of(rev, head),
        author=rev.author,
        planner=Planned(name=rev.planner["name"], version=rev.planner["version"], seed=rev.planner["seed"]),
        created=_utc(rev.created),
        accepted=None if rev.accepted is None else _utc(rev.accepted),
        accepted_by=rev.accepted_by,
        target_seconds=_seconds(rev.target),
        duration_seconds=_seconds(rev.duration),
        rate=rate or str(shots.rate_of(_source(db, reel))),
        clips=clips,
        conflicts=[Clash.model_validate(c) for c in rev.conflicts],
    )


def _summary(db: Session, reel: Reel, head: Revision | None) -> dict[str, Any]:
    source = _source(db, reel)
    return {
        "id": reel.id,
        "source_id": reel.source_id,
        "source_name": source.name,
        "name": reel.name,
        "target_seconds": _seconds(reel.target),
        "created": _utc(reel.created),
        "current_id": None if head is None else head.id,
        "duration_seconds": None if head is None else _seconds(head.duration),
        "clips": None if head is None else len(head.clips),
        "proposals": sum(state_of(r, head) == "proposal" for r in open_proposals(db, reel)),
    }


def reel_out(db: Session, reel: Reel) -> ReelOut:
    return ReelOut.model_validate(_summary(db, reel, current(db, reel)))


def detail(db: Session, reel: Reel) -> ReelDetail:
    head = current(db, reel)
    rate = str(shots.rate_of(_source(db, reel)))
    return ReelDetail(
        **_summary(db, reel, head),
        current=None if head is None else revision_out(db, head, head, rate=rate),
        open=[revision_out(db, r, head, rate=rate) for r in open_proposals(db, reel)],
        constraints=[
            ConstraintOut(
                id=o.id,
                kind=_constraint(o).kind,
                start_seconds=_seconds(o.value["start"]),
                end_seconds=_seconds(o.value["end"]),
                clip=o.value.get("clip"),
                author=o.author,
                created=_utc(o.created),
            )
            for o in _held(db, reel)
        ],
    )
