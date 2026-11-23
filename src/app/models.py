"""The editor's records in the shared database (schema `reels`, see `[tool.strata-db]` in pyproject.toml; migrations in `migrations/`).

A source is a file of footage as it was added: never changed, never moved by the app (spec §1: source media immutable). It is known by its
content (`fingerprint`: size and SHA-256 of both ends, `strata_python.files.quick_fingerprint`), so adding the same tape twice, or after it
was moved, finds the one already there. What ffprobe said about it is kept whole (`probe`), because the time model and the proxies read
different parts of it and ffprobe is slow on a long tape. Files made from a source (a restored copy, a proxy) come next, each with what made it."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, DateTime, MetaData, String
from sqlalchemy.orm import DeclarativeBase, Mapped, MappedAsDataclass, mapped_column
from strata_python.db import NAMING_CONVENTION


def _now() -> datetime:
    return datetime.now(UTC)


class Base(MappedAsDataclass, DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


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
    added: Mapped[datetime] = mapped_column(DateTime(timezone=True), default_factory=_now)
