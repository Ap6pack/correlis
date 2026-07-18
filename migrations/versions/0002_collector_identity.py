"""collector identity

Revision ID: 0002_collector_identity
Revises: 0001_observation_evidence_store
Create Date: 2026-07-18 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

json_type = sa.JSON().with_variant(JSONB, "postgresql")
revision = "0002_collector_identity"
down_revision = "0001_observation_evidence_store"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "collectors",
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("collector_id", sa.String(128), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("source", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("metadata_json", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_authenticated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("tenant_id", "collector_id"),
    )
    op.create_index("ix_collectors_tenant_status", "collectors", ["tenant_id", "status"])
    op.create_index("ix_collectors_tenant_source", "collectors", ["tenant_id", "source"])
    op.create_table(
        "collector_credentials",
        sa.Column("credential_id", sa.String(36), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("collector_id", sa.String(128), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("token_version", sa.String(16), nullable=False),
        sa.Column("secret_digest", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id", "collector_id"], ["collectors.tenant_id", "collectors.collector_id"]
        ),
        sa.PrimaryKeyConstraint("credential_id"),
    )
    op.create_index(
        "ix_collector_credentials_tenant_collector",
        "collector_credentials",
        ["tenant_id", "collector_id"],
    )
    op.create_index(
        "ix_collector_credentials_tenant_collector_revoked",
        "collector_credentials",
        ["tenant_id", "collector_id", "revoked_at"],
    )
    op.create_index("ix_collector_credentials_expires_at", "collector_credentials", ["expires_at"])
    op.create_table(
        "collector_auth_events",
        sa.Column("event_id", sa.String(36), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=True),
        sa.Column("collector_id", sa.String(128), nullable=True),
        sa.Column("credential_id", sa.String(36), nullable=True),
        sa.Column("request_id", sa.String(128), nullable=True),
        sa.Column("request_method", sa.String(16), nullable=False),
        sa.Column("request_path", sa.String(2048), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index(
        "ix_collector_auth_events_occurred_at", "collector_auth_events", ["occurred_at"]
    )
    op.create_index(
        "ix_collector_auth_events_tenant_occurred",
        "collector_auth_events",
        ["tenant_id", "occurred_at"],
    )
    op.create_index(
        "ix_collector_auth_events_tenant_collector_occurred",
        "collector_auth_events",
        ["tenant_id", "collector_id", "occurred_at"],
    )
    op.create_index(
        "ix_collector_auth_events_outcome_occurred",
        "collector_auth_events",
        ["outcome", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_collector_auth_events_outcome_occurred", table_name="collector_auth_events")
    op.drop_index(
        "ix_collector_auth_events_tenant_collector_occurred", table_name="collector_auth_events"
    )
    op.drop_index("ix_collector_auth_events_tenant_occurred", table_name="collector_auth_events")
    op.drop_index("ix_collector_auth_events_occurred_at", table_name="collector_auth_events")
    op.drop_table("collector_auth_events")
    op.drop_index("ix_collector_credentials_expires_at", table_name="collector_credentials")
    op.drop_index(
        "ix_collector_credentials_tenant_collector_revoked", table_name="collector_credentials"
    )
    op.drop_index("ix_collector_credentials_tenant_collector", table_name="collector_credentials")
    op.drop_table("collector_credentials")
    op.drop_index("ix_collectors_tenant_source", table_name="collectors")
    op.drop_index("ix_collectors_tenant_status", table_name="collectors")
    op.drop_table("collectors")
