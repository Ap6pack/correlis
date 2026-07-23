from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime

from correlis_ontology import CORE_ONTOLOGY, OntologyRegistry, OntologyValidationError
from correlis_schema import EntityRef, ProvenanceClass, relationship_id
from sqlalchemy.orm import Session

from .correlation_graph import CorrelationGraphReader, evaluate_cor_seq_001
from .correlation_rules import BUILTIN_CORRELATION_RULES, CorrelationRuleRegistry
from .models import (
    CorrelationProjectionConfigRecord,
    ProjectorCheckpointRecord,
    RelationshipDerivationEvidenceRecord,
    RelationshipDerivationRecord,
    RelationshipDerivationSupportRecord,
    RelationshipEvidenceRecord,
    RelationshipObservationRecord,
    RelationshipRecord,
)
from .observation_sequence import SequencedObservation
from .projections import ProjectionInvariantError, ProjectorIdentity
from .relationship_projection import (
    CORRELATION_PROJECTOR_NAME,
    RELATIONSHIP_PROJECTOR_NAME,
    correlation_projector_identity,
)


class CorrelationProjectionNotConfigured(ProjectionInvariantError):
    pass


class CorrelationConfigurationMismatch(ProjectionInvariantError):
    pass


class CorrelationDependencyNotReady(ProjectionInvariantError):
    pass


def _clock() -> datetime:
    return datetime.now(UTC)


def _aware(dt: datetime) -> bool:
    return dt.tzinfo is not None and dt.utcoffset() is not None


def _stored_datetime(dt: datetime) -> datetime:
    if dt.tzinfo is None or dt.utcoffset() is None:
        return dt.replace(tzinfo=UTC)
    return dt


class CorrelationProjectionHandler:
    def __init__(
        self,
        *,
        projection_version: str,
        relationship_projection_version: str,
        rule_registry: CorrelationRuleRegistry = BUILTIN_CORRELATION_RULES,
        ontology_registry: OntologyRegistry = CORE_ONTOLOGY,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        identity = correlation_projector_identity(projection_version)
        relationship_identity = ProjectorIdentity(
            RELATIONSHIP_PROJECTOR_NAME, relationship_projection_version
        )
        self._identity = identity
        self._projection_version = identity.version
        self._relationship_projection_version = relationship_identity.version
        self._rule_registry = rule_registry
        self._ontology_registry = ontology_registry
        self._clock = clock or _clock

    @property
    def projector_identity(self) -> ProjectorIdentity:
        return self._identity

    def __call__(self, session: Session, item: SequencedObservation) -> None:
        self._validate_config(session)
        self._require_relationship_checkpoint(session, item.ingest_sequence)
        graph = CorrelationGraphReader(session)
        candidate = evaluate_cor_seq_001(
            graph, item, relationship_projection_version=self._relationship_projection_version
        )
        if candidate is None:
            return
        trigger_time = item.observation.event_time
        if not _aware(trigger_time):
            raise ProjectionInvariantError(
                "correlation projection requires timezone-aware trigger event time"
            )
        now = self._clock()
        if not _aware(now):
            raise ProjectionInvariantError(
                "correlation projection clock must return a timezone-aware datetime"
            )
        try:
            self._ontology_registry.validate_edge(
                candidate.relationship_type,
                EntityRef(
                    id=candidate.source_entity_id,
                    type=candidate.source_entity_type,
                    label=candidate.source_entity_id,
                ),
                EntityRef(
                    id=candidate.target_entity_id,
                    type=candidate.target_entity_type,
                    label=candidate.target_entity_id,
                ),
            )
        except OntologyValidationError as exc:
            raise ProjectionInvariantError(
                "correlation candidate violates configured ontology"
            ) from exc
        rid = relationship_id(
            item.observation.tenant_id,
            candidate.source_entity_id,
            candidate.relationship_type,
            candidate.target_entity_id,
            ProvenanceClass.DETERMINISTIC,
            candidate.rule_id,
        )
        self._upsert_relationship(session, item, candidate, rid, now)
        self._insert_trigger_observation(session, item, rid, now)
        for evidence_id in set(candidate.trigger_evidence_ids + candidate.supporting_evidence_ids):
            self._upsert_evidence(session, item, rid, evidence_id, now)
        self._upsert_derivation(session, item, candidate, rid, now)
        for support_id in candidate.supporting_relationship_ids:
            self._insert_support(session, item, rid, support_id, now)
        for evidence_id in candidate.trigger_evidence_ids:
            self._insert_derivation_evidence(session, item, rid, evidence_id, "trigger", now)
        for evidence_id in candidate.supporting_evidence_ids:
            self._insert_derivation_evidence(session, item, rid, evidence_id, "support", now)
        session.flush()

    def _validate_config(self, session: Session) -> None:
        rec = session.get(
            CorrelationProjectionConfigRecord,
            {"projector_name": self._identity.name, "projection_version": self._identity.version},
        )
        if rec is None:
            raise CorrelationProjectionNotConfigured("correlation projection is not configured")
        expected = (
            self._identity.name,
            self._projection_version,
            RELATIONSHIP_PROJECTOR_NAME,
            self._relationship_projection_version,
            self._rule_registry.name,
            self._rule_registry.version,
            self._rule_registry.manifest_sha256(),
            self._rule_registry.manifest(),
            self._ontology_registry.name,
            self._ontology_registry.version,
        )
        actual = (
            rec.projector_name,
            rec.projection_version,
            rec.relationship_projector_name,
            rec.relationship_projection_version,
            rec.ruleset_name,
            rec.ruleset_version,
            rec.rule_manifest_sha256,
            deepcopy(rec.rule_manifest_json),
            rec.ontology_name,
            rec.ontology_version,
        )
        if actual != expected:
            raise CorrelationConfigurationMismatch("correlation projection configuration mismatch")

    def _require_relationship_checkpoint(self, session: Session, sequence: int) -> None:
        rec = session.get(
            ProjectorCheckpointRecord,
            {
                "projector_name": RELATIONSHIP_PROJECTOR_NAME,
                "projector_version": self._relationship_projection_version,
            },
        )
        if rec is None or int(rec.last_processed_sequence) < sequence:
            raise CorrelationDependencyNotReady(
                "relationship projection dependency is not caught up"
            )

    def _upsert_relationship(self, session, item, c, rid, now):
        tenant = item.observation.tenant_id
        pk = {
            "projection_version": self._relationship_projection_version,
            "tenant_id": tenant,
            "relationship_id": rid,
        }
        rec = session.get(RelationshipRecord, pk)
        expected = (
            c.relationship_type.value,
            ProvenanceClass.DETERMINISTIC.value,
            c.rule_id,
            c.rule_version,
            c.source_entity_id,
            c.source_entity_type.value,
            c.target_entity_id,
            c.target_entity_type.value,
            self._ontology_registry.name,
            self._ontology_registry.version,
        )
        if rec is None:
            session.add(
                RelationshipRecord(
                    **pk,
                    relationship_type=expected[0],
                    provenance=expected[1],
                    rule_id=expected[2],
                    rule_version=expected[3],
                    source_entity_id=expected[4],
                    source_entity_type=expected[5],
                    target_entity_id=expected[6],
                    target_entity_type=expected[7],
                    confidence=c.confidence,
                    ontology_name=expected[8],
                    ontology_version=expected[9],
                    first_seen=item.observation.event_time,
                    last_seen=item.observation.event_time,
                    first_ingest_sequence=item.ingest_sequence,
                    last_ingest_sequence=item.ingest_sequence,
                    created_at=now,
                    updated_at=now,
                )
            )
            return
        actual = (
            rec.relationship_type,
            rec.provenance,
            rec.rule_id,
            rec.rule_version,
            rec.source_entity_id,
            rec.source_entity_type,
            rec.target_entity_id,
            rec.target_entity_type,
            rec.ontology_name,
            rec.ontology_version,
        )
        if actual != expected:
            raise ProjectionInvariantError(
                "correlation deterministic relationship identity mismatch"
            )
        changed = False
        if item.observation.event_time < _stored_datetime(rec.first_seen):
            rec.first_seen = item.observation.event_time
            changed = True
        if item.observation.event_time > _stored_datetime(rec.last_seen):
            rec.last_seen = item.observation.event_time
            changed = True
        if item.ingest_sequence < rec.first_ingest_sequence:
            rec.first_ingest_sequence = item.ingest_sequence
            changed = True
        if item.ingest_sequence > rec.last_ingest_sequence:
            rec.last_ingest_sequence = item.ingest_sequence
            changed = True
        if c.confidence > rec.confidence:
            rec.confidence = c.confidence
            changed = True
        if changed:
            rec.updated_at = now

    def _insert_trigger_observation(self, session, item, rid, now):
        pk = {
            "projection_version": self._relationship_projection_version,
            "tenant_id": item.observation.tenant_id,
            "relationship_id": rid,
            "observation_id": item.observation.id,
        }
        if session.get(RelationshipObservationRecord, pk) is None:
            session.add(
                RelationshipObservationRecord(
                    **pk,
                    ingest_sequence=item.ingest_sequence,
                    event_time=item.observation.event_time,
                    created_at=now,
                )
            )

    def _upsert_evidence(self, session, item, rid, evidence_id, now):
        pk = {
            "projection_version": self._relationship_projection_version,
            "tenant_id": item.observation.tenant_id,
            "relationship_id": rid,
            "evidence_id": evidence_id,
        }
        rec = session.get(RelationshipEvidenceRecord, pk)
        if rec is None:
            session.add(
                RelationshipEvidenceRecord(
                    **pk,
                    first_seen=item.observation.event_time,
                    last_seen=item.observation.event_time,
                    first_ingest_sequence=item.ingest_sequence,
                    last_ingest_sequence=item.ingest_sequence,
                    created_at=now,
                    updated_at=now,
                )
            )
            return
        changed = False
        if item.observation.event_time < _stored_datetime(rec.first_seen):
            rec.first_seen = item.observation.event_time
            changed = True
        if item.observation.event_time > _stored_datetime(rec.last_seen):
            rec.last_seen = item.observation.event_time
            changed = True
        if item.ingest_sequence < rec.first_ingest_sequence:
            rec.first_ingest_sequence = item.ingest_sequence
            changed = True
        if item.ingest_sequence > rec.last_ingest_sequence:
            rec.last_ingest_sequence = item.ingest_sequence
            changed = True
        if changed:
            rec.updated_at = now

    def _upsert_derivation(self, session, item, c, rid, now):
        pk = {
            "relationship_projection_version": self._relationship_projection_version,
            "tenant_id": item.observation.tenant_id,
            "relationship_id": rid,
            "trigger_observation_id": item.observation.id,
        }
        expected = (
            CORRELATION_PROJECTOR_NAME,
            self._projection_version,
            c.rule_id,
            c.rule_version,
            item.ingest_sequence,
            item.observation.event_time,
            c.confidence,
            c.reason_code,
        )
        rec = session.get(RelationshipDerivationRecord, pk)
        if rec is None:
            session.add(
                RelationshipDerivationRecord(
                    **pk,
                    correlation_projector_name=expected[0],
                    correlation_projection_version=expected[1],
                    rule_id=expected[2],
                    rule_version=expected[3],
                    trigger_ingest_sequence=expected[4],
                    event_time=expected[5],
                    confidence=expected[6],
                    reason_code=expected[7],
                    created_at=now,
                )
            )
            return
        actual = (
            rec.correlation_projector_name,
            rec.correlation_projection_version,
            rec.rule_id,
            rec.rule_version,
            rec.trigger_ingest_sequence,
            _stored_datetime(rec.event_time),
            rec.confidence,
            rec.reason_code,
        )
        if actual != expected:
            raise ProjectionInvariantError("correlation derivation immutable field mismatch")

    def _insert_support(self, session, item, rid, support_id, now):
        if support_id == rid:
            raise ProjectionInvariantError("correlation support relationship cannot be self")
        if (
            session.get(
                RelationshipRecord,
                {
                    "projection_version": self._relationship_projection_version,
                    "tenant_id": item.observation.tenant_id,
                    "relationship_id": support_id,
                },
            )
            is None
        ):
            raise ProjectionInvariantError("correlation support relationship is missing")
        pk = {
            "relationship_projection_version": self._relationship_projection_version,
            "tenant_id": item.observation.tenant_id,
            "relationship_id": rid,
            "trigger_observation_id": item.observation.id,
            "support_relationship_id": support_id,
        }
        if session.get(RelationshipDerivationSupportRecord, pk) is None:
            session.add(RelationshipDerivationSupportRecord(**pk, created_at=now))

    def _insert_derivation_evidence(self, session, item, rid, evidence_id, role, now):
        pk = {
            "relationship_projection_version": self._relationship_projection_version,
            "tenant_id": item.observation.tenant_id,
            "relationship_id": rid,
            "trigger_observation_id": item.observation.id,
            "evidence_id": evidence_id,
            "evidence_role": role,
        }
        if session.get(RelationshipDerivationEvidenceRecord, pk) is None:
            session.add(RelationshipDerivationEvidenceRecord(**pk, created_at=now))
