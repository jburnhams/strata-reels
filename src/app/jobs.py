"""Work that runs in the background: the durable queue of strata_python.tasks.queue in this app's database (overview §6).

Hashing a tape and, from step 2, every analysis run as queued jobs, so they survive a restart, retry an outside failure
with a backoff, can be cancelled, and never block a request. The queue, its retries, crash recovery and the job
processes are strata-python's; this module names the app's kinds and what each does.

- **The server runs them**: `Runner` starts a `Worker` once the database opens and stops it with the server. Each job
  runs in a job process of its own (strata-python's default), so a cancel ends it and every tool it started.
- **The CLI can too**: `./app work` runs what is queued, in its own process, until the queue is empty: what a checkout
  with no server running uses, and what the integration tests drive.
- **Handlers are module-level functions** (a job process imports them by name) and open the database themselves
  (`_records`, once per process), since a job process is not the server.

Kinds:

- `hash`: the full SHA-256 of a source's content (`Source.sha256`), its identity in every cache key. Queued when a source
  is added (`hash_later`). The file is checked against the source's quick fingerprint before and after reading, so a
  file changed or replaced under the same path is refused rather than hashed as the source. Holds the `disk` resource:
  two tapes read at once from one drive are slower than one after the other."""

import logging
import os
import threading
from typing import Any

from sqlalchemy import Engine
from sqlalchemy.orm import Session
from strata_python import files
from strata_python.db import url_for
from strata_python.db.web import Database
from strata_python.tasks.queue import Handler, Job, Queue, Worker

from app import db
from app.models import JOBS, Source

log = logging.getLogger(__name__)


class SourceChanged(ValueError):
    """The file at a source's path is no longer that footage (edited, or another file in its place)."""


def queue(engine: Engine) -> Queue:
    return Queue(engine, JOBS)


def hash_later(q: Queue, source: Source) -> int | None:
    """Queue the content hash of a source that has none yet; the job's id, or None when it is already known. Asking
    again while one is queued or running returns that one."""
    if source.sha256 is not None:
        return None
    return q.enqueue("hash", {"source": source.id}, domain=f"source:{source.id}", dedupe=True, resources=["disk"])


def hash_source(job: Job) -> dict[str, Any]:
    """The `hash` job: read the whole file and store its SHA-256 on the source."""
    source_id = int(job.options["source"])
    with Session(_records()) as s:
        source = s.get(Source, source_id)
        if source is None:
            raise KeyError(f"there is no source {source_id}")
        if source.sha256 is not None:
            return {"sha256": source.sha256}
        path, expected = source.path, source.fingerprint
    _same_footage(path, expected)
    digest = files.sha256_hex(path)
    _same_footage(path, expected)  # and still: a file rewritten while it was read is not the source either
    with Session(_records()) as s:
        source = s.get(Source, source_id)
        if source is None:
            raise KeyError(f"there is no source {source_id}")
        source.sha256 = digest
        s.commit()
    return {"sha256": digest}


HANDLERS: dict[str, Handler] = {"hash": hash_source}


def work(q: Queue) -> list[Job]:
    """Run every job that is ready, one at a time in this process, until none is; the jobs as they ended. A failed job
    that will retry later is left queued, so this ends even when one keeps failing."""
    ran: list[Job] = []
    while (job := q.step(HANDLERS)) is not None:
        ran.append(q.get(job.id) or job)  # as stored: its result, error and next state
    return ran


class Runner:
    """The server's workers. `start` returns at once: the database is opened in the background (a server nobody
    records on still starts, and a database that cannot be opened yet is tried again every `retry_s`), then a `Worker`
    claims and runs jobs until `stop`."""

    def __init__(
        self,
        records: Database,
        *,
        concurrency: int = 2,
        retry_s: float = 30.0,
        isolate: bool = True,
        poll_s: float = 1.0,
    ) -> None:
        self.records = records
        self.concurrency = concurrency
        self.isolate = isolate  # False runs jobs on this process's threads: the unit tests
        self.poll_s = poll_s
        self.retry_s = retry_s
        self.worker: Worker | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._open, name="reels-jobs", daemon=True)
        self._thread.start()

    def _open(self) -> None:
        while not self._stop.is_set():
            try:
                engine = self.records.engine()
            except Exception:  # a bad URL or a failed migration: the requests say why; keep trying
                log.exception("jobs wait for the database")
                self._stop.wait(self.retry_s)
                continue
            worker = Worker(
                queue(engine), HANDLERS, concurrency=self.concurrency, isolate=self.isolate, poll_s=self.poll_s
            )
            if not self._stop.is_set():
                self.worker = worker.start()
            return

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        if self.worker is not None:
            self.worker.stop()
            self.worker = None


_engines: dict[str, Engine] = {}
_engines_lock = threading.Lock()


def _records() -> Engine:
    """This process's engine on the app's database, opened (and migrated) on first use and kept, since a job process
    runs many jobs. Keyed by the URL the settings name now, so a test, or a CLI run with another REELS_HOME, gets its
    own."""
    url = url_for(db.PROJECT.app, db.PROJECT.schema, default=db.default_url())
    with _engines_lock:
        if url not in _engines:
            _engines[url] = db.PROJECT.open(url)
        return _engines[url]


def close_records() -> None:
    """Dispose this process's engines (a test's teardown: a SQLite file left open cannot be removed on Windows)."""
    with _engines_lock:
        for e in _engines.values():
            e.dispose()
        _engines.clear()


def _same_footage(path: str, fingerprint: str) -> None:
    if not os.path.isfile(path):
        raise FileNotFoundError(f"{path} is not there: add the tape again from where it is now")
    if files.quick_fingerprint(path) != fingerprint:
        raise SourceChanged(f"{os.path.basename(path)} has changed since it was added: it is not the same footage")
