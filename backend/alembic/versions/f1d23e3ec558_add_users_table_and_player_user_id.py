"""add_users_table_and_player_user_id

Revision ID: f1d23e3ec558
Revises: 75041f81c664
Create Date: 2026-04-22 14:29:04.597679

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f1d23e3ec558"
down_revision: Union[str, None] = "75041f81c664"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token"),
    )
    op.create_index(op.f("ix_users_token"), "users", ["token"], unique=True)

    with op.batch_alter_table("players") as batch_op:
        batch_op.add_column(sa.Column("user_id", sa.String(length=36), nullable=True))
        batch_op.create_index(op.f("ix_players_user_id"), ["user_id"], unique=False)
        batch_op.create_unique_constraint(
            "uq_player_competition_user", ["competition_id", "user_id"]
        )
        batch_op.create_foreign_key(
            "fk_players_user_id_users", "users", ["user_id"], ["id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("players") as batch_op:
        batch_op.drop_constraint("fk_players_user_id_users", type_="foreignkey")
        batch_op.drop_constraint("uq_player_competition_user", type_="unique")
        batch_op.drop_index(op.f("ix_players_user_id"))
        batch_op.drop_column("user_id")

    op.drop_index(op.f("ix_users_token"), table_name="users")
    op.drop_table("users")
