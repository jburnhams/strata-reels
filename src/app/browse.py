"""What the scene browser reads (R1 step 4): a source's scenes with their pictures, words and faces, its transcript and
its waveform, put together from the stored results for the web app, and the files they point at.

Nothing here asks strata-models or ffmpeg for anything: every value comes from a result a job stored (`derived`,
`shots`, `scenes`, `speech`, `describe`), so the browser shows whatever the jobs have finished, scene by scene, and says
what it is waiting for. A step that has not run is `None` (or `waiting`), never an empty answer that reads as "none".

- **Pictures of a scene** are the thumbnails made every ten seconds that fall inside it, since a strip through the scene
  shows it move on; a scene shorter than the step, which may hold none, gets its shots' keyframes instead.
- **Files are served by name from a result's payload**, never by a path from the request, so a name with `..` in it
  is simply not one of them (404).
- **The waveform is reduced here** (`peaks`) to the number of slices the page draws: a three-hour tape at fifty slices a
  second is half a million pairs, which no page needs. Each slice keeps the lowest and highest of the ones it covers,
  so a short loud moment is never averaged away. Tape sound is quiet; the page scales the drawing (strata-react's
  `Waveform normalise`), not this, so the numbers stay the recording's own."""

import json
import os
from typing import Any, Literal

from pydantic import Field
from sqlalchemy.orm import Session
from strata_python import mediatime
from strata_python.contracts import ApiModel

from app import derived, describe, results, scenes, shots, speech
from app.models import Result, Source
from app.sources import Described

PEAK_BUCKETS = 4000  # the default: a slice per pixel of a wide screen, twice over
MAX_BUCKETS = 20000


class Frame(ApiModel):
    """A picture of the scene at `time` seconds; `url` is a path from the app's root, which the page puts its base before
    (`api.url`), as for every request."""

    time: float
    url: str


class Guess(ApiModel):
    guess: str
    because: str


class Picture(ApiModel):
    quality: Literal["good", "fair", "poor"]
    problems: list[str]


class Description(ApiModel):
    """strata-models' description of a scene (preset `scene-describe-v1`): what is seen kept apart from what is
    guessed, each guess with its evidence."""

    summary: str
    observed: list[str]
    inferred: list[Guess]
    setting: str
    people: int
    children: int
    activity: str
    tags: list[str]
    picture: Picture
    model: str | None = Field(description="the model that wrote it")


class Scene(ApiModel):
    """One scene: its place on the tape, how sure the cut that starts it is, and what step 3 found about it (`None`
    where that job has not run yet)."""

    index: int = Field(description="from 0, in time order")
    start: float
    end: float
    shots: int
    confidence: float | None = Field(description="that the cut before it starts a scene; None for the first")
    reasons: list[str] = Field(description="why the cut before it is a boundary, in words")
    frames: list[Frame]
    description: Description | None
    faces: int | None = Field(description="the most faces on any one of its keyframes; None before the faces job")
    speech: str | None = Field(
        description="what is said in it (doubtful segments left out); None before the transcript"
    )


class Analysis(ApiModel):
    """Which of the source's results exist, for the page's finding and its waiting words."""

    proxy: bool
    thumbnails: bool
    peaks: bool
    shots: int | None
    transcript: bool | None = Field(description="None for a source with no sound")
    faces: bool
    described: int


class Scenes(ApiModel):
    """A source's scenes as the browser shows them. `waiting` says, in a sentence, what the scenes wait for while there
    are none yet."""

    source: Described
    analysis: Analysis
    waiting: str | None
    scenes: list[Scene]


class Word(ApiModel):
    start: float
    end: float
    word: str
    probability: float | None


class Segment(ApiModel):
    start: float
    end: float
    text: str
    doubtful: bool = Field(description="Whisper itself doubts it is speech")
    words: list[Word]


class Transcript(ApiModel):
    """The whole source's speech, or no segments with `waiting` set before the transcript job has run."""

    language: str | None
    model: str | None
    waiting: str | None
    segments: list[Segment]


class Peaks(ApiModel):
    """The source's sound as the lowest and highest sample of each slice, -1 to 1, over `seconds`."""

    seconds: float
    min: list[float]
    max: list[float]


def _seconds(t: str) -> float:
    return float(mediatime.rational(t))


def _asset(db: Session, source: Source, kind: str) -> Result | None:
    """The current derived asset of `kind`, or None when it is not made or its file has gone."""
    if source.sha256 is None:
        return None
    found = results.find(db, derived.key_for(source, kind))
    return found if found is not None and os.path.exists(derived.path_of(found)) else None


def _keyframes(db: Session, source: Source, cut: Result | None) -> Result | None:
    if cut is None:
        return None
    return next(
        (r for r in results.current(db, source, "keyframes") if r.parameters.get("shots") == cut.cache_key), None
    )


def _description(r: Result | None) -> Description | None:
    if r is None:
        return None
    d: dict[str, Any] = r.payload["description"]
    return Description.model_validate({**d, "model": r.model})


def scenes_of(db: Session, source: Source) -> Scenes:
    """The browser's view of `source`: its scenes with whatever has been found about each."""
    base = f"/api/sources/{source.id}"
    thumbs = _asset(db, source, "thumbnails")
    cut = shots.current(db, source)
    keys = _keyframes(db, source, cut)
    grouped = scenes.current(db, source)
    if grouped is not None and (cut is None or grouped.parameters.get("shots") != cut.cache_key):
        grouped = None  # shots found again since: the old grouping is not of these shots
    sound = speech.has_sound(source)
    said = speech.current(db, source) if sound else None
    faces = describe.current_faces(db, source)
    told = describe.current_descriptions(db, source)
    analysis = Analysis(
        proxy=_asset(db, source, "proxy") is not None,
        thumbnails=thumbs is not None,
        peaks=_asset(db, source, "peaks") is not None,
        shots=None if cut is None else sum(not s["black"] for s in cut.payload["shots"]),
        transcript=(said is not None) if sound else None,
        faces=faces is not None,
        described=len(told),
    )
    if grouped is None or cut is None:
        return Scenes(source=Described.of(source), analysis=analysis, waiting=_waiting(source, cut), scenes=[])
    thumb_times = (
        [] if thumbs is None else [(_seconds(t["time"]), str(t["name"])) for t in thumbs.payload["thumbnails"]]
    )
    key_names = {} if keys is None else {int(f["frame"]): str(f["name"]) for f in keys.payload["frames"]}
    rate = mediatime.Rate.parse(grouped.payload["rate"])
    starts = {b["shot"]: b for b in grouped.payload["boundaries"]}
    out: list[Scene] = []
    for i, sc in enumerate(grouped.payload["scenes"]):
        start, end = _seconds(sc["start"]), _seconds(sc["end"])
        frames = [Frame(time=t, url=f"{base}/thumbnails/{n}") for t, n in thumb_times if start <= t < end]
        if not frames:
            frames = [
                Frame(time=float(rate.time_of(f)), url=f"{base}/keyframes/{key_names[f]}")
                for f in describe.scene_frames(cut.payload, sc, set(key_names))
            ]
        boundary = starts.get(sc["shots"][0])
        out.append(
            Scene(
                index=i,
                start=start,
                end=end,
                shots=len(sc["shots"]),
                confidence=sc["confidence"],
                reasons=[] if boundary is None else list(boundary["reasons"]),
                frames=frames,
                description=_description(told.get(sc["start"])),
                faces=None if faces is None else describe.people_in(faces.payload, cut.payload, sc),
                speech=speech.said_in(said.payload, start, end) if said is not None else ("" if not sound else None),
            )
        )
    return Scenes(source=Described.of(source), analysis=analysis, waiting=None, scenes=out)


def _waiting(source: Source, cut: Result | None) -> str:
    if source.sha256 is None:
        return "Reading the whole file first, to know it by its content."
    if cut is None:
        return "Finding where the camera stopped and started (the shots job)."
    return "Grouping the shots into scenes (the keyframes and scenes jobs)."


def transcript_of(db: Session, source: Source) -> Transcript:
    if not speech.has_sound(source):
        return Transcript(language=None, model=None, waiting=None, segments=[])
    said = speech.current(db, source)
    if said is None:
        return Transcript(language=None, model=None, waiting="Listening for speech (the transcript job).", segments=[])
    p = said.payload
    return Transcript(
        language=p["language"],
        model=said.model,
        waiting=None,
        segments=[Segment.model_validate(s) for s in p["segments"]],
    )


def reduce(lo: list[float], hi: list[float], buckets: int) -> tuple[list[float], list[float]]:
    """`buckets` slices from the given ones, each the lowest and highest of those it covers (all of them when there
    are no more than `buckets`)."""
    n = min(len(lo), len(hi))
    if n <= buckets:
        return lo[:n], hi[:n]
    out_lo: list[float] = []
    out_hi: list[float] = []
    for b in range(buckets):
        a, z = b * n // buckets, max((b + 1) * n // buckets, b * n // buckets + 1)
        out_lo.append(min(lo[a:z]))
        out_hi.append(max(hi[a:z]))
    return out_lo, out_hi


def peaks_of(db: Session, source: Source, buckets: int = PEAK_BUCKETS) -> Peaks:
    """The source's waveform in `buckets` slices. KeyError before the peaks job has made it (or for a source with no
    sound, which gets none)."""
    found = _asset(db, source, "peaks")
    if found is None:
        raise KeyError(
            f"{source.name} has no waveform yet" if speech.has_sound(source) else f"{source.name} has no sound"
        )
    with open(derived.path_of(found), encoding="utf-8") as f:
        p = json.load(f)
    lo, hi = reduce(p["min"], p["max"], max(1, min(buckets, MAX_BUCKETS)))
    return Peaks(seconds=len(p["min"]) / p["per_second"], min=lo, max=hi)


def file_of(
    db: Session, source: Source, what: Literal["proxy", "thumbnails", "keyframes"], name: str | None = None
) -> str:
    """The path of a file the browser shows: the proxy, or a thumbnail or keyframe named in its result's payload.
    KeyError for anything else."""
    if what == "proxy":
        found = _asset(db, source, "proxy")
        if found is None:
            raise KeyError(f"{source.name} has no browser copy yet: the proxy job makes it")
        return derived.path_of(found)
    r = _asset(db, source, "thumbnails") if what == "thumbnails" else _keyframes(db, source, shots.current(db, source))
    listed = (
        [] if r is None else [str(x["name"]) for x in r.payload["thumbnails" if what == "thumbnails" else "frames"]]
    )
    if r is None or name not in listed:
        raise KeyError(f"{source.name} has no {what[:-1]} {name}")
    return os.path.join(derived.path_of(r), name)
