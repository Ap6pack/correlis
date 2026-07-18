from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .models import EvidenceRef, utc_now


class ActionActorKind(StrEnum):
    ANALYST = "analyst"
    SERVICE = "service"
    SYSTEM = "system"


class OperationalActionType(StrEnum):
    CONFIRM_RELATIONSHIP = "confirm_relationship"
    REJECT_RELATIONSHIP = "reject_relationship"
    MARK_CONTAINED = "mark_contained"
    ASSIGN_OWNER = "assign_owner"
    REQUEST_EVIDENCE = "request_evidence"
    SUPPRESS_RELATIONSHIP = "suppress_relationship"
    RERUN_RULE = "rerun_rule"
    EXPORT_EVIDENCE = "export_evidence"
    OPEN_REMEDIATION_TASK = "open_remediation_task"
    RECORD_CONTAINMENT_DECISION = "record_containment_decision"


class ActionTargetType(StrEnum):
    ENTITY = "entity"
    RELATIONSHIP = "relationship"
    ATTACK_SCENE = "attack_scene"
    INCIDENT = "incident"
    OBSERVATION = "observation"
    EVIDENCE = "evidence"
    RULE = "rule"
    REMEDIATION_TASK = "remediation_task"


class ActionActor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=256)
    kind: ActionActorKind
    display_name: str | None = Field(default=None, max_length=256)


class ActionTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: ActionTargetType
    id: str = Field(min_length=1, max_length=512)


class OperationalAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid4()))
    tenant_id: str = Field(min_length=1, max_length=128)
    type: OperationalActionType
    actor: ActionActor
    target: ActionTarget
    occurred_at: datetime = Field(default_factory=utc_now)
    reason: str | None = Field(default=None, max_length=4096)
    evidence: list[EvidenceRef] = Field(min_length=1)
    attributes: dict[str, Any] = Field(default_factory=dict)
