"""Normalize reported hardware capacities for admin aggregation.

Revision ID: 0004_node_capacity
Revises: 0003_test_workloads
"""

from alembic import op
import sqlalchemy as sa


revision = "0004_node_capacity"
down_revision = "0003_test_workloads"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("nodes", sa.Column("logical_cpu_cores", sa.BigInteger(), nullable=True))
    op.add_column("nodes", sa.Column("ram_bytes", sa.BigInteger(), nullable=True))
    op.add_column("nodes", sa.Column("storage_total_bytes", sa.BigInteger(), nullable=True))
    op.add_column("nodes", sa.Column("storage_available_bytes", sa.BigInteger(), nullable=True))
    op.add_column("nodes", sa.Column("gpu_cores", sa.BigInteger(), nullable=True))

    nodes = sa.table(
        "nodes",
        sa.column("hardware", sa.JSON()),
        sa.column("logical_cpu_cores", sa.BigInteger()),
        sa.column("ram_bytes", sa.BigInteger()),
        sa.column("storage_total_bytes", sa.BigInteger()),
        sa.column("storage_available_bytes", sa.BigInteger()),
        sa.column("gpu_cores", sa.BigInteger()),
    )
    hardware = nodes.c.hardware
    op.execute(
        sa.update(nodes).values(
            logical_cpu_cores=sa.func.coalesce(
                hardware["logical_cpu_cores"].as_integer(),
                hardware["logical_cpu_count"].as_integer(),
                hardware["cpu_cores"].as_integer(),
            ),
            ram_bytes=sa.func.coalesce(
                hardware["ram_bytes"].as_integer(),
                hardware["memory_bytes"].as_integer(),
            ),
            storage_total_bytes=sa.func.coalesce(
                hardware["storage_total_bytes"].as_integer(),
                hardware["storage_bytes"].as_integer(),
            ),
            storage_available_bytes=hardware["storage_available_bytes"].as_integer(),
            gpu_cores=sa.func.coalesce(
                hardware["gpu_cores"].as_integer(),
                hardware["gpu_core_count"].as_integer(),
            ),
        )
    )


def downgrade():
    op.drop_column("nodes", "gpu_cores")
    op.drop_column("nodes", "storage_available_bytes")
    op.drop_column("nodes", "storage_total_bytes")
    op.drop_column("nodes", "ram_bytes")
    op.drop_column("nodes", "logical_cpu_cores")