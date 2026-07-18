from __future__ import annotations

from datetime import datetime

from correlis_schema import (
    ActionActorKind,
    ActionTargetType,
    EntityRef,
    EntityType,
    EventClass,
    Observation,
    OperationalAction,
    Severity,
    utc_now,
)

from .core import CORE_ONTOLOGY
from .errors import OntologyValidationError
from .registry import OntologyRegistry


def operational_action_to_observation(
    action: OperationalAction,
    *,
    actor_entity: EntityRef,
    target_entity: EntityRef | None = None,
    registry: OntologyRegistry = CORE_ONTOLOGY,
    ingest_time: datetime | None = None,
) -> Observation:
    registry.validate_action(action)
    if actor_entity.id != action.actor.id:
        raise OntologyValidationError(
            "action_actor_entity_mismatch", "action actor entity id does not match actor id"
        )
    if action.actor.kind == ActionActorKind.ANALYST and actor_entity.type != EntityType.IDENTITY:
        raise OntologyValidationError(
            "action_actor_entity_type_not_allowed",
            "analyst actor entity must be an identity",
            actor_type=actor_entity.type,
        )
    if action.actor.kind in {
        ActionActorKind.SERVICE,
        ActionActorKind.SYSTEM,
    } and actor_entity.type not in {EntityType.IDENTITY, EntityType.APPLICATION}:
        raise OntologyValidationError(
            "action_actor_entity_type_not_allowed",
            "service or system actor entity must be identity or application",
            actor_type=actor_entity.type,
        )
    registry.validate_entity(actor_entity)

    observation_object = None
    if action.target.type == ActionTargetType.ENTITY:
        if target_entity is None:
            raise OntologyValidationError(
                "action_target_entity_required", "entity target requires a target entity"
            )
        if target_entity.id != action.target.id:
            raise OntologyValidationError(
                "action_target_entity_mismatch", "target entity id does not match action target id"
            )
        registry.validate_entity(target_entity)
        observation_object = target_entity

    return Observation(
        id=f"action:{action.id}",
        tenant_id=action.tenant_id,
        event_time=action.occurred_at,
        ingest_time=ingest_time or utc_now(),
        source="correlis.action",
        sensor_id=f"{action.actor.kind.value}:{action.actor.id}",
        event_class=EventClass.ANALYST_ACTION,
        activity=action.type.value,
        severity=Severity.INFORMATIONAL,
        confidence=1.0,
        subject=actor_entity,
        object=observation_object,
        relationship=None,
        evidence=list(action.evidence),
        correlation_keys={
            "action_id": action.id,
            "action_target_type": action.target.type.value,
            "action_target_id": action.target.id,
        },
        attributes={
            "action_type": action.type.value,
            "actor_kind": action.actor.kind.value,
            "actor_display_name": action.actor.display_name,
            "target": {"type": action.target.type.value, "id": action.target.id},
            "reason": action.reason,
            "action_attributes": dict(action.attributes),
        },
    )
