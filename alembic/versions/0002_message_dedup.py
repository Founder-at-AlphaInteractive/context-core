"""Replace message content_hash unique constraint with external_id unique index.

Revision ID: 0002_message_dedup
Revises: 0001_initial
Create Date: 2024-12-02 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002_message_dedup"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("uq_message_conv_hash", "messages", type_="unique")
    op.create_index(
        "uq_message_conv_external_id",
        "messages",
        ["conversation_id", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_message_conv_external_id",
        table_name="messages",
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )
    op.create_unique_constraint(
        "uq_message_conv_hash",
        "messages",
        ["conversation_id", "content_hash"],
    )
