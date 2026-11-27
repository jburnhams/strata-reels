"""The baseline planner: a tape's scenes chosen and trimmed into a reel of a target length (plan step 5, spec S6 and §5).

Pure and deterministic: `plan` is a function of the scenes and what was found about them, the person's constraints, the
target, the seed and `PARAMETERS`, so the same inputs give the same reel, a revision can be made again from what it
records (`reels`), and a test needs no database. The language-model planner comes after this one and is measured
against it; this is the "simple baseline to beat" the spec asks of every stage.

**What makes a scene worth showing** (spec §5's score, its terms kept as reasons in words, never one opaque number):
people in it (the describer's count) and children above all, since a family tape is watched for them; speech, as far
as it goes (`speech_words` words count in full); the picture's quality (a poor one costs more than a good one earns).
A signal that has not been found yet adds nothing. These weights are first guesses, not measured: they are to be fitted
once a person has corrected a few reels, as the scenes' and the chapters' were.

**Coverage over repetition**: each clip already taken from a chapter lowers that chapter's other scenes by `repeat`,
so a reel visits the tape's occasions instead of the one with the most talking (spec §5: coverage minus redundancy).

**Trims**: a scene loses `trim_s` at each end (the camcorder starting and stopping) when it is long enough to spare it,
and a long one is cut to `max_clip_s`: around the most speech that fits without cutting a phrase (the transcript's
segments; plan step 6's `cut-points` role does this properly later), else its middle. Every cut is on a frame, and
the clips never add up to more than the target: the last one is shortened to what is left, down to `min_clip_s`.

**The person's word wins over the scores** (spec §1, §6): a `lock` keeps a clip's exact range, a `pin` puts a scene in,
a `ban` keeps one out. Pins and bans name the scene's span when they were said; a regrouped tape's scene is the one
that overlaps that span most. When two of them disagree (a ban on a scene that is pinned or has a locked clip), the
later one holds and the other is reported as a conflict, never dropped in silence; so is a pin whose stretch is no
longer in any scene, and locks and pins that are longer than the target on their own (they are all kept).
The clips play in tape order. A seed other than 0 moves each scene's score a little (`jitter`), for another version."""

import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any, Literal

from strata_python import mediatime
from strata_python.mediatime import Rate, Span

NAME = "baseline"
VERSION = 1  # raise when `plan` gives something else for the same inputs
PARAMETERS: dict[str, Any] = {
    "max_clip_s": "10",  # the longest clip taken from one scene
    "min_clip_s": "3",  # the shortest clip worth showing; a scene shorter than this is left out unless pinned
    "trim_s": "1/2",  # cut from each end of a scene that is long enough to spare it
    "pad_s": "1/4",  # kept before and after a phrase when a long scene is cut around its speech
    "people": 1.0,
    "children": 0.5,
    "speech": 1.0,  # for `speech_words` words or more; less in proportion
    "speech_words": 20,
    "good": 0.5,  # the describer says the picture is good
    "poor": -1.5,  # ... or poor
    "repeat": 0.75,  # taken off a chapter's scenes for each clip already chosen from it
    "jitter": 0.6,  # how far a seed other than 0 moves a score, at most half this either way
}

Kind = Literal["pin", "ban", "lock"]


@dataclass(frozen=True)
class Said:
    """A phrase the transcript is sure of (not doubtful), in source time, and how many words it has."""

    span: Span
    words: int


@dataclass(frozen=True)
class Scene:
    """One scene as the planner sees it: its span of the tape, its chapter (None before chapters are grouped), its
    description (the describer's payload, None before it has run) and the phrases said in it."""

    span: Span
    chapter: int | None = None
    description: Mapping[str, Any] | None = None
    said: tuple[Said, ...] = ()


@dataclass(frozen=True)
class Constraint:
    """The person's word on the reel (an override, `id` its row): `pin` and `ban` name a scene by its span when it was
    said, `lock` a clip by its exact range."""

    id: int
    kind: Kind
    span: Span


@dataclass(frozen=True)
class Clip:
    """A stretch of the tape in the reel: half-open, on frames. `scene` is the start of the scene it was cut from."""

    span: Span
    scene: Fraction
    score: float
    reasons: tuple[str, ...]
    locked: bool = False
    pinned: bool = False


@dataclass(frozen=True)
class Conflict:
    """Something the person asked that this plan could not do, in a sentence; `override` is the one it could not keep
    (None when it is about the target)."""

    override: int | None
    message: str


@dataclass(frozen=True)
class Plan:
    clips: tuple[Clip, ...]
    conflicts: tuple[Conflict, ...] = ()
    duration: Fraction = field(default=Fraction(0))


def clock(t: Fraction) -> str:
    """m:ss (or h:mm:ss) for a message, to the second."""
    s = math.floor(t)
    h, m = divmod(s // 60, 60)
    return f"{h}:{m:02d}:{s % 60:02d}" if h else f"{m}:{s % 60:02d}"


def score(scene: Scene, parameters: Mapping[str, Any] | None = None) -> tuple[float, list[str]]:
    """How much the scene is worth showing, and why in words."""
    p = {**PARAMETERS, **(parameters or {})}
    total = 0.0
    reasons: list[str] = []
    d = scene.description
    if d is None:
        reasons.append("not described yet")
    else:
        people, children = int(d.get("people") or 0), int(d.get("children") or 0)
        if people:
            total += float(p["people"])
            reasons.append("1 person" if people == 1 else f"{people} people")
        if children:
            total += float(p["children"])
            reasons.append("a child" if children == 1 else f"{children} children")
        picture: Mapping[str, Any] = d.get("picture") or {}
        quality = picture.get("quality")
        if quality == "good":
            total += float(p["good"])
            reasons.append("good picture")
        elif quality == "poor":
            total += float(p["poor"])
            reasons.append("poor picture")
    words = sum(s.words for s in scene.said)
    if words:
        total += float(p["speech"]) * min(1.0, words / int(p["speech_words"]))
        reasons.append("1 word said" if words == 1 else f"{words} words said")
    return round(total, 4), reasons


def _ceil(rate: Rate, t: Fraction) -> Fraction:
    return rate.time_of(math.ceil(t * rate.fps))


def _floor(rate: Rate, t: Fraction) -> Fraction:
    return rate.time_of(math.floor(t * rate.fps))


def usable(scene: Scene, rate: Rate, parameters: Mapping[str, Any] | None = None) -> Span | None:
    """The part of the scene a clip may come from: less `trim_s` at each end when it can spare it, on frames; None
    for a scene shorter than `min_clip_s`."""
    p = {**PARAMETERS, **(parameters or {})}
    shortest, trim = mediatime.rational(p["min_clip_s"]), mediatime.rational(p["trim_s"])
    start, end = _ceil(rate, scene.span.start), _floor(rate, scene.span.end)
    if end - start < shortest:
        return None
    inner_start, inner_end = _ceil(rate, start + trim), _floor(rate, end - trim)
    return Span(inner_start, inner_end) if inner_end - inner_start >= shortest else Span(start, end)


def window(
    scene: Scene, room: Span, length: Fraction, rate: Rate, parameters: Mapping[str, Any] | None = None
) -> tuple[Span, str | None]:
    """`length` (whole frames) of `room`, where it says most: the most words without cutting a phrase, else the
    middle. Returns the span and, when it was cut, why there."""
    p = {**PARAMETERS, **(parameters or {})}
    frames = math.floor(min(length, room.duration) * rate.fps)
    size = rate.time_of(frames)
    if size >= room.duration:
        return room, None
    pad = mediatime.rational(p["pad_s"])
    latest = room.end - size

    def at(a: Fraction) -> Fraction:
        return min(max(room.start, _floor(rate, a)), _floor(rate, latest))

    middle = at(room.start + (room.duration - size) / 2)
    said = [s for s in scene.said if s.span.overlaps(room)]
    if not said:
        return Span(middle, middle + size), "the middle of a long scene"
    starts = {middle, at(room.start), at(latest)}
    for s in said:
        starts |= {at(s.span.start - pad), at(s.span.end + pad - size)}

    def value(a: Fraction) -> tuple[int, Fraction]:
        w = Span(a, a + size)
        inside = sum(s.words for s in said if w.start <= s.span.start and s.span.end <= w.end)
        cut = sum(s.words for s in said if s.span.overlaps(w) and not (w.start <= s.span.start and s.span.end <= w.end))
        return inside - 2 * cut, -abs(a - middle)

    best = max(sorted(starts), key=value)
    if value(best)[0] <= 0:
        return Span(middle, middle + size), "the middle of a long scene"
    return Span(best, best + size), "cut around what is said"


def _jitter(seed: int, scene: Scene, spread: float) -> float:
    if not seed:
        return 0.0
    h = hashlib.sha256(f"{seed}:{mediatime.text(scene.span.start)}".encode()).digest()
    return (int.from_bytes(h[:8]) / 2**64 - 0.5) * spread


def _scene_of(scenes: Sequence[Scene], span: Span) -> int | None:
    """The scene that overlaps `span` most (the earlier on a tie), or None when none does."""
    best: tuple[Fraction, int] | None = None
    for i, sc in enumerate(scenes):
        common = sc.span & span
        if common is not None and (best is None or common.duration > best[0]):
            best = (common.duration, i)
    return None if best is None else best[1]


def plan(
    scenes: Sequence[Scene],
    target: Fraction,
    rate: Rate,
    constraints: Sequence[Constraint] = (),
    *,
    seed: int = 0,
    parameters: Mapping[str, Any] | None = None,
) -> Plan:
    """The reel: clips in tape order that add up to no more than `target` (unless the locks and pins alone do), with
    what could not be kept. ValueError for a target that is not above 0."""
    if target <= 0:
        raise ValueError(f"a reel's length is above 0 seconds, not {clock(target)}")
    p = {**PARAMETERS, **(parameters or {})}
    longest, shortest = mediatime.rational(p["max_clip_s"]), mediatime.rational(p["min_clip_s"])
    conflicts: list[Conflict] = []

    # Which scenes each constraint is about; a lock is about every scene its clip touches.
    about: dict[int, list[Constraint]] = {}
    for c in sorted(constraints, key=lambda c: c.id):
        if c.kind == "lock":
            touched = [i for i, sc in enumerate(scenes) if sc.span.overlaps(c.span)]
        else:
            found = _scene_of(scenes, c.span)
            if found is None:
                what = "pinned" if c.kind == "pin" else "banned"
                span = f"{clock(c.span.start)} to {clock(c.span.end)}"
                conflicts.append(Conflict(c.id, f"The {what} stretch {span} is no longer in any scene."))
                continue
            touched = [found]
        for i in touched:
            about.setdefault(i, []).append(c)

    # Where a ban meets a pin or a lock, the later word holds.
    dropped: set[int] = set()
    banned: set[int] = set()
    for i, said in sorted(about.items()):
        bans = [c for c in said if c.kind == "ban"]
        keeps = [c for c in said if c.kind != "ban"]
        if not bans:
            continue
        if not keeps or max(c.id for c in bans) > max(c.id for c in keeps):
            banned.add(i)
            losers = [c for c in keeps if c.id not in dropped]
        else:
            losers = [c for c in bans if c.id not in dropped]
        for c in losers:
            dropped.add(c.id)
            word = {"pin": "pin on", "ban": "ban on", "lock": "lock on a clip in"}[c.kind]
            conflicts.append(
                Conflict(c.id, f"The {word} the scene at {clock(scenes[i].span.start)} gave way to a later word.")
            )

    clips: list[Clip] = []
    taken: set[int] = set()  # scenes with a clip in the reel
    for c in sorted(constraints, key=lambda c: c.span.start):
        if c.kind != "lock" or c.id in dropped:
            continue
        touched = [i for i, sc in enumerate(scenes) if sc.span.overlaps(c.span)]
        taken |= set(touched)
        home = _scene_of(scenes, c.span)
        clips.append(
            Clip(c.span, c.span.start if home is None else scenes[home].span.start, 0.0, ("locked",), locked=True)
        )
    pins = sorted({i for i, said in about.items() for c in said if c.kind == "pin" and c.id not in dropped} - taken)
    for i in pins:
        sc = scenes[i]
        room = usable(sc, rate, p) or Span(_ceil(rate, sc.span.start), _floor(rate, sc.span.end))
        if room.duration <= 0:
            conflicts.append(Conflict(None, f"The pinned scene at {clock(sc.span.start)} is shorter than a frame."))
            continue
        span, why = window(sc, room, longest, rate, p)
        value, reasons = score(sc, p)
        clips.append(Clip(span, sc.span.start, value, ("pinned", *reasons, *([why] if why else [])), pinned=True))
        taken.add(i)

    used = sum((c.span.duration for c in clips), Fraction(0))
    if used > target:
        conflicts.append(
            Conflict(None, f"The locked and pinned clips last {clock(used)}, more than the {clock(target)} asked for.")
        )

    # The rest, best first, each chapter's worth falling as it is used.
    rooms = {i: usable(sc, rate, p) for i, sc in enumerate(scenes) if i not in taken and i not in banned}
    left = {i: room for i, room in rooms.items() if room is not None}
    base = {i: score(scenes[i], p) for i in left}
    jitter = {i: _jitter(seed, scenes[i], float(p["jitter"])) for i in left}
    per_chapter: dict[int, int] = {}
    for i in taken:
        ch = scenes[i].chapter
        if ch is not None:
            per_chapter[ch] = per_chapter.get(ch, 0) + 1
    remaining = target - used
    while left and remaining >= shortest:

        def worth(i: int) -> tuple[float, Fraction]:
            ch = scenes[i].chapter
            again = 0 if ch is None else per_chapter.get(ch, 0)
            return base[i][0] + jitter[i] - float(p["repeat"]) * again, -scenes[i].span.start

        i = max(left, key=worth)
        sc, room = scenes[i], left.pop(i)
        span, why = window(sc, room, min(longest, remaining), rate, p)
        if span.duration < shortest:
            continue
        value, reasons = base[i]
        clips.append(Clip(span, sc.span.start, value, (*reasons, *([why] if why else []))))
        remaining -= span.duration
        if sc.chapter is not None:
            per_chapter[sc.chapter] = per_chapter.get(sc.chapter, 0) + 1

    clips.sort(key=lambda c: (c.span.start, c.span.end))
    total = sum((c.span.duration for c in clips), Fraction(0))
    return Plan(tuple(clips), tuple(conflicts), total)
