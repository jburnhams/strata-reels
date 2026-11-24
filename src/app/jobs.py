"""Work that runs in the background: each tape's analysis as one strata-python workflow `Pipeline`, advanced by a job on
the durable queue of `strata_python.tasks.queue` in this app's database (overview §6).

A tape's steps are the stages of the `tape` pipeline, one run per source (subject `source:<id>`). Each stage names
what it reads (`needs`), so the pipeline knows the order, what a failure blocks and what a change makes run again;
its records (every attempt, with its answer, metrics, error and time) go in the `workflow_events` table
(`models.STAGES`, `DbStore`). What a stage found stays in reels' own `results` table (spec §6: the cache, keyed on the
request, that the browser reads); a stage's record holds that result's id and cache key, so a stage after it runs
again only when the result it read is another one.

- **One job per tape** (`workflow:tape`, domain `tape:source:<id>`; `ingest_later` queues it, deduped while one is
  queued or running). It runs every stage not done for the tape's current inputs, in order; a stage that fails is
  recorded and the stages that do not need it still run, then the job fails and the queue retries it with its
  backoff, starting at the failed stage. It holds the `disk` resource for the whole run (the hash, the assets and the
  keyframes read the footage), so two tapes are never read from one drive at once; a tape's steps run one after
  another (Jonathan chose this over a job per step, 2026-11-24).
- **The server runs them**: `Runner` starts a `Worker` once the database opens and stops it with the server. Each job
  runs in a job process of its own (strata-python's default), so a cancel ends it and every tool it started.
- **The CLI can too**: `./app work` runs what is queued, in its own process, until the queue is empty.
- **Handlers and stages are module-level functions** (a job process imports them by name) and open the database
  themselves (`_records`, once per process), since a job process is not the server.

Stages (each finds its result stored, and makes nothing, when the same request ran before):

- `hash`: the full SHA-256 of the source's content (`Source.sha256`), its identity in every cache key. Keyed on the
  quick fingerprint the source was added with; the file is checked against it before and after reading, so a file
  changed or replaced under the same path is refused rather than hashed as the source.
- `proxy`, `thumbnails`, `peaks`: the derived assets (`derived`), each a result with a file; their version is their
  recipe, so a changed recipe makes them again. Peaks only for a source with sound (the stage says it skipped).
- `shots`, then `keyframes` (pictures of each shot), then `scenes` (the shots grouped), then `faces` (on the
  keyframes): strata-models' `shot-boundaries`, `image-embed` and `face-detect`.
- `transcript`: the whole source's speech from strata-models' `transcribe`, for a source with sound.
- `describe`: a description of each scene, which needs the scenes and the transcript, so every description has its
  speech.

Restoration (strata-models' `video-enhance`) comes later in the pipeline, from the raw source."""

import logging
import os
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session
from strata_python import files
from strata_python.db import url_for
from strata_python.db.web import Database
from strata_python.tasks.queue import Handler, Job, Queue, Worker
from strata_python.workflow import Context, Pipeline, request_key, step
from strata_python.workflow.store import DbStore, enqueue

from app import db, derived, describe, scenes, shots, speech
from app.models import JOBS, STAGES, Result, Source

log = logging.getLogger(__name__)


class SourceChanged(ValueError):
    """The file at a source's path is no longer that footage (edited, or another file in its place)."""


def queue(engine: Engine) -> Queue:
    return Queue(engine, JOBS)


@dataclass(frozen=True)
class Tape:
    """What a stage is given (`Context.app`): the source it is about and the job running it (for the results'
    provenance)."""

    source_id: int
    job_id: int | None = None


def subject_of(source: Source | int) -> str:
    return f"source:{source if isinstance(source, int) else source.id}"


def source_id_of(subject: str) -> int:
    m = re.fullmatch(r"source:(\d+)", subject)
    if m is None:
        raise KeyError(f"{subject!r} is not a tape: a subject is source:<id>")
    return int(m.group(1))


def inputs_of(subject: str) -> dict[str, Any]:
    """A tape's inputs now: what the `hash` stage is keyed on. KeyError (404) for a source there is not."""
    return inputs_in(_records(), subject)


def inputs_in(engine: Engine, subject: str) -> dict[str, Any]:
    """`inputs_of` on a given database (the server's, which it opened itself)."""
    source_id = source_id_of(subject)
    with Session(engine) as s:
        source = s.get(Source, source_id)
        if source is None:
            raise KeyError(f"there is no source {source_id}")
        return {"fingerprint": source.fingerprint}


def _source(s: Session, ctx: Context) -> Source:
    tape: Tape = ctx.app
    source = s.get(Source, tape.source_id)
    if source is None:
        raise KeyError(f"there is no source {tape.source_id}")
    return source


def _found(ctx: Context, r: Result, new: bool, **more: Any) -> dict[str, Any]:
    """A stage's answer: the result it stands on (id and cache key, so a stage after it runs again only when this is
    another result) and what it says in brief; whether it was made now is a metric, not part of the answer."""
    ctx.measure(new=new)
    return {"result": r.id, "key": r.cache_key, **more}


def _hash(ctx: Context) -> dict[str, Any]:
    with Session(_records()) as s:
        source = _source(s, ctx)
        path, expected, known = source.path, source.fingerprint, source.sha256
    _same_footage(path, expected)
    if known is not None:  # read before (a tape from before the pipeline): its fingerprint still matches, so it stands
        return {"sha256": known}
    digest = files.sha256_hex(path)
    _same_footage(path, expected)  # and still: a file rewritten while it was read is not the source either
    with Session(_records()) as s:
        source = _source(s, ctx)
        source.sha256 = digest
        s.commit()
    return {"sha256": digest}


def _asset(kind: str) -> Callable[[Context], dict[str, Any]]:
    def make(ctx: Context) -> dict[str, Any]:
        with Session(_records(), expire_on_commit=False) as s:
            source = _source(s, ctx)
            if kind not in derived.wanted(source):
                return {"result": None, "skipped": "no sound"}
            _same_footage(source.path, source.fingerprint)
            r, new = derived.make(s, source, kind, job_id=ctx.app.job_id)
            return _found(ctx, r, new, file=r.file)

    make.__name__ = make.__qualname__ = f"make_{kind}"
    return make


def _shots(ctx: Context) -> dict[str, Any]:
    with Session(_records(), expire_on_commit=False) as s:
        source = _source(s, ctx)
        _same_footage(source.path, source.fingerprint)
        r, new = shots.detect(s, source, job_id=ctx.app.job_id)
        return _found(ctx, r, new, shots=len(r.payload["shots"]))


def _keyframes(ctx: Context) -> dict[str, Any]:
    with Session(_records(), expire_on_commit=False) as s:
        source = _source(s, ctx)
        _same_footage(source.path, source.fingerprint)
        r, new = shots.keyframes(s, source, job_id=ctx.app.job_id)
        return _found(ctx, r, new, file=r.file)


def _scenes(ctx: Context) -> dict[str, Any]:
    with Session(_records(), expire_on_commit=False) as s:
        r, new = scenes.find_scenes(s, _source(s, ctx), job_id=ctx.app.job_id)
        return _found(ctx, r, new, scenes=len(r.payload["scenes"]))


def _faces(ctx: Context) -> dict[str, Any]:
    with Session(_records(), expire_on_commit=False) as s:
        r, new = describe.find_faces(s, _source(s, ctx), job_id=ctx.app.job_id)
        return _found(ctx, r, new, faces=sum(len(f["faces"]) for f in r.payload["frames"]))


def _transcript(ctx: Context) -> dict[str, Any]:
    with Session(_records(), expire_on_commit=False) as s:
        source = _source(s, ctx)
        if not speech.has_sound(source):
            return {"result": None, "skipped": "no sound"}
        _same_footage(source.path, source.fingerprint)
        r, new = speech.transcribe(s, source, job_id=ctx.app.job_id)
        return _found(ctx, r, new, segments=len(r.payload["segments"]))


def _describe(ctx: Context) -> dict[str, Any]:
    with Session(_records(), expire_on_commit=False) as s:
        made = describe.describe(s, _source(s, ctx), job_id=ctx.app.job_id)
        described = sum(new for _, new in made)
        ctx.measure(new=described)
        # each scene's result, so a stage after this one would run again when any description is another
        return {"results": [r.id for r, _ in made], "key": request_key(*(r.cache_key for r, _ in made))}


def _version(*parts: Any) -> str:
    """A stage's version from what this app asks of it (its parameters), so a release that asks otherwise runs it again.
    What strata-models answers with (its model and settings) is checked by the stage itself, against its stored result,
    when the tape is added again (`ingest_later(again=True)`)."""
    return request_key(*parts)[:12]


def _recipe(kind: str) -> str:
    """A derived stage's version: its recipe (reels' parameters and strata-python's), so a changed one runs it again."""
    return request_key(derived.parameters(kind))[:12]


TAPE = Pipeline(
    "tape",
    [
        step("hash", _hash, keys=("fingerprint",), resources=("disk",), label="Read the whole file"),
        step("proxy", _asset("proxy"), needs=("hash",), version=_recipe("proxy"), resources=("disk",), label="Make a browser copy"),
        step("thumbnails", _asset("thumbnails"), needs=("hash",), version=_recipe("thumbnails"), resources=("disk",), label="Make thumbnails"),
        step("peaks", _asset("peaks"), needs=("hash",), version=_recipe("peaks"), resources=("disk",), label="Draw the sound"),
        step("shots", _shots, needs=("hash",), version=_version(shots.ASK, str(shots.BLACK_SHARE)), label="Find the shots"),
        step("keyframes", _keyframes, needs=("shots",), version=_version(shots.KEYFRAMES), resources=("disk",), label="Take pictures of each shot"),
        step("scenes", _scenes, needs=("shots", "keyframes"), version=scenes.VERSION, label="Group the shots into scenes"),
        step("faces", _faces, needs=("keyframes",), version=_version(describe.FACE_FIELDS), label="Find the faces"),
        step("transcript", _transcript, needs=("hash",), version=_version(speech.PARAMETERS), label="Write down what is said"),
        step("describe", _describe, needs=("scenes", "transcript"), version=_version(describe.PRESET, describe.FRAMES, speech.SAID_CHARS), label="Describe the scenes"),
    ],
)  # fmt: skip


def store() -> DbStore:
    """The tape pipeline's records in this process's database."""
    return DbStore(_records(), STAGES, TAPE)


# The stages that stand on what is outside the pipeline's keys: a file in the app's home (which can be deleted) or
# strata-models' model and settings (which can change). Each checks those against its stored result when it runs.
RECHECKED = ("proxy", "thumbnails", "peaks", "shots", "transcript")


def ingest_later(q: Queue, source: Source | int, *, again: bool = False, by: str | None = None) -> int:
    """Queue the job that advances a tape through every stage not done; its id (the one already queued or running for
    the tape, if there is one). A tape whose stages are all done for its inputs runs nothing, unless it is `again` (the
    tape added again): then every stage after the hash runs, and each makes again only what it finds missing or out of
    date (a deleted browser copy, a new model on strata-models) and otherwise finds its stored result."""
    subject = subject_of(source)
    if again:
        s = store()
        for name in RECHECKED:  # each clears what needs it, so everything after the hash
            TAPE.clear(s, subject, name, by=by)
    return enqueue(q, TAPE, subject)


# The kinds of job before the tape pipeline (one per step, each queueing the next): left queued they would wait for
# ever, since no worker has their handlers. `adopt` cancels them and queues each tape's one job instead.
OLD_KINDS = ("hash", "proxy", "thumbnails", "peaks", "shots", "keyframes", "scenes", "faces", "transcript", "describe")


def adopt(q: Queue) -> list[int]:
    """Bring a queue from before the tape pipeline onto it: cancel the old kinds of job still waiting or running, and
    queue the tape job of every source the pipeline has no record of; the sources queued. Cheap when there is nothing
    to do, so the server and `./app work` call it every time they start."""
    for kind in OLD_KINDS:
        while page := q.page(state=("queued", "running"), kind=kind, limit=200).items:
            for t in page:
                q.cancel(int(t.id))
            if all(t.state == "running" for t in page):  # asked to stop: they leave the list when they do
                break
    known = set(store().subjects())
    with Session(_records()) as s:
        ids = list(s.scalars(select(Source.id).order_by(Source.id)))
    queued = [i for i in ids if subject_of(i) not in known]
    for i in queued:
        ingest_later(q, i)
    return queued


def advance_tape(job: Job) -> dict[str, Any]:
    """The `workflow:tape` job: advance one tape as far as it goes. Raises the first stage's error after the stages
    that do not need it have run, so the queue retries it and the next attempt starts there."""
    subject: str = job.options["subject"]  # not str(): a job without one is a bug, failed at once
    inputs = inputs_of(subject)
    tape = Tape(source_id_of(subject), job.id)
    return TAPE.advance(store(), subject, inputs, tape, stopping=lambda: job.stopping).as_json()


HANDLERS: dict[str, Handler] = {TAPE.kind: advance_tape}


def stages_of(job: Job) -> dict[str, str]:
    """Each stage's state now for the tape a `workflow:tape` job advances (what `./app work` prints under it); empty
    for any other job, or one whose source is gone."""
    subject = job.options.get("subject")
    if job.kind != TAPE.kind or not isinstance(subject, str):
        return {}
    try:
        inputs = inputs_of(subject)
    except KeyError:
        return {}
    return dict(TAPE.status(store(), subject, inputs).states)


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
                adopt(worker.queue)
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
