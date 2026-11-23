"""Footage in: add a file as a source, and list them (milestone R1 step 1, docs/implementation-plan.md).

Adding reads the file and never writes it: the fingerprint (size and SHA-256 of both ends, seconds even for a tape of several GB) says which
footage it is, and ffprobe says what is in it. A file whose fingerprint is already a source is that source: its path is brought up to date
(the tape was moved) and nothing else changes, so adding is safe to repeat. A file ffprobe cannot read, or one with no video, is refused with
ffprobe's reason, before anything is stored."""

import os
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from strata_python import files, media

from app.models import Source


class SourceError(ValueError):
    """The file cannot be a source: it is missing, unreadable or has no picture."""


def as_json(s: Source) -> dict[str, Any]:
    video = media.Probe.from_json(s.path, s.probe).video
    return {
        "id": s.id,
        "name": s.name,
        "path": s.path,
        "size": s.size,
        "duration": s.duration,
        "video": None
        if video is None
        else {
            "codec": video.codec,
            "width": video.width,
            "height": video.height,
            "rate": video.raw.get(
                "r_frame_rate"
            ),  # exact, as ffprobe writes it ("30000/1001"): strata_python.mediatime reads it
            "field_order": video.raw.get("field_order"),  # tt/bb/tb/bt for interlaced tape, progressive, or absent
        },
        "added": s.added.isoformat(),
    }


def add(db: Session, path: str, probe: Callable[[str], media.Probe] = media.probe) -> tuple[Source, bool]:
    """The source for `path` and whether it is new. Raises SourceError for a file that cannot be one. `probe` is ffprobe; unit tests pass
    one that answers without it (the integration tests run the real one)."""
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(path):
        raise SourceError(f"there is no file {path}")
    try:
        fingerprint = files.quick_fingerprint(path)
    except OSError as e:  # there, but not ours to read (permissions, a drive gone mid-read): a refusal, not a crash
        raise SourceError(f"{os.path.basename(path)} cannot be read: {e.strerror or e}") from e
    found = _by_fingerprint(db, fingerprint)
    if found is not None:
        if found.path != path:
            found.path, found.name = path, os.path.basename(path)
            db.commit()
        return found, False
    try:
        info = probe(path)
    except media.MediaError as e:
        raise SourceError(f"{os.path.basename(path)} is not footage ffprobe can read: {e}") from e
    if info.video is None:
        raise SourceError(f"{os.path.basename(path)} has no video stream")
    source = Source(
        fingerprint=fingerprint,
        path=path,
        name=os.path.basename(path),
        size=os.path.getsize(path),
        duration=info.duration,
        probe=info.raw,
    )
    db.add(source)
    try:
        db.commit()
    except IntegrityError:
        # the same tape added twice at once: both looked, found nothing and probed (seconds), and the other stored it first
        db.rollback()
        other = _by_fingerprint(db, fingerprint)
        if other is None:
            raise
        return other, False
    return source, True


def _by_fingerprint(db: Session, fingerprint: str) -> Source | None:
    return db.scalars(select(Source).where(Source.fingerprint == fingerprint)).first()


def all_sources(db: Session) -> list[Source]:
    """Every source, oldest first."""
    return list(db.scalars(select(Source).order_by(Source.id)))
