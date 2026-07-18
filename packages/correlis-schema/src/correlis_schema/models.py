from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


class Severity(StrEnum):
    INFORMATIONAL = "informational"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ProvenanceClass(StrEnum):
    OBSERVED = "observed"
    DETERMINISTIC = "deterministic"
    ANALYTIC = "analytic"
    AI_SUGGESTED = "ai_suggested"
    ANALYST_CONFIRMED = "analyst_confirmed"
    ANALYST_REJECTED = "analyst_rejected"


class EntityType(StrEnum):
    ASSET = "asset"
    APPLICATION = "application"
    IDENTITY = "identity"
    PROCESS = "process"
    NETWORK_ENDPOINT = "network_endpoint"
    CLOUD_RESOURCE = "cloud_resource"
    VULNERABILITY = "vulnerability"
    IP_ADDRESS = "ip_address"
    DOMAIN = "domain"
    FILE = "file"
    CERTIFICATE = "certificate"
    DATA_STORE = "data_store"


class EventClass(StrEnum):
    EXPOSURE_FINDING = "exposure_finding"
    NETWORK_ACTIVITY = "network_activity"
    PROCESS_ACTIVITY = "process_activity"
    AUTHENTICATION = "authentication"
    DATA_ACCESS = "data_access"
    CLOUD_ACTIVITY = "cloud_activity"
    THREAT_INTEL = "threat_intel"
    ANALYST_ACTION = "analyst_action"


class EvidenceType(StrEnum):
    RAW_EVENT = "raw_event"
    CONFIG_SNAPSHOT = "config_snapshot"
    SCANNER_FINDING = "scanner_finding"
    THREAT_INTEL_RECORD = "threat_intel_record"
    ANALYST_NOTE = "analyst_note"
    DERIVED_ARTIFACT = "derived_artifact"


class RelationshipType(StrEnum):
    HAS_VULNERABILITY = "has_vulnerability"
    TARGETED = "targeted"
    COMMUNICATES_WITH = "communicates_with"
    RUNS_ON = "runs_on"
    SPAWNED = "spawned"
    AUTHENTICATED_TO = "authenticated_to"
    ACCESSED = "accessed"
    RESOLVED_TO = "resolved_to"
    EXPLOITED = "exploited"
    COMPROMISED = "compromised"
    MOVED_LATERALLY_TO = "moved_laterally_to"


class IncidentState(StrEnum):
    POTENTIAL = "potential"
    OBSERVED = "observed"
    CONFIRMED = "confirmed"
    CONTAINED = "contained"
    CLOSED = "closed"


class GeoPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    country_code: str | None = Field(default=None, min_length=2, max_length=2)


class EntityRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=3, max_length=256)
    type: EntityType
    label: str = Field(min_length=1, max_length=256)
    attributes: dict[str, Any] = Field(default_factory=dict)


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid4()))
    type: EvidenceType
    source: str = Field(min_length=1, max_length=128)
    locator: str = Field(min_length=1, max_length=2048)
    sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    collected_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid4()))
    tenant_id: str = Field(min_length=1, max_length=128)
    event_time: datetime
    ingest_time: datetime = Field(default_factory=utc_now)
    source: str = Field(min_length=1, max_length=128)
    sensor_id: str = Field(min_length=1, max_length=256)
    event_class: EventClass
    activity: str = Field(min_length=1, max_length=128)
    severity: Severity = Severity.INFORMATIONAL
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    subject: EntityRef
    object: EntityRef | None = None
    relationship: RelationshipType | None = None
    evidence: list[EvidenceRef] = Field(min_length=1)
    attack_techniques: list[str] = Field(default_factory=list)
    correlation_keys: dict[str, str] = Field(default_factory=dict)
    attributes: dict[str, Any] = Field(default_factory=dict)
    geo: GeoPoint | None = None

    @model_validator(mode="after")
    def validate_direct_relationship(self) -> Observation:
        if self.relationship is not None and self.object is None:
            raise ValueError("a direct relationship requires an object entity")
        return self


class Relationship(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    tenant_id: str
    source_entity_id: str
    target_entity_id: str
    type: RelationshipType
    provenance: ProvenanceClass
    confidence: float = Field(ge=0.0, le=1.0)
    first_seen: datetime
    last_seen: datetime
    evidence_refs: list[str] = Field(min_length=1)
    rule_id: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_derivation(self) -> Relationship:
        derived = {
            ProvenanceClass.DETERMINISTIC,
            ProvenanceClass.ANALYTIC,
            ProvenanceClass.AI_SUGGESTED,
        }
        if self.provenance in derived and not self.rule_id:
            raise ValueError("derived relationships require a rule_id")
        return self


class SceneDelta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_id: str
    observation: Observation
    upsert_entities: list[EntityRef] = Field(default_factory=list)
    upsert_relationships: list[Relationship] = Field(default_factory=list)
    state: IncidentState


class AttackScene(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    tenant_id: str
    title: str
    state: IncidentState = IncidentState.POTENTIAL
    created_at: datetime
    updated_at: datetime
    entities: dict[str, EntityRef] = Field(default_factory=dict)
    relationships: dict[str, Relationship] = Field(default_factory=dict)
    observations: list[Observation] = Field(default_factory=list)
    summary: str | None = None
    uncertainty: list[str] = Field(default_factory=list)
