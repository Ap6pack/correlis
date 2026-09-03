from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from correlis_schema import IncidentState


@dataclass(frozen=True, slots=True)
class ProjectedAttackScene:
    projection_version: str
    tenant_id: str
    scene_id: str
    entity_projection_version: str
    relationship_projection_version: str
    title: str
    state: IncidentState
    first_seen: datetime
    last_seen: datetime
    first_ingest_sequence: int
    last_ingest_sequence: int
    summary: str | None
    uncertainty: tuple[str, ...]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ProjectedAttackScenePage:
    items: tuple[ProjectedAttackScene, ...]
    next_after_scene_id: str | None
    has_more: bool


@dataclass(frozen=True, slots=True)
class AttackSceneEntityMembership:
    projection_version: str
    tenant_id: str
    scene_id: str
    entity_projection_version: str
    entity_id: str
    first_seen: datetime
    last_seen: datetime
    first_ingest_sequence: int
    last_ingest_sequence: int


@dataclass(frozen=True, slots=True)
class AttackSceneRelationshipMembership:
    projection_version: str
    tenant_id: str
    scene_id: str
    relationship_projection_version: str
    relationship_id: str
    first_seen: datetime
    last_seen: datetime
    first_ingest_sequence: int
    last_ingest_sequence: int


@dataclass(frozen=True, slots=True)
class AttackSceneObservationMembership:
    projection_version: str
    tenant_id: str
    scene_id: str
    observation_id: str
    ingest_sequence: int
    event_time: datetime


@dataclass(frozen=True, slots=True)
class AttackSceneStateTransition:
    projection_version: str
    tenant_id: str
    scene_id: str
    ingest_sequence: int
    trigger_observation_id: str
    from_state: IncidentState
    to_state: IncidentState
    reason_code: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AttackSceneLineage:
    scene: ProjectedAttackScene
    entities: tuple[AttackSceneEntityMembership, ...]
    relationships: tuple[AttackSceneRelationshipMembership, ...]
    observations: tuple[AttackSceneObservationMembership, ...]
    state_transitions: tuple[AttackSceneStateTransition, ...]
