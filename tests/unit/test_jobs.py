"""The job queue as the app uses it (app/jobs.py): the content hash as a job, run in this process (`jobs.work`, the queue's
deterministic `step`) or by the server's `Runner` on threads, over the test's migrated database and real files. Job
processes (the server's default) run in tests/integration."""

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


def test_a_new_source_is_hashed_once_by_a_queued_job(engine: Engine, tmp_path, tape_models):
    content = b"\x01\x02" * 300_000
    source = add(engine, tmp_path, content)
    q = jobs.queue(engine)
    jid = jobs.hash_later(q, source)
    assert jid is not None
    assert jobs.hash_later(q, source) == jid  # asked again while queued: the same job
    done, *derived = jobs.work(q)
    assert (done.id, done.state, done.domain) == (jid, "done", f"source:{source.id}")
    # the hash queues the derived assets, the shots and the speech, which run next (the tape has sound, so peaks and a
    # transcript too); the shots queue the keyframes, they the scenes, and the scenes the faces and descriptions
    assert [(j.kind, j.state) for j in derived] == [
        ("proxy", "done"), ("thumbnails", "done"), ("peaks", "done"), ("shots", "done"), ("transcript", "done"),
        ("keyframes", "done"), ("scenes", "done"), ("faces", "done"), ("describe", "done"),
    ]  # fmt: skip
    digest = hashlib.sha256(content).hexdigest()
    assert done.result == {"sha256": digest}
    assert sha256_of(engine, source.id) == digest
    with Session(engine) as s:
        known = s.get(Source, source.id)
        assert known is not None
        assert jobs.hash_later(q, known) is None  # known: nothing to queue
    assert jobs.work(q) == []
    # a second hash job that finds the hash already there (queued twice from two places) reads nothing
    q.enqueue("hash", {"source": source.id})
    again, *assets = jobs.work(q)
    assert again.result == {"sha256": digest}
    # and queues the assets again, in case a crash came between storing the hash and queueing them: each finds its own
    assert [(j.kind, j.result is not None and j.result["new"]) for j in assets] == [
        ("proxy", False),
        ("thumbnails", False),
        ("peaks", False),
        ("shots", False),
        ("transcript", False),
        ("keyframes", False),
        ("describe", False),  # the transcript queues it when the scenes are there
        ("scenes", False),
        ("faces", False),
        ("describe", False),
    ]


def test_a_changed_or_missing_file_is_not_hashed_as_the_source(engine: Engine, tmp_path):
    changed = add(engine, tmp_path, b"a" * 5000, "changed.mkv")
    (tmp_path / "changed.mkv").write_bytes(b"b" * 5000)
    gone = add(engine, tmp_path, b"c" * 5000, "gone.mkv")
    (tmp_path / "gone.mkv").unlink()
    q = jobs.queue(engine)
    jobs.hash_later(q, changed)
    jobs.hash_later(q, gone)
    first, second = jobs.work(q)  # each fails and is queued to retry later (an outside failure), so work() ends
    assert first.state == second.state == "queued"
    assert "changed.mkv has changed since it was added" in (first.error or "")
    assert "gone.mkv is not there" in (second.error or "")
    assert sha256_of(engine, changed.id) is None
    assert sha256_of(engine, gone.id) is None


def test_a_hash_job_for_a_source_that_is_not_there_fails(engine: Engine):
    q = jobs.queue(engine)
    q.enqueue("hash", {"source": 404})
    (job,) = jobs.work(q)
    assert "there is no source 404" in (job.error or "")


def test_the_server_queues_the_hash_and_runs_it_while_it_is_up(db_env: str, tmp_path, tape_models):
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
        while (
            len(q.page().items) < 8 or any(t.state != "done" for t in q.page().items)
        ) and time.monotonic() < deadline:
            time.sleep(0.05)
    # newest first: the hash, then the assets and shots it queued (no audio stream, so no peaks), then what they queued
    assert [(t.kind, t.state) for t in reversed(q.page().items)] == [
        ("hash", "done"),
        ("proxy", "done"),
        ("thumbnails", "done"),
        ("shots", "done"),
        ("keyframes", "done"),
        ("scenes", "done"),
        ("faces", "done"),
        ("describe", "done"),  # at once: there is no sound to wait for
    ]
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
