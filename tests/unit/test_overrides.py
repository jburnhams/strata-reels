"""What the person said (app/overrides.py), kept apart from the results and append-only, on a real migrated database."""

import pytest
from sqlalchemy.orm import Session

from app import overrides


def test_an_override_is_changed_by_a_new_row_and_withdrawn_in_place(db_session: Session):
    o = overrides.add(
        db_session,
        "result",
        7,
        "description",
        "Grandma's 80th, not a wedding",
        author="Ada",
        reason="it is the cake",
    )
    assert (o.mode, o.scope, o.author, o.reason) == ("require", "target", "Ada", "it is the cake")
    assert overrides.active(db_session, "result", 7) == [o]
    n = overrides.change(db_session, o, "Grandma's 80th", author="Ada")
    assert o.superseded_by == n.id
    assert o.value == "Grandma's 80th, not a wedding"  # kept: a revision that obeyed it can still say what it was
    assert (n.kind, n.mode, n.target_id) == ("description", "require", 7)
    assert overrides.active(db_session, "result", 7) == [n]
    with pytest.raises(ValueError, match="already replaced"):
        overrides.change(db_session, o, "again", author="Ada")
    overrides.withdraw(db_session, n)
    when = n.withdrawn
    assert when is not None
    overrides.withdraw(db_session, n)
    assert n.withdrawn == when
    assert overrides.active(db_session, "result", 7) == []
    assert not overrides.holds(n)


def test_locks_and_bans_hold_side_by_side_for_other_targets(db_session: Session):
    pin = overrides.add(db_session, "scene", 3, "pin", True, mode="require", scope="source", author="Ada")
    ban = overrides.add(db_session, "scene", 3, "ban", True, mode="forbid", author="agent for Ada")
    hint = overrides.add(
        db_session, "clip", 3, "preference", {"pace": "slow"}, mode="advisory", scope="all", author="Ada"
    )
    assert overrides.active(db_session, "scene", 3) == [pin, ban]  # both kept: a conflict is the planner's to report
    assert overrides.active(db_session, "clip", 3) == [hint]


@pytest.mark.parametrize(
    ("target", "mode", "scope", "author", "message"),
    [
        ("frame", "require", "target", "J", "target is one of result, scene, clip, tape, reel, not 'frame'"),
        ("scene", "must", "target", "J", "mode is one of advisory, prefer, require, forbid"),
        ("scene", "require", "world", "J", "scope is one of target, source, all"),
        ("scene", "require", "target", " ", "says who made it"),
    ],
)
def test_unknown_targets_modes_scopes_and_no_author_are_refused(
    db_session: Session, target, mode, scope, author, message
):
    with pytest.raises(ValueError, match=message):
        overrides.add(db_session, target, 1, "lock", True, mode=mode, scope=scope, author=author)
    assert overrides.active(db_session, target, 1) == []
