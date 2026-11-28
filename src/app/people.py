"""Who is on the tape: faces followed through each shot, named by the person, and who is in each scene (R2, spec S2).

Recognition is strata-models' (overview: every face model and every identity lives there, shared with the other apps);
this module asks it, keeps what it answered, and records what the person said. Jonathan asked for it on 2026-11-27: a
stage after ingest that waits until every face on the tape is named or skipped, so the scene descriptions can say who
does what.

- **Tracks** (stage `face_tracks`): strata-models' `face-tracks` on the raw footage, cut at the shots this app found,
  so no track runs across a cut. One track is one person's face through one shot, so the person names a person once
  per track, not once per frame; two tracks seen in one frame are two people. Its whole answer (boxes ten times a
  second, an embedding per track and per still) is a file, `tracks.json`, beside the stills' frames under
  `derived/<cache key>/face-tracks/`; the result's payload is the outline (each track's shot, span and stills).
- **Into the face store** (stage `store_faces`): each still of each track becomes a picture in strata-models' face
  store (app `strata-reels`, tagged with the tape, `TAG:<sha256 prefix>`), with the box and embedding face-tracks found,
  so the person's names are the same people every app knows. The app's tag is opt-in: no other app's search or
  grouping sees these faces unless it names it (family footage, other people's children).
- **Naming** (gate `name_people`, the People tab): the tracks nobody has decided about are grouped into people by
  strata-models (`group-tracks`: never two tracks seen together in one group, a known person suggested when the
  confirmed faces nearest a group agree). The person names a group (a new person or one known already), says it is
  not faces, or skips it (someone not worth a name). Names and "not a face" are decisions in the face store; a skip is
  this app's override on the tape (`face-skip`), since it is about this tape's editing and nobody's identity. Every
  answer given here is also logged as an override (`face-said`), which is the `cast` stage's input, so a name given
  after the gate still reaches the scenes. The gate approves itself once no track is left to decide (`auto`), or the
  person approves it to skip the rest. A track whose stills are all under the face store's bar (tiny or doubtful) is
  the store's to hide and never waits for a name.
- **Cast** (stage `cast`): who is in each scene, from the tracks the person named, and where each named face is on
  each keyframe, which the describer is given (`describe`, preset `scene-describe-v3`)."""

import dataclasses
import json
import os
import uuid
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Literal

from pydantic import Field
from sqlalchemy.orm import Session
from strata_python import mediatime
from strata_python.clients import faces as fs
from strata_python.clients import models as sm
from strata_python.contracts import ApiModel

from app import derived, overrides, results, scenes, settings, shots
from app.models import Override, Result, Source

ROLE = "face-tracks"
APP = shots.APP
TAG = APP  # every picture's app tag, made opt-in: other apps search these faces only when they name it
ASK: dict[str, Any] = {"stills": 3}  # fps and tracker are the role's (eval face-tracks-home)
MAX_SIDE = 1600  # face-tracks decodes stills at most this many pixels across (its default)
STORE = results.Provenance(role="face-store")
FOLDER = results.Provenance(role=ROLE)
SKIP, SAID = "face-skip", "face-said"
VERSION = 1  # raise when `cast_of` changes what it gives for the same inputs

Status = Literal["named", "not-face", "skipped", "hidden", "open"]


# --- tracks -----------------------------------------------------------------------------------------------------------


def track(db: Session, source: Source, *, job_id: int | None = None) -> tuple[Result, bool]:
    """The tape's face tracks, asked of strata-models unless the same request is stored, and whether they are new.
    KeyError before the shots are found."""
    if source.sha256 is None:
        raise ValueError(f"{source.name} has no content hash yet")
    found = shots.current(db, source)
    if found is None:
        raise KeyError(f"{source.name} has no shots yet: its faces are followed shot by shot")
    params = {**ASK, "shots": found.cache_key}
    now = sm.role_settings(ROLE)
    key: str | None = None
    if now is not None:
        planned = results.Provenance(role=ROLE, model=now.model, settings_hash=now.settings_hash)
        key = results.cache_key("face-tracks", [source.sha256], planned, params)
        stored = results.find(db, key)
        if stored is not None and os.path.isfile(_file(stored)):
            return stored, False
    # Each result keeps its own files (results are append-only): the folder is the planned model and settings' key, so
    # a changed model writes beside the old tracks, not over them; with the plan unknown, a folder of its own.
    folder_key = key or results.cache_key("face-tracks", [source.sha256], FOLDER, {**params, "nonce": uuid.uuid4().hex})
    rel = os.path.join(derived.folder(folder_key), "face-tracks")
    folder = os.path.join(settings.SETTINGS.home(), rel)
    os.makedirs(folder, exist_ok=True)
    cuts = [float(mediatime.rational(s["start"])) for s in found.payload["shots"][1:]]
    ask = {"path": os.path.abspath(source.path), "cuts": cuts, "out_dir": os.path.join(folder, "stills"), **ASK}
    answer, served = sm.run_with_provenance(ROLE, ask, APP, priority="low")
    got = sm.FaceTracks.from_json(answer)
    with open(os.path.join(folder, "tracks.json.part"), "w", encoding="utf-8") as f:
        json.dump(answer, f)
    os.replace(os.path.join(folder, "tracks.json.part"), os.path.join(folder, "tracks.json"))
    return results.record(
        db,
        source,
        "face-tracks",
        results.Provenance.of(ROLE, dataclasses.asdict(served)),
        inputs=[source.sha256],
        parameters=params,
        payload=outline(got),
        file=rel,
        job_id=job_id,
    )


def outline(got: sm.FaceTracks) -> dict[str, Any]:
    """What the result's row holds: each track's shot, span, size and stills (no boxes, no embeddings: the file has
    them), and the pairs seen together."""
    return {
        "width": got.width,
        "height": got.height,
        "fps": got.fps,
        "tracks": [
            {
                "id": t.id,
                "shot": t.shot,
                "start": round(t.start, 3),
                "end": round(t.end, 3),
                "faces": t.faces,
                "stills": [round(s.time, 3) for s in t.stills],
            }
            for t in got.tracks
        ],
        "together": [list(p) for p in got.together],
    }


def _file(r: Result) -> str:
    return os.path.join(derived.path_of(r), "tracks.json")


def tracks_of(r: Result) -> sm.FaceTracks:
    """The whole answer a `face-tracks` result was made from."""
    with open(_file(r), encoding="utf-8") as f:
        return sm.FaceTracks.from_json(json.load(f))


def current_tracks(db: Session, source: Source) -> Result | None:
    """The tracks of the current shots, or None before the stage has run for them."""
    found = shots.current(db, source)
    if found is None:
        return None
    return next(
        (r for r in results.current(db, source, "face-tracks") if r.parameters.get("shots") == found.cache_key), None
    )


# --- the face store ---------------------------------------------------------------------------------------------------


def tape_tag(source: Source) -> str:
    assert source.sha256 is not None  # noqa: S101 - its tracks needed it
    return f"{TAG}:{source.sha256[:16]}"


def still_size(width: int, height: int, max_side: int = MAX_SIDE) -> tuple[int, int]:
    """The size face-tracks writes a still's frame at: the video's, scaled to at most `max_side` across, rounded to
    even (its decoder's rule), so a box in the video's pixels can be put in the frame's."""
    k = min(1.0, max_side / max(width, height))
    return max(2, round(width * k / 2) * 2), max(2, round(height * k / 2) * 2)


def ref_of(source: Source, tracking: str, track_id: int, time: float) -> str:
    """A still's reference in the face store: the tape, the tracking it came from (its result's cache key, so a new
    tracking never answers an earlier one's stored picture), the track and the time."""
    assert source.sha256 is not None  # noqa: S101
    return f"{source.sha256[:16]}/{tracking[:16]}/t{track_id}/{time:.3f}"


def store(db: Session, source: Source, *, job_id: int | None = None) -> tuple[Result, bool]:
    """Every still of every track into the face store, one picture each, unless it is there (indexing the same ref
    again answers the stored picture). The result maps each track to its stills' faces. KeyError before the tracks."""
    tracked = current_tracks(db, source)
    if tracked is None:
        raise KeyError(f"{source.name} has no face tracks yet")
    got = tracks_of(tracked)
    model = str(got.raw.get("model") or sm.role_model("faces") or "buffalo_l")
    params = {"tracks": tracked.cache_key, "model": model}
    provenance = dataclasses.replace(STORE, model=model)
    stored = results.find(db, results.cache_key("face-store", [source.sha256 or ""], provenance, params))
    if stored is not None:
        return stored, False
    fs.set_tag(TAG, opt_in=True)
    w, h = still_size(got.width, got.height)
    kx, ky = w / got.width if got.width else 1.0, h / got.height if got.height else 1.0
    folder = os.path.join(derived.path_of(tracked), "stills")
    by_track: dict[str, list[int]] = {}
    for t in got.tracks:
        faces: list[int] = []
        for st in t.stills:
            if not st.image or not st.embedding or len(st.bbox) != 4:
                continue
            x0, y0, x1, y1 = st.bbox
            found = {
                "bbox": [round(x0 * kx, 2), round(y0 * ky, 2), round(x1 * kx, 2), round(y1 * ky, 2)],
                "det_score": st.det_score,
                "kps": [[round(x * kx, 2), round(y * ky, 2)] for x, y in st.kps] if st.kps else None,
                "pose": st.pose or None,
                "embedding": st.embedding,
                "found_by": ROLE,
            }
            img = fs.index(
                APP,
                ref_of(source, tracked.cache_key, t.id, st.time),
                os.path.join(folder, os.path.basename(st.image)),
                tags=[tape_tag(source)],
                faces=[found],
                model=model,
                width=w,
                height=h,
            )
            faces += [f.id for f in img.faces[:1]]
        if faces:
            by_track[str(t.id)] = faces
    return results.record(
        db,
        source,
        "face-store",
        provenance,
        inputs=[source.sha256 or ""],
        parameters=params,
        payload={"tag": tape_tag(source), "faces": by_track},
        job_id=job_id,
    )


def current_store(db: Session, source: Source) -> Result | None:
    tracked = current_tracks(db, source)
    if tracked is None:
        return None
    return next(
        (r for r in results.current(db, source, "face-store") if r.parameters.get("tracks") == tracked.cache_key),
        None,
    )


# --- what the person said ---------------------------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Decided:
    """Where each track of a tape stands: its person (named), not a face, skipped, or open."""

    status: dict[str, Status]
    person: dict[str, int]  # track -> identity, for named ones

    def open(self) -> list[str]:
        return [k for k, s in self.status.items() if s == "open"]


def skipped(db: Session, source: Source, kept: Result) -> set[str]:
    """The tracks the person skipped, of the tracking `kept` (a face-store result) was made from: a track id is only
    the same track within one tracking, so a skip from an earlier one does not hide a new track that has its number."""
    tracking = kept.parameters["tracks"]
    return {
        str(k)
        for o in overrides.active(db, "tape", source.id)
        if o.kind == SKIP and o.value.get("tracking") == tracking
        for k in o.value["tracks"]
    }


def said_key(db: Session, source: Source) -> list[int]:
    """Every answer the person gave about the tape's faces here (the `cast` stage's input): ids, in order."""
    return [o.id for o in overrides.active(db, "tape", source.id) if o.kind in (SKIP, SAID)]


def decisions(faces: Mapping[str, Sequence[int]], tag: str) -> dict[int, tuple[int | None, str | None]]:
    """face id -> (person, status) for the tape's stills, read from the face store."""
    want = {f for ids in faces.values() for f in ids}
    out: dict[int, tuple[int | None, str | None]] = {}
    for img in fs.images(APP, tag=tag):
        for f in img.faces:
            if f.id in want:
                out[f.id] = (f.identity_id, f.status)
    return out


def decide(
    faces: Mapping[str, Sequence[int]],
    said: Mapping[int, tuple[int | None, str | None]],
    skips: set[str],
    hidden: Iterable[str] = (),
) -> Decided:
    """Each track's state from its stills' decisions: named when a still is confirmed for someone (the person most of
    its confirmed stills are), not a face when every decided still is rejected with nobody, else skipped when the
    person skipped it, hidden when the store hides all its stills, else open. Pure."""
    status: dict[str, Status] = {}
    person: dict[str, int] = {}
    hide = set(hidden)
    for key, ids in faces.items():
        who = Counter(said[f][0] for f in ids if f in said and said[f][1] == "confirmed" and said[f][0] is not None)
        if who:
            status[key], person[key] = "named", int(who.most_common(1)[0][0] or 0)
        elif any(f in said and said[f][1] == "rejected" and said[f][0] is None for f in ids):
            status[key] = "not-face"
        elif key in skips:
            status[key] = "skipped"
        elif key in hide:
            status[key] = "hidden"
        else:
            status[key] = "open"
    return Decided(status, person)


# --- the People tab ---------------------------------------------------------------------------------------------------


class Suggestion(ApiModel):
    id: int
    name: str | None
    share: float = Field(description="of the confirmed faces nearest the group, the part that are this person's")


class Member(ApiModel):
    """One track of a group, as the person sees it: a still to recognise it by, so a group that holds two people can
    be split (the answer goes to the tracks chosen, the rest stay a group)."""

    track: str
    face: int | None = Field(description="the face store's id of the track's first still, for a crop")
    seconds: float = Field(description="how long the track is on screen")
    start: float = Field(description="when it starts, seconds into the tape")


class Group(ApiModel):
    """Tracks that look like one person, nobody's yet: name it, say it is not faces, or skip it; or any of its
    tracks, when it is more than one person."""

    tracks: list[str]
    members: list[Member] = Field(description="every track of the group, the longest first")
    seconds: float = Field(description="how long its faces are on screen, all its tracks together")
    first: float = Field(description="when its first track starts, seconds into the tape")
    suggested: Suggestion | None


class Person(ApiModel):
    """Someone named on this tape."""

    id: int
    name: str | None
    tracks: int
    seconds: float
    face: int | None = Field(description="one of their stills here, for a crop")


class People(ApiModel):
    """The tape's faces as the People tab shows them. `waiting` says, in a sentence, what they wait for while there are
    none yet; `done` is the person's word that the naming is finished (the gate)."""

    waiting: str | None
    groups: list[Group]
    people: list[Person]
    open: int = Field(description="tracks nobody has decided about")
    named: int
    skipped: int
    not_faces: int
    hidden: int = Field(description="tracks whose faces are all too small or unsure to name")
    done: bool


def people_of(db: Session, source: Source, *, done: bool) -> People:
    """The People tab: groups of the tracks left to decide, and the people named on the tape."""
    kept = current_store(db, source)
    tracked = current_tracks(db, source)
    if kept is None or tracked is None:
        waiting = "Faces are followed once the shots are found" if tracked is None else "Faces are being stored"
        return People(
            waiting=waiting, groups=[], people=[], open=0, named=0, skipped=0, not_faces=0, hidden=0, done=done
        )
    faces: dict[str, list[int]] = kept.payload["faces"]
    said = decisions(faces, kept.payload["tag"])
    first = decide(faces, said, skipped(db, source, kept))
    got = tracks_of(tracked)
    by_id = {str(t.id): t for t in got.tracks}
    open_keys = first.open()
    grouped = (
        fs.group_tracks(
            {k: by_id[k].embedding for k in open_keys},
            app=APP,
            faces={k: faces[k] for k in open_keys},
            apart=[(str(a), str(b)) for a, b in got.together],
            include=[TAG],
        )
        if open_keys
        else fs.TrackGroups([], [])
    )
    state = decide(faces, said, skipped(db, source, kept), grouped.hidden)
    groups = [
        Group(
            tracks=g.tracks,
            members=members(g.tracks, faces, by_id),
            seconds=round(sum(by_id[k].end - by_id[k].start for k in g.tracks), 1),
            first=round(min(by_id[k].start for k in g.tracks), 1),
            suggested=None
            if g.suggested is None
            else Suggestion(id=g.suggested.identity_id, name=g.suggested.name, share=g.suggested.share),
        )
        for g in grouped.groups
    ]
    mine: dict[int, list[str]] = defaultdict(list)
    for k, who in state.person.items():
        mine[who].append(k)
    people = [
        Person(
            id=who,
            name=fs.identity(who, app=APP).name,
            tracks=len(ks),
            seconds=round(sum(by_id[k].end - by_id[k].start for k in ks if k in by_id), 1),
            face=members([k for k in ks if k in by_id], faces, by_id)[0].face,
        )
        for who, ks in mine.items()
    ]
    people.sort(key=lambda p: (-p.seconds, p.id))
    counts = Counter(state.status.values())
    return People(
        waiting=None,
        groups=groups,
        people=people,
        open=counts["open"],
        named=counts["named"],
        skipped=counts["skipped"],
        not_faces=counts["not-face"],
        hidden=counts["hidden"],
        done=done,
    )


def members(keys: Sequence[str], faces: Mapping[str, Sequence[int]], by_id: Mapping[str, sm.FaceTrack]) -> list[Member]:
    """Every track of a group with one still each, the longest first (then the earliest), so the person sees all of
    it and can pick out the ones that are someone else."""
    order = sorted(keys, key=lambda k: (-(by_id[k].end - by_id[k].start), by_id[k].start, k))
    return [
        Member(
            track=k,
            face=next(iter(faces.get(k, ())), None),
            seconds=round(by_id[k].end - by_id[k].start, 1),
            start=round(by_id[k].start, 1),
        )
        for k in order
    ]


Answer = Literal["name", "not-faces", "skip"]


def answer(
    db: Session,
    source: Source,
    tracks: Sequence[str],
    what: Answer,
    *,
    author: str,
    name: str | None = None,
    person: int | None = None,
) -> Override:
    """The person's answer for a group of tracks: `name` them (a new person called `name`, or `person` known
    already), say they are `not-faces`, or `skip` them. Names go to the face store; every answer is logged here, the
    `cast` stage's input. ValueError for tracks not on the tape or a name without a person."""
    kept = current_store(db, source)
    if kept is None:
        raise KeyError(f"{source.name} has no faces to name yet")
    faces: dict[str, list[int]] = kept.payload["faces"]
    unknown = [k for k in tracks if k not in faces]
    if not tracks or unknown:
        raise ValueError(f"no track {', '.join(unknown) or '(none given)'} on {source.name}")
    ids = [f for k in tracks for f in faces[k]]
    if what == "skip":
        return overrides.add(
            db, "tape", source.id, SKIP, {"tracks": list(tracks), "tracking": kept.parameters["tracks"]}, author=author
        )
    if what == "not-faces":
        batch = fs.assign(ids, None, app=APP, status="rejected", note=source.name)
        who = None
    else:
        if person is None:
            if not (name or "").strip():
                raise ValueError("a new person needs a name")
            person = fs.create_identity(name=(name or "").strip(), app=APP).id
        batch = fs.assign(ids, person, app=APP, note=source.name)
        who = person
    return overrides.add(
        db, "tape", source.id, SAID, {"tracks": list(tracks), "person": who, "batch": batch}, author=author
    )


def is_open(db: Session, source: Source) -> bool:
    """Whether any track of the tape is left for the person to decide (the gate's policy asks this)."""
    kept = current_store(db, source)
    if kept is None:
        return True
    faces: dict[str, list[int]] = kept.payload["faces"]
    first = decide(faces, decisions(faces, kept.payload["tag"]), skipped(db, source, kept))
    if not first.open():
        return False
    tracked = current_tracks(db, source)
    assert tracked is not None  # noqa: S101 - the store result names it
    got = {str(t.id): t for t in tracks_of(tracked).tracks}
    hidden = fs.group_tracks(
        {k: got[k].embedding for k in first.open()}, app=APP, faces={k: faces[k] for k in first.open()}, include=[TAG]
    ).hidden
    return bool(set(first.open()) - set(hidden))


# --- the cast ---------------------------------------------------------------------------------------------------------


def where(box: Sequence[float], width: int, height: int) -> str:
    """Where a face is in the picture, in words the describer can use: left, middle or right, and near or far."""
    x0, y0, x1, y1 = box[:4]
    cx = (x0 + x1) / 2 / width if width else 0.5
    side = "on the left" if cx < 1 / 3 else "on the right" if cx > 2 / 3 else "in the middle"
    tall = (y1 - y0) / height if height else 0.0
    near = ", close to the camera" if tall > 0.3 else ", far away" if tall < 0.1 else ""
    return side + near


def box_at(t: sm.FaceTrack, time: float, within: float = 0.3) -> list[float] | None:
    """The track's box nearest `time`, if it has one within `within` seconds."""
    best = min(t.boxes, key=lambda b: abs(b[0] - time), default=None)
    return None if best is None or abs(best[0] - time) > within else best[1:5]


def cast_of(
    got: sm.FaceTracks,
    person: Mapping[str, int],
    names: Mapping[int, str | None],
    scenes_payload: Mapping[str, Any],
    keyframes: Sequence[tuple[int, float]],
) -> dict[str, Any]:
    """Who is in each scene, and where each named face is on each keyframe: pure, from the tracks, the person each
    named track is, the people's names, the scenes and the keyframes (frame, seconds). A scene's people are in order
    of their time on screen in it."""
    named = [t for t in got.tracks if str(t.id) in person]
    out_scenes: list[dict[str, Any]] = []
    for sc in scenes_payload["scenes"]:
        start, end = float(mediatime.rational(sc["start"])), float(mediatime.rational(sc["end"]))
        seen: dict[int, float] = defaultdict(float)
        for t in named:
            overlap = min(end, t.end) - max(start, t.start)
            if overlap > 0:
                seen[person[str(t.id)]] += overlap
        out_scenes.append(
            {
                "start": sc["start"],
                "people": [
                    {"id": who, "name": names.get(who), "seconds": round(s, 1)}
                    for who, s in sorted(seen.items(), key=lambda kv: (-kv[1], kv[0]))
                ],
            }
        )
    frames: dict[str, list[dict[str, Any]]] = {}
    for frame, at in keyframes:
        here = [
            {"id": person[str(t.id)], "where": where(b, got.width, got.height)}
            for t in named
            if t.start - 0.3 <= at <= t.end + 0.3 and (b := box_at(t, at)) is not None
        ]
        if here:
            frames[str(frame)] = here
    return {"scenes": out_scenes, "frames": frames, "names": {str(k): v for k, v in names.items()}}


def cast(db: Session, source: Source, *, job_id: int | None = None) -> tuple[Result, bool]:
    """The tape's cast now (`cast_of`), from the tracks, the face store's decisions about them, the scenes and their
    keyframes. Recorded under a key of all of them, so a new name makes a new cast and the same one finds it."""
    kept, tracked = current_store(db, source), current_tracks(db, source)
    grouped, cut = scenes.current(db, source), shots.current(db, source)
    if kept is None or tracked is None or grouped is None or cut is None:
        raise KeyError(f"{source.name} has no stored faces or scenes yet")
    keys = next(
        (r for r in results.current(db, source, "keyframes") if r.parameters.get("shots") == cut.cache_key), None
    )
    if keys is None:
        raise KeyError(f"{source.name} has no keyframes yet")
    faces: dict[str, list[int]] = kept.payload["faces"]
    state = decide(faces, decisions(faces, kept.payload["tag"]), skipped(db, source, kept))
    names = {who: fs.identity(who, app=APP).name for who in sorted(set(state.person.values()))}
    rate = mediatime.Rate.parse(cut.payload["rate"])
    frames = [(int(f["frame"]), float(rate.time_of(int(f["frame"])))) for f in keys.payload["frames"]]
    payload = cast_of(tracks_of(tracked), state.person, names, grouped.payload, frames)
    params = {
        "version": VERSION,
        "store": kept.cache_key,
        "scenes": grouped.cache_key,
        "keyframes": keys.cache_key,
        "people": sorted(state.person.items()),
        "names": sorted((str(k), v or "") for k, v in names.items()),
    }
    return results.record(
        db,
        source,
        "cast",
        results.Provenance(role="reels-cast"),
        inputs=[source.sha256 or ""],
        parameters=params,
        payload=payload,
        job_id=job_id,
    )


def current_cast(db: Session, source: Source) -> Result | None:
    """The cast of the current scenes, or None before the stage has run for them."""
    grouped = scenes.current(db, source)
    if grouped is None:
        return None
    return next(
        (r for r in results.current(db, source, "cast") if r.parameters.get("scenes") == grouped.cache_key), None
    )


def in_scene(cast_payload: Mapping[str, Any] | None, start: str) -> list[dict[str, Any]]:
    """The named people of the scene that starts at `start`: [{id, name, seconds}]."""
    if cast_payload is None:
        return []
    none: list[dict[str, Any]] = []
    return next((list(s["people"]) for s in cast_payload["scenes"] if s["start"] == start), none)


def for_describer(cast_payload: Mapping[str, Any] | None, frames: Sequence[int]) -> list[dict[str, Any]]:
    """The describer's `people`: each named person in these keyframes (in the order given), with the frames they are
    seen in, counted from 1, and where. Empty when nobody is named."""
    if cast_payload is None:
        return []
    names: Mapping[str, Any] = cast_payload["names"]
    seen: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for i, f in enumerate(frames, 1):
        for p in cast_payload["frames"].get(str(f), []):
            seen[int(p["id"])].append({"frame": i, "where": p["where"]})
    return [{"name": names.get(str(who)) or "someone named", "seen": s} for who, s in sorted(seen.items())]
