from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from correlis_schema import EntityType


@dataclass(frozen=True, slots=True)
class ProjectedEntity:
    projection_version: str
    tenant_id: str
    entity_id: str
    canonical_key: str
    entity_type: EntityType
    label: str
    attributes: dict[str, Any]
    ontology_name: str
    ontology_version: str
    first_seen: datetime
    last_seen: datetime
    first_ingest_sequence: int
    last_ingest_sequence: int
    latest_claim_event_time: datetime
    latest_claim_ingest_sequence: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class EntityObservationLineage:
    projection_version: str
    tenant_id: str
    entity_id: str
    observation_id: str
    role: str
    ingest_sequence: int
    event_time: datetime
    source: str
    sensor_id: str


@dataclass(frozen=True, slots=True)
class EntityEvidenceLineage:
    projection_version: str
    tenant_id: str
    entity_id: str
    evidence_id: str
    first_seen: datetime
    last_seen: datetime
    first_ingest_sequence: int
    last_ingest_sequence: int


@dataclass(frozen=True, slots=True)
class EntityIdentityClaim:
    projection_version: str
    tenant_id: str
    entity_id: str
    entity_type: EntityType
    identity_key_name: str
    value_sha256: str
    value: dict[str, Any]
    first_seen: datetime
    last_seen: datetime
    first_ingest_sequence: int
    last_ingest_sequence: int


@dataclass(frozen=True, slots=True)
class EntityLineage:
    entity: ProjectedEntity
    observations: list[EntityObservationLineage]
    evidence: list[EntityEvidenceLineage]
    identity_claims: list[EntityIdentityClaim]


@dataclass(frozen=True, slots=True)
class ProjectedEntityPage:
    items: list[ProjectedEntity]
    after_entity_id: str | None
    next_entity_id: str | None
    has_more: bool
