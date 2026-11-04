"""Durable user-owned conversations. Author: FubukiHanasaki."""

from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "conversations",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("owner_id", sa.String(32), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
    )
    op.create_index("ix_conversations_owner_id", "conversations", ["owner_id"])
    op.create_table(
        "chat_turns",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.String(32),
            sa.ForeignKey("conversations.id"),
            nullable=False,
        ),
        sa.Column(
            "job_id",
            sa.String(32),
            sa.ForeignKey("jobs.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.UniqueConstraint("conversation_id", "request_id"),
    )
    op.create_index("ix_chat_turns_conversation_id", "chat_turns", ["conversation_id"])


def downgrade():
    op.drop_table("chat_turns")
    op.drop_table("conversations")
