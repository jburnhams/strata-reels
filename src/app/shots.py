"""A tape's shots, and one picture of each (R1 step 2, overview §6). Scenes are grouped from these (`scenes`).

- **Shots come from strata-models** (role `shot-boundaries`; ffmpeg's scene score serves it today, measured by its eval
  `shots-spliced`). Reels never detects cuts itself, so a better detector there is one change there. The answer is
  stored as a result of kind `shots` with the model, its settings as they ran and their hash (`results.Provenance.of`).
- **Exact time.** strata-models gives each cut's frame number, counted from the first frame; the frame, not its float
  time, is what is kept, and every time here is `frame / rate` as an exact rational (`mediatime`), from the stream's
  own rate. So a cut is on the frame it was found on, and source time counts from the first frame, as the thumbnails'
  does. Black stretches (blackdetect's seconds) are snapped to the frame they start in.
- **Every shot keeps its cut's score** (how unlike the frame before it is, 0 to 1): the shot level of the boundary
  hierarchy spec S5 asks for. A shot that is black nearly all through (`BLACK_SHARE`) is marked: it is the gap between
  two recordings, not footage to show.
- **Keyframes**: the middle frame of each shot that is not black, as a JPEG (strata-python's `derive.frames`), a
  derived asset like the proxy (kind `keyframes`, a folder under `derived/<cache key>/`). One per shot, however short
  the shot (a camcorder restart on tape is often a second or two, under the thumbnails' 10 s step); they are what the
  scenes compare, and what step 3 describes and looks for faces in. Keyed on the shots result, so new shots make new
  keyframes.

The model's answer and the stored payload use the same words (cuts, shots, black) so one can be read against the other."""

import dataclasses
import os
from fractions import Fraction
from typing import Any

from sqlalchemy.orm import Session
from strata_python import derive, media, mediatime
from strata_python.clients import models as sm

from app import derived, results, settings
from app.models import Result, Source

ROLE = "shot-boundaries"
APP = "strata-reels"  # how strata-models names who asked
BLACK_SHARE = Fraction(9, 10)  # a shot this much inside black stretches is a gap, not footage
# Asked of strata-models with every job; part of the cache key, so a change here is a new request.
ASK = {"black": True}
KEYFRAMES = {"height": 360}


def rate_of(source: Source) -> mediatime.Rate:
    """The video stream's frame rate, exact (r_frame_rate before avg_frame_rate: a tape's is "25/1")."""
    info = media.Probe.from_json(source.path, source.probe)
    v = info.video
    if v is None:
        raise ValueError(f"{source.name} has no picture: it has no shots")
    said = v.raw.get("r_frame_rate") or v.raw.get("avg_frame_rate")
    if not said or str(said).startswith("0"):
        raise ValueError(f"{source.name} does not say its frame rate: its cuts cannot be placed on frames")
    return mediatime.Rate.parse(str(said))


def payload_of(answer: dict[str, Any], rate: mediatime.Rate, frames_known: int | None) -> dict[str, Any]:
    """The stored shots from strata-models' answer: shots by frame (half-open), each with the score of the cut that
    starts it (None for the first) and whether it is black; the black stretches snapped to frames. Exact times as
    `mediatime.text`."""
    found = sm.Shots.from_json(answer)
    total = found.frames or frames_known
    if total is None:
        raise ValueError("strata-models did not say how many frames it read, and the file does not say either")
    cuts = sorted({c.frame: c for c in found.cuts if 0 < c.frame < total}.values(), key=lambda c: c.frame)
    starts = [0, *(c.frame for c in cuts)]
    ends = [*starts[1:], total]
    black = [(_frame(rate, b.start), min(_frame(rate, b.end), total)) for b in found.black]
    black = [(a, b) for a, b in black if b > a]
    shots: list[dict[str, Any]] = []
    for i, (a, b) in enumerate(zip(starts, ends, strict=True)):
        dark = sum(max(0, min(b, y) - max(a, x)) for x, y in black)
        shots.append(
            {
                "start_frame": a,
                "end_frame": b,
                "start": mediatime.text(rate.time_of(a)),
                "end": mediatime.text(rate.time_of(b)),
                "score": None if i == 0 else round(cuts[i - 1].score, 4),
                "black": Fraction(dark, b - a) >= BLACK_SHARE,
            }
        )
    return {
        "rate": mediatime.text(rate.fps),
        "frames": total,
        "shots": shots,
        "black": [
            {
                "start_frame": a,
                "end_frame": b,
                "start": mediatime.text(rate.time_of(a)),
                "end": mediatime.text(rate.time_of(b)),
            }
            for a, b in black
        ],
        "threshold": found.threshold,
        "min_shot_s": found.min_shot_s,
    }


def _frame(rate: mediatime.Rate, seconds: float) -> int:
    # blackdetect's seconds are printed to a few decimals: the frame whose start is nearest
    return rate.frame_at(rate.snap(mediatime.rational(f"{seconds:.6f}")))


def _frames_known(source: Source) -> int | None:
    v = media.Probe.from_json(source.path, source.probe).video
    return None if v is None else v.frames


def detect(db: Session, source: Source, *, job_id: int | None = None) -> tuple[Result, bool]:
    """The source's current shots, asked of strata-models unless a result for the same request (same model, same
    settings) is stored, and whether it is new. ValueError for a source with no hash or no frame rate; strata-models'
    `Unavailable` and `JobFailed` pass through (the job queue retries them)."""
    if source.sha256 is None:
        raise ValueError(f"{source.name} has no content hash yet: its shots wait for the hash job")
    rate = rate_of(source)
    now = sm.role_settings(ROLE)
    if now is not None:
        planned = results.Provenance(role=ROLE, model=now.model, settings_hash=now.settings_hash)
        found = results.find(db, results.cache_key("shots", [source.sha256], planned, ASK))
        if found is not None:
            return found, False
    answer, served = sm.run_with_provenance(ROLE, {"path": os.path.abspath(source.path), **ASK}, APP, priority="low")
    return results.record(
        db,
        source,
        "shots",
        results.Provenance.of(ROLE, dataclasses.asdict(served)),
        inputs=[source.sha256],
        parameters=ASK,
        payload=payload_of(answer, rate, _frames_known(source)),
        job_id=job_id,
    )


def current(db: Session, source: Source) -> Result | None:
    """The source's shots result now (the whole-source one), or None before the shots job has run."""
    return next((r for r in results.current(db, source, "shots") if r.span_start is None), None)


def middles(shots: dict[str, Any]) -> list[int]:
    """The frame each keyframe is taken at: the middle of every shot that is not black."""
    return [(s["start_frame"] + s["end_frame"] - 1) // 2 for s in shots["shots"] if not s["black"]]


def keyframe_parameters(shots: Result) -> dict[str, Any]:
    return {**KEYFRAMES, "shots": shots.cache_key, "recipe": derive.RECIPE["frames"]}


def keyframes(db: Session, source: Source, *, job_id: int | None = None) -> tuple[Result, bool]:
    """The current keyframes of `source`'s current shots, made if they are not there (or their folder has gone), and
    whether they are new. KeyError before the shots are known."""
    if source.sha256 is None:
        raise ValueError(f"{source.name} has no content hash yet")
    shots = current(db, source)
    if shots is None:
        raise KeyError(f"{source.name} has no shots yet: its keyframes wait for the shots job")
    params = keyframe_parameters(shots)
    key = results.cache_key("keyframes", [source.sha256], derived.PRODUCER, params)
    found = results.find(db, key)
    if found is not None and os.path.isdir(derived.path_of(found)):
        return found, False
    rel = os.path.join(derived.folder(key), "keyframes")
    made = derive.frames(
        source.path,
        os.path.join(settings.SETTINGS.home(), rel),
        middles(shots.payload),
        height=int(KEYFRAMES["height"]),
        info=media.Probe.from_json(source.path, source.probe),
    )
    if found is not None:
        return found, True  # the folder was cleared: made again at the same path, the row kept (append-only)
    return results.record(
        db,
        source,
        "keyframes",
        derived.PRODUCER,
        inputs=[source.sha256],
        parameters=params,
        payload={"frames": [{"frame": f.number, "name": os.path.basename(f.path)} for f in made]},
        file=rel,
        job_id=job_id,
    )
