"""immutable Attack Scene projector configuration"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011_attack_scene_config"
down_revision = "0010_attack_scene_storage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")
    op.create_table(
        "attack_scene_projection_configs",
        sa.Column("projector_name", sa.String(128), nullable=False),
        sa.Column("projection_version", sa.String(64), nullable=False),
        sa.Column("entity_projector_name", sa.String(128), nullable=False),
        sa.Column("entity_projection_version", sa.String(64), nullable=False),
        sa.Column("relationship_projector_name", sa.String(128), nullable=False),
        sa.Column("relationship_projection_version", sa.String(64), nullable=False),
        sa.Column("correlation_projector_name", sa.String(128), nullable=False),
        sa.Column("correlation_projection_version", sa.String(64), nullable=False),
        sa.Column("policy_name", sa.String(128), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("policy_manifest_sha256", sa.String(64), nullable=False),
        sa.Column("policy_manifest_json", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("projector_name", "projection_version"),
        sa.CheckConstraint(
            "projector_name = 'attack-scene-projection'", name="ck_attack_scene_configs_projector"
        ),
        sa.CheckConstraint(
            "entity_projector_name = 'entity-projection'",
            name="ck_attack_scene_configs_entity_projector",
        ),
        sa.CheckConstraint(
            "relationship_projector_name = 'relationship-projection'",
            name="ck_attack_scene_configs_relationship_projector",
        ),
        sa.CheckConstraint(
            "correlation_projector_name = 'correlation-projection'",
            name="ck_attack_scene_configs_correlation_projector",
        ),
        sa.CheckConstraint(
            "length(policy_manifest_sha256) = 64", name="ck_attack_scene_configs_manifest_hash"
        ),
        sa.CheckConstraint(
            "length(trim(policy_name)) > 0", name="ck_attack_scene_configs_policy_name"
        ),
        sa.CheckConstraint(
            "length(trim(policy_version)) > 0", name="ck_attack_scene_configs_policy_version"
        ),
        sa.ForeignKeyConstraint(
            ["projector_name", "projection_version"],
            ["projector_checkpoints.projector_name", "projector_checkpoints.projector_version"],
        ),
        sa.ForeignKeyConstraint(
            ["entity_projector_name", "entity_projection_version"],
            ["projector_checkpoints.projector_name", "projector_checkpoints.projector_version"],
        ),
        sa.ForeignKeyConstraint(
            ["relationship_projector_name", "relationship_projection_version"],
            ["projector_checkpoints.projector_name", "projector_checkpoints.projector_version"],
        ),
        sa.ForeignKeyConstraint(
            ["correlation_projector_name", "correlation_projection_version"],
            [
                "correlation_projection_configs.projector_name",
                "correlation_projection_configs.projection_version",
            ],
        ),
    )


def downgrade() -> None:
    op.drop_table("attack_scene_projection_configs")
