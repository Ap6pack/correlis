"""correlation projector configuration

Revision ID: 0008_correlation_config
Revises: 0007_deterministic_relationships
Create Date: 2026-07-23 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008_correlation_config"
down_revision = "0007_deterministic_relationships"
branch_labels = None
depends_on = None


def _json_type():
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "correlation_projection_configs",
        sa.Column("projector_name", sa.String(128), nullable=False),
        sa.Column("projection_version", sa.String(64), nullable=False),
        sa.Column("relationship_projector_name", sa.String(128), nullable=False),
        sa.Column("relationship_projection_version", sa.String(64), nullable=False),
        sa.Column("ruleset_name", sa.String(128), nullable=False),
        sa.Column("ruleset_version", sa.String(64), nullable=False),
        sa.Column("rule_manifest_sha256", sa.String(64), nullable=False),
        sa.Column("rule_manifest_json", _json_type(), nullable=False),
        sa.Column("ontology_name", sa.String(128), nullable=False),
        sa.Column("ontology_version", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("projector_name", "projection_version"),
        sa.CheckConstraint(
            "projector_name = 'correlation-projection'",
            name="ck_correlation_projection_configs_projector_name",
        ),
        sa.CheckConstraint(
            "relationship_projector_name = 'relationship-projection'",
            name="ck_correlation_projection_configs_relationship_projector",
        ),
        sa.CheckConstraint(
            "length(rule_manifest_sha256) = 64",
            name="ck_correlation_projection_configs_manifest_hash",
        ),
        sa.ForeignKeyConstraint(
            ["projector_name", "projection_version"],
            ["projector_checkpoints.projector_name", "projector_checkpoints.projector_version"],
        ),
        sa.ForeignKeyConstraint(
            ["relationship_projector_name", "relationship_projection_version"],
            ["projector_checkpoints.projector_name", "projector_checkpoints.projector_version"],
        ),
        sa.UniqueConstraint(
            "relationship_projector_name",
            "relationship_projection_version",
            name="uq_correlation_projection_configs_relationship_graph",
        ),
    )


def downgrade() -> None:
    op.drop_table("correlation_projection_configs")
    op.execute("DELETE FROM projector_checkpoints WHERE projector_name = 'correlation-projection'")
