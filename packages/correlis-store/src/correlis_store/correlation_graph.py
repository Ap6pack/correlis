from __future__ import annotations

from correlis_ontology import CORE_ONTOLOGY, OntologyValidationError
from correlis_schema import (
    EntityRef,
    EntityType,
    EventClass,
    ProvenanceClass,
    RelationshipType,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .correlation_evaluation import (
    CorrelationRelationshipFact,
    DerivedRelationshipCandidate,
)
from .correlation_rules import BUILTIN_CORRELATION_RULES, COR_SEQ_002
from .models import (
    ObservationEvidenceRecord,
    RelationshipObservationRecord,
    RelationshipRecord,
)
from .observation_sequence import SequencedObservation
from .projections import ProjectionInvariantError


class CorrelationGraphInvariantError(ProjectionInvariantError):
    pass


class CorrelationGraphReader:
    def __init__(self, session: Session):
        self._session = session

    def find_prior_observed_vulnerabilities(
        self,
        *,
        relationship_projection_version: str,
        tenant_id: str,
        vulnerable_entity_id: str,
        before_ingest_sequence: int,
    ) -> tuple[CorrelationRelationshipFact, ...]:
        first_qualifying = func.min(
            RelationshipObservationRecord.ingest_sequence
        ).label("first_qualifying_ingest_sequence")
        stmt = (
            select(RelationshipRecord, first_qualifying)
            .join(
                RelationshipObservationRecord,
                (
                    RelationshipObservationRecord.projection_version
                    == RelationshipRecord.projection_version
                )
                & (
                    RelationshipObservationRecord.tenant_id
                    == RelationshipRecord.tenant_id
                )
                & (
                    RelationshipObservationRecord.relationship_id
                    == RelationshipRecord.relationship_id
                ),
            )
            .where(
                RelationshipRecord.projection_version
                == relationship_projection_version,
                RelationshipRecord.tenant_id == tenant_id,
                RelationshipRecord.relationship_type
                == RelationshipType.HAS_VULNERABILITY.value,
                RelationshipRecord.provenance == ProvenanceClass.OBSERVED.value,
                RelationshipRecord.source_entity_id == vulnerable_entity_id,
                RelationshipObservationRecord.ingest_sequence < before_ingest_sequence,
            )
            .group_by(
                RelationshipRecord.projection_version,
                RelationshipRecord.tenant_id,
                RelationshipRecord.relationship_id,
            )
            .order_by(RelationshipRecord.relationship_id)
        )
        return tuple(
            CorrelationRelationshipFact(
                relationship_id=r.relationship_id,
                relationship_type=RelationshipType(r.relationship_type),
                provenance=ProvenanceClass(r.provenance),
                source_entity_id=r.source_entity_id,
                source_entity_type=EntityType(r.source_entity_type),
                target_entity_id=r.target_entity_id,
                target_entity_type=EntityType(r.target_entity_type),
                first_qualifying_ingest_sequence=int(seq),
            )
            for r, seq in self._session.execute(stmt).all()
        )

    def find_prior_exploits(
        self,
        *,
        relationship_projection_version: str,
        tenant_id: str,
        attack_source_entity_id: str,
        target_entity_id: str,
        before_ingest_sequence: int,
    ) -> tuple[CorrelationRelationshipFact, ...]:
        first_qualifying = func.min(
            RelationshipObservationRecord.ingest_sequence
        ).label("first_qualifying_ingest_sequence")
        stmt = (
            select(RelationshipRecord, first_qualifying)
            .join(
                RelationshipObservationRecord,
                (
                    RelationshipObservationRecord.projection_version
                    == RelationshipRecord.projection_version
                )
                & (
                    RelationshipObservationRecord.tenant_id
                    == RelationshipRecord.tenant_id
                )
                & (
                    RelationshipObservationRecord.relationship_id
                    == RelationshipRecord.relationship_id
                ),
            )
            .where(
                RelationshipRecord.projection_version
                == relationship_projection_version,
                RelationshipRecord.tenant_id == tenant_id,
                RelationshipRecord.relationship_type
                == RelationshipType.EXPLOITED.value,
                RelationshipRecord.provenance.in_(
                    (
                        ProvenanceClass.OBSERVED.value,
                        ProvenanceClass.DETERMINISTIC.value,
                    )
                ),
                RelationshipRecord.source_entity_id == attack_source_entity_id,
                RelationshipRecord.target_entity_id == target_entity_id,
                RelationshipObservationRecord.ingest_sequence < before_ingest_sequence,
            )
            .group_by(
                RelationshipRecord.projection_version,
                RelationshipRecord.tenant_id,
                RelationshipRecord.relationship_id,
            )
            .order_by(RelationshipRecord.relationship_id)
        )
        return tuple(
            CorrelationRelationshipFact(
                relationship_id=r.relationship_id,
                relationship_type=RelationshipType(r.relationship_type),
                provenance=ProvenanceClass(r.provenance),
                source_entity_id=r.source_entity_id,
                source_entity_type=EntityType(r.source_entity_type),
                target_entity_id=r.target_entity_id,
                target_entity_type=EntityType(r.target_entity_type),
                first_qualifying_ingest_sequence=int(seq),
            )
            for r, seq in self._session.execute(stmt).all()
        )

    def evidence_for_prior_relationships(
        self,
        *,
        relationship_projection_version: str,
        tenant_id: str,
        relationship_ids: tuple[str, ...],
        before_ingest_sequence: int,
    ) -> tuple[str, ...]:
        if not relationship_ids:
            return ()
        stmt = (
            select(ObservationEvidenceRecord.evidence_id)
            .join(
                RelationshipObservationRecord,
                (
                    RelationshipObservationRecord.tenant_id
                    == ObservationEvidenceRecord.tenant_id
                )
                & (
                    RelationshipObservationRecord.observation_id
                    == ObservationEvidenceRecord.observation_id
                ),
            )
            .where(
                RelationshipObservationRecord.projection_version
                == relationship_projection_version,
                RelationshipObservationRecord.tenant_id == tenant_id,
                RelationshipObservationRecord.relationship_id.in_(relationship_ids),
                RelationshipObservationRecord.ingest_sequence < before_ingest_sequence,
            )
            .distinct()
            .order_by(ObservationEvidenceRecord.evidence_id)
        )
        return tuple(self._session.scalars(stmt).all())


def _cor_seq_001_definition():
    for definition in BUILTIN_CORRELATION_RULES.definitions():
        if definition.rule_id == "COR-SEQ-001":
            return definition
    raise RuntimeError("COR-SEQ-001 rule definition is not registered")


def evaluate_cor_seq_001(
    graph: CorrelationGraphReader,
    item: SequencedObservation,
    *,
    relationship_projection_version: str,
) -> DerivedRelationshipCandidate | None:
    observation = item.observation
    if observation.activity != "exploit_attempt" or observation.object is None:
        return None
    rule = _cor_seq_001_definition()
    try:
        CORE_ONTOLOGY.validate_edge(
            rule.output_relationship_type, observation.subject, observation.object
        )
    except OntologyValidationError:
        return None
    supporting = graph.find_prior_observed_vulnerabilities(
        relationship_projection_version=relationship_projection_version,
        tenant_id=observation.tenant_id,
        vulnerable_entity_id=observation.object.id,
        before_ingest_sequence=item.ingest_sequence,
    )
    if not supporting:
        return None
    supporting_ids = tuple(f.relationship_id for f in supporting)
    return DerivedRelationshipCandidate(
        rule_id=rule.rule_id,
        rule_version=rule.rule_version,
        reason_code=rule.reason_code,
        relationship_type=rule.output_relationship_type,
        source_entity_id=observation.subject.id,
        source_entity_type=observation.subject.type,
        target_entity_id=observation.object.id,
        target_entity_type=observation.object.type,
        confidence=rule.confidence,
        supporting_relationship_ids=supporting_ids,
        trigger_evidence_ids=tuple(e.id for e in observation.evidence),
        supporting_evidence_ids=graph.evidence_for_prior_relationships(
            relationship_projection_version=relationship_projection_version,
            tenant_id=observation.tenant_id,
            relationship_ids=supporting_ids,
            before_ingest_sequence=item.ingest_sequence,
        ),
    )


def evaluate_cor_seq_002(
    graph: CorrelationGraphReader,
    item: SequencedObservation,
    *,
    relationship_projection_version: str,
) -> DerivedRelationshipCandidate | None:
    observation = item.observation
    if (
        observation.event_class != EventClass.PROCESS_ACTIVITY
        or observation.object is None
        or observation.attributes.get("suspicious_child") is not True
    ):
        return None
    attack_source = observation.correlation_keys.get("attack_source")
    if not isinstance(attack_source, str) or not attack_source.strip():
        return None
    supporting = graph.find_prior_exploits(
        relationship_projection_version=relationship_projection_version,
        tenant_id=observation.tenant_id,
        attack_source_entity_id=attack_source,
        target_entity_id=observation.object.id,
        before_ingest_sequence=item.ingest_sequence,
    )
    if not supporting:
        return None
    source_types = {fact.source_entity_type for fact in supporting}
    target_types = {fact.target_entity_type for fact in supporting}
    if len(source_types) != 1:
        raise CorrelationGraphInvariantError(
            "conflicting source types for prior exploit support"
        )
    if target_types != {observation.object.type}:
        raise CorrelationGraphInvariantError(
            "conflicting target types for prior exploit support"
        )
    source_type = next(iter(source_types))
    try:
        CORE_ONTOLOGY.validate_edge(
            COR_SEQ_002.output_relationship_type,
            EntityRef(id=attack_source, type=source_type, label=attack_source),
            observation.object,
        )
    except OntologyValidationError:
        return None
    supporting_ids = tuple(f.relationship_id for f in supporting)
    return DerivedRelationshipCandidate(
        rule_id=COR_SEQ_002.rule_id,
        rule_version=COR_SEQ_002.rule_version,
        reason_code=COR_SEQ_002.reason_code,
        relationship_type=COR_SEQ_002.output_relationship_type,
        source_entity_id=attack_source,
        source_entity_type=source_type,
        target_entity_id=observation.object.id,
        target_entity_type=observation.object.type,
        confidence=COR_SEQ_002.confidence,
        supporting_relationship_ids=supporting_ids,
        trigger_evidence_ids=tuple(e.id for e in observation.evidence),
        supporting_evidence_ids=graph.evidence_for_prior_relationships(
            relationship_projection_version=relationship_projection_version,
            tenant_id=observation.tenant_id,
            relationship_ids=supporting_ids,
            before_ingest_sequence=item.ingest_sequence,
        ),
    )
