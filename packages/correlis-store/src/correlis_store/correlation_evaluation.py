from __future__ import annotations

from dataclasses import dataclass

from correlis_schema import EntityType, ProvenanceClass, RelationshipType


def _unique_sorted(values) -> tuple[str, ...]:
    return tuple(sorted(set(values)))


@dataclass(frozen=True, slots=True)
class CorrelationRelationshipFact:
    relationship_id: str
    relationship_type: RelationshipType
    provenance: ProvenanceClass
    source_entity_id: str
    source_entity_type: EntityType
    target_entity_id: str
    target_entity_type: EntityType
    first_qualifying_ingest_sequence: int


@dataclass(frozen=True, slots=True)
class DerivedRelationshipCandidate:
    rule_id: str
    rule_version: str
    reason_code: str
    relationship_type: RelationshipType
    source_entity_id: str
    source_entity_type: EntityType
    target_entity_id: str
    target_entity_type: EntityType
    confidence: float
    supporting_relationship_ids: tuple[str, ...]
    trigger_evidence_ids: tuple[str, ...]
    supporting_evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "supporting_relationship_ids", _unique_sorted(self.supporting_relationship_ids)
        )
        object.__setattr__(self, "trigger_evidence_ids", _unique_sorted(self.trigger_evidence_ids))
        object.__setattr__(
            self, "supporting_evidence_ids", _unique_sorted(self.supporting_evidence_ids)
        )
