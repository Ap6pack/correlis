from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from correlis_schema import (
    AttackScene,
    EntityRef,
    EventClass,
    IncidentState,
    Observation,
    ProvenanceClass,
    Relationship,
    RelationshipType,
    SceneDelta,
)


def relationship_id(
    tenant_id: str,
    source_id: str,
    relation: RelationshipType,
    target_id: str,
    provenance: ProvenanceClass,
    rule_id: str | None,
) -> str:
    material = "|".join(
        [tenant_id, source_id, relation.value, target_id, provenance.value, rule_id or "direct"]
    )
    return hashlib.sha256(material.encode()).hexdigest()[:32]


class SceneBuilder:
    def __init__(self, scene_id: str, tenant_id: str, title: str) -> None:
        now = datetime.min.replace(tzinfo=UTC)
        self.scene = AttackScene(
            id=scene_id,
            tenant_id=tenant_id,
            title=title,
            created_at=now,
            updated_at=now,
        )
        self._seen_observations: set[str] = set()

    def apply(self, observation: Observation) -> SceneDelta:
        if observation.id in self._seen_observations:
            return SceneDelta(
                scene_id=self.scene.id,
                observation=observation,
                state=self.scene.state,
            )
        if observation.tenant_id != self.scene.tenant_id:
            raise ValueError("observation tenant does not match attack scene tenant")

        self._seen_observations.add(observation.id)
        self._set_scene_times(observation)

        entities = [observation.subject]
        if observation.object is not None:
            entities.append(observation.object)
        for entity in entities:
            self.scene.entities[entity.id] = entity

        relationships: list[Relationship] = []
        direct = self._direct_relationship(observation)
        if direct is not None:
            relationships.append(self._upsert_relationship(direct))

        for derived in self._derive(observation):
            relationships.append(self._upsert_relationship(derived))

        self.scene.observations.append(observation)
        self._advance_state(observation)

        return SceneDelta(
            scene_id=self.scene.id,
            observation=observation,
            upsert_entities=entities,
            upsert_relationships=relationships,
            state=self.scene.state,
        )

    def _set_scene_times(self, observation: Observation) -> None:
        if not self.scene.observations:
            self.scene.created_at = observation.event_time
        self.scene.updated_at = max(self.scene.updated_at, observation.event_time)

    def _direct_relationship(self, observation: Observation) -> Relationship | None:
        if observation.relationship is None or observation.object is None:
            return None
        return Relationship(
            id=relationship_id(
                observation.tenant_id,
                observation.subject.id,
                observation.relationship,
                observation.object.id,
                ProvenanceClass.OBSERVED,
                None,
            ),
            tenant_id=observation.tenant_id,
            source_entity_id=observation.subject.id,
            target_entity_id=observation.object.id,
            type=observation.relationship,
            provenance=ProvenanceClass.OBSERVED,
            confidence=observation.confidence,
            first_seen=observation.event_time,
            last_seen=observation.event_time,
            evidence_refs=[item.id for item in observation.evidence],
            attributes={"observation_id": observation.id},
        )

    def _derive(self, observation: Observation) -> list[Relationship]:
        derived: list[Relationship] = []
        target = observation.object

        if observation.activity == "exploit_attempt" and target is not None:
            vulnerable = any(
                rel.type == RelationshipType.HAS_VULNERABILITY and rel.source_entity_id == target.id
                for rel in self.scene.relationships.values()
            )
            if vulnerable:
                derived.append(
                    self._derived_relationship(
                        observation,
                        observation.subject,
                        target,
                        RelationshipType.EXPLOITED,
                        confidence=0.85,
                        rule_id="COR-SEQ-001",
                        reason=(
                            "exploit activity targeted an asset with an observed exposure finding"
                        ),
                    )
                )

        if observation.event_class == EventClass.PROCESS_ACTIVITY and target is not None:
            attack_source = observation.correlation_keys.get("attack_source")
            exploited = self._find_relationship(
                RelationshipType.EXPLOITED,
                source_entity_id=attack_source,
                target_entity_id=target.id,
            )
            if exploited and observation.attributes.get("suspicious_child", False):
                source_entity = self.scene.entities.get(attack_source)
                if source_entity is not None:
                    derived.append(
                        self._derived_relationship(
                            observation,
                            source_entity,
                            target,
                            RelationshipType.COMPROMISED,
                            confidence=0.92,
                            rule_id="COR-SEQ-002",
                            reason=(
                                "exploit sequence followed by suspicious server-side "
                                "process execution"
                            ),
                            extra_evidence=exploited.evidence_refs,
                        )
                    )

        if observation.event_class == EventClass.AUTHENTICATION and target is not None:
            compromised_source = self._find_relationship(
                RelationshipType.COMPROMISED,
                target_entity_id=observation.subject.id,
            )
            if compromised_source:
                derived.append(
                    self._derived_relationship(
                        observation,
                        observation.subject,
                        target,
                        RelationshipType.MOVED_LATERALLY_TO,
                        confidence=0.90,
                        rule_id="COR-SEQ-003",
                        reason="a compromised source asset authenticated to a second asset",
                        extra_evidence=compromised_source.evidence_refs,
                    )
                )

        return derived

    def _derived_relationship(
        self,
        observation: Observation,
        source: EntityRef,
        target: EntityRef,
        relation: RelationshipType,
        confidence: float,
        rule_id: str,
        reason: str,
        extra_evidence: list[str] | None = None,
    ) -> Relationship:
        evidence_refs = list(
            dict.fromkeys([*(extra_evidence or []), *[e.id for e in observation.evidence]])
        )
        return Relationship(
            id=relationship_id(
                observation.tenant_id,
                source.id,
                relation,
                target.id,
                ProvenanceClass.DETERMINISTIC,
                rule_id,
            ),
            tenant_id=observation.tenant_id,
            source_entity_id=source.id,
            target_entity_id=target.id,
            type=relation,
            provenance=ProvenanceClass.DETERMINISTIC,
            confidence=confidence,
            first_seen=observation.event_time,
            last_seen=observation.event_time,
            evidence_refs=evidence_refs,
            rule_id=rule_id,
            attributes={"reason": reason, "trigger_observation_id": observation.id},
        )

    def _find_relationship(
        self,
        relation: RelationshipType,
        source_entity_id: str | None = None,
        target_entity_id: str | None = None,
    ) -> Relationship | None:
        if source_entity_id is None and target_entity_id is None:
            return None
        for item in self.scene.relationships.values():
            if item.type != relation:
                continue
            if source_entity_id is not None and item.source_entity_id != source_entity_id:
                continue
            if target_entity_id is not None and item.target_entity_id != target_entity_id:
                continue
            return item
        return None

    def _upsert_relationship(self, relationship: Relationship) -> Relationship:
        existing = self.scene.relationships.get(relationship.id)
        if existing is not None:
            existing.last_seen = max(existing.last_seen, relationship.last_seen)
            existing.confidence = max(existing.confidence, relationship.confidence)
            existing.evidence_refs = list(
                dict.fromkeys([*existing.evidence_refs, *relationship.evidence_refs])
            )
            return existing
        self.scene.relationships[relationship.id] = relationship
        return relationship

    def _advance_state(self, observation: Observation) -> None:
        if observation.attributes.get("confirmed_compromise", False):
            self.scene.state = IncidentState.CONFIRMED
            return
        if (
            observation.event_class != EventClass.EXPOSURE_FINDING
            and self.scene.state == IncidentState.POTENTIAL
        ):
            self.scene.state = IncidentState.OBSERVED


def build_scene(name: str, observations: list[Observation]) -> AttackScene:
    if not observations:
        raise ValueError("cannot build an attack scene without observations")
    tenant_id = observations[0].tenant_id
    builder = SceneBuilder(
        scene_id=f"scene:{name}",
        tenant_id=tenant_id,
        title=name.replace("-", " ").title(),
    )
    for observation in observations:
        builder.apply(observation)
    return builder.scene
