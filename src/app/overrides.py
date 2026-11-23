"""What the person said: corrections, names, locks, pins, bans and preferences (overview §6, spec §3 Constraint/Override).

Kept apart from the results they correct and never written into them, so a re-run of a model cannot lose a correction
and the planner can tell the person's word from a model's guess. Laid over the results when they are read (the screens
and the planner, in later steps). Append-only too: `change` writes a new override that supersedes the old, `withdraw`
marks one taken back, and `active` is what still holds, so a revision can name the overrides it obeyed.

The mode is the spec's lock mode: `advisory` (a hint), `prefer` (a soft constraint the planner weighs), `require` and
`forbid` (hard: a proposal that cannot keep one reports a conflict instead of dropping it)."""

import datetime
from typing import Any, Literal, get_args

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Override

Mode = Literal["advisory", "prefer", "require", "forbid"]
Target = Literal["result", "scene", "clip"]
Scope = Literal["target", "source", "all"]
MODES: tuple[str, ...] = get_args(Mode)
TARGETS: tuple[str, ...] = get_args(Target)
SCOPES: tuple[str, ...] = get_args(Scope)


def add(
    db: Session,
    target_kind: str,
    target_id: int,
    kind: str,
    value: Any,
    *,
    mode: str = "require",
    scope: str = "target",
    author: str,
    reason: str | None = None,
) -> Override:
    """Store an override. ValueError for a target, mode or scope that is not one of the known ones, or no author."""
    _check(target_kind, mode, scope, author)
    o = Override(
        target_kind=target_kind,
        target_id=target_id,
        kind=kind,
        value=value,
        mode=mode,
        scope=scope,
        author=author,
        reason=reason,
    )
    db.add(o)
    db.commit()
    return o


def change(db: Session, old: Override, value: Any, *, author: str, reason: str | None = None) -> Override:
    """A new override in place of `old` (same target, kind, mode and scope), which is marked superseded.
    ValueError when `old` no longer holds."""
    if not holds(old):
        raise ValueError(f"override {old.id} was already replaced or withdrawn")
    _check(old.target_kind, old.mode, old.scope, author)
    new = Override(
        target_kind=old.target_kind,
        target_id=old.target_id,
        kind=old.kind,
        value=value,
        mode=old.mode,
        scope=old.scope,
        author=author,
        reason=reason,
    )
    db.add(new)
    db.flush()
    old.superseded_by = new.id
    db.commit()
    return new


def withdraw(db: Session, o: Override) -> None:
    """Take an override back: it stays in the table, marked with when (a second withdraw changes nothing)."""
    if o.withdrawn is None:
        o.withdrawn = datetime.datetime.now(datetime.UTC)
        db.commit()


def holds(o: Override) -> bool:
    return o.superseded_by is None and o.withdrawn is None


def active(db: Session, target_kind: str, target_id: int) -> list[Override]:
    """The overrides that still hold for one target, oldest first."""
    q = select(Override).where(
        Override.target_kind == target_kind,
        Override.target_id == target_id,
        Override.superseded_by.is_(None),
        Override.withdrawn.is_(None),
    )
    return list(db.scalars(q.order_by(Override.id)))


def _check(target_kind: str, mode: str, scope: str, author: str) -> None:
    for name, value, allowed in (("target", target_kind, TARGETS), ("mode", mode, MODES), ("scope", scope, SCOPES)):
        if value not in allowed:
            raise ValueError(f"{name} is one of {', '.join(allowed)}, not {value!r}")
    if not author.strip():
        raise ValueError("an override says who made it")
