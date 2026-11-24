"""Shots, keyframes and scenes (app/shots.py, app/scenes.py) over the test's migrated database, with strata-models
stood in for over real HTTP (`tape_models` in conftest.py) and ffmpeg at strata-python's edge (`made`).
tests/integration cuts real keyframes from a real file."""

import json
import os

import pytest
from hypothesis import given
from hypothesis import strategies as st
from probes import SHOTS, TAPE, answer
from sqlalchemy import Engine
from sqlalchemy.orm import Session
from strata_python import mediatime
from strata_python.clients import models as sm

from app import cli, derived, jobs, results, scenes, shots, sources
from app.models import Source


@pytest.fixture
def engine(db_env: str) -> Engine:
    return jobs._records()  # pyright: ignore[reportPrivateUsage]


def hashed(engine: Engine, tmp_path, raw=TAPE) -> Source:
    f = tmp_path / "tape.mkv"
    f.write_bytes(b"tape" * 500)
    with Session(engine, expire_on_commit=False) as s:
        source, _ = sources.add(s, str(f), probe=answer(raw))
        source.sha256 = "ab" * 32
        s.commit()
    return source


RATE = mediatime.Rate.parse("25")


def test_shots_are_kept_by_frame_with_exact_times_and_black_marked():
    found = shots.payload_of(
        {
            **SHOTS,
            "cuts": [*SHOTS["cuts"], {"time": 4.0, "frame": 100, "score": 0.6}, {"time": 0, "frame": 0, "score": 1}],
        },
        RATE,
        None,
    )
    assert [(x["start_frame"], x["end_frame"], x["start"], x["score"], x["black"]) for x in found["shots"]] == [
        (0, 100, "0", None, False), (100, 250, "4", 0.6, False), (250, 400, "10", 0.9, False),
    ]  # fmt: skip
    assert found["black"] == [{"start_frame": 240, "end_frame": 250, "start": "48/5", "end": "10"}]
    assert (found["rate"], found["frames"], found["threshold"]) == ("25", 400, 0.3)
    dark = shots.payload_of({**SHOTS, "black": [{"start": 4.0, "end": 9.6}]}, RATE, None)
    assert [x["black"] for x in dark["shots"]] == [False, True, False]  # 140 of 150 frames black
    assert shots.payload_of({**SHOTS, "frames": None}, RATE, 400)["frames"] == 400
    with pytest.raises(ValueError, match="how many frames"):
        shots.payload_of({**SHOTS, "frames": None}, RATE, None)


def test_the_rate_comes_from_the_stream(engine, tmp_path):
    assert shots.rate_of(hashed(engine, tmp_path)).fps == 25

    def source(raw) -> Source:  # never stored: sources.add refuses a file with no picture
        return Source(fingerprint="f", path="/x.mkv", name="x.mkv", size=1, duration=1.0, probe=raw)

    with pytest.raises(ValueError, match="no picture"):
        shots.rate_of(source({**TAPE, "streams": [TAPE["streams"][1]]}))
    with pytest.raises(ValueError, match="does not say its frame rate"):
        shots.rate_of(source({**TAPE, "streams": [{**TAPE["streams"][0], "r_frame_rate": "0/0"}]}))


def test_shots_record_what_made_them_and_are_asked_once(engine, tmp_path, tape_models):
    source = hashed(engine, tmp_path)
    with Session(engine) as s:
        src = s.get(Source, source.id)
        assert src is not None
        r, new = shots.detect(s, src)
        assert new
        assert (r.kind, r.role, r.model, r.inputs, r.parameters) == (
            "shots", "shot-boundaries", "ffmpeg-scene-score", ["ab" * 32], shots.ASK,
        )  # fmt: skip
        assert r.settings_hash is not None
        assert r.options == {"threshold": 0.3}
        assert r.model_job == 1
        job = tape_models.jobs[0]
        assert (job["params"]["path"], job["params"]["black"], job["priority"]) == (src.path, True, "low")
        assert shots.detect(s, src) == (r, False)
        assert len(tape_models.jobs) == 1  # found by its key: not asked again
        tape_models.options["shot-boundaries"] = {"threshold": 0.2}  # the service's settings changed
        newer, new = shots.detect(s, src)
        assert new
        assert newer.id != r.id
        assert shots.current(s, src) == newer
        assert [x.superseded_by for x in results.history(s, src, "shots")] == [newer.id, None]


def test_shots_need_the_hash(engine, tmp_path):
    with Session(engine) as s:
        f = tmp_path / "t.mkv"
        f.write_bytes(b"t" * 100)
        src, _ = sources.add(s, str(f), probe=answer(TAPE))
        with pytest.raises(ValueError, match="no content hash"):
            shots.detect(s, src)
        with pytest.raises(ValueError, match="no content hash"):
            shots.keyframes(s, src)
        with pytest.raises(ValueError, match="no content hash"):
            scenes.find_scenes(s, src)


def test_keyframes_are_the_middle_of_each_shot_shown_and_follow_the_shots(engine, tmp_path, tape_models, made):
    source = hashed(engine, tmp_path)
    with Session(engine) as s:
        src = s.get(Source, source.id)
        assert src is not None
        with pytest.raises(KeyError, match="no shots yet"):
            shots.keyframes(s, src)
        found, _ = shots.detect(s, src)
        keys, new = shots.keyframes(s, src)
        assert new
        assert [f["frame"] for f in keys.payload["frames"]] == [49, 174, 324]
        assert keys.parameters["shots"] == found.cache_key
        assert sorted(os.listdir(derived.path_of(keys))) == ["000049.jpg", "000174.jpg", "000324.jpg"]
        assert shots.keyframes(s, src) == (keys, False)
        for name in os.listdir(derived.path_of(keys)):
            os.unlink(os.path.join(derived.path_of(keys), name))
        os.rmdir(derived.path_of(keys))
        assert shots.keyframes(s, src) == (keys, True)  # a cleared folder is made again under the same row
        assert made == [("tape.mkv", "keyframes"), ("tape.mkv", "keyframes")]
        assert shots.middles({"shots": [{"start_frame": 4, "end_frame": 5, "black": False}]}) == [4]


def test_scenes_group_alike_shots_and_split_at_black(engine, tmp_path, tape_models):
    source = hashed(engine, tmp_path)
    with Session(engine) as s:
        src = s.get(Source, source.id)
        assert src is not None
        with pytest.raises(KeyError, match="no keyframes yet"):
            scenes.find_scenes(s, src)
        shots.detect(s, src)
        shots.keyframes(s, src)
        r, new = scenes.find_scenes(s, src)
        assert new
        assert (r.role, r.model, r.parameters["version"]) == ("reels-scenes", None, scenes.VERSION)
        (vectors,) = results.current(s, src, "embeddings")
        assert (vectors.role, vectors.model, vectors.payload["frames"]) == ("image-embed", "clip-vit-b-32", 3)
        assert r.parameters["embeddings"] == vectors.cache_key
        with open(derived.path_of(vectors)) as f:
            assert json.load(f)["vectors"]["324"] == [0.0, 1.0]
        first, second = r.payload["boundaries"]
        assert (first["at"], first["level"], first["similarity"], first["reasons"]) == (
            "4", "shot", 1.0, ["pictures alike (1.00)"],
        )  # fmt: skip
        assert (second["at"], second["level"], second["reasons"]) == (
            "10", "scene", ["pictures differ (0.00)", "black at the cut"],
        )  # fmt: skip
        assert [(sc["start"], sc["end"], sc["shots"], sc["confidence"]) for sc in r.payload["scenes"]] == [
            ("0", "10", [0, 1], None), ("10", "16", [2], 1.0),
        ]  # fmt: skip
        assert scenes.find_scenes(s, src) == (r, False)
        assert scenes.current(s, src) == r
        assert [j["role"] for j in tape_models.jobs] == ["shot-boundaries", "image-embed"]


def test_vectors_are_checked_against_the_keyframes(engine, tmp_path, tape_models):
    tape_models.results["image-embed"] = {"images": [[1.0, 0.0]], "dim": 2}
    source = hashed(engine, tmp_path)
    with Session(engine) as s:
        src = s.get(Source, source.id)
        assert src is not None
        shots.detect(s, src)
        shots.keyframes(s, src)
        with pytest.raises(sm.JobFailed, match="embedded 1 of 3 keyframes"):
            scenes.find_scenes(s, src)


def shot(a: int, b: int, black: bool = False) -> dict:
    return {
        "start_frame": a, "end_frame": b, "start": mediatime.text(RATE.time_of(a)), "end": mediatime.text(RATE.time_of(b)),
        "score": None if a == 0 else 0.5, "black": black,
    }  # fmt: skip


def test_a_black_shot_is_a_gap_and_a_missing_picture_is_said():
    found = {"rate": "25", "black": [], "shots": [shot(0, 50), shot(50, 60, black=True), shot(60, 100), shot(100, 150)]}
    vectors = {24: [1.0, 0.0], 79: [1.0, 0.0]}  # the last shot's picture is missing
    g = scenes.group(found, vectors)
    assert [(b["shot"], b["after_shot"], b["level"], b["reasons"]) for b in g["boundaries"]] == [
        (2, 0, "scene", ["pictures alike (1.00)", "black between"]),
        (3, 2, "scene", ["no picture to compare"]),
    ]  # fmt: skip
    assert [sc["shots"] for sc in g["scenes"]] == [[0], [2], [3]]
    assert g["boundaries"][0]["confidence"] == 0.9
    assert scenes.group(found, vectors, {"black": 0.4})["boundaries"][0]["level"] == "shot"
    assert scenes.cosine([0.0, 0.0], [1.0, 0.0]) == 0.0


@given(st.lists(st.tuples(st.integers(1, 200), st.booleans(), st.floats(-1, 1)), min_size=1, max_size=30))
def test_scenes_cover_every_shot_shown_once_in_order(parts):
    edges, at = [], 0
    for length, _, _ in parts:
        edges.append((at, at + length))
        at += length
    found = {
        "rate": "25",
        "black": [],
        "shots": [shot(a, b, black) for (a, b), (_, black, _) in zip(edges, parts, strict=True)],
    }
    vectors = {(a + b - 1) // 2: [x, (1 - x * x) ** 0.5] for (a, b), (_, _, x) in zip(edges, parts, strict=True)}
    g = scenes.group(found, vectors)
    shown = [i for i, (_, black, _) in enumerate(parts) if not black]
    assert [i for sc in g["scenes"] for i in sc["shots"]] == shown
    assert len(g["boundaries"]) == max(0, len(shown) - 1)
    assert len(g["scenes"]) == 1 + sum(b["level"] == "scene" for b in g["boundaries"]) if shown else g["scenes"] == []
    assert all(0 <= b["confidence"] <= 1 for b in g["boundaries"])
    assert g == scenes.group(found, vectors)  # pure


def test_the_scenes_command(capsys, engine, tmp_path, tape_models):
    assert cli.main(["scenes", "9"]) == 1
    assert "there is no source 9" in capsys.readouterr().err
    source = hashed(engine, tmp_path)
    assert cli.main(["scenes", str(source.id)]) == 1
    assert "has no scenes yet" in capsys.readouterr().out
    jobs.derive_later(jobs.queue(engine), source)
    jobs.work(jobs.queue(engine))
    assert cli.main(["scenes", str(source.id)]) == 0
    out = capsys.readouterr().out
    assert "    1       0.00 to     10.00 s    2 shots\n" in out
    assert "    2      10.00 to     16.00 s    1 shots  (boundary 100%)" in out
    assert cli.main(["scenes", str(source.id), "--json"]) == 0
    assert len(json.loads(capsys.readouterr().out)["scenes"]) == 2


def test_an_analysis_job_for_a_source_that_is_not_there_fails(engine):
    q = jobs.queue(engine)
    for kind in ("shots", "keyframes", "scenes"):
        q.enqueue(kind, {"source": 404})
    assert all("there is no source 404" in (j.error or "") for j in jobs.work(q))
