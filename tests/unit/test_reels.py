"""Reels (app/reels.py and its routes): a tape's reel as immutable revisions, proposals accepted by compare-and-swap,
and the person's pins, bans and locks kept by every plan. Over the test's migrated database, with the tape analysed by
the real jobs and strata-models stood in for over real HTTP (`tape_models`: two scenes, a chapter each, both described
with people in them, a phrase said in each)."""

from fractions import Fraction

import pytest
from fastapi.testclient import TestClient
from probes import TAPE, answer
from sqlalchemy import Engine
from sqlalchemy.orm import Session
from strata_python.tasks import Conflict

from app import chapters, cli, describe, jobs, overrides, reels, scenes, shots, sources, speech
from app.models import Source


@pytest.fixture
def engine(db_env: str) -> Engine:
    return jobs._records()  # pyright: ignore[reportPrivateUsage]


def analysed(engine: Engine, tmp_path, *, name: str = "tape.mkv", chaptered: bool = True) -> int:
    f = tmp_path / name
    f.write_bytes(name.encode() * 500)
    with Session(engine, expire_on_commit=False) as s:
        source, _ = sources.add(s, str(f), probe=answer(TAPE))
        source.sha256 = (name.encode().hex() * 64)[:64]
        s.commit()
        shots.detect(s, source)
        shots.keyframes(s, source)
        scenes.find_scenes(s, source)
        speech.transcribe(s, source)
        describe.describe(s, source)
        if chaptered:
            chapters.find_chapters(s, source)
        return source.id


def test_a_new_reel_is_a_proposal_cut_from_the_tapes_scenes_and_names_what_it_read(engine, tmp_path, tape_models):
    sid = analysed(engine, tmp_path)
    with Session(engine) as s:
        src = s.get(Source, sid)
        assert src is not None
        reel, rev = reels.create(s, src, Fraction(12))
        assert (reel.name, reel.target) == ("tape.mkv, 0:12", "12")
        assert (rev.base_id, rev.author, rev.accepted) == (None, "planner", None)
        assert rev.planner == {"name": "baseline", "version": 1, "seed": 0, "parameters": reels.planner.PARAMETERS}
        assert rev.inputs["results"]["scenes"] == scenes.current(s, src).cache_key  # type: ignore[union-attr]
        assert rev.inputs["overrides"] == []
        first, second = rev.clips
        # the first scene less half a second at each end (onto frames), then what is left from the second, around its song
        assert (first["start"], first["end"], second["start"], second["end"]) == ("13/25", "237/25", "268/25", "344/25")
        assert (first["scene"], second["scene"]) == ("0", "10")
        assert rev.duration == "12"
        assert first["id"] == reels.clip_id(src, reels.Span.of("13/25", "237/25"))
        assert first["reasons"] == ["2 people", "a child", "4 words said"]
        shown = reels.revision_out(s, rev)
        assert (shown.state, shown.rate, shown.duration_seconds) == ("proposal", "25", 12.0)
        assert [c.at_seconds for c in shown.clips] == [0.0, 8.96]
        # the same plan asked again on the same base is the same proposal
        assert reels.propose(s, reel).id == rev.id
        assert reels.propose(s, reel, seed=0, target=Fraction(12)).id == rev.id


def test_before_its_scenes_a_tape_has_no_reel_and_lengths_are_read_as_people_write_them(engine, tmp_path):
    f = tmp_path / "bare.mkv"
    f.write_bytes(b"bare" * 500)
    with Session(engine) as s:
        bare, _ = sources.add(s, str(f), probe=answer(TAPE))
        with pytest.raises(KeyError, match="no scenes yet"):
            reels.create(s, bare, Fraction(60))
        assert reels.all_reels(s) == []
        with pytest.raises(ValueError, match="above 0"):
            reels.create(s, bare, Fraction(0))
        with pytest.raises(KeyError, match="no reel 9"):
            reels.get(s, 9)
    assert [reels.length(x) for x in (90, "90", "1:30", "1:02:03", 2.5)] == [90, 90, 90, 3723, Fraction(5, 2)]
    for bad in ("0", "-3", "1::2", "1:2:3:4", "soon"):
        with pytest.raises(ValueError, match=r"above 0|seconds, m:ss|not a number"):
            reels.length(bad)


def test_accepting_is_compare_and_swap_on_the_current_revision(engine, tmp_path, tape_models):
    sid = analysed(engine, tmp_path)
    with Session(engine) as s:
        src = s.get(Source, sid)
        assert src is not None
        reel, first = reels.create(s, src, Fraction(8))
        other = reels.propose(s, reel, seed=7)  # another version on the same (no) base
        assert reels.accept(s, reel.id, first.id, author="ada").accepted_by == "ada"
        assert reels.current(s, reel) == first
        assert reels.accept(s, reel.id, first.id, author="ada") == first  # again: nothing changes
        with pytest.raises(Conflict, match="changed since this proposal was made") as stale:
            reels.accept(s, reel.id, other.id, author="ada")
        assert stale.value.payload == {"current": first.id}
        assert reels.state_of(other, first) == "stale"
        longer = reels.propose(s, reel, target=Fraction(14))
        assert (longer.base_id, reel.target, longer.duration) == (
            first.id,
            "14",
            "348/25",
        )  # both scenes, whole: 13.92 s is all there is
        reels.reject(s, reel, other.id)
        with pytest.raises(ValueError, match="was rejected"):
            reels.accept(s, reel.id, other.id, author="ada")
        reels.accept(s, reel.id, longer.id, author="ada")
        assert [reels.state_of(r, longer) for r in (first, other, longer)] == ["past", "rejected", "current"]
        with pytest.raises(ValueError, match="was accepted"):
            reels.reject(s, reel, first.id)
        with pytest.raises(KeyError, match="no revision 99"):
            reels.revision(s, reel, 99)


def test_accept_and_reject_read_the_proposal_again_under_the_lock(engine, tmp_path, tape_models):
    sid = analysed(engine, tmp_path)
    with Session(engine) as one, Session(engine) as two:
        src = one.get(Source, sid)
        assert src is not None
        reel, rev = reels.create(one, src, Fraction(8))
        assert rev.rejected is None  # loaded in this session before the other rejects it
        reels.reject(two, reels.get(two, reel.id), rev.id)
        with pytest.raises(ValueError, match="was rejected"):
            reels.accept(one, reel.id, rev.id, author="ada")
        other = reels.propose(one, reel, seed=5)
        assert other.accepted is None
        reels.accept(two, reel.id, other.id, author="ada")
        with pytest.raises(ValueError, match="was accepted"):
            reels.reject(one, reel, other.id)


def test_pins_bans_and_locks_hold_through_every_plan(engine, tmp_path, tape_models):
    sid = analysed(engine, tmp_path)
    with Session(engine) as s:
        src = s.get(Source, sid)
        assert src is not None
        reel, first = reels.create(s, src, Fraction(8))
        assert [c["scene"] for c in first.clips] == ["0"]
        ban = reels.constrain(s, reel, "ban", scene=0, author="ada")
        assert (ban.mode, ban.value) == ("forbid", {"source": sid, "start": "0", "end": "10"})
        assert reels.constrain(s, reel, "ban", scene=0, author="ada").id == ban.id  # said again: nothing new
        banned = reels.propose(s, reel)
        assert [c["scene"] for c in banned.clips] == ["10"]
        assert banned.inputs["overrides"] == [ban.id]
        pin = reels.constrain(s, reel, "pin", scene=0, author="ada")  # takes the ban back
        assert not overrides.holds(ban)
        pinned = reels.propose(s, reel)
        assert [(c["scene"], c["pinned"]) for c in pinned.clips] == [("0", True)]
        clip = pinned.clips[0]["id"]
        lock = reels.constrain(s, reel, "lock", clip=clip, author="ada")
        assert (lock.value["start"], lock.value["end"]) == (pinned.clips[0]["start"], pinned.clips[0]["end"])
        reels.unconstrain(s, reel, pin.id)
        locked = reels.propose(s, reel, target=Fraction(12))
        assert (locked.clips[0]["id"], locked.clips[0]["locked"]) == (clip, True)  # the same range keeps its id
        later = reels.constrain(s, reel, "ban", scene=0, author="ada")
        overruled = reels.propose(s, reel)
        assert all(c["id"] != clip for c in overruled.clips)
        assert overruled.conflicts == [
            {"override": lock.id, "message": "The lock on a clip in the scene at 0:00 gave way to a later word."}
        ]
        shown = reels.detail(s, reel)
        assert [(c.kind, c.clip) for c in shown.constraints] == [("lock", clip), ("ban", None)]
        assert shown.open[0].conflicts[0].override == lock.id
        reels.unconstrain(s, reel, later.id)
        with pytest.raises(KeyError, match="no pin, ban or lock"):
            reels.unconstrain(s, reel, later.id)
        for kind, scene_, clip_, words in [
            ("star", 0, None, "one of pin, ban, lock"),
            ("pin", None, None, "a pin names a scene"),
            ("ban", 5, None, "scenes 1 to 2, not scene 6"),
            ("lock", None, None, "a lock names a clip"),
            ("lock", None, "cnothere", "no clip cnothere"),
        ]:
            with pytest.raises(ValueError, match=words):
                reels.constrain(s, reel, kind, scene=scene_, clip=clip_, author="ada")


def test_the_reel_routes_plan_accept_and_constrain(client: TestClient, engine, tmp_path, tape_models):
    sid = analysed(engine, tmp_path)
    made = client.post("/api/reels", json={"source_id": sid, "target_seconds": 8})
    assert made.status_code == 201
    reel = made.json()
    assert (reel["current"], reel["proposals"], reel["clips"]) == (None, 1, None)
    (proposal,) = reel["open"]
    assert proposal["state"] == "proposal"
    rid, rev = reel["id"], proposal["id"]
    assert client.get(f"/api/reels/{rid}/revisions/{rev}").json()["clips"][0]["start"] == "1"
    other = client.post(f"/api/reels/{rid}/plan", json={"seed": 3}).json()
    assert client.post(f"/api/reels/{rid}/revisions/{rev}/accept").json()["state"] == "current"
    stale = client.post(f"/api/reels/{rid}/revisions/{other['id']}/accept")
    assert stale.status_code == 409
    assert stale.json()["payload"] == {"current": rev}
    assert client.post(f"/api/reels/{rid}/revisions/{other['id']}/reject").json()["state"] == "rejected"
    listed = client.get("/api/reels").json()
    assert [(r["current_id"], r["duration_seconds"], r["clips"], r["proposals"]) for r in listed] == [(rev, 8.0, 1, 0)]
    assert client.get(f"/api/reels?source={sid}").json() == listed
    assert client.get("/api/reels?source=99").status_code == 404
    banned = client.post(f"/api/reels/{rid}/constraints", json={"kind": "ban", "scene": 0}).json()
    (ban,) = banned["constraints"]
    assert (ban["kind"], ban["start_seconds"], ban["end_seconds"], ban["author"]) == ("ban", 0.0, 10.0, "person")
    replanned = client.post(f"/api/reels/{rid}/plan", json={"target_seconds": 5}).json()
    assert (replanned["base_id"], replanned["target_seconds"]) == (rev, 5.0)
    assert [c["scene"] for c in replanned["clips"]] == ["10"]
    assert client.get(f"/api/reels/{rid}").json()["target_seconds"] == 5.0
    assert client.delete(f"/api/reels/{rid}/constraints/{ban['id']}").json()["constraints"] == []
    assert client.post(f"/api/reels/{rid}/constraints", json={"kind": "lock"}).status_code == 400
    assert client.post(f"/api/reels/{rid}/constraints", json={"kind": "star"}).status_code == 422
    assert client.post("/api/reels", json={"source_id": sid, "target_seconds": 0}).status_code == 400
    assert client.get("/api/reels/9").json()["detail"] == "there is no reel 9"
    assert client.get(f"/api/reels/{rid}/revisions/99").status_code == 404


def test_the_reel_command_plans_accepts_and_lists(engine, tmp_path, tape_models, capsys):
    sid = analysed(engine, tmp_path, chaptered=False)
    assert cli.main(["reels"]) == 0
    assert "no reels yet" in capsys.readouterr().out
    assert cli.main(["reel", str(sid), "--target", "0:08", "--accept"]) == 0
    out = capsys.readouterr().out
    assert "reel #1 tape.mkv, 0:08: revision 1 (current), 1 clips, 0:08 of 0:08" in out
    assert "2 people, a child, 4 words said, cut around what is said" in out
    assert cli.main(["reel", str(sid), "--seed", "2", "--json"]) == 0
    again = capsys.readouterr().out
    assert '"base_id": 1' in again
    assert '"seed": 2' in again
    assert cli.main(["reel", str(sid), "--new", "--name", "Short", "--seed", "1"]) == 0
    assert "reel #2 Short" in capsys.readouterr().out
    assert cli.main(["reels"]) == 0
    listed = capsys.readouterr().out
    assert "revision 1, 1 clips, 1 open" in listed
    assert "no revision accepted, 2 open" in listed
    assert cli.main(["reels", "--json"]) == 0
    assert '"source_name": "tape.mkv"' in capsys.readouterr().out
    assert cli.main(["reel", "99"]) == 1
    assert "there is no source 99" in capsys.readouterr().err
    assert cli.main(["reel", str(sid), "--target", "soon"]) == 1
    assert "not a number" in capsys.readouterr().err
