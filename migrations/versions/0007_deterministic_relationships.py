"""deterministic relationship storage

Revision ID: 0007_deterministic_relationships
Revises: 0006_relationship_projection
Create Date: 2026-07-23 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_deterministic_relationships"
down_revision = "0006_relationship_projection"
branch_labels = None
depends_on = None

_OBSERVED = "provenance = 'observed'"
_DETERMINISTIC = "provenance = 'deterministic'"


def upgrade() -> None:
    with op.batch_alter_table("relationships") as batch:
        batch.drop_constraint("uq_relationships_projection_tenant_direct_edge", type_="unique")
        batch.drop_constraint("ck_relationships_observed", type_="check")
        batch.add_column(sa.Column("rule_id", sa.String(128), nullable=True))
        batch.add_column(sa.Column("rule_version", sa.String(64), nullable=True))
        batch.create_check_constraint(
            "ck_relationships_provenance_supported",
            "provenance IN ('observed', 'deterministic')",
        )
        batch.create_check_constraint(
            "ck_relationships_observed_rule_identity",
            "provenance <> 'observed' OR (rule_id IS NULL AND rule_version IS NULL)",
        )
        batch.create_check_constraint(
            "ck_relationships_deterministic_rule_identity",
            "provenance <> 'deterministic' OR ("
            "rule_id IS NOT NULL AND rule_version IS NOT NULL AND "
            "length(trim(rule_id)) > 0 AND length(trim(rule_version)) > 0)",
        )
    op.create_index(
        "ix_relationships_observed_direct_edge_unique",
        "relationships",
        [
            "projection_version",
            "tenant_id",
            "source_entity_id",
            "relationship_type",
            "target_entity_id",
        ],
        unique=True,
        sqlite_where=sa.text(_OBSERVED),
        postgresql_where=sa.text(_OBSERVED),
    )
    op.create_index(
        "ix_relationships_deterministic_rule_edge_unique",
        "relationships",
        [
            "projection_version",
            "tenant_id",
            "source_entity_id",
            "relationship_type",
            "target_entity_id",
            "rule_id",
        ],
        unique=True,
        sqlite_where=sa.text(_DETERMINISTIC),
        postgresql_where=sa.text(_DETERMINISTIC),
    )


def downgrade() -> None:
    conn = op.get_bind()
    deterministic = sa.text(
        "SELECT projection_version, tenant_id, relationship_id FROM relationships "
        "WHERE provenance = 'deterministic'"
    )
    for row in conn.execute(deterministic).mappings():
        params = dict(row)
        conn.execute(
            sa.text(
                "DELETE FROM relationship_evidence WHERE projection_version = :projection_version "
                "AND tenant_id = :tenant_id AND relationship_id = :relationship_id"
            ),
            params,
        )
        conn.execute(
            sa.text(
                "DELETE FROM relationship_observations WHERE projection_version = :projection_version "
                "AND tenant_id = :tenant_id AND relationship_id = :relationship_id"
            ),
            params,
        )
    op.execute("DELETE FROM relationships WHERE provenance = 'deterministic'")
    op.drop_index("ix_relationships_deterministic_rule_edge_unique", table_name="relationships")
    op.drop_index("ix_relationships_observed_direct_edge_unique", table_name="relationships")
    with op.batch_alter_table("relationships") as batch:
        batch.drop_constraint("ck_relationships_deterministic_rule_identity", type_="check")
        batch.drop_constraint("ck_relationships_observed_rule_identity", type_="check")
        batch.drop_constraint("ck_relationships_provenance_supported", type_="check")
        batch.create_check_constraint("ck_relationships_observed", "provenance = 'observed'")
        batch.create_unique_constraint(
            "uq_relationships_projection_tenant_direct_edge",
            [
                "projection_version",
                "tenant_id",
                "source_entity_id",
                "relationship_type",
                "target_entity_id",
                "provenance",
            ],
        )
        batch.drop_column("rule_version")
        batch.drop_column("rule_id")
