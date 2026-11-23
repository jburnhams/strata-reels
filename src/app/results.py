"""Analysis results: written once, superseded, never overwritten (overview §6, spec §3 EvidenceArtifact).

Every later step (scenes, the chapters screen, the planner) reads what was found about the footage from here, and a
proposal names the results it used, so a row must mean the same thing for ever. Hence:

- **Provenance on every row**: the role, and where strata-models served it, the model and the hash of its settings as it
  ran (`Provenance.of(...)` takes the client's `run_with_provenance` answer as it comes), plus the SHA-256 of every file
  read. A threshold changed in strata-models' `models.toml` under the same model key changes the settings hash, so old
  results stop looking current.
- **Cache key**: hash(inputs + role + model + settings hash + parameters) (spec §6), with the kind and span asked about. `record` with a key that a current
  result already has returns that result and writes nothing, so a job that runs twice stores one answer.
- **Superseded, not overwritten**: a result for the same source, kind and span from a different request (another model,
  other settings, other parameters) is written as a new row and the old one gets `superseded_by`, in one transaction.
  `current` is what the editor shows; `history` keeps the rest for "what did the old model say".

What the person corrects is not here: it is an override (`overrides`), kept apart and laid over these at read time."""

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from strata_python import mediatime

from app.models import Result, Source


@dataclass(frozen=True)
class Provenance:
    """Who produced a result. `role` is a strata-models role, or the app's own producer ("ffprobe") with no model."""

    role: str
    model: str | None = None
    settings_hash: str | None = None
    options: Mapping[str, Any] | None = None
    model_job: int | None = None

    @classmethod
    def of(cls, role: str, served: Mapping[str, Any]) -> "Provenance":
        """From strata-models' answer: `{model, options, settings_hash, job_id}` (its client's `run_with_provenance`
        and `provenance(job_row)`)."""
        job = served.get("job_id")
        return cls(
            role=role,
            model=served.get("model"),
            settings_hash=served.get("settings_hash"),
            options=served.get("options"),
            model_job=None if job is None else int(job),
        )


def cache_key(
    kind: str,
    inputs: Sequence[str],
    provenance: Provenance,
    parameters: Mapping[str, Any],
    span: mediatime.Span | None = None,
) -> str:
    """hash(inputs + role + model + settings hash + parameters): the same request about the same bytes, the same key.
    The kind and the span asked about are part of the question too (a description of 0-12 s is not one of 50-60 s,
    nor of the whole tape). Inputs are sorted (the order files were named in is not part of it); parameters are
    canonical JSON; the span is exact text."""
    p = provenance
    where = None if span is None else span.to_json()
    body = [kind, sorted(inputs), p.role, p.model, p.settings_hash, dict(parameters), where]
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def record(
    db: Session,
    source: Source,
    kind: str,
    provenance: Provenance,
    *,
    inputs: Sequence[str],
    parameters: Mapping[str, Any] | None = None,
    payload: Any = None,
    file: str | None = None,
    span: mediatime.Span | None = None,
    confidence: float | None = None,
    flags: Sequence[str] = (),
    job_id: int | None = None,
) -> tuple[Result, bool]:
    """Store what `provenance` said about `source` and return it with whether it is new. The same request again
    (same cache key, still current) returns the stored row; a different request for the same source, kind and span
    supersedes the current one. ValueError for a result with neither a payload nor a file, with no inputs, or with a
    confidence outside 0 to 1."""
    if payload is None and file is None:
        raise ValueError(f"a {kind} result needs a payload or a file")
    if not inputs:
        raise ValueError(f"a {kind} result must name the files it read (their SHA-256)")
    if confidence is not None and not 0 <= confidence <= 1:
        raise ValueError(f"confidence is between 0 and 1, not {confidence}")
    params = dict(parameters or {})
    key = cache_key(kind, inputs, provenance, params, span)
    start, end = (None, None) if span is None else (mediatime.text(span.start), mediatime.text(span.end))
    same_slot = [r for r in current(db, source, kind) if (r.span_start, r.span_end) == (start, end)]
    for r in same_slot:
        if r.cache_key == key:
            return r, False
    new = Result(
        source_id=source.id,
        kind=kind,
        span_start=start,
        span_end=end,
        role=provenance.role,
        model=provenance.model,
        settings_hash=provenance.settings_hash,
        inputs=sorted(inputs),
        parameters=params,
        cache_key=key,
        payload=payload,
        file=file,
        confidence=confidence,
        flags=list(flags),
        options=None if provenance.options is None else dict(provenance.options),
        model_job=provenance.model_job,
        job_id=job_id,
    )
    db.add(new)
    db.flush()  # its id, for the rows it replaces
    for r in same_slot:
        r.superseded_by = new.id
    db.commit()
    return new, True


def current(db: Session, source: Source, kind: str) -> list[Result]:
    """The results of `kind` about `source` that nothing has replaced, in source-time order (whole-source ones first)."""
    rows = db.scalars(
        select(Result).where(Result.source_id == source.id, Result.kind == kind, Result.superseded_by.is_(None))
    )
    return sorted(rows, key=_when)


def find(db: Session, key: str) -> Result | None:
    """The current result with this cache key: a job looks before it asks strata-models to do the work again."""
    return db.scalars(select(Result).where(Result.cache_key == key, Result.superseded_by.is_(None))).first()


def history(db: Session, source: Source, kind: str) -> list[Result]:
    """Every result of `kind` about `source`, superseded ones included, oldest first."""
    return list(
        db.scalars(select(Result).where(Result.source_id == source.id, Result.kind == kind).order_by(Result.id))
    )


def span(r: Result) -> mediatime.Span | None:
    """The source time the result covers, exact; None when it is about the whole source."""
    if r.span_start is None or r.span_end is None:
        return None
    return mediatime.Span.of(r.span_start, r.span_end)


def _when(r: Result) -> tuple[int, Any, int]:
    s = span(r)
    return (0, 0, r.id) if s is None else (1, s.start, r.id)
