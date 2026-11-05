"""Add bounded admin test runs and assigned shards.

Revision ID: 0003_test_workloads
Revises: 0002_enrollment
"""

from alembic import op
import sqlalchemy as sa


revision = "0003_test_workloads"
down_revision = "0002_enrollment"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "test_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("total_items", sa.Integer(), nullable=False),
        sa.Column("shard_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "test_shards",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("shard_index", sa.Integer(), nullable=False),
        sa.Column("node_id", sa.String(length=36), nullable=False),
        sa.Column("start_inclusive", sa.Integer(), nullable=False),
        sa.Column("end_exclusive", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_sum", sa.BigInteger(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["node_id"], ["nodes.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["test_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "shard_index"),
    )
    op.create_index("ix_test_shards_run_id", "test_shards", ["run_id"])
    op.create_index("ix_test_shards_node_id", "test_shards", ["node_id"])
    op.create_index(
        "ix_nodes_role_enrollment_status",
        "nodes",
        ["role", "enrollment_status"],
    )
    op.create_index(
        "ix_test_shards_node_status_run",
        "test_shards",
        ["node_id", "status", "run_id", "shard_index"],
    )


def downgrade():
    op.drop_index("ix_test_shards_node_status_run", table_name="test_shards")
    op.drop_index("ix_nodes_role_enrollment_status", table_name="nodes")
    op.drop_index("ix_test_shards_node_id", table_name="test_shards")
    op.drop_index("ix_test_shards_run_id", table_name="test_shards")
    op.drop_table("test_shards")
    op.drop_table("test_runs")