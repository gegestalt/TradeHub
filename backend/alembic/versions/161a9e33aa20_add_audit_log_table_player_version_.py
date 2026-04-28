"""add_audit_log_table_player_version_order_partial_status

Revision ID: 161a9e33aa20
Revises: 3fccae9c8d63
Create Date: 2026-04-27 17:37:07.865809

Changes:
  - players.balance_version  INTEGER NOT NULL DEFAULT 0  (optimistic locking)
  - audit_log table created  (DB-persisted audit trail)
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = '161a9e33aa20'
down_revision: Union[str, None] = '3fccae9c8d63'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite requires batch mode to add a NOT NULL column with a default value
    # to an existing table (ALTER TABLE ... ADD COLUMN NOT NULL is unsupported
    # without a server_default in SQLite).
    with op.batch_alter_table("players") as batch_op:
        batch_op.add_column(
            sa.Column("balance_version", sa.Integer(), nullable=False, server_default="0")
        )

    op.create_table(
        "audit_log",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("player_id", sa.String(length=36), nullable=True),
        sa.Column("competition_id", sa.String(length=36), nullable=True),
        sa.Column("order_id", sa.String(length=36), nullable=True),
        sa.Column("ticker", sa.String(length=20), nullable=True),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_log_event_type", "audit_log", ["event_type"])
    op.create_index("ix_audit_log_player_id", "audit_log", ["player_id"])
    op.create_index("ix_audit_log_recorded_at", "audit_log", ["recorded_at"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_recorded_at", table_name="audit_log")
    op.drop_index("ix_audit_log_player_id", table_name="audit_log")
    op.drop_index("ix_audit_log_event_type", table_name="audit_log")
    op.drop_table("audit_log")

    with op.batch_alter_table("players") as batch_op:
        batch_op.drop_column("balance_version")
