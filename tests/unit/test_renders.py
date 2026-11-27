"""Renders (app/renders.py, its job, routes and command): a revision rendered to a file with its manifest, found again
for the same clips, and refused or failed in words. ffmpeg is stood in for at strata_python.render (`made` in
conftest); tests/integration renders a real clip."""

import os
from fractions import Fraction

import pytest
from fastapi.testclient import TestClient
from probes import TAPE, answer
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app import chapters, cli, describe, jobs, reels, renders, scenes, settings, shots, sources, speech
from app.models import Render, Revision, Source


@pytest.fixture
def engine(db_env: str) -> Engine:
    return jobs.records()


def analysed(engine: Engine, tmp_path) -> int:
    """A tape analysed by the real jobs over `tape_models`, as tests/unit/test_reels.py makes one."""
    f = tmp_path / "tape.mkv"
    f.write_bytes(b"tape.mkv" * 500)
    with Session(engine, expire_on_commit=False) as s:
        source, _ = sources.add(s, str(f), probe=answer(TAPE))
        source.sha256 = (b"tape.mkv".hex() * 64)[:64]
        s.commit()
        shots.detect(s, source)
        shots.keyframes(s, source)
        scenes.find_scenes(s, source)
        speech.transcribe(s, source)
        describe.describe(s, source)
        chapters.find_chapters(s, source)
        return source.id


def _reel(engine: Engine, tmp_path) -> tuple[int, int, int]:
    sid = analysed(engine, tmp_path)
    with Session(engine) as s:
        src = s.get(Source, sid)
        assert src is not None
        reel, rev = reels.create(s, src, Fraction(12))
        return sid, reel.id, rev.id


def test_a_render_is_queued_made_by_its_job_and_found_again(engine, tmp_path, tape_models, made):
    _, rid, revid = _reel(engine, tmp_path)
    q = jobs.queue(engine)
    with Session(engine) as s:
        reel = reels.get(s, rid)
        rev = reels.revision(s, reel, revid)
        row = renders.request(s, q, reel, rev)
        assert (row.file, row.job_id is not None) == (None, True)
        assert renders.out(s, q, row).state == "queued"
        # asked again while it waits: the same render, one job
        assert renders.request(s, q, reel, rev).id == row.id
    (ran,) = jobs.work(q)
    assert (ran.kind, ran.state) == ("render", "done")
    assert ("tape.mkv+tape.mkv", "render") in made
    with Session(engine) as s:
        row = s.get(Render, row.id)
        assert row is not None
        shown = renders.out(s, q, row)
        assert shown.state == "done"
        assert shown.url == f"api/reels/{rid}/renders/{row.id}/file"
        assert shown.duration_seconds == 12.0
        m = shown.manifest
        assert m is not None
        assert m["app"]["revision"] == revid
        assert m["app"]["key"] == row.key == renders.key_of(s, s.get(Revision, revid))  # type: ignore[arg-type]
        assert [c["start"] for c in m["app"]["clips"]] == ["13/25", "268/25"]
        assert m["file"]["sha256"] == row.sha256
        # no path on this machine in what is shown: sources by id, the file by its name
        src = s.get(Source, row.manifest["app"]["sources"][0]["id"])  # type: ignore[index]
        assert src is not None
        assert src.path not in str(m)
        assert "source:1" in m["command"]
        assert "reel.mp4" in m["command"]
        assert os.path.isfile(os.path.join(settings.SETTINGS.home(), renders.folder(row.key), "manifest.json"))
        # made: asked again, the same row and no job
        again = renders.request(s, q, reels.get(s, rid), reels.revision(s, reels.get(s, rid), revid))
        assert again.id == row.id
    assert jobs.work(q) == []


def test_the_same_clips_on_another_revision_share_the_file_and_a_deleted_file_is_made_again(
    engine, tmp_path, tape_models
):
    _, rid, revid = _reel(engine, tmp_path)
    q = jobs.queue(engine)
    with Session(engine) as s:
        reel = reels.get(s, rid)
        first = renders.request(s, q, reel, reels.revision(s, reel, revid)).id
    jobs.work(q)
    with Session(engine) as s:
        reel = reels.get(s, rid)
        reels.accept(s, rid, revid, author="person")
        # a plan on the accepted revision with nothing changed makes the same clips
        other = reels.propose(s, reel, seed=0, target=Fraction(12))
        assert other.id != revid
        assert other.clips == reels.revision(s, reel, revid).clips
        shared = renders.request(s, q, reel, other)
        assert shared.id != first
        assert (shared.file, shared.job_id) == (s.get(Render, first).file, None)  # type: ignore[union-attr]
        # the file is shared; what it is of is this revision's, with the render that made it named
        assert shared.manifest is not None
        assert shared.manifest["app"]["revision"] == other.id
        assert shared.manifest["app"]["made_by"] == {"render": first, "reel": rid, "revision": revid}
        assert s.get(Render, first).manifest["app"]["revision"] == revid  # type: ignore[union-attr,index]
        os.remove(renders.path_of(shared))
        remade = renders.request(s, q, reel, other)
        assert remade.file is None
        assert remade.job_id is not None


def test_a_tape_changed_since_it_was_added_fails_the_render_in_words(engine, tmp_path, tape_models):
    sid, rid, revid = _reel(engine, tmp_path)
    q = jobs.queue(engine)
    with Session(engine) as s:
        reel = reels.get(s, rid)
        row = renders.request(s, q, reel, reels.revision(s, reel, revid))
        row_id, job_id = row.id, row.job_id
        src = s.get(Source, sid)
        assert src is not None
        with open(src.path, "wb") as f:
            f.write(b"another tape")
    jobs.work(q)
    with Session(engine) as s:
        shown = renders.out(s, q, s.get(Render, row_id))  # type: ignore[arg-type]
        assert shown.state in ("queued", "failed")  # failed once, waiting for its retry
        job = q.get(job_id)  # type: ignore[arg-type]
        assert job is not None
        assert "has changed since it was added" in (job.error or "")


def test_the_render_routes(client: TestClient, engine, tmp_path, tape_models):
    _, rid, revid = _reel(engine, tmp_path)
    asked = client.post(f"/api/reels/{rid}/revisions/{revid}/render")
    assert asked.status_code == 201
    r = asked.json()
    assert (r["state"], r["url"], r["manifest"]) == ("queued", None, None)
    assert client.get(f"/api/reels/{rid}/renders/{r['id']}/file").status_code == 404
    jobs.work(jobs.queue(engine))
    done = client.get(f"/api/reels/{rid}/renders/{r['id']}").json()
    assert done["state"] == "done"
    assert [x["id"] for x in client.get(f"/api/reels/{rid}/renders").json()] == [r["id"]]
    got = client.get(f"/api/reels/{rid}/renders/{r['id']}/file")
    assert got.status_code == 200
    assert got.headers["content-type"] == "video/mp4"
    assert got.headers["content-disposition"].startswith("attachment;")
    assert client.post(f"/api/reels/{rid}/revisions/99/render").status_code == 404
    assert client.get(f"/api/reels/{rid}/renders/99").status_code == 404


def test_an_empty_revision_or_an_unhashed_tape_is_refused(engine, tmp_path, tape_models):
    sid, rid, revid = _reel(engine, tmp_path)
    q = jobs.queue(engine)
    with Session(engine) as s:
        reel = reels.get(s, rid)
        rev = reels.revision(s, reel, revid)
        src = s.get(Source, sid)
        assert src is not None
        src.sha256 = None
        s.commit()
        with pytest.raises(ValueError, match="not hashed yet"):
            renders.request(s, q, reel, rev)
        rev.clips = []
        with pytest.raises(ValueError, match="no clips"):
            renders.request(s, q, reel, rev)
        s.rollback()


def test_the_render_command(engine, tmp_path, tape_models, capsys):
    _, rid, revid = _reel(engine, tmp_path)
    assert cli.main(["render", str(rid)]) == 1
    assert "no accepted revision" in capsys.readouterr().err
    assert cli.main(["render", str(rid), "--revision", str(revid)]) == 0
    assert "queued" in capsys.readouterr().out
    assert cli.main(["render", str(rid), "--revision", str(revid), "--work"]) == 0
    out = capsys.readouterr().out
    assert "reel.mp4" in out
    assert "sha256" in out
    assert cli.main(["render", "9"]) == 1
