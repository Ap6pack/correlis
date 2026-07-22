from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from correlis_schema import EntityType, ProvenanceClass, RelationshipType


@dataclass(frozen=True, slots=True)
class ProjectedRelationship:
    projection_version: str
    tenant_id: str
    relationship_id: str
    relationship_type: RelationshipType
    provenance: ProvenanceClass
    source_entity_id: str
    source_entity_type: EntityType
    target_entity_id: str
    target_entity_type: EntityType
    confidence: float
    ontology_name: str
    ontology_version: str
    first_seen: datetime
    last_seen: datetime
    first_ingest_sequence: int
    last_ingest_sequence: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class RelationshipObservationLineage:
    projection_version: str
    tenant_id: str
    relationship_id: str
    observation_id: str
    ingest_sequence: int
    event_time: datetime
    source: str
    sensor_id: str
    observation_confidence: float


@dataclass(frozen=True, slots=True)
class RelationshipEvidenceLineage:
    projection_version: str
    tenant_id: str
    relationship_id: str
    evidence_id: str
    first_seen: datetime
    last_seen: datetime
    first_ingest_sequence: int
    last_ingest_sequence: int


@dataclass(frozen=True, slots=True)
class RelationshipLineage:
    relationship: ProjectedRelationship
    observations: list[RelationshipObservationLineage]
    evidence: list[RelationshipEvidenceLineage]


@dataclass(frozen=True, slots=True)
class ProjectedRelationshipPage:
    items: list[ProjectedRelationship]
    after_relationship_id: str | None
    next_relationship_id: str | None
    has_more: bool
