"""The app's records: SQLAlchemy models, migrated by Alembic (`src/app/migrations/`, declared in `src/app/strata-db.toml`).
The data model and its reasons are docs/overview.md §6; the rules that keep these tables honest live in the modules that
write them (`sources`, `results`, `overrides`, `jobs`), not here.

A source is a
file of footage as it was added: never changed, never moved by the app (spec §1: source media immutable). It is known by its
content (`fingerprint`: size and SHA-256 of both ends, `strata_python.files.quick_fingerprint`), so adding the same tape
twice, or after it was moved, finds the one already there. What ffprobe said about it is kept whole (`probe`), because the
time model and the proxies read different parts of it and ffprobe is slow on a long tape. Each model change comes with a reviewed revision: `uv run strata-db revision -m
"..."` writes it from the difference between these models and the migrations (a rename reads as drop + add, so read it),
and `uv run strata-db check` (CI) fails when they disagree. Models stay portable (generic types) so the same code runs on
the local SQLite fallback and on the shared Postgres; see strata-python's README, "Database"."""

import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, DateTime, Float, ForeignKey, Index, Integer, MetaData, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, MappedAsDataclass, mapped_column
from strata_python.db import NAMING_CONVENTION
from strata_python.tasks.queue import jobs_table
from strata_python.workflow.store import workflow_table


class Base(MappedAsDataclass, DeclarativeBase):
    # Written out rather than `make_base()`: ty refuses a function's result as a base class. The shared naming convention
    # gives every constraint the same name on every app and engine, so migrations never drift on a name.
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    fingerprint: Mapped[str] = mapped_column(String(80), unique=True)
    path: Mapped[str] = mapped_column(
        String(4096)
    )  # where it was last seen; the fingerprint, not the path, is what it is
    name: Mapped[str] = mapped_column(String(512))
    size: Mapped[int] = mapped_column(BigInteger)  # bytes: a digitised tape is several GB, past a 32-bit integer
    duration: Mapped[
        float | None
    ]  # seconds, ffprobe's; exact times come from the streams' rates (strata_python.mediatime)
    probe: Mapped[dict[str, Any]] = mapped_column(JSON)
    added: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default_factory=_now)
    # The full SHA-256 of the content, its identity in every cache key and render manifest (spec §3 MediaAsset). Reading
    # a tape of several GB takes a while, so it is filled in by the `hash` stage of the tape's pipeline after the source is added (`jobs`), and is
    # None until then: nothing that needs it may run before it.
    sha256: Mapped[str | None] = mapped_column(String(64), default=None, index=True)


# The job queue's table (strata_python.tasks.queue): part of these models, so the app's migrations create it, and in the
# app's own schema on the shared Postgres. Analysis and hashing run as its jobs (`jobs`).
JOBS = jobs_table(Base.metadata)

# The records of each tape's pipeline (strata_python.workflow, `jobs.TAPE`): one append-only row per attempt, approval
# or clear of a stage, the evidence the tape page shows. What a stage found stays in `results`.
STAGES = workflow_table(Base.metadata)


class Result(Base):
    """What one analysis said about a source (the spec's EvidenceArtifact, overview §6). Append-only: a row is never
    updated except to be marked superseded by the row that replaced it (`results.record`), so every proposal can name
    the exact results it was built from. Observations and inferences are different kinds, never one payload (spec S4)."""

    __tablename__ = "results"
    __table_args__ = (Index("ix_results_current", "source_id", "kind", "superseded_by"),)

    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    # The footage it is about. A result about a derived asset (a restored tape) still names its source; which file it
    # read exactly is in `inputs`.
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"))
    kind: Mapped[str] = mapped_column(String(40))  # shots, transcript, description, faces, ...
    # The span of source time it covers, half-open, as exact rationals (strata_python.mediatime.text: "1001/30000");
    # both None for a result about the whole source. Text, not float, so a cut is on the frame it was found on.
    span_start: Mapped[str | None] = mapped_column(String(40))
    span_end: Mapped[str | None] = mapped_column(String(40))
    # Who said it: the strata-models role, or the app's own producer for something it measures itself ("ffprobe").
    role: Mapped[str] = mapped_column(String(80))
    # The model that served the role and a hash of the settings it ran with (strata-models' provenance), so a changed
    # threshold under the same model key is a different result. None for a producer that has neither.
    model: Mapped[str | None] = mapped_column(String(200))
    settings_hash: Mapped[str | None] = mapped_column(String(64))
    # The SHA-256 of every file it read (sources and derived assets), sorted.
    inputs: Mapped[list[str]] = mapped_column(JSON)
    # The app's own parameters for the request (a sample rate, a span), which are part of the cache key.
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON)
    # hash(kind, span, inputs + role + model + settings hash + parameters) (`results.cache_key`, spec §6): the same request again
    # finds this row instead of running.
    cache_key: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[Any] = mapped_column(JSON, nullable=True)  # the answer, or None when it is a file
    # A heavy payload (a proxy, thumbnails, peaks, frame-wise signals) is a file or folder in the app's home, named here
    # relative to it: `derived/<cache key>/...` (`derived`).
    file: Mapped[str | None] = mapped_column(String(200), default=None)
    confidence: Mapped[float | None] = mapped_column(Float, default=None)  # where the producer gives one, 0 to 1
    flags: Mapped[list[str]] = mapped_column(JSON, default_factory=list)  # quality flags: "low-light", "partial"
    options: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)  # the model's settings, as run
    model_job: Mapped[int | None] = mapped_column(Integer, default=None)  # the strata-models job that produced it
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"), default=None)  # this app's job that asked
    created: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default_factory=_now)
    # The result that replaced this one (a re-run with another model or settings); None while it is current.
    superseded_by: Mapped[int | None] = mapped_column(ForeignKey("results.id"), default=None)


class Override(Base):
    """Something the person said, kept apart from the results it corrects and never written into them (overview §6,
    spec §3 Constraint/Override): a corrected boundary or description, a name for a face, a lock, pin or ban, a
    preference for the planner. Append-only like results: a change is a new row that supersedes the old one, and taking
    one back marks it withdrawn, so a revision can say which overrides it obeyed."""

    __tablename__ = "overrides"
    __table_args__ = (Index("ix_overrides_target", "target_kind", "target_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    # What it is about: a result, a scene or a clip, by id in that kind's table (scenes and clips arrive in later steps,
    # so this is a typed reference rather than a foreign key).
    target_kind: Mapped[str] = mapped_column(String(20))
    target_id: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(40))  # boundary, description, name, lock, pin, ban, preference
    value: Mapped[Any] = mapped_column(JSON)  # what the person said: the new boundary, the text, the name
    mode: Mapped[str] = mapped_column(String(10))  # advisory, prefer, require or forbid (spec §3)
    scope: Mapped[str] = mapped_column(String(20))  # how far it reaches: this target, its source, or every reel
    author: Mapped[str] = mapped_column(String(200))  # who said it: a person, or the agent acting for one
    reason: Mapped[str | None] = mapped_column(Text, default=None)
    created: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default_factory=_now)
    superseded_by: Mapped[int | None] = mapped_column(ForeignKey("overrides.id"), default=None)
    withdrawn: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class Reel(Base):
    """A reel being made from a tape: the handle its revisions hang from (`reels`). The one mutable row of the edit:
    its name and the length last asked for change, its revisions never do."""

    __tablename__ = "reels"

    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    # The tape it is cut from. R1 cuts one tape; each clip names its own source, so a reel of several can follow.
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"))
    name: Mapped[str] = mapped_column(String(200))
    target: Mapped[str] = mapped_column(String(40))  # the length last asked for, exact seconds (mediatime.text)
    created: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default_factory=_now)


class Revision(Base):
    """An immutable version of a reel (overview §6, spec §3 EditGraph and Proposal): its clips in play order and
    everything that made them (the planner, its seed and parameters, the results and overrides it read), so it can be
    made again. A proposal is a revision not accepted yet, on its `base`; accepting is a compare-and-swap on the reel's
    current revision (`reels.accept`), so a stale proposal is a conflict, never a silent overwrite. Never updated
    except to stamp `accepted` or `rejected`."""

    __tablename__ = "revisions"
    __table_args__ = (Index("ix_revisions_reel", "reel_id", "accepted"),)

    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    reel_id: Mapped[int] = mapped_column(ForeignKey("reels.id"))
    # The revision it was proposed on: the reel's current one at the time, None for a reel's first.
    base_id: Mapped[int | None] = mapped_column(ForeignKey("revisions.id"))
    author: Mapped[str] = mapped_column(String(200))  # the planner, or the person or agent who made it
    # {name, version, seed, parameters}: which planner, so a new one can be told from the old by its revisions.
    planner: Mapped[dict[str, Any]] = mapped_column(JSON)
    # {results: {kind: cache key}, overrides: [id, ...]}: what it read, the evidence a proposal names (spec §3).
    inputs: Mapped[dict[str, Any]] = mapped_column(JSON)
    # The clip occurrences in play order (`reels.Clip`): each with its own id, its source and exact half-open range.
    clips: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    conflicts: Mapped[list[dict[str, Any]]] = mapped_column(JSON)  # what it could not keep, each with its override
    target: Mapped[str] = mapped_column(String(40))  # the length asked for, exact seconds
    duration: Mapped[str] = mapped_column(String(40))  # the clips' sum, exact seconds
    # hash(reel, base, planner, target, inputs): the same plan asked again on the same base finds this row.
    key: Mapped[str] = mapped_column(String(64), index=True)
    created: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default_factory=_now)
    accepted: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    accepted_by: Mapped[str | None] = mapped_column(String(200), default=None)  # who made it the current revision
    rejected: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class Render(Base):
    """A revision rendered to a file (`renders`, spec S8): made when it is asked for, stamped once when its job ends
    with the file (under the app's home), its SHA-256 and the manifest of what made it. `key` is the clips', sources'
    and recipe's hash, so the same clips are rendered once."""

    __tablename__ = "renders"

    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    reel_id: Mapped[int] = mapped_column(ForeignKey("reels.id"))
    revision_id: Mapped[int] = mapped_column(ForeignKey("revisions.id"), index=True)
    key: Mapped[str] = mapped_column(String(64), index=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"), default=None)
    file: Mapped[str | None] = mapped_column(String(200), default=None)  # relative to the app's home
    sha256: Mapped[str | None] = mapped_column(String(64), default=None)
    bytes: Mapped[int | None] = mapped_column(BigInteger, default=None)
    manifest: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    created: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default_factory=_now)
    finished: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), default=None)
