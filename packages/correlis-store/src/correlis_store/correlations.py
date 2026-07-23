from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime

from .projections import ProjectorIdentity


@dataclass(frozen=True, slots=True)
class CorrelationProjectionConfig:
    identity: ProjectorIdentity
    relationship_projection_version: str
    ruleset_name: str
    ruleset_version: str
    rule_manifest_sha256: str
    rule_manifest: dict[str, object] = field(repr=False)
    ontology_name: str
    ontology_version: str
    created_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "rule_manifest", deepcopy(self.rule_manifest))


@dataclass(frozen=True, slots=True)
class RelationshipDerivation:
    relationship_projection_version: str
    tenant_id: str
    relationship_id: str
    trigger_observation_id: str
    correlation_projection_version: str
    rule_id: str
    rule_version: str
    trigger_ingest_sequence: int
    event_time: datetime
    confidence: float
    reason_code: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RelationshipDerivationSupport:
    relationship_projection_version: str
    tenant_id: str
    relationship_id: str
    trigger_observation_id: str
    support_relationship_id: str


@dataclass(frozen=True, slots=True)
class RelationshipDerivationEvidence:
    relationship_projection_version: str
    tenant_id: str
    relationship_id: str
    trigger_observation_id: str
    evidence_id: str
    evidence_role: str
