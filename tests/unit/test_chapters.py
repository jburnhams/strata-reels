"""A tape's chapters (app/chapters.py): the pure grouping of scenes by occasion, and the grouping and naming over the
test's migrated database with strata-models stood in for over real HTTP (`tape_models`: two scenes with black between
them, unlike pictures, both described as a birthday)."""

import math

import pytest
from probes import TAPE, answer
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app import chapters, cli, describe, jobs, scenes, shots, sources, speech
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
        "reasons": ["nothing to compare"],
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
