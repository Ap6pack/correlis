"""persistent entity projection

Revision ID: 0005_entity_projection
Revises: 0004_projection_state
Create Date: 2026-07-22 00:00:00.000000

Downgrading deletes only entity-projection projector failures and checkpoints before dropping
entity output tables. Operators must explicitly re-register and rebuild the entity projector
after re-upgrade.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_entity_projection"
down_revision = "0004_projection_state"
branch_labels = None
depends_on = None

json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "entities",
        sa.Column("projection_version", sa.String(64), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("entity_id", sa.String(256), nullable=False),
        sa.Column("canonical_key", sa.String(64), nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("label", sa.String(256), nullable=False),
        sa.Column("attributes_json", json_type, nullable=False),
        sa.Column("ontology_name", sa.String(128), nullable=False),
        sa.Column("ontology_version", sa.String(64), nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_ingest_sequence", sa.BigInteger(), nullable=False),
        sa.Column("last_ingest_sequence", sa.BigInteger(), nullable=False),
        sa.Column("latest_claim_event_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("latest_claim_ingest_sequence", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(canonical_key) = 64", name="ck_entities_canonical_key_length"),
        sa.CheckConstraint(
            "first_ingest_sequence >= 1", name="ck_entities_first_sequence_positive"
        ),
        sa.CheckConstraint(
            "last_ingest_sequence >= first_ingest_sequence", name="ck_entities_sequence_order"
        ),
        sa.CheckConstraint(
            "latest_claim_ingest_sequence >= first_ingest_sequence",
            name="ck_entities_latest_sequence_min",
        ),
        sa.CheckConstraint(
            "latest_claim_ingest_sequence <= last_ingest_sequence",
            name="ck_entities_latest_sequence_max",
        ),
        sa.CheckConstraint("first_seen <= last_seen", name="ck_entities_seen_order"),
        sa.CheckConstraint(
            "latest_claim_event_time <= last_seen", name="ck_entities_latest_claim_seen"
        ),
        sa.PrimaryKeyConstraint("projection_version", "tenant_id", "entity_id"),
        sa.UniqueConstraint(
            "projection_version",
            "tenant_id",
            "canonical_key",
            name="uq_entities_projection_tenant_canonical_key",
        ),
    )
    op.create_index(
        "ix_entities_projection_tenant_type_id",
        "entities",
        ["projection_version", "tenant_id", "entity_type", "entity_id"],
    )
    op.create_index(
        "ix_entities_projection_tenant_last_seen",
        "entities",
        ["projection_version", "tenant_id", "last_seen"],
    )
    op.create_index(
        "ix_entities_projection_tenant_canonical_key",
        "entities",
        ["projection_version", "tenant_id", "canonical_key"],
    )
    op.create_table(
        "entity_observations",
        sa.Column("projection_version", sa.String(64), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("entity_id", sa.String(256), nullable=False),
        sa.Column("observation_id", sa.String(128), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("ingest_sequence", sa.BigInteger(), nullable=False),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["projection_version", "tenant_id", "entity_id"],
            ["entities.projection_version", "entities.tenant_id", "entities.entity_id"],
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "observation_id"],
            ["observations.tenant_id", "observations.observation_id"],
        ),
        sa.ForeignKeyConstraint(
            ["ingest_sequence"], ["observation_ingest_entries.ingest_sequence"]
        ),
        sa.CheckConstraint("role IN ('subject', 'object')", name="ck_entity_observations_role"),
        sa.PrimaryKeyConstraint(
            "projection_version", "tenant_id", "entity_id", "observation_id", "role"
        ),
    )
    op.create_index(
        "ix_entity_observations_entity_sequence",
        "entity_observations",
        ["projection_version", "tenant_id", "entity_id", "ingest_sequence"],
    )
    op.create_index(
        "ix_entity_observations_observation",
        "entity_observations",
        ["projection_version", "tenant_id", "observation_id"],
    )
    op.create_index(
        "ix_entity_observations_sequence",
        "entity_observations",
        ["projection_version", "tenant_id", "ingest_sequence"],
    )
    op.create_table(
        "entity_evidence",
        sa.Column("projection_version", sa.String(64), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("entity_id", sa.String(256), nullable=False),
        sa.Column("evidence_id", sa.String(128), nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_ingest_sequence", sa.BigInteger(), nullable=False),
        sa.Column("last_ingest_sequence", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["projection_version", "tenant_id", "entity_id"],
            ["entities.projection_version", "entities.tenant_id", "entities.entity_id"],
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "evidence_id"], ["evidence_refs.tenant_id", "evidence_refs.evidence_id"]
        ),
        sa.CheckConstraint("first_seen <= last_seen", name="ck_entity_evidence_seen_order"),
        sa.CheckConstraint(
            "first_ingest_sequence >= 1", name="ck_entity_evidence_first_sequence_positive"
        ),
        sa.CheckConstraint(
            "last_ingest_sequence >= first_ingest_sequence",
            name="ck_entity_evidence_sequence_order",
        ),
        sa.PrimaryKeyConstraint("projection_version", "tenant_id", "entity_id", "evidence_id"),
    )
    op.create_index(
        "ix_entity_evidence_entity",
        "entity_evidence",
        ["projection_version", "tenant_id", "entity_id"],
    )
    op.create_index(
        "ix_entity_evidence_evidence",
        "entity_evidence",
        ["projection_version", "tenant_id", "evidence_id"],
    )
    op.create_table(
        "entity_identity_claims",
        sa.Column("projection_version", sa.String(64), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("entity_id", sa.String(256), nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("identity_key_name", sa.String(128), nullable=False),
        sa.Column("value_sha256", sa.String(64), nullable=False),
        sa.Column("value_json", json_type, nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_ingest_sequence", sa.BigInteger(), nullable=False),
        sa.Column("last_ingest_sequence", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["projection_version", "tenant_id", "entity_id"],
            ["entities.projection_version", "entities.tenant_id", "entities.entity_id"],
        ),
        sa.CheckConstraint(
            "length(value_sha256) = 64", name="ck_entity_identity_claims_hash_length"
        ),
        sa.CheckConstraint("first_seen <= last_seen", name="ck_entity_identity_claims_seen_order"),
        sa.CheckConstraint(
            "first_ingest_sequence >= 1", name="ck_entity_identity_claims_first_sequence_positive"
        ),
        sa.CheckConstraint(
            "last_ingest_sequence >= first_ingest_sequence",
            name="ck_entity_identity_claims_sequence_order",
        ),
        sa.PrimaryKeyConstraint(
            "projection_version", "tenant_id", "entity_id", "identity_key_name", "value_sha256"
        ),
    )
    op.create_index(
        "ix_entity_identity_claims_lookup",
        "entity_identity_claims",
        ["projection_version", "tenant_id", "entity_type", "identity_key_name", "value_sha256"],
    )


def downgrade() -> None:
    op.execute("DELETE FROM projector_failures WHERE projector_name = 'entity-projection'")
    op.execute("DELETE FROM projector_checkpoints WHERE projector_name = 'entity-projection'")
    op.drop_index("ix_entity_identity_claims_lookup", table_name="entity_identity_claims")
    op.drop_table("entity_identity_claims")
    op.drop_index("ix_entity_evidence_evidence", table_name="entity_evidence")
    op.drop_index("ix_entity_evidence_entity", table_name="entity_evidence")
    op.drop_table("entity_evidence")
    op.drop_index("ix_entity_observations_sequence", table_name="entity_observations")
    op.drop_index("ix_entity_observations_observation", table_name="entity_observations")
    op.drop_index("ix_entity_observations_entity_sequence", table_name="entity_observations")
    op.drop_table("entity_observations")
    op.drop_index("ix_entities_projection_tenant_canonical_key", table_name="entities")
    op.drop_index("ix_entities_projection_tenant_last_seen", table_name="entities")
    op.drop_index("ix_entities_projection_tenant_type_id", table_name="entities")
    op.drop_table("entities")
