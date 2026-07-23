from __future__ import annotations

import pytest
from correlis_schema import RelationshipType
from correlis_store import (
    BUILTIN_CORRELATION_RULE_CATALOG,
    BUILTIN_CORRELATION_RULES,
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


def test_builtin_catalog_contains_only_version_one_registry_and_manifest_is_unchanged():
    registries = BUILTIN_CORRELATION_RULE_CATALOG.list()
    assert registries == (BUILTIN_CORRELATION_RULES,)
    assert [(r.name, r.version) for r in registries] == [("correlis-sequence", "1")]
    assert BUILTIN_CORRELATION_RULES.manifest() == EXPECTED_V1_MANIFEST
    assert BUILTIN_CORRELATION_RULES.manifest_sha256() == EXPECTED_V1_MANIFEST_SHA256


def test_exact_builtin_ruleset_resolution_and_no_fallbacks():
    assert resolve_correlation_rule_registry("correlis-sequence", "1") is BUILTIN_CORRELATION_RULES
    with pytest.raises(CorrelationRulesetNotFound):
        resolve_correlation_rule_registry("missing", "1")
    with pytest.raises(CorrelationRulesetNotFound):
        resolve_correlation_rule_registry("correlis-sequence", "2")


def test_duplicate_registry_identity_fails_and_listing_order_is_deterministic():
    one = _registry("catalog-test", "1")
    two = _registry("catalog-test", "2")
    assert CorrelationRuleCatalog((two, one)).list() == (two, one)
    with pytest.raises(ValueError, match="duplicate correlation ruleset identity"):
        CorrelationRuleCatalog((one, one))
    with pytest.raises(ValueError, match="duplicate correlation ruleset identity"):
        CorrelationRuleCatalog((one, _registry("catalog-test", "1")))
