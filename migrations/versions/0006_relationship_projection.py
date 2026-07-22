"""persistent relationship projection

Revision ID: 0006_relationship_projection
Revises: 0005_entity_projection
Create Date: 2026-07-22 00:00:00.000000

Downgrading deletes only relationship-projection projector failures and checkpoints before
relationship output tables are dropped. Operators must explicitly re-register and rebuild the
relationship projector after re-upgrade.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_relationship_projection"
down_revision = "0005_entity_projection"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "relationships",
        sa.Column("projection_version", sa.String(64), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("relationship_id", sa.String(32), nullable=False),
        sa.Column("relationship_type", sa.String(64), nullable=False),
        sa.Column("provenance", sa.String(64), nullable=False),
        sa.Column("source_entity_id", sa.String(256), nullable=False),
        sa.Column("source_entity_type", sa.String(64), nullable=False),
        sa.Column("target_entity_id", sa.String(256), nullable=False),
        sa.Column("target_entity_type", sa.String(64), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("ontology_name", sa.String(128), nullable=False),
        sa.Column("ontology_version", sa.String(64), nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_ingest_sequence", sa.BigInteger(), nullable=False),
        sa.Column("last_ingest_sequence", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(relationship_id) = 32", name="ck_relationships_id_length"),
        sa.CheckConstraint(
            "relationship_id = lower(relationship_id) "
            "AND relationship_id NOT LIKE '%g%' AND relationship_id NOT LIKE '%h%' "
            "AND relationship_id NOT LIKE '%i%' AND relationship_id NOT LIKE '%j%' "
            "AND relationship_id NOT LIKE '%k%' AND relationship_id NOT LIKE '%l%' "
            "AND relationship_id NOT LIKE '%m%' AND relationship_id NOT LIKE '%n%' "
            "AND relationship_id NOT LIKE '%o%' AND relationship_id NOT LIKE '%p%' "
            "AND relationship_id NOT LIKE '%q%' AND relationship_id NOT LIKE '%r%' "
            "AND relationship_id NOT LIKE '%s%' AND relationship_id NOT LIKE '%t%' "
            "AND relationship_id NOT LIKE '%u%' AND relationship_id NOT LIKE '%v%' "
            "AND relationship_id NOT LIKE '%w%' AND relationship_id NOT LIKE '%x%' "
            "AND relationship_id NOT LIKE '%y%' AND relationship_id NOT LIKE '%z%'",
            name="ck_relationships_id_lower_hex",
        ),
        sa.CheckConstraint("provenance = 'observed'", name="ck_relationships_observed"),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_relationships_confidence"
        ),
        sa.CheckConstraint(
            "first_ingest_sequence >= 1", name="ck_relationships_first_sequence_positive"
        ),
        sa.CheckConstraint(
            "last_ingest_sequence >= first_ingest_sequence", name="ck_relationships_sequence_order"
        ),
        sa.CheckConstraint("first_seen <= last_seen", name="ck_relationships_seen_order"),
        sa.CheckConstraint("length(source_entity_id) > 0", name="ck_relationships_source_id"),
        sa.CheckConstraint("length(source_entity_type) > 0", name="ck_relationships_source_type"),
        sa.CheckConstraint("length(target_entity_id) > 0", name="ck_relationships_target_id"),
        sa.CheckConstraint("length(target_entity_type) > 0", name="ck_relationships_target_type"),
        sa.PrimaryKeyConstraint("projection_version", "tenant_id", "relationship_id"),
        sa.UniqueConstraint(
            "projection_version",
            "tenant_id",
            "source_entity_id",
            "relationship_type",
            "target_entity_id",
            "provenance",
            name="uq_relationships_projection_tenant_direct_edge",
        ),
    )
    for name, cols in {
        "ix_relationships_projection_tenant_source": [
            "projection_version",
            "tenant_id",
            "source_entity_id",
            "relationship_type",
            "relationship_id",
        ],
        "ix_relationships_projection_tenant_target": [
            "projection_version",
            "tenant_id",
            "target_entity_id",
            "relationship_type",
            "relationship_id",
        ],
        "ix_relationships_projection_tenant_type": [
            "projection_version",
            "tenant_id",
            "relationship_type",
            "relationship_id",
        ],
        "ix_relationships_projection_tenant_last_seen": [
            "projection_version",
            "tenant_id",
            "last_seen",
            "relationship_id",
        ],
    }.items():
        op.create_index(name, "relationships", cols)
    op.create_table(
        "relationship_observations",
        sa.Column("projection_version", sa.String(64), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("relationship_id", sa.String(32), nullable=False),
        sa.Column("observation_id", sa.String(128), nullable=False),
        sa.Column("ingest_sequence", sa.BigInteger(), nullable=False),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["projection_version", "tenant_id", "relationship_id"],
            [
                "relationships.projection_version",
                "relationships.tenant_id",
                "relationships.relationship_id",
            ],
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "observation_id"],
            ["observations.tenant_id", "observations.observation_id"],
        ),
        sa.ForeignKeyConstraint(
            ["ingest_sequence"], ["observation_ingest_entries.ingest_sequence"]
        ),
        sa.PrimaryKeyConstraint(
            "projection_version", "tenant_id", "relationship_id", "observation_id"
        ),
    )
    op.create_index(
        "ix_relationship_observations_relationship_sequence",
        "relationship_observations",
        ["projection_version", "tenant_id", "relationship_id", "ingest_sequence"],
    )
    op.create_index(
        "ix_relationship_observations_observation",
        "relationship_observations",
        ["projection_version", "tenant_id", "observation_id"],
    )
    op.create_table(
        "relationship_evidence",
        sa.Column("projection_version", sa.String(64), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("relationship_id", sa.String(32), nullable=False),
        sa.Column("evidence_id", sa.String(128), nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_ingest_sequence", sa.BigInteger(), nullable=False),
        sa.Column("last_ingest_sequence", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["projection_version", "tenant_id", "relationship_id"],
            [
                "relationships.projection_version",
                "relationships.tenant_id",
                "relationships.relationship_id",
            ],
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "evidence_id"], ["evidence_refs.tenant_id", "evidence_refs.evidence_id"]
        ),
        sa.CheckConstraint("first_seen <= last_seen", name="ck_relationship_evidence_seen_order"),
        sa.CheckConstraint(
            "first_ingest_sequence >= 1", name="ck_relationship_evidence_first_sequence_positive"
        ),
        sa.CheckConstraint(
            "last_ingest_sequence >= first_ingest_sequence",
            name="ck_relationship_evidence_sequence_order",
        ),
        sa.PrimaryKeyConstraint(
            "projection_version", "tenant_id", "relationship_id", "evidence_id"
        ),
    )
    op.create_index(
        "ix_relationship_evidence_relationship",
        "relationship_evidence",
        ["projection_version", "tenant_id", "relationship_id"],
    )
    op.create_index(
        "ix_relationship_evidence_evidence",
        "relationship_evidence",
        ["projection_version", "tenant_id", "evidence_id"],
    )


def downgrade() -> None:
    op.execute("DELETE FROM projector_failures WHERE projector_name = 'relationship-projection'")
    op.execute("DELETE FROM projector_checkpoints WHERE projector_name = 'relationship-projection'")
    op.drop_index("ix_relationship_evidence_evidence", table_name="relationship_evidence")
    op.drop_index("ix_relationship_evidence_relationship", table_name="relationship_evidence")
    op.drop_table("relationship_evidence")
    op.drop_index(
        "ix_relationship_observations_observation", table_name="relationship_observations"
    )
    op.drop_index(
        "ix_relationship_observations_relationship_sequence", table_name="relationship_observations"
    )
    op.drop_table("relationship_observations")
    op.drop_index("ix_relationships_projection_tenant_last_seen", table_name="relationships")
    op.drop_index("ix_relationships_projection_tenant_type", table_name="relationships")
    op.drop_index("ix_relationships_projection_tenant_target", table_name="relationships")
    op.drop_index("ix_relationships_projection_tenant_source", table_name="relationships")
    op.drop_table("relationships")
