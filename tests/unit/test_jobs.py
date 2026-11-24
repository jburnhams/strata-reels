"""The tape pipeline as the app uses it (app/jobs.py): one queued job per tape that advances it through `jobs.TAPE`,
run in this process (`jobs.work`, the queue's deterministic `step`) or by the server's `Runner` on threads, over the
test's migrated database and real files. Job processes (the server's default) run in tests/integration."""

import hashlib
import time

import pytest
from fastapi.testclient import TestClient
from probes import TAPE, answer
from sqlalchemy import Engine
from sqlalchemy.orm import Session
from strata_python import media
from strata_python.db import Project
from strata_python.db.web import Database

from app import jobs, sources
from app.models import Source

STAGES = ["hash", "proxy", "thumbnails", "peaks", "shots", "keyframes", "scenes", "faces", "transcript", "describe"]


@pytest.fixture
def engine(db_env: str) -> Engine:
    """The engine the app's own code opens on the test database (what the handlers use too)."""
    return jobs._records()  # pyright: ignore[reportPrivateUsage]


def add(engine: Engine, tmp_path, content: bytes = b"tape" * 1000, name: str = "tape.mkv") -> Source:
    f = tmp_path / name
    f.write_bytes(content)
    with Session(engine, expire_on_commit=False) as s:
        source, _ = sources.add(s, str(f), probe=answer(TAPE))
    return source


def sha256_of(engine: Engine, source_id: int) -> str | None:
    with Session(engine) as s:
        found = s.get(Source, source_id)
        assert found is not None
        return found.sha256


def made(source: Source) -> dict[str, object]:
    """Whether each stage made its result now (its `new` metric), as the stage records say."""
    records = jobs.store().load(jobs.subject_of(source))
    return {name: r.metrics.get("new") for name, r in records.items()}


def test_the_stages_are_in_the_order_they_need_each_other():
    assert [s.name for s in jobs.TAPE.stages] == STAGES
    assert jobs.TAPE["describe"].needs == ("scenes", "transcript")  # every description has its speech
    assert jobs.TAPE.resources == ["disk"]  # two tapes are never read from one drive at once
    assert jobs.source_id_of(jobs.subject_of(12)) == 12
    with pytest.raises(KeyError, match="not a tape"):
        jobs.source_id_of("book:12")


def test_a_new_tape_runs_every_stage_in_one_job_and_once(engine: Engine, tmp_path, tape_models):
    content = b"\x01\x02" * 300_000
    source = add(engine, tmp_path, content)
    q = jobs.queue(engine)
    jid = jobs.ingest_later(q, source)
    assert jobs.ingest_later(q, source) == jid  # asked again while queued: the same job
    (done,) = jobs.work(q)
    assert (done.id, done.kind, done.state, done.domain) == (jid, "workflow:tape", "done", f"tape:source:{source.id}")
    assert done.result == {"state": "done", "waiting": [], "failed": [], "stages": dict.fromkeys(STAGES, "done")}
    digest = hashlib.sha256(content).hexdigest()
    assert sha256_of(engine, source.id) == digest
    assert made(source) == {"hash": None} | dict.fromkeys(STAGES[1:-1], True) | {"describe": 2}
    # queued again (the tape added again): every stage is done for the same inputs, so nothing runs or is recorded
    history = len(jobs.store().history(jobs.subject_of(source)))
    (again,) = jobs.work(q) if jobs.ingest_later(q, source) else []
    assert again.state == "done"
    assert len(jobs.store().history(jobs.subject_of(source))) == history


def test_a_cleared_stage_runs_again_and_finds_its_result_stored(engine: Engine, tmp_path, tape_models):
    source = add(engine, tmp_path, b"\x03" * 400_000)
    q = jobs.queue(engine)
    jobs.ingest_later(q, source)
    jobs.work(q)
    jobs.TAPE.clear(jobs.store(), jobs.subject_of(source), "shots", by="person")
    jobs.ingest_later(q, source)
    (job,) = jobs.work(q)
    assert job.state == "done"
    # the shots and what follows them ran again, each finding the same request stored; the rest were not touched
    assert made(source) == {"hash": None, "proxy": True, "thumbnails": True, "peaks": True, "transcript": True} | {
        "shots": False, "keyframes": False, "scenes": False, "faces": False, "describe": 0,
    }  # fmt: skip


def test_a_changed_or_missing_file_fails_its_tape_and_is_tried_again(engine: Engine, tmp_path):
    changed = add(engine, tmp_path, b"a" * 5000, "changed.mkv")
    (tmp_path / "changed.mkv").write_bytes(b"b" * 5000)
    gone = add(engine, tmp_path, b"c" * 5000, "gone.mkv")
    (tmp_path / "gone.mkv").unlink()
    q = jobs.queue(engine)
    jobs.ingest_later(q, changed)
    jobs.ingest_later(q, gone)
    first, second = jobs.work(q)  # each fails and is queued to retry later (an outside failure), so work() ends
    assert first.state == second.state == "queued"
    assert "changed.mkv has changed since it was added" in (first.error or "")
    assert "gone.mkv is not there" in (second.error or "")
    assert sha256_of(engine, changed.id) is None
    assert sha256_of(engine, gone.id) is None
    # the hash failed, so nothing that needs it ran: every other stage is blocked behind it
    run = jobs.TAPE.status(jobs.store(), jobs.subject_of(changed), jobs.inputs_of(jobs.subject_of(changed)))
    assert run.failed == ["hash"]
    assert run.records["hash"].error is not None
    assert "has changed" in run.records["hash"].error
    assert {run.states[s] for s in STAGES[1:]} == {"blocked"}


def test_a_job_for_a_source_that_is_not_there_fails(engine: Engine):
    q = jobs.queue(engine)
    q.enqueue(jobs.TAPE.kind, {"subject": "source:404"})
    (job,) = jobs.work(q)
    assert "there is no source 404" in (job.error or "")


def test_the_server_queues_the_tape_and_runs_it_while_it_is_up(db_env: str, tmp_path, tape_models):
    from app.server.app import create_app

    content = b"\x07" * 9000
    f = tmp_path / "tape.mkv"
    f.write_bytes(content)
    raw = {"format": {"duration": "12.5"}, "streams": [{"codec_type": "video", "r_frame_rate": "25/1"}]}
    engine = jobs._records()  # pyright: ignore[reportPrivateUsage]
    with Session(engine) as s:  # as ffprobe would describe it: the server then knows it by content
        sources.add(s, str(f), probe=lambda p: media.Probe.from_json(p, raw))
    q = jobs.queue(engine)
    with TestClient(create_app(run_jobs="threads")) as client:  # the server's Runner, on threads
        assert client.post("/api/sources", json={"path": str(f)}).status_code == 200
        deadline = time.monotonic() + 20
        while any(t.state != "done" for t in q.page().items) and time.monotonic() < deadline:
            time.sleep(0.05)
        run = client.get("/api/workflows/tape/subjects/source:1").json()
    assert [(t.kind, t.state) for t in q.page().items] == [("workflow:tape", "done")]
    assert run["state"] == "done"
    assert run["job"]["state"] == "done"
    # no audio stream: the peaks and transcript stages are done with nothing to make
    stages = {s["name"]: s for s in run["stages"]}
    assert [s["title"] for s in run["stages"]][:2] == ["Read the whole file", "Make a browser copy"]
    assert stages["peaks"]["state"] == stages["transcript"]["state"] == "done"
    assert sha256_of(engine, 1) == hashlib.sha256(content).hexdigest()


def test_the_runner_waits_for_a_database_that_cannot_open_and_stops(tmp_path):
    class Broken(Database):
        def __init__(self) -> None:
            super().__init__(Project.load(jobs.__file__))
            self.tries = 0

        def engine(self) -> Engine:
            self.tries += 1
            raise OSError("the database server is down")

    records = Broken()
    runner = jobs.Runner(records, retry_s=0.01)
    runner.start()
    deadline = time.monotonic() + 10
    while records.tries < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    runner.stop()
    assert records.tries >= 2  # tried again, not given up
    assert runner.worker is None


def test_the_workflow_routes_show_a_tape_and_run_it_again(client: TestClient, engine: Engine, tmp_path, tape_models):
    source = add(engine, tmp_path, b"\x05" * 200_000)
    subject = jobs.subject_of(source)
    assert client.get("/api/workflows/tape/subjects/source:9").status_code == 404
    assert client.get("/api/workflows/tape/subjects/book:1").status_code == 404
    before = client.get(f"/api/workflows/tape/subjects/{subject}").json()
    assert (before["state"], before["job"]) == ("blocked", None)  # added outside the server: nothing queued yet
    queued = client.post(f"/api/workflows/tape/subjects/{subject}/run").json()
    assert queued["job"]["state"] == "queued"
    jobs.work(jobs.queue(engine))
    listed = client.get("/api/workflows/tape/subjects", params={"state": "done"}).json()
    assert [(r["subject"], r["state"]) for r in listed["items"]] == [(subject, "done")]
    (hashed,) = client.get(f"/api/workflows/tape/subjects/{subject}/stages/hash").json()
    assert hashed["data"] == {"sha256": hashlib.sha256(b"\x05" * 200_000).hexdigest()}
    cleared = client.post(f"/api/workflows/tape/subjects/{subject}/stages/faces/clear").json()
    assert {s["name"]: s["state"] for s in cleared["stages"]}["faces"] == "ready"
    assert cleared["job"]["state"] == "queued"
