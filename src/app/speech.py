"""What is said on a tape: one transcript of the whole source (R1 step 3, overview §6).

- **From strata-models** (role `transcribe`: Whisper large-v3-turbo today), with word timings and its voice activity
  filter, since a home tape is mostly long stretches of no speech, where Whisper otherwise invents words. Reels never
  runs a speech model itself. Stored as a result of kind `transcript` with the model, its settings as they ran and their
  hash, keyed on the source's hash and `ASK`.
- **The whole source at once**, not scene by scene: Whisper needs the speech around a sentence to place it, and the
  scenes may be regrouped later (a person's correction) without the speech changing. A scene reads its part with
  `said_in`.
- **Doubtful segments are kept and marked**, not dropped: a segment Whisper itself thinks is probably not speech
  (`no_speech_prob` at least 0.6) or decoded with a low average log-probability (below -1.0) is `doubtful`. Those are
  Whisper's own thresholds for the same two signals (its `no_speech_threshold` and `logprob_threshold`); a tape's hiss
  and music pass the filter often enough that what reads the transcript has to know. `said_in` leaves them out.
- Times are Whisper's seconds (to the hundredth), from the start of the file, as the thumbnails' and shots' are."""

import dataclasses
import os
from typing import Any

from sqlalchemy.orm import Session
from strata_python import media
from strata_python.clients import models as sm

from app import results
from app.models import Result, Source

ROLE = "transcribe"
APP = "strata-reels"
# Asked of strata-models with every job; part of the cache key, so a change here is a new request.
ASK: dict[str, Any] = {"word_timestamps": True, "vad_filter": True}
NO_SPEECH = 0.6  # Whisper's no_speech_threshold
LOW_LOGPROB = -1.0  # Whisper's logprob_threshold
SAID_CHARS = 1500  # the most of a scene's speech given to the describer: a few minutes of talk


def has_sound(source: Source) -> bool:
    return media.Probe.from_json(source.path, source.probe).audio is not None


def payload_of(answer: dict[str, Any]) -> dict[str, Any]:
    """The stored transcript from strata-models' answer: segments in time order, each with its words and whether it
    is doubtful."""
    t = sm.Transcript.from_json(answer)
    segments: list[dict[str, Any]] = []
    for s in sorted(t.segments, key=lambda s: s.start):
        doubtful = (s.no_speech_prob is not None and s.no_speech_prob >= NO_SPEECH) or (
            s.avg_logprob is not None and s.avg_logprob < LOW_LOGPROB
        )
        segments.append(
            {
                "start": round(s.start, 3),
                "end": round(s.end, 3),
                "text": s.text.strip(),
                "doubtful": doubtful,
                "words": [
                    {
                        "start": round(w.start, 3),
                        "end": round(w.end, 3),
                        "word": w.word,
                        "probability": None if w.probability is None else round(w.probability, 3),
                    }
                    for w in s.words
                ],
            }
        )
    return {
        "language": t.language,
        "language_probability": None if t.language_probability is None else round(t.language_probability, 3),
        "duration": t.duration,
        "segments": segments,
    }


def transcribe(db: Session, source: Source, *, job_id: int | None = None) -> tuple[Result, bool]:
    """The source's current transcript, asked of strata-models unless the same request is stored, and whether it is
    new. ValueError for a source with no hash or no sound; strata-models' `Unavailable` and `JobFailed` pass through
    (the job queue retries them)."""
    if source.sha256 is None:
        raise ValueError(f"{source.name} has no content hash yet: its speech waits for the hash job")
    if not has_sound(source):
        raise ValueError(f"{source.name} has no sound: there is nothing to transcribe")
    now = sm.role_settings(ROLE)
    if now is not None:
        planned = results.Provenance(role=ROLE, model=now.model, settings_hash=now.settings_hash)
        found = results.find(db, results.cache_key("transcript", [source.sha256], planned, ASK))
        if found is not None:
            return found, False
    answer, served = sm.run_with_provenance(ROLE, {"path": os.path.abspath(source.path), **ASK}, APP, priority="low")
    return results.record(
        db,
        source,
        "transcript",
        results.Provenance.of(ROLE, dataclasses.asdict(served)),
        inputs=[source.sha256],
        parameters=ASK,
        payload=payload_of(answer),
        job_id=job_id,
    )


def current(db: Session, source: Source) -> Result | None:
    """The source's transcript now, or None before the transcript step has run."""
    return next((r for r in results.current(db, source, "transcript") if r.span_start is None), None)


def said_in(transcript: dict[str, Any] | None, start: float, end: float) -> str:
    """What is said between `start` and `end` seconds: the text of every segment that is not doubtful and overlaps
    them, at most `SAID_CHARS` (cut at a word, with an ellipsis). Empty with no transcript (a source with no sound)."""
    if transcript is None:
        return ""
    text = " ".join(
        s["text"] for s in transcript["segments"] if not s["doubtful"] and s["start"] < end and s["end"] > start
    ).strip()
    if len(text) <= SAID_CHARS:
        return text
    return text[:SAID_CHARS].rsplit(" ", 1)[0] + " …"
