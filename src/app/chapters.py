"""A tape's chapters: its scenes grouped by occasion, and each chapter named (plan step 4, spec S5's next level up).

A family tape holds a few occasions one after another (Christmas morning, a day at the beach, a birthday tea), each
made of several scenes: the camcorder was stopped and started, and the room or the people moved on. A chapter is a run
of neighbouring scenes of one occasion. What says two neighbouring scenes are of one occasion:

- **They look alike as a whole.** Each scene's keyframe vectors (CLIP, the `embeddings` result the scenes were grouped
  from) are averaged, and neighbours are compared by the cosine of their averages. Scenes were already split where
  shots look unlike each other, so this compares at a coarser grain: the same house's rooms score higher than a beach.
- **Their descriptions share words.** The describer's `tags` (plain nouns: christmas, cake, garden) and `activity` of
  each scene; the share of words two neighbours have in common (Jaccard). An occasion word in both ("christmas") is
  the strongest evidence a picture cannot give.
- **Black between them**: the scenes' boundary says the tape was stopped, faded or left unrecorded there, which on a
  home tape usually means another day.
- **Indoors then outdoors** (the describer's `setting`): a weak sign of a move.

Each signal adds to the log-odds that a chapter starts at a scene boundary (`PARAMETERS`); a signal that is not there
(no vectors, no description) adds nothing. These weights are first guesses, not measured: they are to be fitted on
the chapters people correct, as the scenes' were on the first real tape. As for scenes, every boundary is kept with its
confidence, its level (`chapter` at one half or more, else `scene`) and its reasons in words, never a flat partition.

The grouping (`group`) is a pure function, stored as a result of kind `chapters` with role `reels-chapters`, keyed on
the scenes, the vectors and the descriptions it read. Then each chapter is **named** by strata-models (role
`chapter-name`, preset `chapter-name-v1`, text only: the prompt lives there) from its scenes' descriptions and speech:
a title, a summary, guesses with their evidence (spec S4), tags, and whether the scenes look like more than one
occasion (`mixed`, shown as a doubt on the page). One result of kind `chapter` per chapter, its span the chapter's,
recorded one by one so a job that stops part way keeps the names it has.

**The person corrects them** (overview §6): at a scene they say a chapter starts there, or that it belongs with the
chapter before (`mark`), and they can give a chapter its own title (`rename`). Each is an override on the tape, never
an edit of a result: a mark is one of the tape's inputs to the `chapters` stage (`jobs.inputs_in`), so a new mark runs
the grouping again, which follows every mark it has (confidence 1 or 0, the guess kept beside it) and asks names only
for the chapters that changed; a title is laid over the namer's when the chapters are read. Saying the chapters are
right is the pipeline's `check_chapters` gate. The marks and the checked tapes are what `fit` learns the weights from:
each boundary stores the signals it was judged by, so a fit needs no model run."""

import dataclasses
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy.orm import Session
from strata_python import mediatime
from strata_python.clients import models as sm
from strata_python.workflow import request_key

from app import derived, describe, overrides, results, scenes, shots, speech
from app.models import Override, Result, Source

PRODUCER = results.Provenance(role="reels-chapters")
VERSION = 2  # raise when `group` changes what it gives for the same inputs (2: marks, and the signals for `fit`)
PARAMETERS: dict[str, Any] = {
    "similar": 0.8,  # cosine of two scenes' mean pictures at which the picture says neither way
    "spread": 0.05,  # how quickly the picture's say grows away from `similar`
    "shared": 0.2,  # share of description words in common at which the words say neither way
    "shared_spread": 0.1,
    "black": 2.0,  # log-odds added by black between two scenes
    "setting": 0.5,  # log-odds added by indoors next to outdoors
}
NAME_ROLE = "chapter-name"
NAME_PRESET = "chapter-name-v1"
NAMED_SCENES = 24  # scenes of one chapter shown to the namer at most (spread through it), for a 9B model's context
SAID_CHARS = 400  # speech of each scene shown to the namer
NAME_VERSION = (
    2  # raise when what a name is keyed on changes (2: the scenes, not the grouping, so a regroup keeps names)
)
MARK = "chapter-start"  # an override on the tape: {"at": a scene's start, "start": whether a chapter starts there}
TITLE = "chapter-title"  # an override on the tape: {"at": a chapter's start, "title": the person's title for it}
TITLE_CHARS = 120


def mean(vectors: Sequence[Sequence[float]]) -> list[float] | None:
    if not vectors:
        return None
    return [sum(xs) / len(vectors) for xs in zip(*vectors, strict=True)]


def words_of(description: Mapping[str, Any] | None) -> set[str] | None:
    """A description's tags and activity as lowercase words; None when the scene is not described."""
    if description is None:
        return None
    out = {str(t).strip().lower() for t in description.get("tags", [])}
    out |= {w for w in str(description.get("activity", "")).lower().split() if len(w) > 3}
    return {w for w in out if w}


def _logistic(x: float) -> float:
    if x < 0:  # written for each side so neither overflows on a long way from 0
        z = math.exp(x)
        return z / (1 + z)
    return 1 / (1 + math.exp(-x))


def group(
    grouped: Mapping[str, Any],
    pictures: Sequence[Sequence[float] | None],
    told: Sequence[Mapping[str, Any] | None],
    parameters: Mapping[str, Any] | None = None,
    marks: Mapping[str, bool] | None = None,
) -> dict[str, Any]:
    """Chapters from a scenes payload (`scenes.group`), each scene's mean picture vector and each scene's description
    (None where there is none), in scene order, following the person's `marks` (a scene's start: whether a chapter
    starts there). Pure: the same arguments give the same answer."""
    p = {**PARAMETERS, **(parameters or {})}
    marks = marks or {}
    all_scenes = list(grouped["scenes"])
    if len(pictures) != len(all_scenes) or len(told) != len(all_scenes):
        raise ValueError("one picture and one description (or None) a scene")
    dark = {b["shot"] for b in grouped["boundaries"] if any(r.startswith("black") for r in b["reasons"])}
    boundaries: list[dict[str, Any]] = []
    chapters: list[dict[str, Any]] = []
    for i, sc in enumerate(all_scenes):
        if i == 0:
            chapters.append(_chapter(sc, i, None))
            continue
        logit = 0.0
        reasons: list[str] = []
        a, b = pictures[i - 1], pictures[i]
        similarity = None if a is None or b is None else scenes.cosine(a, b)
        if similarity is not None:
            logit -= (similarity - float(p["similar"])) / float(p["spread"])
            reasons.append(
                f"{'pictures alike' if similarity >= float(p['similar']) else 'pictures differ'} ({similarity:.2f})"
            )
        wa, wb = words_of(told[i - 1]), words_of(told[i])
        shared: list[str] = []
        share: float | None = None
        if wa is not None and wb is not None and (wa or wb):
            shared = sorted(wa & wb)
            share = len(shared) / len(wa | wb)
            logit -= (share - float(p["shared"])) / float(p["shared_spread"])
            reasons.append(f"described alike: {', '.join(shared)}" if shared else "described differently")
        black = sc["shots"][0] in dark
        if black:
            logit += float(p["black"])
            reasons.append("black between")
        sa, sb = (None if t is None else t.get("setting") for t in (told[i - 1], told[i]))
        moved = {sa, sb} == {"indoor", "outdoor"}
        if moved:
            logit += float(p["setting"])
            reasons.append(f"{sa}s then {sb}s")
        guess = _logistic(logit) if reasons else 0.5
        if not reasons:
            reasons.append("nothing to compare")
        marked = marks.get(sc["start"])
        confidence = guess if marked is None else float(marked)
        if marked is not None:
            reasons.append("you started a chapter here" if marked else "you put it with the chapter before")
        level = "chapter" if confidence >= 0.5 else "scene"
        boundaries.append(
            {
                "scene": i,
                "at": sc["start"],
                "level": level,
                "confidence": round(confidence, 4),
                "similarity": None if similarity is None else round(similarity, 4),
                "shared": shared,
                "reasons": reasons,
                "guess": round(guess, 4),  # what the signals said, before any mark
                "marked": marked,
                "signals": {"similarity": similarity, "share": share, "black": black, "moved": moved},
            }
        )
        if level == "chapter":
            chapters.append(_chapter(sc, i, confidence))
        else:
            last = chapters[-1]
            last["scenes"].append(i)
            last["end_frame"], last["end"] = sc["end_frame"], sc["end"]
    return {
        "version": VERSION,
        "parameters": p,
        "rate": grouped["rate"],
        "marks": dict(sorted(marks.items())),
        "boundaries": boundaries,
        "chapters": chapters,
    }


def _chapter(sc: Mapping[str, Any], i: int, confidence: float | None) -> dict[str, Any]:
    return {
        "start_frame": sc["start_frame"],
        "end_frame": sc["end_frame"],
        "start": sc["start"],
        "end": sc["end"],
        "scenes": [i],
        "confidence": None if confidence is None else round(confidence, 4),
    }


def _pictures(db: Session, grouped: Result, found: Result) -> list[list[float] | None]:
    """Each scene's mean keyframe vector, from the embeddings the scenes were grouped from (None where it has none)."""
    stored = results.find(db, str(grouped.parameters.get("embeddings")))
    if stored is None:
        return [None] * len(grouped.payload["scenes"])
    with open(derived.path_of(stored), encoding="utf-8") as f:
        vectors = {int(k): v for k, v in json.load(f)["vectors"].items()}
    return [
        mean([vectors[n] for n in describe.scene_frames(found.payload, sc, set(vectors))])
        for sc in grouped.payload["scenes"]
    ]


def find_chapters(db: Session, source: Source, *, job_id: int | None = None) -> tuple[Result, bool]:
    """The source's chapters, grouped now unless the same grouping of the same results is stored. KeyError before the
    scenes are grouped. The `chapters` stage needs `describe`, so every scene's description that can be is written."""
    if source.sha256 is None:
        raise ValueError(f"{source.name} has no content hash yet")
    found, grouped = shots.current(db, source), scenes.current(db, source)
    if found is None or grouped is None or grouped.parameters.get("shots") != found.cache_key:
        raise KeyError(f"{source.name} has no scenes yet: its chapters wait for the scenes step")
    told = describe.current_descriptions(db, source)
    described = [told.get(sc["start"]) for sc in grouped.payload["scenes"]]
    marked = marks(db, source)
    params = {
        **PARAMETERS,
        "version": VERSION,
        "scenes": grouped.cache_key,
        "embeddings": grouped.parameters.get("embeddings"),
        "descriptions": request_key(*(None if r is None else r.cache_key for r in described)),
        "marks": marks_key(marked),
    }
    stored = results.find(db, results.cache_key("chapters", [source.sha256], PRODUCER, params))
    if stored is not None:
        return stored, False
    payload = group(
        grouped.payload,
        _pictures(db, grouped, found),
        [None if r is None else r.payload["description"] for r in described],
        marks=marked,
    )
    return results.record(
        db, source, "chapters", PRODUCER, inputs=[source.sha256], parameters=params, payload=payload, job_id=job_id
    )


def current(db: Session, source: Source) -> Result | None:
    """The chapters of the current scenes, or None before they are grouped (or when the scenes were grouped again).
    A grouping supersedes the one before (`results.record`), so between a mark and the job that obeys it this is the
    grouping before the mark (`follows_marks` says which)."""
    grouped = scenes.current(db, source)
    if grouped is None:
        return None
    return next(
        (r for r in results.current(db, source, "chapters") if r.parameters.get("scenes") == grouped.cache_key), None
    )


def follows_marks(db: Session, source: Source, grouped: Result) -> bool:
    """Whether `grouped` follows the person's marks now: False between a mark and the grouping that obeys it."""
    return grouped.parameters.get("marks", []) == marks_key(marks(db, source))


# --- the person's corrections ----------------------------------------------------------------------------------------


def _said(db: Session, source: Source, kind: str) -> dict[str, Override]:
    return {str(o.value["at"]): o for o in overrides.active(db, "tape", source.id) if o.kind == kind}


def marks(db: Session, source: Source) -> dict[str, bool]:
    """The person's marks on the tape: a scene's start (`mediatime.text`), and whether a chapter starts there."""
    return {at: bool(o.value["start"]) for at, o in _said(db, source, MARK).items()}


def marks_key(m: Mapping[str, bool]) -> list[list[Any]]:
    """The marks as the `chapters` stage's input and the grouping's parameter: in time order, plain JSON."""
    return [[at, start] for at, start in sorted(m.items(), key=lambda kv: mediatime.rational(kv[0]))]


def titles(db: Session, source: Source) -> dict[str, str]:
    """The person's titles, by the start of the chapter they name."""
    return {at: str(o.value["title"]) for at, o in _said(db, source, TITLE).items()}


def _put(db: Session, source: Source, kind: str, at: str, value: dict[str, Any] | None, author: str) -> None:
    """Make the person's word on `at` the one that holds: a new override, a change to the one there, or (None) the one
    there taken back. Saying the same again changes nothing."""
    old = _said(db, source, kind).get(at)
    if value is None:
        if old is not None:
            overrides.withdraw(db, old)
    elif old is None:
        overrides.add(db, "tape", source.id, kind, value, author=author)
    elif old.value != value:
        overrides.change(db, old, value, author=author)


def mark(db: Session, source: Source, scene: int, start: bool | None, *, author: str) -> None:
    """Say a chapter starts at scene `scene` (True), that the scene belongs with the chapter before (False), or leave it
    to the grouping again (None). KeyError before the scenes are grouped; ValueError for the first scene or one there
    is not."""
    grouped = scenes.current(db, source)
    if grouped is None:
        raise KeyError(f"{source.name} has no scenes yet")
    found = grouped.payload["scenes"]
    if not 0 < scene < len(found):
        raise ValueError(f"a chapter can be marked at scenes 2 to {len(found)}, not scene {scene + 1}")
    at = str(found[scene]["start"])
    _put(db, source, MARK, at, None if start is None else {"at": at, "start": start}, author)


def rename(db: Session, source: Source, chapter: int, title: str | None, *, author: str) -> None:
    """Give chapter `chapter` the person's title, or (None) go back to the namer's. KeyError before there are chapters;
    ValueError for a chapter there is not, or a title that is empty or too long."""
    grouped = current(db, source)
    if grouped is None:
        raise KeyError(f"{source.name} has no chapters yet")
    found = grouped.payload["chapters"]
    if not 0 <= chapter < len(found):
        raise ValueError(f"{source.name} has chapters 1 to {len(found)}, not chapter {chapter + 1}")
    at = str(found[chapter]["start"])
    if title is None:
        _put(db, source, TITLE, at, None, author)
        return
    title = " ".join(title.split())
    if not title:
        raise ValueError("a chapter's title needs a word or more")
    if len(title) > TITLE_CHARS:
        raise ValueError(f"a chapter's title is at most {TITLE_CHARS} characters")
    _put(db, source, TITLE, at, {"at": at, "title": title}, author)


# --- learning the weights from the corrections -----------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Example:
    """A boundary the person has judged: the signals it was grouped by, and whether a chapter starts there."""

    similarity: float | None
    share: float | None
    black: bool
    moved: bool
    starts: bool


def logit_of(e: Example, p: Mapping[str, Any]) -> float:
    """The log-odds that a chapter starts at `e` under parameters `p`: `group`'s sum, from the stored signals."""
    x = 0.0
    if e.similarity is not None:
        x -= (e.similarity - float(p["similar"])) / float(p["spread"])
    if e.share is not None:
        x -= (e.share - float(p["shared"])) / float(p["shared_spread"])
    return x + float(p["black"]) * e.black + float(p["setting"]) * e.moved


def examples_of(payload: Mapping[str, Any], checked: bool) -> list[Example]:
    """What a grouping teaches: each boundary the person marked, and every boundary of a tape they said is right
    (checked), with what it was grouped as. Nothing from a grouping before `VERSION` 2, which kept no signals."""
    out: list[Example] = []
    for b in payload["boundaries"]:
        signals = b.get("signals")
        if signals is None or (b.get("marked") is None and not checked):
            continue
        starts = b["marked"] if b.get("marked") is not None else b["level"] == "chapter"
        out.append(
            Example(signals["similarity"], signals["share"], bool(signals["black"]), bool(signals["moved"]), starts)
        )
    return out


# How far each parameter may sensibly move for one unit of the prior's pull: a few marks move the weights a little, many
# move them as far as they say. `spread` and `shared_spread` are fitted as logarithms, so they stay above 0.
_SCALES = {"similar": 0.1, "spread": 1.0, "shared": 0.1, "shared_spread": 1.0, "black": 2.0, "setting": 1.0}
_LOGS = ("spread", "shared_spread")


def fit(
    examples: Sequence[Example], start: Mapping[str, Any] | None = None, *, prior: float = 1.0, steps: int = 500
) -> dict[str, float]:
    """The parameters that best agree with the person's judgements (logistic regression on `group`'s own sum), held
    towards `start` (the weights now) by a Gaussian prior of weight `prior`, so a handful of marks cannot throw them
    far. Gradient descent with a step that halves whenever it would make things worse: deterministic, and quick for
    the hundreds of boundaries a few tapes have."""
    p0 = {k: float((start or PARAMETERS)[k]) for k in _SCALES}
    origin = {k: math.log(v) if k in _LOGS else v for k, v in p0.items()}
    if not examples:
        return p0
    n = len(examples)

    def params(theta: Mapping[str, float]) -> dict[str, float]:  # a spread kept within e^-20 to e^20: finite
        return {k: math.exp(min(max(v, -20.0), 20.0)) if k in _LOGS else v for k, v in theta.items()}

    def loss(theta: Mapping[str, float]) -> float:
        p = params(theta)
        held = sum(((theta[k] - origin[k]) / _SCALES[k]) ** 2 for k in theta) * prior / 2
        return held + sum(_log_loss(logit_of(e, p), e.starts) for e in examples)

    def gradient(theta: Mapping[str, float]) -> dict[str, float]:
        p = params(theta)
        grad = {k: prior * (theta[k] - origin[k]) / _SCALES[k] ** 2 for k in theta}
        for e in examples:
            err = _logistic(logit_of(e, p)) - e.starts  # d(log loss)/d(logit)
            if e.similarity is not None:
                grad["similar"] += err / p["spread"]
                grad["spread"] += err * (e.similarity - p["similar"]) / p["spread"]  # d/d(log spread)
            if e.share is not None:
                grad["shared"] += err / p["shared_spread"]
                grad["shared_spread"] += err * (e.share - p["shared"]) / p["shared_spread"]
            grad["black"] += err * e.black
            grad["setting"] += err * e.moved
        return grad

    theta, rate, now = dict(origin), 0.1 / n, loss(origin)
    for _ in range(steps):
        g = gradient(theta)
        while rate > 1e-12:
            tried = {k: theta[k] - rate * g[k] * _SCALES[k] ** 2 for k in theta}  # each in its own units
            after = loss(tried)
            if after <= now:
                theta, now, rate = tried, after, rate * 1.2
                break
            rate /= 2
    return {k: round(v, 4) for k, v in params(theta).items()}


def _log_loss(logit: float, starts: bool) -> float:
    """-log P(what the person said), from the log-odds, without overflow."""
    x = -logit if starts else logit
    return x + math.log1p(math.exp(-x)) if x > 0 else math.log1p(math.exp(x))


def agreement(examples: Sequence[Example], p: Mapping[str, Any]) -> float | None:
    """The share of judged boundaries the parameters group as the person did; None with none judged."""
    if not examples:
        return None
    return sum((logit_of(e, p) >= 0) == e.starts for e in examples) / len(examples)


def context_of(
    chapter: Mapping[str, Any], grouped: Mapping[str, Any], told: Mapping[str, Result], said: dict[str, Any] | None
) -> dict[str, Any]:
    """What the namer is told about a chapter: up to `NAMED_SCENES` of its scenes, spread through it, each with its
    place, length, description and speech."""
    picked = describe.pick(list(chapter["scenes"]), NAMED_SCENES)
    out: list[dict[str, Any]] = []
    for i in picked:
        sc = grouped["scenes"][i]
        start, end = mediatime.rational(sc["start"]), mediatime.rational(sc["end"])
        r = told.get(sc["start"])
        d: dict[str, Any] = {} if r is None else r.payload["description"]
        heard = speech.said_in(said, float(start), float(end))
        out.append(
            {
                "start": _clock(float(start)),
                "seconds": round(float(end - start), 1),
                "summary": d.get("summary"),
                "activity": d.get("activity"),
                "setting": d.get("setting"),
                "tags": d.get("tags", []),
                "guesses": [g["guess"] for g in d.get("inferred", [])],
                "people": d.get("people"),
                "children": d.get("children"),
                "speech": heard if len(heard) <= SAID_CHARS else heard[: SAID_CHARS - 2].rstrip() + " …",
            }
        )
    start, end = mediatime.rational(chapter["start"]), mediatime.rational(chapter["end"])
    return {"seconds": round(float(end - start), 1), "scene_count": len(chapter["scenes"]), "scenes": out}


def _clock(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def name(db: Session, source: Source, *, job_id: int | None = None) -> list[tuple[Result, bool]]:
    """A name for every current chapter, each asked of strata-models unless the same request is stored, and whether
    it is new. KeyError before the chapters are grouped."""
    assert source.sha256 is not None  # noqa: S101 - the chapters needed it
    grouped_chapters = current(db, source)
    grouped = scenes.current(db, source)
    if grouped_chapters is None or grouped is None:
        raise KeyError(f"{source.name} has no chapters yet: their names wait for the chapters step")
    told = describe.current_descriptions(db, source)
    said = speech.current(db, source)
    now = sm.role_settings(NAME_ROLE)
    out: list[tuple[Result, bool]] = []
    for chapter in grouped_chapters.payload["chapters"]:
        context = context_of(chapter, grouped.payload, told, None if said is None else said.payload)
        # Keyed on what the namer is told and the scenes, not the grouping: a chapter a regroup left as it was keeps its name.
        params = {"preset": NAME_PRESET, "version": NAME_VERSION, "scenes": grouped.cache_key, "context": context}
        span = mediatime.Span.of(mediatime.rational(chapter["start"]), mediatime.rational(chapter["end"]))
        if now is not None:
            planned = results.Provenance(role=NAME_ROLE, model=now.model, settings_hash=now.settings_hash)
            stored = results.find(db, results.cache_key("chapter", [source.sha256], planned, params, span))
            if stored is not None:
                out.append((stored, False))
                continue
        answer, served = sm.run_with_provenance(
            NAME_ROLE, {"preset": NAME_PRESET, "context": context}, shots.APP, priority="low"
        )
        said_back = sm.Answer.from_json(answer).json
        if not isinstance(said_back, dict):
            raise sm.JobFailed(
                f"strata-models' {NAME_ROLE} did not answer in its schema for the chapter at {chapter['start']}"
            )
        out.append(
            results.record(
                db,
                source,
                "chapter",
                results.Provenance.of(NAME_ROLE, dataclasses.asdict(served)),
                inputs=[source.sha256],
                parameters=params,
                payload={"name": said_back},
                span=span,
                job_id=job_id,
            )
        )
    return out


def current_names(db: Session, source: Source) -> dict[str, Result]:
    """The names of the current chapters, by the chapter's start (`mediatime.text`); empty before they are named. A
    name is of a chapter when it covers the same span of the same scenes; the latest, when there are several."""
    grouped = current(db, source)
    found = scenes.current(db, source)
    if grouped is None or found is None:
        return {}
    spans = {(c["start"], c["end"]) for c in grouped.payload["chapters"]}
    out: dict[str, Result] = {}
    for r in results.current(db, source, "chapter"):
        if r.span_start is None or (r.span_start, r.span_end) not in spans:
            continue
        if r.parameters.get("scenes") != found.cache_key:
            continue
        if r.span_start not in out or r.id > out[r.span_start].id:
            out[r.span_start] = r
    return out
