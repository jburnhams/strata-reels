"""What each scene shows, and how many people are in it (R1 step 3, spec S4).

- **A description per scene** from strata-models (role `scene-describe`, preset `scene-describe-v2`: a vision-language
  model, Qwen3.5-9B today). It is given up to `FRAMES` of the scene's keyframes, spread through it, and its length,
  shot count and speech (`speech.said_in`). The preset keeps what is seen (`observed`) apart from what is guessed
  (`inferred`, each guess with its evidence), as spec S4 asks, counts people and children without naming anyone, and
  says what is wrong with the picture. One result of kind `description` per scene, its span the scene's, so a long tape
  is described scene by scene and a job that stops part way keeps what it finished. The prompt is strata-models', not
  this app's: a better prompt is a new preset there, and its name is in this result's parameters.
- **Faces** (role `faces`, insightface today) on every keyframe, boxes and scores only: no embeddings, so no one can be
  recognised until the person has said who may be (R2, with consent). One result of kind `faces` for the keyframes; a
  scene's count (`people_in`) is the most faces on any one of its keyframes, a floor rather than a count: faces turned
  away are not found, and not everyone is in one picture.
- Both are keyed on what they read (the scenes and the transcript, the keyframes), so a regrouping or a new transcript
  asks again, and `current_descriptions` gives only those of the current scenes."""

import dataclasses
import os
from typing import Any

from sqlalchemy.orm import Session
from strata_python import mediatime
from strata_python.clients import models as sm

from app import derived, results, scenes, shots, speech
from app.models import Result, Source

ROLE = "scene-describe"
PRESET = "scene-describe-v2"
FACES_ROLE = "faces"
FRAMES = 6  # keyframes shown to the describer: enough to see a long shot move on, few enough for a 9B model's context
FACE_FIELDS = ["bbox", "det_score"]  # never the embedding: recognition waits for consent (R2)


def pick(frames: list[int], n: int = FRAMES) -> list[int]:
    """`n` of `frames` spread from the first to the last (all of them when there are no more than `n`)."""
    if len(frames) <= n:
        return list(frames)
    return [frames[round(i * (len(frames) - 1) / (n - 1))] for i in range(n)] if n > 1 else [frames[len(frames) // 2]]


def scene_frames(shots_payload: dict[str, Any], scene: dict[str, Any], kept: set[int]) -> list[int]:
    """The keyframes of a scene's shots, in time order."""
    frames = sorted(kept)
    return [f for i in scene["shots"] for f in shots.frames_in(shots_payload["shots"][i], frames)]


def _keyframes(db: Session, source: Source, found: Result) -> Result:
    keys = next(
        (r for r in results.current(db, source, "keyframes") if r.parameters.get("shots") == found.cache_key), None
    )
    if keys is None:
        raise KeyError(f"{source.name} has no keyframes yet")
    return keys


def _inputs(db: Session, source: Source) -> tuple[Result, Result, Result]:
    """The current shots, their keyframes and the scenes grouped from them. KeyError before the scenes step has run."""
    found = shots.current(db, source)
    grouped = scenes.current(db, source)
    if found is None or grouped is None or grouped.parameters.get("shots") != found.cache_key:
        raise KeyError(f"{source.name} has no scenes yet: its descriptions wait for the scenes step")
    return found, _keyframes(db, source, found), grouped


def describe(db: Session, source: Source, *, job_id: int | None = None) -> list[tuple[Result, bool]]:
    """A description of every current scene, each asked of strata-models unless the same request is stored, and
    whether it is new. Recorded one by one, so a failure part way keeps the scenes already described. The transcript
    is read when there is one; the `describe` stage needs the `transcript` stage (`jobs.TAPE`), so it has run."""
    assert source.sha256 is not None  # noqa: S101 - the scenes needed it
    found, keys, grouped = _inputs(db, source)
    said = speech.current(db, source)
    folder = derived.path_of(keys)
    names = {int(f["frame"]): str(f["name"]) for f in keys.payload["frames"]}
    now = sm.role_settings(ROLE)
    out: list[tuple[Result, bool]] = []
    for scene in grouped.payload["scenes"]:
        frames = pick(scene_frames(found.payload, scene, set(names)))
        start, end = mediatime.rational(scene["start"]), mediatime.rational(scene["end"])
        context = {
            "seconds": round(float(end - start), 1),
            "shots": len(scene["shots"]),
            "speech": speech.said_in(None if said is None else said.payload, float(start), float(end)),
        }
        params = {
            "preset": PRESET,
            "scenes": grouped.cache_key,
            "transcript": None if said is None else said.cache_key,
            "frames": frames,
            "context": context,
        }
        span = mediatime.Span.of(start, end)
        if now is not None:
            planned = results.Provenance(role=ROLE, model=now.model, settings_hash=now.settings_hash)
            stored = results.find(db, results.cache_key("description", [source.sha256], planned, params, span))
            if stored is not None:
                out.append((stored, False))
                continue
        ask = {"images": [os.path.join(folder, names[f]) for f in frames], "preset": PRESET, "context": context}
        answer, served = sm.run_with_provenance(ROLE, ask, shots.APP, priority="low")
        said_back = sm.Answer.from_json(answer).json
        if not isinstance(said_back, dict):
            raise sm.JobFailed(f"strata-models' {ROLE} did not answer in its schema for the scene at {scene['start']}")
        out.append(
            results.record(
                db,
                source,
                "description",
                results.Provenance.of(ROLE, dataclasses.asdict(served)),
                inputs=[source.sha256],
                parameters=params,
                payload={"description": said_back, "frames": frames, "speech": context["speech"]},
                span=span,
                job_id=job_id,
            )
        )
    return out


def current_descriptions(db: Session, source: Source) -> dict[str, Result]:
    """The descriptions of the current scenes, by the scene's start (`mediatime.text`); empty before they are made."""
    grouped = scenes.current(db, source)
    if grouped is None:
        return {}
    return {
        r.span_start: r
        for r in results.current(db, source, "description")
        if r.span_start is not None and r.parameters.get("scenes") == grouped.cache_key
    }


def find_faces(db: Session, source: Source, *, job_id: int | None = None) -> tuple[Result, bool]:
    """The faces on every keyframe of the current shots, asked of strata-models unless the same request is stored.
    KeyError before the keyframes are made."""
    if source.sha256 is None:
        raise ValueError(f"{source.name} has no content hash yet")
    found = shots.current(db, source)
    if found is None:
        raise KeyError(f"{source.name} has no shots yet: its faces wait for the keyframes job")
    keys = _keyframes(db, source, found)
    params: dict[str, Any] = {"keyframes": keys.cache_key, "fields": FACE_FIELDS}
    now = sm.role_settings(FACES_ROLE)
    if now is not None:
        planned = results.Provenance(role=FACES_ROLE, model=now.model, settings_hash=now.settings_hash)
        stored = results.find(db, results.cache_key("faces", [source.sha256], planned, params))
        if stored is not None:
            return stored, False
    folder = derived.path_of(keys)
    frames = [int(f["frame"]) for f in keys.payload["frames"]]
    paths = [os.path.join(folder, str(f["name"])) for f in keys.payload["frames"]]
    answer, served = sm.run_with_provenance(
        FACES_ROLE, {"paths": paths, "fields": FACE_FIELDS}, shots.APP, priority="low"
    )
    each = sm.Faces.many(answer)
    if len(each) != len(frames):
        raise sm.JobFailed(f"strata-models looked for faces in {len(each)} of {len(frames)} keyframes")
    payload = {
        "frames": [
            {
                "frame": n,
                "width": f.width,
                "height": f.height,
                "faces": [
                    {
                        "box": [round(x, 1) for x in face.bbox],
                        "score": None if face.det_score is None else round(face.det_score, 3),
                    }
                    for face in f.faces
                ],
            }
            for n, f in zip(frames, each, strict=True)
        ]
    }
    return results.record(
        db,
        source,
        "faces",
        results.Provenance.of(FACES_ROLE, dataclasses.asdict(served)),
        inputs=[source.sha256],
        parameters=params,
        payload=payload,
        job_id=job_id,
    )


def current_faces(db: Session, source: Source) -> Result | None:
    """The faces on the current keyframes, or None before the faces step has run for them."""
    found = shots.current(db, source)
    if found is None:
        return None
    try:
        keys = _keyframes(db, source, found)
    except KeyError:
        return None
    return next(
        (r for r in results.current(db, source, "faces") if r.parameters.get("keyframes") == keys.cache_key), None
    )


def people_in(faces: dict[str, Any], shots_payload: dict[str, Any], scene: dict[str, Any]) -> int:
    """The most faces on any one keyframe of the scene."""
    by_frame = {int(f["frame"]): len(f["faces"]) for f in faces["frames"]}
    return max((by_frame.get(f, 0) for f in scene_frames(shots_payload, scene, set(by_frame))), default=0)
