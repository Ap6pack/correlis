from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest
from correlis_store import (
    BUILTIN_ATTACK_SCENE_POLICY,
    AttackSceneMembershipPlanner,
    AttackScenePlanningInvariantError,
    AttackSceneProjectionConfig,
    PlannedAttackSceneObservation,
    ProjectorIdentity,
)
from correlis_store.models import Base
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def config(**changes: object) -> AttackSceneProjectionConfig:
    values = dict(
        identity=ProjectorIdentity("attack-scene-projection", "1"),
        entity_projection_version="entity-v1",
        relationship_projection_version="relationship-v1",
        correlation_projection_version="correlation-v1",
        policy_name=BUILTIN_ATTACK_SCENE_POLICY.policy_name,
        policy_version=BUILTIN_ATTACK_SCENE_POLICY.policy_version,
        policy_manifest_sha256=BUILTIN_ATTACK_SCENE_POLICY.manifest_sha256(),
        policy_manifest=BUILTIN_ATTACK_SCENE_POLICY.manifest,
        created_at=NOW,
    )
    values.update(changes)
    return AttackSceneProjectionConfig(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("identity", ProjectorIdentity("wrong-projection", "1")),
        ("policy_name", "missing-policy"),
        ("policy_manifest_sha256", "0" * 64),
        ("policy_manifest", {"changed": True}),
    ],
)
def test_planner_rejects_policy_or_identity_drift(key: str, value: object) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session, pytest.raises(AttackScenePlanningInvariantError):
        AttackSceneMembershipPlanner(session, config(**{key: value}))


def test_planned_values_are_frozen_and_payload_free() -> None:
    planned = PlannedAttackSceneObservation("observation-1", 1, NOW)
    with pytest.raises(FrozenInstanceError):
        planned.ingest_sequence = 2  # type: ignore[misc]
    assert set(planned.__slots__) == {"observation_id", "ingest_sequence", "event_time"}
