"""The baseline planner (app/planner.py): scenes chosen and trimmed to a length, the person's pins, bans and locks kept,
and what cannot be kept said. Pure, so no database: scenes are built here at PAL's 25 frames a second."""

from fractions import Fraction

import pytest
from hypothesis import given
from hypothesis import strategies as st
from strata_python.mediatime import Rate, Span

from app import planner
from app.planner import Constraint, Said, Scene

PAL = Rate.parse("25")


def on_frames(span: Span) -> bool:
    return (span.start * 25).denominator == 1 == (span.end * 25).denominator


def told(people: int = 0, children: int = 0, quality: str = "fair") -> dict:
    return {"people": people, "children": children, "picture": {"quality": quality, "problems": []}}


def scene(start: float, end: float, chapter: int | None = None, d: dict | None = None, said=()) -> Scene:
    return Scene(Span.of(start, end), chapter, d, tuple(said))


def phrase(start: float, end: float, words: int) -> Said:
    return Said(Span.of(start, end), words)


def test_a_scene_is_worth_its_people_children_speech_and_picture_and_says_why():
    assert planner.score(scene(0, 10)) == (0.0, ["not described yet"])
    full = scene(0, 10, d=told(3, 1, "good"), said=[phrase(1, 2, 10), phrase(3, 4, 30)])
    assert planner.score(full) == (3.0, ["3 people", "a child", "good picture", "40 words said"])
    one = scene(0, 10, d=told(1, 2, "poor"), said=[phrase(1, 2, 1)])
    assert planner.score(one) == (1.0 + 0.5 - 1.5 + 0.05, ["1 person", "2 children", "poor picture", "1 word said"])


def test_the_best_scenes_fill_the_length_in_tape_order_and_the_last_is_cut_to_fit():
    found = [
        scene(0, 20, 0, told(0)),  # nobody: last choice
        scene(20, 40, 1, told(2, 1)),
        scene(40, 60, 2, told(1)),
        scene(60, 62, 3, told(5)),  # too short to show
    ]
    got = planner.plan(found, Fraction(25), PAL)
    assert [c.scene for c in got.clips] == [0, 20, 40]  # tape order, not the order they were chosen in
    assert [c.span.duration for c in got.clips] == [5, 10, 10]  # the last one chosen (nobody in it) is cut to fit
    assert got.duration == 25
    assert got.conflicts == ()
    assert got.clips[1].reasons == ("2 people", "a child", "the middle of a long scene")
    # an unreachable length takes every scene there is, each at most `max_clip_s`
    assert planner.plan(found, Fraction(600), PAL).duration == 30


def test_a_reel_visits_every_chapter_before_it_repeats_one():
    found = [scene(0, 20, 0, told(3)), scene(20, 40, 0, told(3)), scene(40, 60, 1, told(2))]
    got = planner.plan(found, Fraction(20), PAL)
    assert [c.scene for c in got.clips] == [0, 40]  # the second of chapter 0 would score 3.0 - 0.75 < 2.0
    alone = [scene(0, 20, None, told(3)), scene(20, 40, None, told(3)), scene(40, 60, None, told(2))]
    assert [c.scene for c in planner.plan(alone, Fraction(20), PAL).clips] == [0, 20]  # no chapters: no say


def test_a_long_scene_is_cut_around_what_is_said_without_cutting_a_phrase():
    sc = scene(0, 60, d=told(1), said=[phrase(30.3, 33, 4), phrase(34, 38, 12), phrase(50, 52, 3)])
    room = planner.usable(sc, PAL)
    assert room == Span.of("13/25", "1487/25")  # half a second off each end, onto frames inside it
    span, why = planner.window(sc, room, Fraction(10), PAL)
    assert why == "cut around what is said"
    assert span.duration == 10
    assert span.start <= Fraction(303, 10)  # both phrases whole, with the pad before the first
    assert span.end >= 38
    assert on_frames(span)
    # a scene with nothing said is cut in its middle; one that fits is kept whole
    assert planner.window(scene(0, 60), room, Fraction(10), PAL) == (Span.of(25, 35), "the middle of a long scene")
    assert planner.window(sc, Span.of(2, 8), Fraction(10), PAL) == (Span.of(2, 8), None)
    # a phrase longer than the clip cannot be kept whole: the middle, not a cut phrase
    talky = scene(0, 60, said=[phrase(5, 50, 40)])
    assert planner.window(talky, room, Fraction(10), PAL)[1] == "the middle of a long scene"


def test_short_scenes_keep_their_ends_and_too_short_ones_are_left_out():
    assert planner.usable(scene(0, "3.52"), PAL) == Span.of(0, "88/25")  # trimming would leave less than 3 s
    assert planner.usable(scene(0, "2.96"), PAL) is None
    assert planner.usable(scene("0.01", 10), PAL) == Span.of("14/25", "237/25")  # onto frames, then trimmed


def test_pins_put_scenes_in_bans_keep_them_out_and_locks_keep_a_clip_exactly():
    found = [scene(0, 20, 0, told(3)), scene(20, 40, 1, told(0)), scene(40, 60, 2, told(2))]
    lock = Span.of("41", "43.04")
    got = planner.plan(
        found,
        Fraction(15),
        PAL,
        [
            Constraint(1, "ban", Span.of(0, 20)),
            Constraint(2, "pin", Span.of(21, 39)),  # the scene's span when it was said: regrouped since, still found
            Constraint(3, "lock", lock),
        ],
    )
    assert [(c.scene, c.pinned, c.locked) for c in got.clips] == [(20, True, False), (40, False, True)]
    assert got.clips[1].span == lock  # exactly, though the scene would give another trim
    assert got.clips[0].reasons[0] == "pinned"
    assert got.duration == 10 + Fraction(51, 25)  # nothing else fits in what is left, and the banned scene is out
    assert got.conflicts == ()


def test_the_later_word_wins_and_the_other_is_reported():
    found = [scene(0, 20, 0, told(3)), scene(20, 40, 1, told(2))]
    pinned_then_banned = planner.plan(
        found, Fraction(30), PAL, [Constraint(1, "pin", Span.of(0, 20)), Constraint(2, "ban", Span.of(0, 20))]
    )
    assert [c.scene for c in pinned_then_banned.clips] == [20]
    assert pinned_then_banned.conflicts == (
        planner.Conflict(1, "The pin on the scene at 0:00 gave way to a later word."),
    )
    banned_then_locked = planner.plan(
        found, Fraction(30), PAL, [Constraint(1, "ban", Span.of(0, 20)), Constraint(2, "lock", Span.of(2, 4))]
    )
    assert banned_then_locked.clips[0].span == Span.of(2, 4)
    assert banned_then_locked.conflicts[0].override == 1
    locked_then_banned = planner.plan(
        found, Fraction(30), PAL, [Constraint(1, "lock", Span.of(2, 4)), Constraint(2, "ban", Span.of(0, 20))]
    )
    assert all(not c.locked for c in locked_then_banned.clips)
    assert (
        locked_then_banned.conflicts[0].message == "The lock on a clip in the scene at 0:00 gave way to a later word."
    )


def test_what_cannot_be_kept_is_said_and_locks_and_pins_are_never_dropped_for_length():
    found = [scene(0, 20, 0, told(3)), scene(20, 40, 1, told(2))]
    got = planner.plan(
        found,
        Fraction(5),
        PAL,
        [
            Constraint(1, "pin", Span.of(0, 20)),
            Constraint(2, "pin", Span.of(20, 40)),
            Constraint(3, "pin", Span.of(80, 90)),
        ],
    )
    assert len(got.clips) == 2
    assert got.duration == 20
    assert [c.message for c in got.conflicts] == [
        "The pinned stretch 1:20 to 1:30 is no longer in any scene.",
        "The locked and pinned clips last 0:20, more than the 0:05 asked for.",
    ]
    tiny = planner.plan([scene(0, 2, 0)], Fraction(5), PAL, [Constraint(1, "pin", Span.of(0, 2))])
    assert tiny.clips[0].span == Span.of(0, 2)  # a pinned scene too short to show is shown whole
    with pytest.raises(ValueError, match="above 0 seconds"):
        planner.plan(found, Fraction(0), PAL)


def test_another_seed_is_another_version_and_the_same_seed_the_same():
    found = [scene(i * 20, i * 20 + 20, i, told(1)) for i in range(12)]  # all alike: the seed breaks the ties
    first = planner.plan(found, Fraction(30), PAL)
    assert [c.scene for c in first.clips] == [0, 20, 40]  # ties go to the earliest
    seeded = [planner.plan(found, Fraction(30), PAL, seed=s) for s in (1, 2, 3)]
    assert seeded[0] == planner.plan(found, Fraction(30), PAL, seed=1)
    assert len({tuple(c.scene for c in p.clips) for p in [first, *seeded]}) > 1


def test_clock():
    assert (planner.clock(Fraction(59)), planner.clock(Fraction(3725, 1))) == ("0:59", "1:02:05")


@given(
    st.lists(st.tuples(st.integers(1, 2000), st.integers(0, 3), st.integers(0, 2)), min_size=1, max_size=30),
    st.integers(1, 4000),
    st.integers(0, 3),
)
def test_any_plan_fits_its_length_on_frames_inside_its_scenes_without_overlap(shape, target_frames, seed):
    found: list[Scene] = []
    at = 0
    for frames, people, chapter in shape:
        found.append(Scene(Span(PAL.time_of(at), PAL.time_of(at + frames)), chapter, told(people)))
        at += frames
    target = PAL.time_of(target_frames)
    got = planner.plan(found, target, PAL, seed=seed)
    assert got.duration <= target
    assert got.duration == sum(c.span.duration for c in got.clips)
    for a, b in zip(got.clips, got.clips[1:], strict=False):
        assert a.span.end <= b.span.start
    for c in got.clips:
        assert on_frames(c.span)
        assert any(sc.span.start <= c.span.start and c.span.end <= sc.span.end for sc in found)
        assert c.span.duration >= 3 - Fraction(1, 25)
