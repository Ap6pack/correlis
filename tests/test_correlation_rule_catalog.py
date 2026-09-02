from __future__ import annotations

import pytest
from correlis_schema import RelationshipType
from correlis_store import (
    BUILTIN_CORRELATION_RULE_CATALOG,
    BUILTIN_CORRELATION_RULES,
    COR_SEQ_003,
    CORRELATION_RULESET_V2,
    CORRELATION_RULESET_V3,
    CorrelationRuleCatalog,
    CorrelationRuleDefinition,
    CorrelationRuleRegistry,
    CorrelationRulesetNotFound,
    resolve_correlation_rule_registry,
)

EXPECTED_V1_MANIFEST = {
    "ruleset_name": "correlis-sequence",
    "ruleset_version": "1",
    "rules": [
        {
            "rule_id": "COR-SEQ-001",
            "rule_version": "1",
            "display_name": "Exploit against known vulnerability",
            "description": (
                "Defines the future deterministic relationship from exploit activity to "
                "a known vulnerability."
            ),
            "reason_code": "exploit_against_known_vulnerability",
            "output_relationship_type": "exploited",
            "confidence": 0.85,
            "evaluation_order": 100,
        }
    ],
}
EXPECTED_V1_MANIFEST_SHA256 = "10268cfa7db0510e60fa14049a9d1227cab19cd164e044d643236e5a9d3f93e9"
EXPECTED_V2_MANIFEST_SHA256 = "4afce9cd2588b149a028591d3acbe89b01adf6367be91dc61c7d53d2c26e5f6d"


def _registry(name: str, version: str) -> CorrelationRuleRegistry:
    return CorrelationRuleRegistry(
        name=name,
        version=version,
        definitions=(
            CorrelationRuleDefinition(
                rule_id=f"{name}-{version}",
                rule_version="1",
                display_name="Test rule",
                description="Test rule",
                reason_code="test_rule",
                output_relationship_type=RelationshipType.EXPLOITED,
                confidence=0.5,
                evaluation_order=100,
            ),
        ),
    )


def test_version_one_registry_manifest_is_unchanged():
    assert BUILTIN_CORRELATION_RULES.manifest() == EXPECTED_V1_MANIFEST
    assert BUILTIN_CORRELATION_RULES.manifest_sha256() == EXPECTED_V1_MANIFEST_SHA256


def test_exact_builtin_ruleset_resolution_and_no_fallbacks():
    assert resolve_correlation_rule_registry("correlis-sequence", "1") is BUILTIN_CORRELATION_RULES
    with pytest.raises(CorrelationRulesetNotFound):
        resolve_correlation_rule_registry("missing", "1")


def test_duplicate_registry_identity_fails_and_listing_order_is_deterministic():
    one = _registry("catalog-test", "1")
    two = _registry("catalog-test", "2")
    assert CorrelationRuleCatalog((two, one)).list() == (two, one)
    with pytest.raises(ValueError, match="duplicate correlation ruleset identity"):
        CorrelationRuleCatalog((one, one))
    with pytest.raises(ValueError, match="duplicate correlation ruleset identity"):
        CorrelationRuleCatalog((one, _registry("catalog-test", "1")))


def test_builtin_catalog_contains_versions_one_two_and_three_in_order():
    registries = BUILTIN_CORRELATION_RULE_CATALOG.list()
    assert [(r.name, r.version) for r in registries] == [
        ("correlis-sequence", "1"),
        ("correlis-sequence", "2"),
        ("correlis-sequence", "3"),
    ]
    v1 = resolve_correlation_rule_registry("correlis-sequence", "1")
    v2 = resolve_correlation_rule_registry("correlis-sequence", "2")
    v3 = resolve_correlation_rule_registry("correlis-sequence", "3")
    assert v1 is BUILTIN_CORRELATION_RULES
    assert v2 is CORRELATION_RULESET_V2
    assert v3 is CORRELATION_RULESET_V3
    assert v1.manifest() == EXPECTED_V1_MANIFEST
    assert v1.manifest_sha256() == EXPECTED_V1_MANIFEST_SHA256
    assert v2.manifest_sha256() == EXPECTED_V2_MANIFEST_SHA256
    assert [d.rule_id for d in v1.definitions()] == ["COR-SEQ-001"]
    assert [d.rule_id for d in v2.definitions()] == ["COR-SEQ-001", "COR-SEQ-002"]
    assert [d.rule_id for d in v3.definitions()] == [
        "COR-SEQ-001",
        "COR-SEQ-002",
        "COR-SEQ-003",
    ]
    assert [r["rule_id"] for r in v2.manifest()["rules"]] == [
        "COR-SEQ-001",
        "COR-SEQ-002",
    ]
    assert [r["evaluation_order"] for r in v2.manifest()["rules"]] == [100, 200]
    assert [r["rule_id"] for r in v3.manifest()["rules"]] == [
        "COR-SEQ-001",
        "COR-SEQ-002",
        "COR-SEQ-003",
    ]
    assert [r["evaluation_order"] for r in v3.manifest()["rules"]] == [100, 200, 300]
    assert COR_SEQ_003.rule_id == "COR-SEQ-003"


def test_unknown_builtin_ruleset_version_does_not_fallback_to_v1():
    with pytest.raises(CorrelationRulesetNotFound):
        resolve_correlation_rule_registry("correlis-sequence", "99")
