from datetime import UTC, datetime

import pytest
from correlis_schema import (
    EntityRef,
    EntityType,
    EvidenceRef,
    EvidenceType,
    Observation,
    ProvenanceClass,
    Relationship,
    RelationshipType,
)
from pydantic import ValidationError

EVIDENCE = EvidenceRef(
    type=EvidenceType.RAW_EVENT,
    source="test",
    locator="test://event/1",
    sha256="a" * 64,
)


def test_observation_requires_object_for_relationship():
    with pytest.raises(ValidationError):
        Observation(
            tenant_id="demo",
            event_time=datetime.now(UTC),
            source="test",
            sensor_id="sensor:test",
            event_class="network_activity",
            activity="connection",
            subject=EntityRef(id="ip:1", type=EntityType.IP_ADDRESS, label="1.1.1.1"),
            relationship=RelationshipType.TARGETED,
            evidence=[EVIDENCE],
        )


def test_derived_relationship_requires_rule_id():
    with pytest.raises(ValidationError):
        Relationship(
            id="rel-1",
            tenant_id="demo",
            source_entity_id="ip:1",
            target_entity_id="asset:1",
            type=RelationshipType.EXPLOITED,
            provenance=ProvenanceClass.DETERMINISTIC,
            confidence=0.8,
            first_seen=datetime.now(UTC),
            last_seen=datetime.now(UTC),
            evidence_refs=[EVIDENCE.id],
        )
