"""The scene browser's reads (app/browse.py and its routes) over the test's migrated database: a tape taken through
ingest and step 3 with strata-models stood in for over real HTTP (`tape_models`) and ffmpeg at strata-python's edge
(`made`: two thumbnails, at 0 and 10 s, and two slices of peaks), then read back through the HTTP API."""

import pytest
from fastapi.testclient import TestClient
from probes import TAPE, answer
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app import browse, derived, describe, jobs, scenes, shots, sources, speech
from app.models import Source


@pytest.fixture
def engine(db_env: str) -> Engine:
    return jobs._records()  # pyright: ignore[reportPrivateUsage]


def added(engine: Engine, tmp_path, raw=TAPE, *, hashed: bool = True) -> int:
    f = tmp_path / "tape.mkv"
    f.write_bytes(b"tape" * 500)
    with Session(engine, expire_on_commit=False) as s:
        source, _ = sources.add(s, str(f), probe=answer(raw))
        if hashed:
            source.sha256 = "ab" * 32
        s.commit()
        return source.id


def analysed(engine: Engine, source_id: int, *, assets=derived.KINDS, steps=("scenes", "speech", "faces", "describe")):
    with Session(engine) as s:
        src = s.get(Source, source_id)
        assert src is not None
        for kind in assets:
            derived.make(s, src, kind)
        if "scenes" in steps:
            shots.detect(s, src)
            shots.keyframes(s, src)
            scenes.find_scenes(s, src)
        if "speech" in steps:
            speech.transcribe(s, src)
        if "faces" in steps:
            describe.find_faces(s, src)
        if "describe" in steps:
            describe.describe(s, src)


def test_before_the_hash_the_scenes_say_what_they_wait_for(client: TestClient, engine, tmp_path):
    sid = added(engine, tmp_path, hashed=False)
    got = client.get(f"/api/sources/{sid}/scenes").json()
    assert got["scenes"] == []
    assert got["waiting"] == "Reading the whole file first, to know it by its content."
    assert got["analysis"] == {
        "proxy": False, "thumbnails": False, "peaks": False, "shots": None, "transcript": False, "faces": False,
        "described": 0,
    }  # fmt: skip
    assert got["source"]["name"] == "tape.mkv"
    assert client.get(f"/api/sources/{sid}").json()["id"] == sid
    assert client.get("/api/sources/999/scenes").status_code == 404
    assert client.get("/api/sources/999").json()["detail"] == "there is no source 999"


def test_after_the_shots_the_scenes_wait_for_their_grouping(client: TestClient, engine, tmp_path, tape_models):
    sid = added(engine, tmp_path)
    assert client.get(f"/api/sources/{sid}/scenes").json()["waiting"].startswith("Finding where the camera stopped")
    with Session(engine) as s:
        src = s.get(Source, sid)
        assert src is not None
        shots.detect(s, src)
    got = client.get(f"/api/sources/{sid}/scenes").json()
    assert got["waiting"] == "Grouping the shots into scenes (the keyframes and scenes jobs)."
    assert got["analysis"]["shots"] == 3


def test_each_scene_has_its_pictures_speech_description_and_faces(client: TestClient, engine, tmp_path, tape_models):
    sid = added(engine, tmp_path)
    analysed(engine, sid)
    got = client.get(f"/api/sources/{sid}/scenes").json()
    assert got["waiting"] is None
    assert got["analysis"] == {
        "proxy": True, "thumbnails": True, "peaks": True, "shots": 3, "transcript": True, "faces": True,
        "described": 2,
    }  # fmt: skip
    first, second = got["scenes"]
    assert (first["index"], first["start"], first["end"], first["shots"]) == (0, 0.0, 10.0, 2)
    assert (first["confidence"], first["reasons"]) == (None, [])  # the tape's start begins it
    assert second["confidence"] > 0.5
    assert "black at the cut" in second["reasons"]
    # the thumbnails inside each scene (0 s in the first, 10 s in the second), by name under the source
    # (each URL carries its result's key, so a remade thumbnail is a new URL the browser has not kept)
    v = first["frames"][0]["url"].split("?v=")[1]
    assert len(v) == 12
    assert first["frames"] == [{"time": 0.0, "url": f"/api/sources/{sid}/thumbnails/000000.jpg?v={v}"}]
    assert second["frames"] == [{"time": 10.0, "url": f"/api/sources/{sid}/thumbnails/000001.jpg?v={v}"}]
    assert "immutable" in client.get(first["frames"][0]["url"]).headers["cache-control"]
    assert (first["speech"], second["speech"]) == ("Say hello to Grandma!", "Happy birthday to you")
    assert (first["faces"], second["faces"]) == (2, 0)
    d = first["description"]
    assert d["model"] == "qwen3.5-9b-vl"
    assert d["inferred"] == [{"guess": "a birthday", "because": "a cake"}]
    assert d["picture"]["quality"] in ("good", "fair", "poor")


def test_without_thumbnails_a_scene_shows_its_keyframes_and_unrun_steps_are_none(
    client: TestClient, engine, tmp_path, tape_models
):
    sid = added(engine, tmp_path)
    analysed(engine, sid, assets=(), steps=("scenes",))
    got = client.get(f"/api/sources/{sid}/scenes").json()
    first, second = got["scenes"]
    assert [f["url"].rsplit("/", 1)[1].split("?")[0] for f in first["frames"]] == ["000049.jpg", "000174.jpg"]
    assert first["frames"][0]["time"] == pytest.approx(49 / 25)
    assert all(f["url"].startswith(f"/api/sources/{sid}/keyframes/") for f in second["frames"])
    assert (first["description"], first["faces"], first["speech"]) == (None, None, None)
    assert got["analysis"]["transcript"] is False
    assert client.get(first["frames"][0]["url"]).content == b"jpg"


def test_a_silent_source_has_no_speech_to_wait_for(client: TestClient, engine, tmp_path, tape_models):
    sid = added(engine, tmp_path, {**TAPE, "streams": [TAPE["streams"][0]]})
    analysed(engine, sid, assets=(), steps=("scenes",))
    got = client.get(f"/api/sources/{sid}/scenes").json()
    assert got["analysis"]["transcript"] is None
    assert [sc["speech"] for sc in got["scenes"]] == ["", ""]
    assert client.get(f"/api/sources/{sid}/transcript").json() == {
        "language": None, "model": None, "waiting": None, "segments": [],
    }  # fmt: skip
    assert client.get(f"/api/sources/{sid}/peaks").json()["detail"] == "tape.mkv has no sound"


def test_the_transcript_keeps_its_doubtful_segments_and_words(client: TestClient, engine, tmp_path, tape_models):
    sid = added(engine, tmp_path)
    assert (
        client.get(f"/api/sources/{sid}/transcript").json()["waiting"] == "Listening for speech (the transcript job)."
    )
    analysed(engine, sid, assets=(), steps=("speech",))
    t = client.get(f"/api/sources/{sid}/transcript").json()
    assert (t["language"], t["model"], t["waiting"]) == ("en", "whisper-large-v3-turbo", None)
    assert [(x["start"], x["doubtful"]) for x in t["segments"]] == [(1.0, True), (6.0, False), (11.0, False)]
    assert t["segments"][1]["words"] == [{"start": 6.0, "end": 6.3, "word": " Say", "probability": 0.9}]


def test_peaks_are_reduced_keeping_each_slices_extremes(client: TestClient, engine, tmp_path, tape_models):
    assert browse.reduce([-0.1, -0.9, -0.2, -0.3], [0.1, 0.2, 0.8, 0.3], 2) == ([-0.9, -0.3], [0.2, 0.8])
    assert browse.reduce([-1, -2, -3], [1, 2, 3], 5) == ([-1, -2, -3], [1, 2, 3])
    assert browse.reduce([-1, -2, -3], [1, 2, 3], 2) == ([-1, -3], [1, 3])
    sid = added(engine, tmp_path)
    assert client.get(f"/api/sources/{sid}/peaks").json()["detail"] == "tape.mkv has no waveform yet"
    analysed(engine, sid, assets=("peaks",), steps=())
    assert client.get(f"/api/sources/{sid}/peaks").json() == {"seconds": 0.04, "min": [-0.5, -0.25], "max": [0.5, 0.25]}
    assert client.get(f"/api/sources/{sid}/peaks?buckets=1").json()["min"] == [-0.5]
    assert client.get(f"/api/sources/{sid}/peaks?buckets=0").status_code == 422


def test_files_are_served_by_their_names_in_a_result_and_nothing_else(
    client: TestClient, engine, tmp_path, tape_models
):
    sid = added(engine, tmp_path)
    assert client.get(f"/api/sources/{sid}/proxy").json()["detail"].endswith("the proxy job makes it")
    analysed(engine, sid, assets=("proxy", "thumbnails"), steps=())
    got = client.get(f"/api/sources/{sid}/proxy")
    assert (got.content, got.headers["content-type"]) == (b"mp4", "video/mp4")
    assert got.headers["cache-control"] == "no-cache"  # the same URL for whichever proxy is current
    part = client.get(f"/api/sources/{sid}/proxy", headers={"Range": "bytes=1-2"})
    assert (part.status_code, part.content) == (206, b"p4")  # the player seeks with ranges
    assert client.get(f"/api/sources/{sid}/thumbnails/000001.jpg").content == b"jpg"
    assert client.get(f"/api/sources/{sid}/thumbnails/000009.jpg").status_code == 404
    assert client.get(f"/api/sources/{sid}/thumbnails/..%2F..%2Fproxy.mp4").status_code == 404
    assert client.get(f"/api/sources/{sid}/keyframes/000049.jpg").status_code == 404  # no keyframes made yet


def test_the_shared_jobs_routes_list_the_queue(client: TestClient, engine, tmp_path):
    sid = added(engine, tmp_path, hashed=False)
    with Session(engine) as s:
        src = s.get(Source, sid)
        assert src is not None
        jobs.ingest_later(jobs.queue(engine), src)
    page = client.get("/api/jobs").json()
    assert [(j["kind"], j["state"], j["domain"]) for j in page["items"]] == [("hash", "queued", f"source:{sid}")]
    assert client.get("/api/jobs/counts").json()["queued"] == 1
    job = page["items"][0]["id"]
    assert client.post(f"/api/jobs/{job}/cancel").json()["state"] == "cancelled"
    assert client.post(f"/api/jobs/{job}/retry").json()["state"] == "queued"
