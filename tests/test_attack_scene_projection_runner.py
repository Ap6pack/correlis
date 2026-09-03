from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from correlis_store import (
    AttackSceneProjectionConfig,
    AttackSceneProjectionHandler,
    AttackSceneProjectionInvariantError,
    ProjectorIdentity,
    resolve_attack_scene_policy,
)
from correlis_store import attack_scene_projection_handler as handler_module


def config(name="attack-scene-projection"):
    policy = resolve_attack_scene_policy("correlis-root-chain", "1")
    return AttackSceneProjectionConfig(
        identity=ProjectorIdentity(name, "1"),
        entity_projection_version="1",
        relationship_projection_version="1",
        correlation_projection_version="1",
        policy_name=policy.policy_name,
        policy_version=policy.policy_version,
        policy_manifest_sha256=policy.manifest_sha256(),
        policy_manifest=policy.manifest,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def item(sequence=7):
    return SimpleNamespace(
        ingest_sequence=sequence,
        observation=SimpleNamespace(tenant_id="tenant-a"),
    )


def test_handler_rejects_non_attack_scene_identity():
    with pytest.raises(AttackSceneProjectionInvariantError):
        AttackSceneProjectionHandler(config=config("entity-projection"))


def test_no_roots_is_a_successful_noop_without_reading_clock(monkeypatch):
    planner = Mock()
    planner.list_root_relationship_ids.return_value = ()
    monkeypatch.setattr(handler_module, "AttackSceneMembershipPlanner", Mock(return_value=planner))
    clock = Mock(side_effect=AssertionError("no-op must not require a timestamp"))
    session = Mock()

    AttackSceneProjectionHandler(config=config(), clock=clock)(session, item())

    planner.list_root_relationship_ids.assert_called_once_with(
        tenant_id="tenant-a",
        through_ingest_sequence=7,
        after_relationship_id=None,
        limit=500,
    )
    session.flush.assert_called_once_with()
    clock.assert_not_called()


def test_all_tenant_roots_are_planned_across_500_item_pages(monkeypatch):
    first_page = tuple(f"root-{number:03}" for number in range(500))
    planner = Mock()
    planner.list_root_relationship_ids.side_effect = (first_page, ("root-final",))
    planner.plan_scene.side_effect = lambda **kwargs: SimpleNamespace(
        root_relationship_id=kwargs["root_relationship_id"]
    )
    monkeypatch.setattr(handler_module, "AttackSceneMembershipPlanner", Mock(return_value=planner))
    handler = AttackSceneProjectionHandler(config=config())
    handler._persist_plan = Mock()  # type: ignore[method-assign]
    session = Mock()

    handler(session, item())

    assert planner.plan_scene.call_count == 501
    assert handler._persist_plan.call_count == 501
    assert (
        planner.list_root_relationship_ids.call_args_list[1].kwargs["after_relationship_id"]
        == first_page[-1]
    )


def test_listed_root_that_cannot_be_planned_is_an_invariant(monkeypatch):
    planner = Mock()
    planner.list_root_relationship_ids.return_value = ("root",)
    planner.plan_scene.return_value = None
    monkeypatch.setattr(handler_module, "AttackSceneMembershipPlanner", Mock(return_value=planner))

    with pytest.raises(AttackSceneProjectionInvariantError):
        AttackSceneProjectionHandler(config=config())(Mock(), item())
