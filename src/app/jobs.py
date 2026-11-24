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
  two tapes read at once from one drive are slower than one after the other. When it is done it queues the source's
  derived assets.
- `proxy`, `thumbnails`, `peaks`: the source's derived assets from the raw footage (`derived`), each a result with a
  file. Queued by the `hash` job (they are keyed on the hash), or by `ingest_later` for a source already hashed. Each
  checks the file is still the source's footage first. Peaks only for a source with sound. They hold `disk` too.

- `shots` (step 2): the source's shots, asked of strata-models' `shot-boundaries` (`shots.detect`), queued by the
  `hash` job beside the assets. It holds no resource here: strata-models queues the work on its own CPU lane. Then
  `keyframes`, a picture of each shot (`disk`), then `scenes`, which asks strata-models for the keyframes' vectors and
  groups the shots (`scenes.find_scenes`). Each queues the next when it is done; each finds its result stored and
  makes nothing when the same request ran before.
- `transcript` (step 3): the whole source's speech from strata-models' `transcribe` (`speech.transcribe`), queued by
  the `hash` job beside the shots, for a source with sound. When the scenes are done they queue `faces` (`describe.
  find_faces`, on the keyframes) and `describe` (`describe.describe`, a description of each scene); `describe` waits
  for the transcript on a source with sound, and the `transcript` job queues it again when it ends.

Ingest is the first four. Restoration (strata-models' `video-enhance`) comes later in the pipeline, from the raw source."""

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

from app import db, derived, describe, scenes, shots, speech
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


def derive_later(q: Queue, source: Source) -> list[int]:
    """Queue what a hashed source gets next: its derived assets and its shots; the jobs' ids (the same ones while they
    are queued or running)."""
    if source.sha256 is None:
        return []
    made = [
        q.enqueue(kind, {"source": source.id}, domain=f"source:{source.id}", dedupe=True, resources=["disk"])
        for kind in derived.wanted(source)
    ]
    made.append(later(q, "shots", source))
    if speech.has_sound(source):
        made.append(later(q, "transcript", source))
    return made


def later(q: Queue, kind: str, source: Source) -> int:
    """Queue one of the analysis jobs (`shots`, `keyframes`, `scenes`, `transcript`, `faces`, `describe`) for a
    source."""
    resources = ["disk"] if kind == "keyframes" else []
    return q.enqueue(kind, {"source": source.id}, domain=f"source:{source.id}", dedupe=True, resources=resources)


def ingest_later(q: Queue, source: Source) -> list[int]:
    """Everything a source added needs: its hash, which then queues the derived assets, or those at once when the
    hash is known (a tape added again). A job whose asset is already stored finds it and makes nothing."""
    jid = hash_later(q, source)
    return [jid] if jid is not None else derive_later(q, source)


def hash_source(job: Job) -> dict[str, Any]:
    """The `hash` job: read the whole file and store its SHA-256 on the source."""
    source_id = int(job.options["source"])
    with Session(_records()) as s:
        source = s.get(Source, source_id)
        if source is None:
            raise KeyError(f"there is no source {source_id}")
        if source.sha256 is not None:
            # a retry after the hash was stored but before its assets were all queued: queue them now (deduped, and
            # a job whose asset is stored finds it)
            derive_later(queue(_records()), source)
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
        derive_later(queue(_records()), source)
    return {"sha256": digest}


def derive_asset(job: Job) -> dict[str, Any]:
    """The `proxy`, `thumbnails` and `peaks` jobs: make the asset (or find it made) and store it as a result."""
    source_id = int(job.options["source"])
    with Session(_records(), expire_on_commit=False) as s:
        source = s.get(Source, source_id)
        if source is None:
            raise KeyError(f"there is no source {source_id}")
        _same_footage(source.path, source.fingerprint)
        r, new = derived.make(s, source, job.kind, job_id=job.id)
        return {"result": r.id, "file": r.file, "new": new}


def find_shots(job: Job) -> dict[str, Any]:
    """The `shots` job: the source's shots from strata-models, then its keyframes queued."""
    with Session(_records(), expire_on_commit=False) as s:
        source = _source(s, job)
        _same_footage(source.path, source.fingerprint)
        r, new = shots.detect(s, source, job_id=job.id)
        later(queue(_records()), "keyframes", source)
        return {"result": r.id, "shots": len(r.payload["shots"]), "new": new}


def make_keyframes(job: Job) -> dict[str, Any]:
    """The `keyframes` job: a picture of each shot, then the scenes queued."""
    with Session(_records(), expire_on_commit=False) as s:
        source = _source(s, job)
        _same_footage(source.path, source.fingerprint)
        r, new = shots.keyframes(s, source, job_id=job.id)
        later(queue(_records()), "scenes", source)
        return {"result": r.id, "file": r.file, "new": new}


def group_scenes(job: Job) -> dict[str, Any]:
    """The `scenes` job: the keyframes' vectors from strata-models, then the shots grouped into scenes."""
    with Session(_records(), expire_on_commit=False) as s:
        source = _source(s, job)
        r, new = scenes.find_scenes(s, source, job_id=job.id)
        q = queue(_records())
        later(q, "faces", source)
        later(q, "describe", source)
        return {"result": r.id, "scenes": len(r.payload["scenes"]), "new": new}


def transcribe(job: Job) -> dict[str, Any]:
    """The `transcript` job: the whole source's speech from strata-models, then the descriptions queued if the scenes
    are already there (they wait for the speech)."""
    with Session(_records(), expire_on_commit=False) as s:
        source = _source(s, job)
        _same_footage(source.path, source.fingerprint)
        r, new = speech.transcribe(s, source, job_id=job.id)
        if scenes.current(s, source) is not None:
            later(queue(_records()), "describe", source)
        return {"result": r.id, "segments": len(r.payload["segments"]), "new": new}


def find_faces(job: Job) -> dict[str, Any]:
    """The `faces` job: the faces on every keyframe, from strata-models."""
    with Session(_records(), expire_on_commit=False) as s:
        r, new = describe.find_faces(s, _source(s, job), job_id=job.id)
        return {"result": r.id, "faces": sum(len(f["faces"]) for f in r.payload["frames"]), "new": new}


def describe_scenes(job: Job) -> dict[str, Any]:
    """The `describe` job: a description of each scene from strata-models. On a source with sound it waits for the
    transcript (ending at once, done: the `transcript` job queues it again), so every description has its speech."""
    with Session(_records(), expire_on_commit=False) as s:
        source = _source(s, job)
        if speech.has_sound(source) and speech.current(s, source) is None:
            return {"waiting": "transcript", "new": False}
        made = describe.describe(s, source, job_id=job.id)
        described = sum(new for _, new in made)
        return {"scenes": len(made), "described": described, "new": described > 0}


def _source(s: Session, job: Job) -> Source:
    source_id = int(job.options["source"])
    source = s.get(Source, source_id)
    if source is None:
        raise KeyError(f"there is no source {source_id}")
    return source


HANDLERS: dict[str, Handler] = {
    "hash": hash_source,
    **dict.fromkeys(derived.KINDS, derive_asset),
    "shots": find_shots,
    "keyframes": make_keyframes,
    "scenes": group_scenes,
    "transcript": transcribe,
    "faces": find_faces,
    "describe": describe_scenes,
}


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
