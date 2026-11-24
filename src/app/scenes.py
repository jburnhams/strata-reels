"""A tape's scenes: its shots grouped by what they show (R1 step 2, spec S5).

On a family tape a shot ends wherever the camcorder was stopped; a scene (the birthday, the beach) is the run of shots
of one event. What says two neighbouring shots belong together:

- **They look alike.** The keyframes (`shots.keyframes`) are embedded by strata-models (role `image-embed`, CLIP), and
  neighbouring shots are compared by the mean cosine similarity of every keyframe of one with every one of the other.
  The same room or garden from a restarted camcorder scores high; another place scores low. Stored as a result of kind `embeddings` (a file of the vectors, kept for search and
  near-duplicates later), with the model and settings that made it.
- **Black between them** (a shot that is black nearly all through, or a black stretch at the cut): the tape was
  stopped, faded or left unrecorded, which on a home tape usually means another day.

The grouping is this module's own code, deterministic, over those results: a pure function (`group`) of the shots,
the vectors and `PARAMETERS`, stored as a result of kind `scenes` with role `reels-scenes`, `VERSION` in its parameters
and the results it read as its cache key's parameters, so any change to them makes a new grouping. Spec S5 asks for a
hierarchy of candidate boundaries with a confidence and a reason, not a flat partition: every cut is kept as a boundary
with its confidence that it also starts a scene, its level (`scene` when that is at least one half, else `shot`) and its
reasons in words. The planner and the chapters screen read the levels; a person's correction is an override on the
result (step 4), never an edit of it.

The confidence from similarity is a logistic around `similar` (cosine where a boundary is as likely as not), `spread`
wide. Both are fitted to one real family tape (2026-11-24, CLIP ViT-B/32): 36 of its 47 boundaries marked by eye as a
new scene or not, the rest left out as arguable. They put 31 of the 36 on the right side, as does any cosine from 0.66
to 0.70 (the first guess, 0.82 on one picture a shot, put 23: it split nearly every shot into a scene). Thirty-six boundaries of one house's rooms are few: what would
measure it better is more tapes, whose corrections on step 4's chapters screen are the marks."""

import dataclasses
import json
import math
import os
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy.orm import Session
from strata_python import files, mediatime
from strata_python.clients import models as sm

from app import derived, results, settings, shots
from app.models import Result, Source

EMBED_ROLE = "image-embed"
PRODUCER = results.Provenance(role="reels-scenes")
VERSION = 2  # raise when `group` changes what it gives for the same inputs
PARAMETERS: dict[str, Any] = {
    "similar": 0.67,  # cosine at which a boundary is as likely as not
    "spread": 0.05,  # how quickly the confidence moves away from one half
    "black": 0.9,  # the confidence of a boundary with black at it
    "black_near_s": "1",  # a black stretch this close before or after a cut is at it
}


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity; strata-models' vectors are L2-normalised, but a vector read back rounded is not quite."""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return 0.0 if na == 0 or nb == 0 else dot / (na * nb)


def alike(a: Sequence[Sequence[float]], b: Sequence[Sequence[float]]) -> float | None:
    """The mean cosine similarity of every picture of one shot with every picture of another; None when either has none."""
    pairs = [cosine(x, y) for x in a for y in b]
    return sum(pairs) / len(pairs) if pairs else None


def group(
    found: dict[str, Any], vectors: Mapping[int, Sequence[float]], parameters: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Scenes from a shots payload (`shots.payload_of`) and the keyframes' vectors by the keyframe's frame number.
    Pure: the same arguments give the same answer. Black shots are in no scene: they are the gaps between them."""
    p = {**PARAMETERS, **(parameters or {})}
    rate = mediatime.Rate.parse(found["rate"])
    near = rate.frame_at(mediatime.rational(p["black_near_s"]))
    black = [(int(b["start_frame"]), int(b["end_frame"])) for b in found["black"]]
    taken = sorted(vectors)
    boundaries: list[dict[str, Any]] = []
    scenes: list[dict[str, Any]] = []
    prev: dict[str, Any] | None = None
    prev_index = -1
    gap = False  # a black shot since the previous shot shown
    for i, shot in enumerate(found["shots"]):
        if shot["black"]:
            gap = True
            continue
        if prev is None:
            scenes.append(_scene(shot, i, None))
        else:
            at = int(shot["start_frame"])
            reasons: list[str] = []
            confidence = 0.5
            similarity = alike(
                [vectors[f] for f in shots.frames_in(prev, taken)], [vectors[f] for f in shots.frames_in(shot, taken)]
            )
            if similarity is not None:
                confidence = 1 / (1 + math.exp((similarity - float(p["similar"])) / float(p["spread"])))
                reasons.append(
                    f"{'pictures alike' if similarity >= float(p['similar']) else 'pictures differ'} ({similarity:.2f})"
                )
            else:
                reasons.append("no picture to compare")
            dark = gap or any(x <= at + near and y >= int(prev["end_frame"]) - near for x, y in black)
            if dark:
                confidence = max(confidence, float(p["black"]))
                reasons.append("black between" if gap else "black at the cut")
            level = "scene" if confidence >= 0.5 else "shot"
            boundaries.append(
                {
                    "frame": at,
                    "at": shot["start"],
                    "level": level,
                    "confidence": round(confidence, 4),
                    "similarity": None if similarity is None else round(similarity, 4),
                    "cut_score": shot["score"],
                    "reasons": reasons,
                    "after_shot": prev_index,
                    "shot": i,
                }
            )
            if level == "scene":
                scenes.append(_scene(shot, i, confidence))
            else:
                last = scenes[-1]
                last["shots"].append(i)
                last["end_frame"], last["end"] = shot["end_frame"], shot["end"]
        prev, prev_index, gap = shot, i, False
    return {"version": VERSION, "parameters": p, "rate": found["rate"], "boundaries": boundaries, "scenes": scenes}


def _scene(shot: dict[str, Any], i: int, confidence: float | None) -> dict[str, Any]:
    return {
        "start_frame": shot["start_frame"],
        "end_frame": shot["end_frame"],
        "start": shot["start"],
        "end": shot["end"],
        "shots": [i],
        # how sure the cut that starts it is a scene's start; None for the first, which the tape's start begins
        "confidence": None if confidence is None else round(confidence, 4),
    }


def _vectors_file(r: Result) -> dict[int, list[float]]:
    with open(derived.path_of(r), encoding="utf-8") as f:
        return {int(k): v for k, v in json.load(f)["vectors"].items()}


def embed(db: Session, source: Source, keys: Result, *, job_id: int | None = None) -> tuple[Result, bool]:
    """The keyframes' CLIP vectors, asked of strata-models unless the same request is stored (and its file is there)."""
    assert source.sha256 is not None  # noqa: S101 - the keyframes needed it
    params = {"keyframes": keys.cache_key}
    now = sm.role_settings(EMBED_ROLE)
    if now is not None:
        planned = results.Provenance(role=EMBED_ROLE, model=now.model, settings_hash=now.settings_hash)
        found = results.find(db, results.cache_key("embeddings", [source.sha256], planned, params))
        if found is not None and os.path.isfile(derived.path_of(found)):
            return found, False
    folder = derived.path_of(keys)
    frames = [int(f["frame"]) for f in keys.payload["frames"]]
    paths = [os.path.join(folder, str(f["name"])) for f in keys.payload["frames"]]
    answer, served = sm.run_with_provenance(EMBED_ROLE, {"paths": paths}, shots.APP, priority="low")
    images = sm.Embeddings.from_json(answer).images
    if len(images) != len(frames):
        raise sm.JobFailed(f"strata-models embedded {len(images)} of {len(frames)} keyframes")
    provenance = results.Provenance.of(EMBED_ROLE, dataclasses.asdict(served))
    key = results.cache_key("embeddings", [source.sha256], provenance, params)
    rel = os.path.join(derived.folder(key), "embeddings.json")
    out = os.path.join(settings.SETTINGS.home(), rel)
    vectors = {str(n): [round(x, 5) for x in v] for n, v in zip(frames, images, strict=True)}
    files.write_json_atomic(out, {"dim": len(images[0]) if images else 0, "vectors": vectors}, indent=None)
    return results.record(
        db,
        source,
        "embeddings",
        provenance,
        inputs=[source.sha256],
        parameters=params,
        payload={"frames": len(frames), "sha256": files.sha256_hex(out)},
        file=rel,
        job_id=job_id,
    )


def find_scenes(db: Session, source: Source, *, job_id: int | None = None) -> tuple[Result, bool]:
    """The source's current scenes, grouped now unless the same grouping of the same results is stored; the vectors
    are asked of strata-models first when they are not. KeyError before the keyframes are made."""
    if source.sha256 is None:
        raise ValueError(f"{source.name} has no content hash yet")
    found = shots.current(db, source)
    keys = next(
        (r for r in results.current(db, source, "keyframes") if found and r.parameters.get("shots") == found.cache_key),
        None,
    )
    if found is None or keys is None:
        raise KeyError(f"{source.name} has no keyframes yet: its scenes wait for the keyframes job")
    vectors, _ = embed(db, source, keys, job_id=job_id)
    params = {**PARAMETERS, "version": VERSION, "shots": found.cache_key, "embeddings": vectors.cache_key}
    stored = results.find(db, results.cache_key("scenes", [source.sha256], PRODUCER, params))
    if stored is not None:
        return stored, False
    grouped = group(found.payload, _vectors_file(vectors))
    return results.record(
        db, source, "scenes", PRODUCER, inputs=[source.sha256], parameters=params, payload=grouped, job_id=job_id
    )


def current(db: Session, source: Source) -> Result | None:
    """The source's scenes now, or None before they are grouped."""
    return next((r for r in results.current(db, source, "scenes") if r.span_start is None), None)
