"""Who is on the tape (app/people.py, its stages and its routes) over the test's migrated database, with strata-models
stood in for over real HTTP: `tape_faces` answers face-tracks with probes.TRACKS (A in the first two shots, B beside A in
the second and alone in the last), and the stand-in face store (strata-python's `fake_models.faces`) keeps the stills,
the person's decisions and people, and groups tracks by likeness."""

import os

import pytest
from fastapi.testclient import TestClient
from probes import TAPE, answer
from sqlalchemy import Engine
from sqlalchemy.orm import Session
from strata_python.clients import faces as fs
from strata_python.clients import models as sm

from app import describe, jobs, overrides, people, scenes, shots, sources, speech
from app.models import Source


@pytest.fixture
def engine(db_env: str) -> Engine:
    return jobs._records()  # pyright: ignore[reportPrivateUsage]


def added(engine: Engine, tmp_path) -> Source:
    f = tmp_path / "tape.mkv"
    f.write_bytes(b"tape" * 500)
    with Session(engine, expire_on_commit=False) as s:
        source, _ = sources.add(s, str(f), probe=answer(TAPE))
        source.sha256 = "ab" * 32
        s.commit()
    return source


def tracked(s: Session, src: Source) -> None:
    shots.detect(s, src)
    shots.keyframes(s, src)
    scenes.find_scenes(s, src)
    people.track(s, src)
    people.store(s, src)


def by_group(got: people.People) -> dict[str, list[str]]:
    return {g.tracks[0]: g.tracks for g in got.groups}


def test_tracks_are_asked_shot_by_shot_and_kept_in_a_file(engine, tmp_path, tape_faces):
    src = added(engine, tmp_path)
    with Session(engine) as s:
        with pytest.raises(KeyError, match="no shots yet"):
            people.track(s, src)
        shots.detect(s, src)
        r, new = people.track(s, src)
        assert new
        (job,) = [j for j in tape_faces.jobs if j["role"] == "face-tracks"]
        assert job["params"]["cuts"] == [4.0, 10.0]  # no track runs across a cut
        assert job["params"]["out_dir"].startswith(os.path.join(os.path.dirname(people._file(r)), "stills"))  # pyright: ignore[reportPrivateUsage]
        assert (r.kind, r.model, r.parameters["stills"]) == ("face-tracks", "buffalo_l-tracks", 3)
        assert [t["id"] for t in r.payload["tracks"]] == [1, 2, 3, 4]
        assert r.payload["together"] == [[2, 3]]
        assert "embedding" not in r.payload["tracks"][0]  # the file has those
        assert people.tracks_of(r).tracks[0].embedding == [1.0, 0.0]
        assert people.track(s, src) == (r, False)  # stored: not asked again
        os.remove(people._file(r))  # pyright: ignore[reportPrivateUsage]
        again, _ = people.track(s, src)  # its file gone: asked again, the same row kept (append-only)
        assert again.id == r.id
        assert os.path.isfile(people._file(again))  # pyright: ignore[reportPrivateUsage]
        assert len([j for j in tape_faces.jobs if j["role"] == "face-tracks"]) == 2


def test_every_still_goes_into_the_face_store_under_the_tapes_tag(engine, tmp_path, tape_faces):
    src = added(engine, tmp_path)
    with Session(engine) as s:
        with pytest.raises(KeyError, match="no face tracks"):
            people.store(s, src)
        tracked(s, src)
        kept = people.current_store(s, src)
        assert kept is not None
        assert kept.payload["tag"] == "strata-reels:" + "ab" * 8
        assert {k: len(v) for k, v in kept.payload["faces"].items()} == {"1": 2, "2": 2, "3": 2, "4": 2}
        assert people.store(s, src) == (kept, False)
        tracking = kept.parameters["tracks"]
    store = tape_faces.faces
    assert store.tags == {"strata-reels": True}  # other apps see these faces only when they ask for them
    assert len(store.images) == 8
    first = store.images[1]
    assert (first["app"], first["ref"], first["width"], first["height"]) == (
        "strata-reels",
        f"abababababababab/{tracking[:16]}/t1/1.000",  # a new tracking never answers an earlier one's picture
        720,
        576,
    )
    assert first["tags"] == ["strata-reels", "strata-reels:abababababababab"]
    assert first["faces"][0]["box"] == [40.0, 100.0, 200.0, 300.0]  # a 720-wide tape's stills are not scaled


def test_a_still_of_a_wide_video_has_its_box_in_the_frames_pixels():
    assert people.still_size(720, 576) == (720, 576)
    assert people.still_size(3840, 2160) == (1600, 900)
    assert people.still_size(1921, 1081) == (1600, 900)


def test_naming_a_group_skipping_one_and_the_gate(engine, tmp_path, tape_faces, client: TestClient):
    src = added(engine, tmp_path)
    with Session(engine) as s:
        tracked(s, src)
        got = people.people_of(s, src, done=False)
        assert by_group(got) == {"1": ["1", "2"], "3": ["3", "4"]}  # A twice, B twice: two people to name
        assert (got.open, got.named, got.done) == (4, 0, False)
        a = next(g for g in got.groups if g.tracks[0] == "1")
        assert (a.seconds, a.first) == (7.5, 0.5)
        kept = people.current_store(s, src)
        assert kept is not None
        # every track once, the longest first, each with its first still
        assert [(m.track, m.face) for m in a.members] == [(k, kept.payload["faces"][k][0]) for k in ("2", "1")]
        assert people.is_open(s, src)
    sid = src.id
    named = client.post(f"/api/sources/{sid}/people", json={"tracks": ["1", "2"], "answer": "name", "name": "Ann"})
    assert named.status_code == 200
    body = named.json()
    assert [(p["name"], p["tracks"], p["seconds"]) for p in body["people"]] == [("Ann", 2, 7.5)]
    assert [g["tracks"] for g in body["groups"]] == [["3", "4"]]
    assert (body["open"], body["named"]) == (2, 2)
    assert [p["name"] for p in client.get("/api/people", params={"q": "an"}).json()] == ["Ann"]
    skipped = client.post(f"/api/sources/{sid}/people", json={"tracks": ["3", "4"], "answer": "skip"}).json()
    assert (skipped["groups"], skipped["open"], skipped["skipped"]) == ([], 0, 2)
    with Session(engine) as s:
        assert not people.is_open(s, src)  # the gate's policy lets the tape on
        assert len(people.said_key(s, src)) == 2  # both answers are the cast stage's input
        kept = people.current_store(s, src)
        assert kept is not None
        assert people.skipped(s, src, kept) == {"3", "4"}
        # a skip from another tracking is of other tracks, whatever their numbers
        overrides.add(
            s, "tape", src.id, people.SKIP, {"tracks": ["1"], "tracking": "an-earlier-tracking"}, author="ada"
        )
        assert people.skipped(s, src, kept) == {"3", "4"}
    bad = client.post(f"/api/sources/{sid}/people", json={"tracks": ["9"], "answer": "skip"})
    assert (bad.status_code, bad.json()["detail"]) == (400, "no track 9 on tape.mkv")
    nameless = client.post(f"/api/sources/{sid}/people", json={"tracks": ["1"], "answer": "name"})
    assert nameless.json()["detail"] == "a new person needs a name"


def test_not_faces_and_a_person_known_already(engine, tmp_path, tape_faces):
    src = added(engine, tmp_path)
    ann = fs.create_identity(name="Ann", app="strata-reels")
    with Session(engine) as s:
        tracked(s, src)
        people.answer(s, src, ["1", "2"], "name", author="person", person=ann.id)
        people.answer(s, src, ["3"], "not-faces", author="person")
        got = people.people_of(s, src, done=False)
        assert (got.named, got.not_faces, got.open) == (2, 1, 1)
        assert [p.id for p in got.people] == [ann.id]
        assert by_group(got) == {"4": ["4"]}


def test_a_known_person_is_suggested_and_hidden_faces_wait_for_nobody(engine, tmp_path, tape_faces):
    src = added(engine, tmp_path)
    ann = fs.create_identity(name="Ann", app="strata-reels")
    tape_faces.faces.suggest["1"] = ann.id
    with Session(engine) as s:
        tracked(s, src)
        groups = {g.tracks[0]: g for g in people.people_of(s, src, done=False).groups}
        suggested = groups["1"].suggested
        assert suggested is not None
        assert (suggested.id, suggested.name) == (ann.id, "Ann")
        assert groups["3"].suggested is None
        kept = people.current_store(s, src)
        assert kept is not None
        tape_faces.faces.hidden.update(f for ids in kept.payload["faces"].values() for f in ids)  # all under the bar
        got = people.people_of(s, src, done=False)
        assert (got.groups, got.hidden, got.open) == ([], 4, 0)
        assert not people.is_open(s, src)


def test_the_tracks_state_comes_from_its_stills():
    faces = {"a": [1, 2], "b": [3], "c": [4], "d": [5], "e": [6]}
    said = {1: (7, "confirmed"), 2: (8, "suggested"), 3: (None, "rejected"), 4: (7, "suggested")}
    got = people.decide(faces, said, skips={"c", "b"}, hidden={"e"})
    assert got.status == {"a": "named", "b": "not-face", "c": "skipped", "d": "open", "e": "hidden"}
    assert got.person == {"a": 7}
    assert got.open() == ["d"]


def test_where_a_face_is_in_words():
    assert people.where([0, 0, 100, 200], 720, 576) == "on the left, close to the camera"
    assert people.where([300, 0, 400, 100], 720, 576) == "in the middle"
    assert people.where([600, 0, 640, 40], 720, 576) == "on the right, far away"
    t = sm.FaceTrack(1, 0, 1.0, 2.0, 3, None, [], [[1.0, 1, 2, 3, 4, 0.9], [1.5, 5, 6, 7, 8, 0.9]], [])
    assert people.box_at(t, 1.4) == [5, 6, 7, 8]
    assert people.box_at(t, 2.5) is None


def test_the_cast_says_who_is_in_each_scene_and_where_on_each_keyframe(engine, tmp_path, tape_faces):
    src = added(engine, tmp_path)
    with Session(engine) as s:
        tracked(s, src)
        people.answer(s, src, ["1", "2"], "name", author="person", name="Ann")
        people.answer(s, src, ["3", "4"], "name", author="person", name="Ben")
        r, new = people.cast(s, src)
        assert new
        assert people.cast(s, src) == (r, False)
        assert people.current_cast(s, src) == r
        first, second = r.payload["scenes"]
        assert [(p["name"], p["seconds"]) for p in first["people"]] == [("Ann", 7.5), ("Ben", 3.0)]
        assert [p["name"] for p in second["people"]] == ["Ben"]
        assert people.in_scene(r.payload, "10") == second["people"]
        assert people.in_scene(r.payload, "99") == []
        # the keyframes: 49 (Ann, close, on the left), 174 (Ann and Ben), 324 (Ben)
        assert people.for_describer(r.payload, [49, 174]) == [
            {"name": "Ann", "seen": [{"frame": 1, "where": "on the left, close to the camera"}, {"frame": 2, "where": "on the left"}]},
            {"name": "Ben", "seen": [{"frame": 2, "where": "on the right"}]},
        ]  # fmt: skip
        assert people.for_describer(None, [49]) == []
        # the describer is given them, and a description says who does what
        speech.transcribe(s, src)
        made = describe.describe(s, src)
        asked = [j["params"]["context"]["people"] for j in tape_faces.jobs if j["role"] == "scene-describe"]
        assert [p["name"] for p in asked[0]] == ["Ann", "Ben"]
        assert made[0][0].payload["people"] == asked[0]
        # Ben renamed through this app: a new cast, which the descriptions are keyed on
        people.answer(s, src, ["4"], "name", author="person", name="Benjamin")
        later, _ = people.cast(s, src)
        assert later.id != r.id
        assert [p["name"] for p in later.payload["scenes"][1]["people"]] == ["Benjamin"]


def test_the_tape_waits_for_its_people_then_goes_on(engine, tmp_path, tape_faces, client: TestClient):
    src = added(engine, tmp_path)
    q = jobs.queue(engine)
    jobs.ingest_later(q, src)
    (job,) = jobs.work(q)
    assert job.result is not None
    assert job.result["waiting"] == ["name_people"]
    stages = job.result["stages"]
    assert (stages["store_faces"], stages["cast"], stages["describe"], stages["transcript"]) == (
        "done", "blocked", "blocked", "done",
    )  # fmt: skip
    got = client.get(f"/api/sources/{src.id}/people").json()
    assert (len(got["groups"]), got["done"]) == (2, False)
    face = got["groups"][0]["members"][0]["face"]
    crop = client.get(f"/api/sources/{src.id}/faces/{face}/crop")
    assert (crop.status_code, crop.content[:4]) == (200, b"\x89PNG")
    assert client.get(f"/api/sources/{src.id}/faces/999/crop").status_code == 404
    client.post(
        f"/api/sources/{src.id}/people", json={"tracks": got["groups"][0]["tracks"], "answer": "name", "name": "Ann"}
    )
    jobs.work(q)
    assert client.get(f"/api/sources/{src.id}/people").json()["done"] is False  # Ben is left
    # the person skips the rest: the gate's own approve
    subject = jobs.subject_of(src)
    approved = client.post(f"/api/workflows/tape/subjects/{subject}/stages/name_people/approve", json={}).json()
    assert {s["name"]: s["state"] for s in approved["stages"]}["name_people"] == "done"
    jobs.work(q)
    assert client.get(f"/api/sources/{src.id}/people").json()["done"] is True
    scenes_now = client.get(f"/api/sources/{src.id}/scenes").json()
    assert scenes_now["analysis"]["cast"] is True
    assert [[p["name"] for p in sc["people"]] for sc in scenes_now["scenes"]] == [["Ann"], []]
    assert scenes_now["scenes"][0]["description"]["who"] == []  # the stand-in describer says nobody
