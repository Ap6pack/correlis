from __future__ import annotations

from datetime import UTC, datetime

import pytest
from correlis_ontology import (
    CORE_ONTOLOGY,
    OntologyValidationError,
    operational_action_to_observation,
)
from correlis_schema import (
    ActionActor,
    ActionActorKind,
    ActionTarget,
    ActionTargetType,
    EntityRef,
    EntityType,
    EventClass,
    EvidenceRef,
    EvidenceType,
    OperationalAction,
    OperationalActionType,
)


def evidence() -> EvidenceRef:
    return EvidenceRef(
        type=EvidenceType.ANALYST_NOTE, source="test", locator="note:1", sha256="a" * 64
    )


def action(**overrides) -> OperationalAction:
    values = dict(
        id="act-1",
        tenant_id="tenant",
        type=OperationalActionType.CONFIRM_RELATIONSHIP,
        actor=ActionActor(id="identity:analyst", kind=ActionActorKind.ANALYST),
        target=ActionTarget(type=ActionTargetType.RELATIONSHIP, id="rel-1"),
        occurred_at=datetime(2024, 1, 1, tzinfo=UTC),
        reason="reviewed evidence",
        evidence=[evidence()],
        attributes={"ticket": "T-1"},
    )
    values.update(overrides)
    return OperationalAction(**values)


def identity_actor() -> EntityRef:
    return EntityRef(id="identity:analyst", type=EntityType.IDENTITY, label="Analyst")


def test_every_action_has_policy():
    assert {item.type for item in CORE_ONTOLOGY.manifest().action_types} == set(
        OperationalActionType
    )
    assert all(
        item.requires_evidence and item.emits_observation
        for item in CORE_ONTOLOGY.manifest().action_types
    )


def test_invalid_action_target_rejected():
    bad = action(target=ActionTarget(type=ActionTargetType.ENTITY, id="asset:web-01"))
    with pytest.raises(OntologyValidationError) as exc:
        CORE_ONTOLOGY.validate_action(bad)
    assert exc.value.code == "action_target_type_not_allowed"


def test_required_reason_and_evidence_policies():
    with pytest.raises(OntologyValidationError) as exc:
        CORE_ONTOLOGY.validate_action(action(reason="   "))
    assert exc.value.code == "action_reason_required"
    no_evidence = action().model_copy(update={"evidence": []})
    with pytest.raises(OntologyValidationError) as exc:
        CORE_ONTOLOGY.validate_action(no_evidence)
    assert exc.value.code == "action_evidence_required"
    CORE_ONTOLOGY.validate_action(
        action(
            type=OperationalActionType.ASSIGN_OWNER,
            target=ActionTarget(type=ActionTargetType.INCIDENT, id="inc-1"),
            reason=None,
        )
    )


def test_operational_action_conversion_is_canonical_for_entity_target():
    target = EntityRef(id="asset:web-01", type=EntityType.ASSET, label="web-01")
    source_action = action(
        type=OperationalActionType.MARK_CONTAINED,
        target=ActionTarget(type=ActionTargetType.ENTITY, id=target.id),
    )
    converted = operational_action_to_observation(
        source_action,
        actor_entity=identity_actor(),
        target_entity=target,
        ingest_time=datetime(2024, 1, 2, tzinfo=UTC),
    )
    assert converted.id == "action:act-1"
    assert converted.event_class == EventClass.ANALYST_ACTION
    assert converted.subject == identity_actor()
    assert converted.object == target
    assert converted.evidence[0].id == source_action.evidence[0].id
    assert converted.correlation_keys == {
        "action_id": "act-1",
        "action_target_type": "entity",
        "action_target_id": target.id,
    }
    assert converted.attributes["reason"] == "reviewed evidence"
    assert converted.attributes["action_attributes"] == {"ticket": "T-1"}


def test_non_entity_target_does_not_create_fake_object():
    converted = operational_action_to_observation(
        action(), actor_entity=identity_actor(), ingest_time=datetime(2024, 1, 2, tzinfo=UTC)
    )
    assert converted.object is None


def test_actor_entity_rules_and_mismatches():
    with pytest.raises(OntologyValidationError):
        operational_action_to_observation(
            action(), actor_entity=EntityRef(id="asset:web-01", type=EntityType.ASSET, label="web")
        )
    with pytest.raises(OntologyValidationError):
        operational_action_to_observation(
            action(),
            actor_entity=EntityRef(id="identity:other", type=EntityType.IDENTITY, label="Other"),
        )
