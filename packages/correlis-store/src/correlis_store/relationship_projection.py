from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from correlis_ontology import CORE_ONTOLOGY, OntologyRegistry, OntologyValidationError
from correlis_schema import ProvenanceClass, relationship_id
from sqlalchemy.orm import Session

from .models import RelationshipEvidenceRecord, RelationshipObservationRecord, RelationshipRecord
from .observation_sequence import SequencedObservation
from .projections import ProjectionHandlerError, ProjectionInvariantError, ProjectorIdentity

RELATIONSHIP_PROJECTOR_NAME = "relationship-projection"
DEFAULT_RELATIONSHIP_PROJECTOR_VERSION = "1"
CORRELATION_PROJECTOR_NAME = "correlation-projection"
DEFAULT_CORRELATION_PROJECTOR_VERSION = "1"


def relationship_projector_identity(
    version: str = DEFAULT_RELATIONSHIP_PROJECTOR_VERSION,
) -> ProjectorIdentity:
    return ProjectorIdentity(RELATIONSHIP_PROJECTOR_NAME, version)


def correlation_projector_identity(
    version: str = DEFAULT_CORRELATION_PROJECTOR_VERSION,
) -> ProjectorIdentity:
    return ProjectorIdentity(CORRELATION_PROJECTOR_NAME, version)


def _aware(dt: datetime) -> bool:
    return dt.tzinfo is not None and dt.utcoffset() is not None


def _clock() -> datetime:
    return datetime.now(UTC)


def _stored_datetime(dt: datetime) -> datetime:
    if dt.tzinfo is None or dt.utcoffset() is None:
        return dt.replace(tzinfo=UTC)
    return dt


class RelationshipProjectionHandler:
    def __init__(
        self,
        *,
        projection_version: str = DEFAULT_RELATIONSHIP_PROJECTOR_VERSION,
        ontology_registry: OntologyRegistry = CORE_ONTOLOGY,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        identity = relationship_projector_identity(projection_version)
        self._identity = identity
        self._projection_version = identity.version
        self._registry = ontology_registry
        self._clock = clock or _clock

    @property
    def projector_identity(self) -> ProjectorIdentity:
        return self._identity

    def __call__(self, session: Session, item: SequencedObservation) -> None:
        obs = item.observation
        if obs.relationship is None:
            return
        if not _aware(obs.event_time):
            raise ProjectionHandlerError(
                "relationship_event_time_timezone_required",
                "Relationship projection requires timezone-aware observation event times.",
            )
        if obs.object is None:
            raise ProjectionHandlerError(
                "relationship_object_required",
                "Direct relationship observations require an object entity.",
            )
        try:
            self._registry.validate_edge(obs.relationship, obs.subject, obs.object)
        except OntologyValidationError as exc:
            raise ProjectionHandlerError(
                "relationship_ontology_validation_failed",
                "Relationship is incompatible with the configured ontology.",
            ) from exc
        now = self._clock()
        if not _aware(now):
            raise ProjectionInvariantError(
                "relationship projection clock must return a timezone-aware datetime"
            )
        rid = relationship_id(
            obs.tenant_id,
            obs.subject.id,
            obs.relationship,
            obs.object.id,
            ProvenanceClass.OBSERVED,
            None,
        )
        self._upsert_relationship(session, item, rid, now)
        pk = {
            "projection_version": self._projection_version,
            "tenant_id": obs.tenant_id,
            "relationship_id": rid,
            "observation_id": obs.id,
        }
        if session.get(RelationshipObservationRecord, pk) is None:
            session.add(
                RelationshipObservationRecord(
                    **pk,
                    ingest_sequence=item.ingest_sequence,
                    event_time=obs.event_time,
                    created_at=now,
                )
            )
        for evidence in obs.evidence:
            self._upsert_evidence(session, item, rid, evidence.id, now)
        session.flush()

    def _upsert_relationship(
        self, session: Session, item: SequencedObservation, rid: str, now: datetime
    ) -> RelationshipRecord:
        obs = item.observation
        assert obs.relationship is not None and obs.object is not None
        pk = {
            "projection_version": self._projection_version,
            "tenant_id": obs.tenant_id,
            "relationship_id": rid,
        }
        rec = session.get(RelationshipRecord, pk)
        if rec is None:
            rec = RelationshipRecord(
                **pk,
                relationship_type=obs.relationship.value,
                provenance=ProvenanceClass.OBSERVED.value,
                rule_id=None,
                rule_version=None,
                source_entity_id=obs.subject.id,
                source_entity_type=obs.subject.type.value,
                target_entity_id=obs.object.id,
                target_entity_type=obs.object.type.value,
                confidence=obs.confidence,
                ontology_name=self._registry.name,
                ontology_version=self._registry.version,
                first_seen=obs.event_time,
                last_seen=obs.event_time,
                first_ingest_sequence=item.ingest_sequence,
                last_ingest_sequence=item.ingest_sequence,
                created_at=now,
                updated_at=now,
            )
            session.add(rec)
            return rec
        expected = (
            obs.relationship.value,
            ProvenanceClass.OBSERVED.value,
            None,
            None,
            obs.subject.id,
            obs.subject.type.value,
            obs.object.id,
            obs.object.type.value,
        )
        actual = (
            rec.relationship_type,
            rec.provenance,
            rec.rule_id,
            rec.rule_version,
            rec.source_entity_id,
            rec.source_entity_type,
            rec.target_entity_id,
            rec.target_entity_type,
        )
        if actual != expected:
            raise ProjectionInvariantError(
                "relationship deterministic identity collision or corrupt state"
            )
        if (
            rec.ontology_name != self._registry.name
            or rec.ontology_version != self._registry.version
        ):
            raise ProjectionHandlerError(
                "relationship_projection_ontology_mismatch",
                "Relationship projection version does not match the stored ontology version.",
            )
        changed = False
        if obs.event_time < _stored_datetime(rec.first_seen):
            rec.first_seen = obs.event_time
            changed = True
        if obs.event_time > _stored_datetime(rec.last_seen):
            rec.last_seen = obs.event_time
            changed = True
        if item.ingest_sequence < rec.first_ingest_sequence:
            rec.first_ingest_sequence = item.ingest_sequence
            changed = True
        if item.ingest_sequence > rec.last_ingest_sequence:
            rec.last_ingest_sequence = item.ingest_sequence
            changed = True
        if obs.confidence > rec.confidence:
            rec.confidence = obs.confidence
            changed = True
        if changed:
            rec.updated_at = now
        return rec

    def _upsert_evidence(
        self,
        session: Session,
        item: SequencedObservation,
        rid: str,
        evidence_id: str,
        now: datetime,
    ) -> None:
        obs = item.observation
        pk = {
            "projection_version": self._projection_version,
            "tenant_id": obs.tenant_id,
            "relationship_id": rid,
            "evidence_id": evidence_id,
        }
        rec = session.get(RelationshipEvidenceRecord, pk)
        if rec is None:
            session.add(
                RelationshipEvidenceRecord(
                    **pk,
                    first_seen=obs.event_time,
                    last_seen=obs.event_time,
                    first_ingest_sequence=item.ingest_sequence,
                    last_ingest_sequence=item.ingest_sequence,
                    created_at=now,
                    updated_at=now,
                )
            )
            return
        changed = False
        if obs.event_time < _stored_datetime(rec.first_seen):
            rec.first_seen = obs.event_time
            changed = True
        if obs.event_time > _stored_datetime(rec.last_seen):
            rec.last_seen = obs.event_time
            changed = True
        if item.ingest_sequence < rec.first_ingest_sequence:
            rec.first_ingest_sequence = item.ingest_sequence
            changed = True
        if item.ingest_sequence > rec.last_ingest_sequence:
            rec.last_ingest_sequence = item.ingest_sequence
            changed = True
        if changed:
            rec.updated_at = now
