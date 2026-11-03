"""Identity and outbound node registration. Author: FubukiHanasaki."""

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("username", sa.String(80), unique=True, nullable=False),
        sa.Column("password_hash", sa.Text, nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("grants", sa.JSON, nullable=False),
    )
    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("user_id", sa.String(32), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("hash", sa.String(64), unique=True, nullable=False),
        sa.Column("scopes", sa.JSON, nullable=False),
        sa.Column("expires_at", sa.Float, nullable=False),
        sa.Column("revoked_at", sa.Float),
    )
    op.create_table(
        "registration_tokens",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("hash", sa.String(64), unique=True, nullable=False),
        sa.Column("expires_at", sa.Float, nullable=False),
        sa.Column("used_at", sa.Float),
        sa.Column(
            "issued_by", sa.String(32), sa.ForeignKey("users.id"), nullable=False
        ),
    )
    op.create_table(
        "nodes",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("os", sa.String(32), nullable=False),
        sa.Column("capabilities", sa.JSON, nullable=False),
        sa.Column("policy", sa.JSON, nullable=False),
        sa.Column("local_policy", sa.JSON, nullable=False),
        sa.Column("last_seen", sa.Float, nullable=False),
        sa.Column("disabled", sa.Boolean, nullable=False),
        sa.Column("owner_id", sa.String(32), sa.ForeignKey("users.id"), nullable=False),
    )
    op.create_table(
        "node_credentials",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("node_id", sa.String(32), sa.ForeignKey("nodes.id"), nullable=False),
        sa.Column("hash", sa.String(64), unique=True, nullable=False),
        sa.Column("expires_at", sa.Float, nullable=False),
        sa.Column("revoked_at", sa.Float),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("actor", sa.String(80), nullable=False),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("target", sa.String(80), nullable=False),
        sa.Column("timestamp", sa.Float, nullable=False),
        sa.Column("outcome", sa.String(40), nullable=False),
    )


def downgrade():
    for table in (
        "audit_events",
        "node_credentials",
        "nodes",
        "registration_tokens",
        "api_keys",
        "users",
    ):
        op.drop_table(table)
