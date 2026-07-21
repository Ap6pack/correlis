"""projection state

Revision ID: 0004_projection_state
Revises: 0003_observation_sequence
Create Date: 2026-07-21 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_projection_state"
down_revision = "0003_observation_sequence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "projector_checkpoints",
        sa.Column("projector_name", sa.String(128), nullable=False),
        sa.Column("projector_version", sa.String(64), nullable=False),
        sa.Column("last_processed_sequence", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("last_failure_sequence", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("last_processed_sequence >= 0", name="ck_projector_checkpoints_sequence_nonnegative"),
        sa.CheckConstraint("status IN ('idle', 'failed', 'paused')", name="ck_projector_checkpoints_status"),
        sa.CheckConstraint("((status = 'failed' AND last_failure_sequence IS NOT NULL AND last_failure_sequence > last_processed_sequence) OR (status <> 'failed' AND last_failure_sequence IS NULL))", name="ck_projector_checkpoints_failure_state"),
        sa.PrimaryKeyConstraint("projector_name", "projector_version"),
    )
    op.create_table(
        "projector_failures",
        sa.Column("projector_name", sa.String(128), nullable=False),
        sa.Column("projector_version", sa.String(64), nullable=False),
        sa.Column("ingest_sequence", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("observation_id", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=False),
        sa.Column("error_type", sa.String(256), nullable=False),
        sa.Column("safe_message", sa.String(2048), nullable=False),
        sa.Column("first_failed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_failed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["projector_name", "projector_version"], ["projector_checkpoints.projector_name", "projector_checkpoints.projector_version"]),
        sa.ForeignKeyConstraint(["ingest_sequence"], ["observation_ingest_entries.ingest_sequence"]),
        sa.ForeignKeyConstraint(["tenant_id", "observation_id"], ["observations.tenant_id", "observations.observation_id"]),
        sa.CheckConstraint("status IN ('active', 'resolved')", name="ck_projector_failures_status"),
        sa.CheckConstraint("attempt_count >= 1", name="ck_projector_failures_attempt_count"),
        sa.CheckConstraint("((status = 'active' AND resolved_at IS NULL) OR (status = 'resolved' AND resolved_at IS NOT NULL))", name="ck_projector_failures_resolved_state"),
        sa.PrimaryKeyConstraint("projector_name", "projector_version", "ingest_sequence"),
    )
    op.create_index("ix_projector_failures_projector_status_last_failed", "projector_failures", ["projector_name", "projector_version", "status", "last_failed_at"])
    op.create_index("ix_projector_failures_status_last_failed", "projector_failures", ["status", "last_failed_at"])
    op.create_index("ix_projector_failures_ingest_sequence", "projector_failures", ["ingest_sequence"])


def downgrade() -> None:
    op.drop_index("ix_projector_failures_ingest_sequence", table_name="projector_failures")
    op.drop_index("ix_projector_failures_status_last_failed", table_name="projector_failures")
    op.drop_index("ix_projector_failures_projector_status_last_failed", table_name="projector_failures")
    op.drop_table("projector_failures")
    op.drop_table("projector_checkpoints")
