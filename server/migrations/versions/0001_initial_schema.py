"""Create the initial node and challenge schema without dropping existing data.

Revision ID: 0001_initial
Revises:
"""

from alembic import op
import sqlalchemy as sa


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("nodes"):
        op.create_table(
            "nodes",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("public_key", sa.String(length=64), nullable=False),
            sa.Column("role", sa.String(length=32), nullable=False),
            sa.Column("hardware", sa.JSON(), nullable=False),
            sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )

    if not sa.inspect(bind).has_table("challenges"):
        op.create_table(
            "challenges",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("public_key", sa.String(length=64), nullable=False),
            sa.Column("nonce", sa.String(length=64), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("used", sa.Boolean(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )

    inspector = sa.inspect(bind)
    node_indexes = {index["name"] for index in inspector.get_indexes("nodes")}
    if "ix_nodes_public_key" not in node_indexes:
        op.create_index("ix_nodes_public_key", "nodes", ["public_key"], unique=True)

    challenge_indexes = {
        index["name"] for index in inspector.get_indexes("challenges")
    }
    if "ix_challenges_public_key" not in challenge_indexes:
        op.create_index(
            "ix_challenges_public_key", "challenges", ["public_key"], unique=False
        )


def downgrade():
    raise RuntimeError(
        "The initial baseline migration is irreversible to protect existing node data"
    )