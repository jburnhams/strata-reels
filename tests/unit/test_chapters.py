"""A tape's chapters (app/chapters.py): the pure grouping of scenes by occasion, and the grouping and naming over the
test's migrated database with strata-models stood in for over real HTTP (`tape_models`: two scenes with black between
them, unlike pictures, both described as a birthday)."""

import json
import math

import pytest
from probes import TAPE, answer
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app import chapters, cli, describe, jobs, overrides, scenes, shots, sources, speech
from app.models import Source


def scene(i: int, start: int, end: int) -> dict:
    return {"start_frame": start * 25, "end_frame": end * 25, "start": str(start), "end": str(end), "shots": [i]}


def told(tags: list[str], activity: str = "talking", setting: str = "indoor") -> dict:
    return {"tags": tags, "activity": activity, "setting": setting}


def payload(n: int, black_before: set[int] = frozenset()) -> dict:  # type: ignore[assignment]
    return {
        "rate": "25",
        "scenes": [scene(i, i * 10, i * 10 + 10) for i in range(n)],
        "boundaries": [
            {"shot": i, "reasons": ["pictures differ (0.50)"] + (["black between"] if i in black_before else [])}
            for i in range(1, n)
        ],
    }


def test_scenes_that_look_and_read_alike_are_one_chapter_and_a_new_occasion_starts_another():
    xmas = told(["christmas", "tree", "presents"], "opening presents")
    beach = told(["beach", "sand", "sea"], "playing on a beach", "outdoor")
    got = chapters.group(
        payload(4),
        [[1.0, 0.0], [0.98, 0.2], [0.0, 1.0], [0.1, 1.0]],
        [xmas, told(["christmas", "presents", "paper"], "opening presents"), beach, beach],
    )
    assert [c["scenes"] for c in got["chapters"]] == [[0, 1], [2, 3]]
    first, new, same = got["boundaries"]
    assert (first["level"], new["level"], same["level"]) == ("scene", "chapter", "scene")
    assert first["shared"] == ["christmas", "opening", "presents"]
    assert first["reasons"][1] == "described alike: christmas, opening, presents"
    assert new["reasons"] == ["pictures differ (0.20)", "described differently", "indoors then outdoors"]
    assert new["confidence"] > 0.99
    assert first["confidence"] < 0.01
    assert got["chapters"][1]["confidence"] == new["confidence"]
    assert (got["chapters"][0]["end"], got["chapters"][1]["start"]) == ("20", "20")
    assert got["version"] == chapters.VERSION
    assert got["parameters"] == chapters.PARAMETERS


def test_black_between_scenes_tips_a_doubtful_boundary_and_nothing_to_compare_says_so():
    b, c = math.acos(0.9), math.acos(0.9) + math.acos(0.8)
    alike = [[1.0, 0.0], [math.cos(b), math.sin(b)], [math.cos(c), math.sin(c)]]  # cosines 0.9, then 0.8: no say
    words = [told(["garden"]), told(["garden", "lawn"]), told(["kitchen"])]
    p = payload(3, black_before={2})
    got = chapters.group(p, alike, [None, None, None])
    assert [b["level"] for b in got["boundaries"]] == ["scene", "chapter"]  # black tips the even one
    assert got["boundaries"][1]["reasons"][-1] == "black between"
    none = chapters.group(p, [None, None, None], [None, None, None])
    assert none["boundaries"][0] == {
        "scene": 1, "at": "10", "level": "chapter", "confidence": 0.5, "similarity": None, "shared": [],
        "reasons": ["nothing to compare"], "guess": 0.5, "marked": None,
        "signals": {"similarity": None, "share": None, "black": False, "moved": False},
    }  # fmt: skip
    worded = chapters.group(p, [None, None, None], words)
    assert [c["scenes"] for c in worded["chapters"]] == [[0, 1], [2]]
    with pytest.raises(ValueError, match="one picture and one description"):
        chapters.group(p, [None], words)
    assert chapters.mean([]) is None
    assert chapters.words_of(None) is None


def test_the_namer_is_told_of_a_long_chapter_in_brief():
    grouped = payload(30)
    chapter = {"start": "0", "end": "300", "scenes": list(range(30))}
    long = {"segments": [{"start": 0, "end": 300, "text": "word " * 500, "doubtful": False}]}
    context = chapters.context_of(chapter, grouped, {}, long)
    assert (context["seconds"], context["scene_count"], len(context["scenes"])) == (300.0, 30, chapters.NAMED_SCENES)
    assert context["scenes"][0]["summary"] is None  # not described: said as missing, not left out
    assert context["scenes"][-1]["start"] == "4:50"
    assert all(len(s["speech"]) <= chapters.SAID_CHARS for s in context["scenes"])
    assert context["scenes"][0]["speech"].endswith(" …")
    assert chapters._clock(3725) == "1:02:05"  # pyright: ignore[reportPrivateUsage]


@pytest.fixture
def engine(db_env: str) -> Engine:
    return jobs._records()  # pyright: ignore[reportPrivateUsage]


def described(engine: Engine, tmp_path) -> Source:
    f = tmp_path / "tape.mkv"
    f.write_bytes(b"tape" * 500)
    with Session(engine, expire_on_commit=False) as s:
        source, _ = sources.add(s, str(f), probe=answer(TAPE))
        source.sha256 = "ab" * 32
        s.commit()
        shots.detect(s, source)
        shots.keyframes(s, source)
        scenes.find_scenes(s, source)
        speech.transcribe(s, source)
        describe.describe(s, source)
    return source


def test_a_tapes_chapters_are_grouped_once_and_each_named_from_its_scenes(engine, tmp_path, tape_models):
    with Session(engine) as s:
        f = tmp_path / "bare.mkv"
        f.write_bytes(b"bare" * 500)
        bare, _ = sources.add(s, str(f), probe=answer(TAPE))
        with pytest.raises(ValueError, match="no content hash"):
            chapters.find_chapters(s, bare)
        bare.sha256 = "cd" * 32
        with pytest.raises(KeyError, match="no scenes yet"):
            chapters.find_chapters(s, bare)
        with pytest.raises(KeyError, match="no chapters yet"):
            chapters.name(s, bare)
        assert chapters.current(s, bare) is None
        assert chapters.current_names(s, bare) == {}
    source = described(engine, tmp_path)
    with Session(engine) as s:
        src = s.get(Source, source.id)
        assert src is not None
        grouped, new = chapters.find_chapters(s, src)
        assert new
        assert (grouped.kind, grouped.role) == ("chapters", "reels-chapters")
        # the two scenes look unlike (their keyframes' vectors are at right angles) and black is between them
        assert [c["scenes"] for c in grouped.payload["chapters"]] == [[0], [1]]
        (boundary,) = grouped.payload["boundaries"]
        assert boundary["similarity"] == 0.0
        assert boundary["shared"] == ["birthday", "talking"]  # described alike, but the picture and the black win
        assert boundary["reasons"][-1] == "black between"
        assert chapters.find_chapters(s, src) == (grouped, False)
        assert chapters.current(s, src) == grouped
        named = chapters.name(s, src)
        assert [new for _, new in named] == [True, True]
        first, second = (r for r, _ in named)
        assert (first.kind, first.role, first.model) == ("chapter", "chapter-name", "qwen3.5-9b-vl")
        assert (first.span_start, first.span_end, second.span_start) == ("0", "10", "10")
        assert first.payload["name"]["title"] == "Scenes from 0:00"
        asked = [j["params"] for j in tape_models.jobs if j["role"] == "chapter-name"]
        assert all(a["preset"] == chapters.NAME_PRESET for a in asked)
        told_first = asked[0]["context"]["scenes"][0]
        assert told_first["speech"] == "Say hello to Grandma!"
        assert told_first["summary"].startswith("frames ")  # the scene's own description
        assert told_first["guesses"] == ["a birthday"]
        assert chapters.name(s, src) == [(first, False), (second, False)]  # stored: not asked again
        assert chapters.current_names(s, src) == {"0": first, "10": second}


def test_the_chapters_command_lists_each_with_its_name(capsys, engine, tmp_path, tape_models):
    assert cli.main(["chapters", "99"]) == 1
    source = described(engine, tmp_path)
    assert cli.main(["chapters", str(source.id)]) == 1  # not grouped yet
    assert "has no chapters yet" in capsys.readouterr().out
    with Session(engine) as s:
        src = s.get(Source, source.id)
        assert src is not None
        chapters.find_chapters(s, src)
    assert cli.main(["chapters", str(source.id)]) == 0
    out = capsys.readouterr().out
    assert "1 scenes" in out
    assert "Scenes from" not in out
    with Session(engine) as s:
        src = s.get(Source, source.id)
        assert src is not None
        chapters.name(s, src)
    assert cli.main(["chapters", str(source.id)]) == 0
    out = capsys.readouterr().out
    assert "Scenes from 0:10" in out
    assert "scenes at 0:10" in out
    assert cli.main(["chapters", str(source.id), "--json"]) == 0
    assert '"title": "Scenes from 0:00"' in capsys.readouterr().out


def test_a_mark_settles_a_boundary_and_keeps_the_guess_and_signals_beside_it():
    p = payload(3)
    pictures = [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]]  # alike, then unlike
    guessed = chapters.group(p, pictures, [None, None, None])
    assert [c["scenes"] for c in guessed["chapters"]] == [[0, 1], [2]]
    marked = chapters.group(p, pictures, [None, None, None], marks={"10": True, "20": False})
    assert [c["scenes"] for c in marked["chapters"]] == [[0], [1, 2]]
    start, joined = marked["boundaries"]
    assert (start["confidence"], start["marked"], start["reasons"][-1]) == (1.0, True, "you started a chapter here")
    assert (joined["confidence"], joined["marked"]) == (0.0, False)
    assert joined["reasons"][-1] == "you put it with the chapter before"
    assert joined["guess"] > 0.99  # what the pictures said, kept for fitting
    assert joined["signals"] == {"similarity": 0.0, "share": None, "black": False, "moved": False}
    assert marked["marks"] == {"10": True, "20": False}
    assert marked["chapters"][1]["confidence"] == 1.0  # it starts where the person said, against the guess


def example(similarity: float, starts: bool) -> chapters.Example:
    return chapters.Example(similarity, None, black=False, moved=False, starts=starts)


def test_the_weights_are_fitted_to_what_the_person_said_and_held_near_todays():
    # this person starts a chapter wherever two scenes' pictures are below 0.9 alike, where the weights say 0.8
    judged = [example(0.70 + i * 0.01, 0.70 + i * 0.01 < 0.9) for i in range(30)]
    fitted = chapters.fit(judged)
    assert fitted["similar"] > 0.85
    now, then = chapters.agreement(judged, chapters.PARAMETERS), chapters.agreement(judged, fitted)
    assert now is not None
    assert then is not None
    assert then > now
    assert then >= 0.9
    held = chapters.fit(judged, prior=1000.0)  # held hard: they hardly move
    assert abs(held["similar"] - chapters.PARAMETERS["similar"]) < 0.02
    assert chapters.fit([]) == {k: float(v) for k, v in chapters.PARAMETERS.items()}
    assert chapters.agreement([], chapters.PARAMETERS) is None
    worded = chapters.Example(None, 0.5, black=True, moved=True, starts=False)
    assert chapters.logit_of(worded, chapters.PARAMETERS) == pytest.approx(-3.0 + 2.0 + 0.5)


def test_what_a_grouping_teaches_is_its_marks_and_all_of_a_checked_tape():
    got = chapters.group(payload(3), [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]], [None] * 3, marks={"10": True})
    marked_only = chapters.examples_of(got, checked=False)
    assert [e.starts for e in marked_only] == [True]
    every = chapters.examples_of(got, checked=True)
    assert [(e.similarity, e.starts) for e in every] == [(1.0, True), (0.0, True)]
    old = {"boundaries": [{"level": "chapter", "reasons": [], "marked": None}]}  # VERSION 1 kept no signals
    assert chapters.examples_of(old, checked=True) == []


def named_by_model(tape_models) -> int:
    return sum(j["role"] == "chapter-name" for j in tape_models.jobs)


def test_marks_regroup_the_chapters_and_a_chapter_left_as_it_was_keeps_its_name(engine, tmp_path, tape_models):
    with Session(engine) as s:
        f = tmp_path / "bare.mkv"
        f.write_bytes(b"bare" * 500)
        bare, _ = sources.add(s, str(f), probe=answer(TAPE))
        with pytest.raises(KeyError, match="no scenes yet"):
            chapters.mark(s, bare, 1, True, author="Ada")
        with pytest.raises(KeyError, match="no chapters yet"):
            chapters.rename(s, bare, 0, "Tea", author="Ada")
    source = described(engine, tmp_path)
    with Session(engine) as s:
        src = s.get(Source, source.id)
        assert src is not None
        apart, _ = chapters.find_chapters(s, src)
        chapters.name(s, src)
        asked = named_by_model(tape_models)
        for scene in (0, 2):
            with pytest.raises(ValueError, match="scenes 2 to 2"):
                chapters.mark(s, src, scene, True, author="Ada")
        chapters.mark(s, src, 1, False, author="Ada")  # one birthday: the black was the camcorder paused
        assert chapters.marks(s, src) == {"10": False}
        assert chapters.current(s, src) == apart  # until it is grouped again
        assert not chapters.follows_marks(s, src, apart)
        together, new = chapters.find_chapters(s, src)
        assert new
        assert [c["scenes"] for c in together.payload["chapters"]] == [[0, 1]]
        assert chapters.current(s, src) == together
        assert chapters.follows_marks(s, src, together)
        assert [n for _, n in chapters.name(s, src)] == [True]  # the joined chapter is new: asked
        assert named_by_model(tape_models) == asked + 1
        chapters.mark(s, src, 1, None, author="Ada")  # taken back: grouped as before, and named as before
        chapters.mark(s, src, 1, None, author="Ada")  # nothing left to take back
        assert chapters.marks(s, src) == {}
        again, new = chapters.find_chapters(s, src)
        assert new  # the joined grouping replaced it, so it is grouped again: cheap, and the same
        assert again.payload == apart.payload
        assert chapters.current(s, src) == again
        assert [n for _, n in chapters.name(s, src)] == [False, False]
        assert named_by_model(tape_models) == asked + 1
        assert set(chapters.current_names(s, src)) == {"0", "10"}


def test_a_chapter_takes_the_persons_title_and_gives_it_back(engine, tmp_path, tape_models):
    source = described(engine, tmp_path)
    with Session(engine) as s:
        src = s.get(Source, source.id)
        assert src is not None
        chapters.find_chapters(s, src)
        chapters.rename(s, src, 1, "  Grandma's   birthday ", author="Ada")
        assert chapters.titles(s, src) == {"10": "Grandma's birthday"}
        chapters.rename(s, src, 1, "Grandma's birthday", author="Ada")  # said again: nothing new
        assert len(overrides.active(s, "tape", src.id)) == 1
        chapters.rename(s, src, 1, "Tea at Grandma's", author="Ada")
        (held,) = overrides.active(s, "tape", src.id)
        assert held.value == {"at": "10", "title": "Tea at Grandma's"}
        chapters.rename(s, src, 1, None, author="Ada")
        assert chapters.titles(s, src) == {}
        for chapter, title, why in (
            (2, "Tea", "chapters 1 to 2"),
            (0, "  ", "a word or more"),
            (0, "x" * 121, "at most"),
        ):
            with pytest.raises(ValueError, match=why):
                chapters.rename(s, src, chapter, title, author="Ada")


def test_the_page_marks_and_renames_chapters_and_the_tape_is_grouped_again(client, engine, tmp_path, tape_models):
    source = described(engine, tmp_path)
    with Session(engine) as s:
        src = s.get(Source, source.id)
        assert src is not None
        chapters.find_chapters(s, src)
        chapters.name(s, src)
    base = f"/api/sources/{source.id}"
    marked = client.put(f"{base}/scenes/1/chapter-mark", json={"start": False})
    assert marked.status_code == 200
    body = marked.json()
    assert [sc["mark"] for sc in body["scenes"]] == [None, False]
    assert body["regrouping"]  # the job that groups them again is queued
    assert len(body["chapters"]) == 2
    jobs.work(jobs.queue(engine))
    after = client.get(f"{base}/scenes").json()
    assert (after["regrouping"], [c["scenes"] for c in after["chapters"]]) == (False, [[0, 1]])
    titled = client.put(f"{base}/chapters/0/title", json={"title": "Grandma's birthday"}).json()
    assert titled["chapters"][0]["title"] == "Grandma's birthday"
    assert titled["chapters"][0]["name"]["title"] == "Scenes from 0:00"  # the namer's, kept beside it
    refused = client.put(f"{base}/scenes/0/chapter-mark", json={"start": True})
    assert (refused.status_code, refused.json()["detail"]) == (
        400,
        "a chapter can be marked at scenes 2 to 2, not scene 1",
    )
    assert client.put("/api/sources/99/chapters/0/title", json={"title": "x"}).status_code == 404
    assert client.put(f"{base}/chapters/0/title", json={"title": None}).json()["chapters"][0]["title"] is None


def test_the_fit_command_learns_from_marks_and_checked_tapes(capsys, engine, tmp_path, tape_models):
    assert cli.main(["fit-chapters"]) == 1
    assert "nothing to learn from yet" in capsys.readouterr().out
    source = described(engine, tmp_path)
    with Session(engine) as s:
        src = s.get(Source, source.id)
        assert src is not None
        chapters.find_chapters(s, src)  # grouped, not yet judged: teaches nothing
        assert cli.main(["fit-chapters"]) == 1
        capsys.readouterr()
        chapters.mark(s, src, 1, False, author="Ada")
        chapters.rename(s, src, 0, "Grandma's birthday", author="Ada")
        chapters.find_chapters(s, src)
    assert cli.main(["fit-chapters"]) == 0
    out = capsys.readouterr().out
    assert "1 boundaries judged on 1 tapes (tape.mkv)" in out
    assert "agree with you now 0%" in out
    assert cli.main(["fit-chapters", "--json", "--prior", "5"]) == 0
    fitted = json.loads(capsys.readouterr().out)
    assert (fitted["boundaries"], fitted["agreement_now"]) == (1, 0.0)
    assert fitted["fitted"]["black"] < chapters.PARAMETERS["black"]  # the black was not a new occasion here
    assert cli.main(["chapters", str(source.id)]) == 0
    assert "Grandma's birthday (yours)" in capsys.readouterr().out
