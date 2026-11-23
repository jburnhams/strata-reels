"""The app's records: SQLAlchemy models, migrated by Alembic (`src/app/migrations/`, declared in `src/app/strata-db.toml`).

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

from sqlalchemy import JSON, BigInteger, DateTime, MetaData, String
from sqlalchemy.orm import DeclarativeBase, Mapped, MappedAsDataclass, mapped_column
from strata_python.db import NAMING_CONVENTION


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
