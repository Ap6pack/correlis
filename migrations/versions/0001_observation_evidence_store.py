"""observation evidence store

Revision ID: 0001_observation_evidence_store
Revises: 
Create Date: 2026-07-18 00:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

json_type = sa.JSON().with_variant(JSONB, "postgresql")

revision = "0001_observation_evidence_store"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "observations",
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("observation_id", sa.String(length=128), nullable=False),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingest_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=128), nullable=False),
        sa.Column("sensor_id", sa.String(length=256), nullable=False),
        sa.Column("event_class", sa.String(length=128), nullable=False),
        sa.Column("activity", sa.String(length=128), nullable=False),
        sa.Column("severity", sa.String(length=64), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("payload_json", json_type, nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("inserted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "observation_id"),
    )
    op.create_index("ix_observations_tenant_event_time", "observations", ["tenant_id", "event_time"])
    op.create_index(
        "ix_observations_tenant_source_event_time",
        "observations",
        ["tenant_id", "source", "event_time"],
    )
    op.create_index(
        "ix_observations_tenant_event_class_event_time",
        "observations",
        ["tenant_id", "event_class", "event_time"],
    )
    op.create_table(
        "evidence_refs",
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("evidence_id", sa.String(length=128), nullable=False),
        sa.Column("evidence_type", sa.String(length=128), nullable=False),
        sa.Column("source", sa.String(length=128), nullable=False),
        sa.Column("locator", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_json", json_type, nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("inserted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "evidence_id"),
    )
    op.create_index("ix_evidence_refs_tenant_sha256", "evidence_refs", ["tenant_id", "sha256"])
    op.create_table(
        "observation_evidence",
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("observation_id", sa.String(length=128), nullable=False),
        sa.Column("evidence_id", sa.String(length=128), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "observation_id"], ["observations.tenant_id", "observations.observation_id"]
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "evidence_id"], ["evidence_refs.tenant_id", "evidence_refs.evidence_id"]
        ),
        sa.PrimaryKeyConstraint("tenant_id", "observation_id", "evidence_id"),
    )


def downgrade() -> None:
    op.drop_table("observation_evidence")
    op.drop_index("ix_evidence_refs_tenant_sha256", table_name="evidence_refs")
    op.drop_table("evidence_refs")
    op.drop_index("ix_observations_tenant_event_class_event_time", table_name="observations")
    op.drop_index("ix_observations_tenant_source_event_time", table_name="observations")
    op.drop_index("ix_observations_tenant_event_time", table_name="observations")
    op.drop_table("observations")
