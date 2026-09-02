from __future__ import annotations

import pytest
from correlis_store import (
    BUILTIN_ATTACK_SCENE_POLICY,
    BUILTIN_ATTACK_SCENE_POLICY_CATALOG,
    AttackScenePolicyCatalog,
    AttackScenePolicyDefinition,
    AttackScenePolicyNotFound,
    attack_scene_id,
    resolve_attack_scene_policy,
)


def test_builtin_attack_scene_policy_is_exact_and_deterministic():
    assert BUILTIN_ATTACK_SCENE_POLICY_CATALOG.list() == (BUILTIN_ATTACK_SCENE_POLICY,)
    assert resolve_attack_scene_policy("correlis-root-chain", "1") is BUILTIN_ATTACK_SCENE_POLICY
    assert len(BUILTIN_ATTACK_SCENE_POLICY.manifest_sha256()) == 64
    assert (
        BUILTIN_ATTACK_SCENE_POLICY.manifest_sha256()
        == BUILTIN_ATTACK_SCENE_POLICY.manifest_sha256()
    )
    assert BUILTIN_ATTACK_SCENE_POLICY.manifest["anchor"] == {
        "relationship_type": "exploited",
        "provenance": "deterministic",
        "rule_id": "COR-SEQ-001",
        "rule_version": "1",
        "only_automatic_scene_root": True,
    }


@pytest.mark.parametrize("name, version", [("unknown", "1"), ("correlis-root-chain", "2")])
def test_policy_resolution_has_no_fallback(name, version):
    with pytest.raises(AttackScenePolicyNotFound):
        resolve_attack_scene_policy(name, version)


def test_catalog_rejects_duplicate_identity():
    policy = AttackScenePolicyDefinition("p", "1", {})
    with pytest.raises(ValueError):
        AttackScenePolicyCatalog((policy, policy))


def test_attack_scene_id_is_root_derived_and_validated():
    assert attack_scene_id("a" * 32) == "scene:" + "a" * 32
    with pytest.raises(ValueError):
        attack_scene_id("  ")
