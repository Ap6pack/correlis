"""correlation derivation lineage storage

Revision ID: 0009_correlation_lineage
Revises: 0008_correlation_config
Create Date: 2026-07-23 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_correlation_lineage"
down_revision = "0008_correlation_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "relationship_derivations",
        sa.Column("relationship_projection_version", sa.String(64), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("relationship_id", sa.String(32), nullable=False),
        sa.Column("trigger_observation_id", sa.String(128), nullable=False),
        sa.Column("correlation_projector_name", sa.String(128), nullable=False),
        sa.Column("correlation_projection_version", sa.String(64), nullable=False),
        sa.Column("rule_id", sa.String(128), nullable=False),
        sa.Column("rule_version", sa.String(64), nullable=False),
        sa.Column("trigger_ingest_sequence", sa.BigInteger(), nullable=False),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("reason_code", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["relationship_projection_version", "tenant_id", "relationship_id"],
            [
                "relationships.projection_version",
                "relationships.tenant_id",
                "relationships.relationship_id",
            ],
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "trigger_observation_id"],
            ["observations.tenant_id", "observations.observation_id"],
        ),
        sa.ForeignKeyConstraint(
            ["trigger_ingest_sequence"], ["observation_ingest_entries.ingest_sequence"]
        ),
        sa.ForeignKeyConstraint(
            ["correlation_projector_name", "correlation_projection_version"],
            [
                "correlation_projection_configs.projector_name",
                "correlation_projection_configs.projection_version",
            ],
        ),
        sa.CheckConstraint(
            "correlation_projector_name = 'correlation-projection'",
            name="ck_relationship_derivations_projector_name",
        ),
        sa.CheckConstraint(
            "trigger_ingest_sequence >= 1", name="ck_relationship_derivations_trigger_sequence"
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_relationship_derivations_confidence"
        ),
        sa.CheckConstraint("length(trim(rule_id)) > 0", name="ck_relationship_derivations_rule_id"),
        sa.CheckConstraint(
            "length(trim(rule_version)) > 0", name="ck_relationship_derivations_rule_version"
        ),
        sa.CheckConstraint(
            "length(trim(reason_code)) > 0", name="ck_relationship_derivations_reason_code"
        ),
        sa.PrimaryKeyConstraint(
            "relationship_projection_version",
            "tenant_id",
            "relationship_id",
            "trigger_observation_id",
        ),
    )
    op.create_index(
        "ix_relationship_derivations_correlation_sequence",
        "relationship_derivations",
        ["correlation_projector_name", "correlation_projection_version", "trigger_ingest_sequence"],
    )
    op.create_index(
        "ix_relationship_derivations_relationship_sequence",
        "relationship_derivations",
        [
            "relationship_projection_version",
            "tenant_id",
            "relationship_id",
            "trigger_ingest_sequence",
        ],
    )
    op.create_index(
        "ix_relationship_derivations_rule_sequence",
        "relationship_derivations",
        ["relationship_projection_version", "tenant_id", "rule_id", "trigger_ingest_sequence"],
    )
    op.create_index(
        "ix_relationship_derivations_trigger_observation",
        "relationship_derivations",
        ["tenant_id", "trigger_observation_id"],
    )

    op.create_table(
        "relationship_derivation_supports",
        sa.Column("relationship_projection_version", sa.String(64), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("relationship_id", sa.String(32), nullable=False),
        sa.Column("trigger_observation_id", sa.String(128), nullable=False),
        sa.Column("support_relationship_id", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            [
                "relationship_projection_version",
                "tenant_id",
                "relationship_id",
                "trigger_observation_id",
            ],
            [
                "relationship_derivations.relationship_projection_version",
                "relationship_derivations.tenant_id",
                "relationship_derivations.relationship_id",
                "relationship_derivations.trigger_observation_id",
            ],
        ),
        sa.ForeignKeyConstraint(
            ["relationship_projection_version", "tenant_id", "support_relationship_id"],
            [
                "relationships.projection_version",
                "relationships.tenant_id",
                "relationships.relationship_id",
            ],
        ),
        sa.CheckConstraint(
            "support_relationship_id <> relationship_id",
            name="ck_relationship_derivation_supports_not_self",
        ),
        sa.PrimaryKeyConstraint(
            "relationship_projection_version",
            "tenant_id",
            "relationship_id",
            "trigger_observation_id",
            "support_relationship_id",
        ),
    )

    op.create_table(
        "relationship_derivation_evidence",
        sa.Column("relationship_projection_version", sa.String(64), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("relationship_id", sa.String(32), nullable=False),
        sa.Column("trigger_observation_id", sa.String(128), nullable=False),
        sa.Column("evidence_id", sa.String(128), nullable=False),
        sa.Column("evidence_role", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            [
                "relationship_projection_version",
                "tenant_id",
                "relationship_id",
                "trigger_observation_id",
            ],
            [
                "relationship_derivations.relationship_projection_version",
                "relationship_derivations.tenant_id",
                "relationship_derivations.relationship_id",
                "relationship_derivations.trigger_observation_id",
            ],
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "evidence_id"], ["evidence_refs.tenant_id", "evidence_refs.evidence_id"]
        ),
        sa.CheckConstraint(
            "evidence_role IN ('trigger', 'support')",
            name="ck_relationship_derivation_evidence_role",
        ),
        sa.PrimaryKeyConstraint(
            "relationship_projection_version",
            "tenant_id",
            "relationship_id",
            "trigger_observation_id",
            "evidence_id",
            "evidence_role",
        ),
    )


def downgrade() -> None:
    op.drop_table("relationship_derivation_evidence")
    op.drop_table("relationship_derivation_supports")
    op.drop_index(
        "ix_relationship_derivations_trigger_observation", table_name="relationship_derivations"
    )
    op.drop_index(
        "ix_relationship_derivations_rule_sequence", table_name="relationship_derivations"
    )
    op.drop_index(
        "ix_relationship_derivations_relationship_sequence", table_name="relationship_derivations"
    )
    op.drop_index(
        "ix_relationship_derivations_correlation_sequence", table_name="relationship_derivations"
    )
    op.drop_table("relationship_derivations")
