from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from correlis_schema import RelationshipType

BUILTIN_CORRELATION_RULESET_NAME = "correlis-sequence"
BUILTIN_CORRELATION_RULESET_VERSION = "1"


@dataclass(frozen=True, slots=True)
class CorrelationRuleDefinition:
    rule_id: str
    rule_version: str
    display_name: str
    description: str
    reason_code: str
    output_relationship_type: RelationshipType
    confidence: float
    evaluation_order: int

    def __post_init__(self) -> None:
        if not self.rule_id.strip():
            raise ValueError("rule_id must be nonblank")
        if not self.rule_version.strip():
            raise ValueError("rule_version must be nonblank")
        if not self.reason_code.strip():
            raise ValueError("reason_code must be nonblank")
        if self.confidence < 0 or self.confidence > 1:
            raise ValueError("confidence must be between 0 and 1")
        if self.evaluation_order <= 0:
            raise ValueError("evaluation_order must be positive")


class CorrelationRulesetNotFound(LookupError):
    pass


class CorrelationRuleRegistry:
    def __init__(self, *, name: str, version: str, definitions) -> None:
        if not name.strip():
            raise ValueError("ruleset name must be nonblank")
        if not version.strip():
            raise ValueError("ruleset version must be nonblank")
        self._name = name
        self._version = version
        self._definitions = tuple(definitions)
        rule_ids = [d.rule_id for d in self._definitions]
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("duplicate correlation rule IDs")
        orders = [d.evaluation_order for d in self._definitions]
        if len(orders) != len(set(orders)):
            raise ValueError("duplicate correlation rule evaluation orders")

    @property
    def name(self) -> str:
        return self._name

    @property
    def version(self) -> str:
        return self._version

    def definitions(self) -> tuple[CorrelationRuleDefinition, ...]:
        return self._definitions

    def manifest(self) -> dict[str, object]:
        return {
            "ruleset_name": self._name,
            "ruleset_version": self._version,
            "rules": [
                {
                    "rule_id": d.rule_id,
                    "rule_version": d.rule_version,
                    "display_name": d.display_name,
                    "description": d.description,
                    "reason_code": d.reason_code,
                    "output_relationship_type": d.output_relationship_type.value,
                    "confidence": d.confidence,
                    "evaluation_order": d.evaluation_order,
                }
                for d in sorted(self._definitions, key=lambda item: item.evaluation_order)
            ],
        }

    def manifest_sha256(self) -> str:
        encoded = json.dumps(self.manifest(), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


class CorrelationRuleCatalog:
    def __init__(self, registries: tuple[CorrelationRuleRegistry, ...]) -> None:
        self._registries = tuple(registries)
        identities: set[tuple[str, str]] = set()
        for registry in self._registries:
            identity = (registry.name, registry.version)
            if identity in identities:
                raise ValueError(
                    f"duplicate correlation ruleset identity: {registry.name}/{registry.version}"
                )
            identities.add(identity)
        self._by_identity = {
            (registry.name, registry.version): registry for registry in self._registries
        }

    def get(
        self, ruleset_name: str, ruleset_version: str
    ) -> CorrelationRuleRegistry | None:
        return self._by_identity.get((ruleset_name, ruleset_version))

    def require(self, ruleset_name: str, ruleset_version: str) -> CorrelationRuleRegistry:
        registry = self.get(ruleset_name, ruleset_version)
        if registry is None:
            raise CorrelationRulesetNotFound(
                f"correlation ruleset not found: {ruleset_name}/{ruleset_version}"
            )
        return registry

    def list(self) -> tuple[CorrelationRuleRegistry, ...]:
        return self._registries


BUILTIN_CORRELATION_RULES = CorrelationRuleRegistry(
    name=BUILTIN_CORRELATION_RULESET_NAME,
    version=BUILTIN_CORRELATION_RULESET_VERSION,
    definitions=(
        CorrelationRuleDefinition(
            rule_id="COR-SEQ-001",
            rule_version="1",
            display_name="Exploit against known vulnerability",
            description=(
                "Defines the future deterministic relationship from exploit activity to "
                "a known vulnerability."
            ),
            reason_code="exploit_against_known_vulnerability",
            output_relationship_type=RelationshipType.EXPLOITED,
            confidence=0.85,
            evaluation_order=100,
        ),
    ),
)


BUILTIN_CORRELATION_RULE_CATALOG = CorrelationRuleCatalog((BUILTIN_CORRELATION_RULES,))


def resolve_correlation_rule_registry(
    ruleset_name: str, ruleset_version: str
) -> CorrelationRuleRegistry:
    return BUILTIN_CORRELATION_RULE_CATALOG.require(ruleset_name, ruleset_version)
