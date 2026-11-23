"""sources

Revision ID: 92e993263160
Revises:
Create Date: 2026-11-23 16:44:43.107644

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "92e993263160"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # JSON and Double are generic over their Python type in SQLAlchemy's stubs, which strict pyright calls partly unknown
    probe: sa.JSON[object] = sa.JSON()
    seconds: sa.Double[float] = sa.Double()
    op.create_table(
        "sources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("fingerprint", sa.String(length=80), nullable=False),
        sa.Column("path", sa.String(length=4096), nullable=False),
        sa.Column("name", sa.String(length=512), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("duration", seconds, nullable=True),
        sa.Column("probe", probe, nullable=False),
        sa.Column("added", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sources")),
        sa.UniqueConstraint("fingerprint", name=op.f("uq_sources_fingerprint")),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("sources")
