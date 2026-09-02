"""durable Attack Scene storage

Revision ID: 0010_attack_scene_storage
Revises: 0009_correlation_lineage
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010_attack_scene_storage"
down_revision = "0009_correlation_lineage"
branch_labels = None
depends_on = None

json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
STATES = "'potential', 'observed', 'confirmed', 'contained', 'closed'"


def upgrade() -> None:
    op.create_table(
        "attack_scenes",
        sa.Column("projection_version", sa.String(64), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("scene_id", sa.String(128), nullable=False),
        sa.Column("entity_projection_version", sa.String(64), nullable=False),
        sa.Column("relationship_projection_version", sa.String(64), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_ingest_sequence", sa.BigInteger(), nullable=False),
        sa.Column("last_ingest_sequence", sa.BigInteger(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("uncertainty_json", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("projection_version", "tenant_id", "scene_id"),
        *[
            sa.CheckConstraint(
                f"length(trim({column})) > 0", name=f"ck_attack_scenes_{column}_nonblank"
            )
            for column in (
                "projection_version",
                "tenant_id",
                "scene_id",
                "entity_projection_version",
                "relationship_projection_version",
                "title",
            )
        ],
        sa.CheckConstraint(f"state IN ({STATES})", name="ck_attack_scenes_state"),
        sa.CheckConstraint("first_ingest_sequence >= 1", name="ck_attack_scenes_first_sequence"),
        sa.CheckConstraint(
            "last_ingest_sequence >= first_ingest_sequence", name="ck_attack_scenes_sequence_order"
        ),
        sa.CheckConstraint("first_seen <= last_seen", name="ck_attack_scenes_seen_order"),
        sa.CheckConstraint(
            "CAST(uncertainty_json AS TEXT) LIKE '[%]'", name="ck_attack_scenes_uncertainty_array"
        ),
    )
    for name, columns in (
        ("ix_attack_scenes_state", ["projection_version", "tenant_id", "state", "scene_id"]),
        ("ix_attack_scenes_last_seen", ["projection_version", "tenant_id", "last_seen"]),
        (
            "ix_attack_scenes_last_sequence",
            ["projection_version", "tenant_id", "last_ingest_sequence"],
        ),
        ("ix_attack_scenes_relationship_graph", ["relationship_projection_version", "tenant_id"]),
    ):
        op.create_index(name, "attack_scenes", columns)

    _create_graph_membership(
        "attack_scene_entities", "entity", "entity_projection_version", "entity_id", 256, "entities"
    )
    _create_graph_membership(
        "attack_scene_relationships",
        "relationship",
        "relationship_projection_version",
        "relationship_id",
        32,
        "relationships",
    )

    op.create_table(
        "attack_scene_observations",
        *_identity_columns(),
        sa.Column("observation_id", sa.String(128), nullable=False),
        sa.Column("ingest_sequence", sa.BigInteger(), nullable=False),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("projection_version", "tenant_id", "scene_id", "observation_id"),
        _scene_fk(),
        sa.ForeignKeyConstraint(
            ["tenant_id", "observation_id"],
            ["observations.tenant_id", "observations.observation_id"],
        ),
        sa.ForeignKeyConstraint(
            ["ingest_sequence"], ["observation_ingest_entries.ingest_sequence"]
        ),
    )
    op.create_index(
        "ix_attack_scene_observations_order",
        "attack_scene_observations",
        ["projection_version", "tenant_id", "scene_id", "ingest_sequence"],
    )
    op.create_index(
        "ix_attack_scene_observations_lookup",
        "attack_scene_observations",
        ["projection_version", "tenant_id", "observation_id"],
    )
    op.create_index(
        "ix_attack_scene_observations_sequence", "attack_scene_observations", ["ingest_sequence"]
    )

    op.create_table(
        "attack_scene_state_transitions",
        *_identity_columns(),
        sa.Column("ingest_sequence", sa.BigInteger(), nullable=False),
        sa.Column("trigger_observation_id", sa.String(128), nullable=False),
        sa.Column("from_state", sa.String(32), nullable=False),
        sa.Column("to_state", sa.String(32), nullable=False),
        sa.Column("reason_code", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "projection_version",
            "tenant_id",
            "scene_id",
            "ingest_sequence",
            "trigger_observation_id",
        ),
        _scene_fk(),
        sa.ForeignKeyConstraint(
            ["tenant_id", "trigger_observation_id"],
            ["observations.tenant_id", "observations.observation_id"],
        ),
        sa.ForeignKeyConstraint(
            ["ingest_sequence"], ["observation_ingest_entries.ingest_sequence"]
        ),
        sa.CheckConstraint("ingest_sequence >= 1", name="ck_attack_scene_transitions_sequence"),
        sa.CheckConstraint(
            f"from_state IN ({STATES})", name="ck_attack_scene_transitions_from_state"
        ),
        sa.CheckConstraint(f"to_state IN ({STATES})", name="ck_attack_scene_transitions_to_state"),
        sa.CheckConstraint(
            "from_state <> to_state", name="ck_attack_scene_transitions_state_change"
        ),
        sa.CheckConstraint(
            "length(trim(reason_code)) > 0", name="ck_attack_scene_transitions_reason"
        ),
    )
    op.create_index(
        "ix_attack_scene_transitions_order",
        "attack_scene_state_transitions",
        ["projection_version", "tenant_id", "scene_id", "ingest_sequence"],
    )
    op.create_index(
        "ix_attack_scene_transitions_state",
        "attack_scene_state_transitions",
        ["projection_version", "tenant_id", "to_state", "ingest_sequence"],
    )
    op.create_index(
        "ix_attack_scene_transitions_trigger",
        "attack_scene_state_transitions",
        ["tenant_id", "trigger_observation_id"],
    )


def _identity_columns():
    return (
        sa.Column("projection_version", sa.String(64), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("scene_id", sa.String(128), nullable=False),
    )


def _scene_fk():
    return sa.ForeignKeyConstraint(
        ["projection_version", "tenant_id", "scene_id"],
        ["attack_scenes.projection_version", "attack_scenes.tenant_id", "attack_scenes.scene_id"],
    )


def _create_graph_membership(table, noun, version_column, id_column, id_length, target):
    op.create_table(
        table,
        *_identity_columns(),
        sa.Column(version_column, sa.String(64), nullable=False),
        sa.Column(id_column, sa.String(id_length), nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_ingest_sequence", sa.BigInteger(), nullable=False),
        sa.Column("last_ingest_sequence", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("projection_version", "tenant_id", "scene_id", id_column),
        _scene_fk(),
        sa.ForeignKeyConstraint(
            [version_column, "tenant_id", id_column],
            [f"{target}.projection_version", f"{target}.tenant_id", f"{target}.{id_column}"],
        ),
        sa.CheckConstraint("first_ingest_sequence >= 1", name=f"ck_{table}_first_sequence"),
        sa.CheckConstraint(
            "last_ingest_sequence >= first_ingest_sequence", name=f"ck_{table}_sequence_order"
        ),
        sa.CheckConstraint("first_seen <= last_seen", name=f"ck_{table}_seen_order"),
    )
    op.create_index(f"ix_{table}_scene", table, ["projection_version", "tenant_id", "scene_id"])
    op.create_index(f"ix_{table}_canonical", table, [version_column, "tenant_id", id_column])
    op.create_index(f"ix_{table}_lookup", table, ["projection_version", "tenant_id", id_column])


def downgrade() -> None:
    op.drop_table("attack_scene_state_transitions")
    op.drop_table("attack_scene_observations")
    op.drop_table("attack_scene_relationships")
    op.drop_table("attack_scene_entities")
    op.drop_table("attack_scenes")
