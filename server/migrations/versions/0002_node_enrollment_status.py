"""Add enrollment approval state to nodes.

Revision ID: 0002_enrollment
Revises: 0001_initial
"""

from alembic import op
import sqlalchemy as sa


revision = "0002_enrollment"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "nodes",
        sa.Column(
            "enrollment_status",
            sa.String(length=16),
            server_default="pending",
            nullable=False,
        ),
    )
    op.execute("UPDATE nodes SET enrollment_status = 'approved'")


def downgrade():
    with op.batch_alter_table("nodes") as batch_op:
        batch_op.drop_column("enrollment_status")