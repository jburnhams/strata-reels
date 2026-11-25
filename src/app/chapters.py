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
recorded one by one so a job that stops part way keeps the names it has."""

import dataclasses
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy.orm import Session
from strata_python import mediatime
from strata_python.clients import models as sm
from strata_python.workflow import request_key

from app import derived, describe, results, scenes, shots, speech
from app.models import Result, Source

PRODUCER = results.Provenance(role="reels-chapters")
VERSION = 1  # raise when `group` changes what it gives for the same inputs
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
    return 1 / (1 + math.exp(-x))


def group(
    grouped: Mapping[str, Any],
    pictures: Sequence[Sequence[float] | None],
    told: Sequence[Mapping[str, Any] | None],
    parameters: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Chapters from a scenes payload (`scenes.group`), each scene's mean picture vector and each scene's description
    (None where there is none), in scene order. Pure: the same arguments give the same answer."""
    p = {**PARAMETERS, **(parameters or {})}
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
        if wa is not None and wb is not None and (wa or wb):
            shared = sorted(wa & wb)
            share = len(shared) / len(wa | wb)
            logit -= (share - float(p["shared"])) / float(p["shared_spread"])
            reasons.append(f"described alike: {', '.join(shared)}" if shared else "described differently")
        if sc["shots"][0] in dark:
            logit += float(p["black"])
            reasons.append("black between")
        sa, sb = (None if t is None else t.get("setting") for t in (told[i - 1], told[i]))
        if {sa, sb} == {"indoor", "outdoor"}:
            logit += float(p["setting"])
            reasons.append(f"{sa}s then {sb}s")
        confidence = _logistic(logit) if reasons else 0.5
        if not reasons:
            reasons.append("nothing to compare")
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
    params = {
        **PARAMETERS,
        "version": VERSION,
        "scenes": grouped.cache_key,
        "embeddings": grouped.parameters.get("embeddings"),
        "descriptions": request_key(*(None if r is None else r.cache_key for r in described)),
    }
    stored = results.find(db, results.cache_key("chapters", [source.sha256], PRODUCER, params))
    if stored is not None:
        return stored, False
    payload = group(
        grouped.payload,
        _pictures(db, grouped, found),
        [None if r is None else r.payload["description"] for r in described],
    )
    return results.record(
        db, source, "chapters", PRODUCER, inputs=[source.sha256], parameters=params, payload=payload, job_id=job_id
    )


def current(db: Session, source: Source) -> Result | None:
    """The chapters of the current scenes, or None before they are grouped (or when the scenes were grouped again)."""
    grouped = scenes.current(db, source)
    if grouped is None:
        return None
    return next(
        (r for r in results.current(db, source, "chapters") if r.parameters.get("scenes") == grouped.cache_key), None
    )


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
        params = {"preset": NAME_PRESET, "chapters": grouped_chapters.cache_key, "context": context}
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
    """The names of the current chapters, by the chapter's start (`mediatime.text`); empty before they are named."""
    grouped = current(db, source)
    if grouped is None:
        return {}
    return {
        r.span_start: r
        for r in results.current(db, source, "chapter")
        if r.span_start is not None and r.parameters.get("chapters") == grouped.cache_key
    }
