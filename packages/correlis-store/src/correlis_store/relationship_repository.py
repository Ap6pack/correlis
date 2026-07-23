from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager

from correlis_schema import EntityType, ProvenanceClass, RelationshipType
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .correlations import (
    RelationshipDerivation,
    RelationshipDerivationEvidence,
    RelationshipDerivationSupport,
)
from .models import (
    ObservationRecord,
    RelationshipDerivationEvidenceRecord,
    RelationshipDerivationRecord,
    RelationshipDerivationSupportRecord,
    RelationshipEvidenceRecord,
    RelationshipObservationRecord,
    RelationshipRecord,
)
from .relationships import (
    ProjectedRelationship,
    ProjectedRelationshipPage,
    RelationshipEvidenceLineage,
    RelationshipLineage,
    RelationshipObservationLineage,
)


def _limit(v: int) -> int:
    if v < 1 or v > 500:
        raise ValueError("limit must be between 1 and 500")
    return v


def _rel(r: RelationshipRecord) -> ProjectedRelationship:
    return ProjectedRelationship(
        r.projection_version,
        r.tenant_id,
        r.relationship_id,
        RelationshipType(r.relationship_type),
        ProvenanceClass(r.provenance),
        r.rule_id,
        r.rule_version,
        r.source_entity_id,
        EntityType(r.source_entity_type),
        r.target_entity_id,
        EntityType(r.target_entity_type),
        r.confidence,
        r.ontology_name,
        r.ontology_version,
        r.first_seen,
        r.last_seen,
        int(r.first_ingest_sequence),
        int(r.last_ingest_sequence),
        r.created_at,
        r.updated_at,
    )


class RelationshipRepository:
    def __init__(self, session_or_factory: Session | sessionmaker[Session] | Callable[[], Session]):
        self._session_or_factory = session_or_factory

    @contextmanager
    def _session_scope(self) -> Iterator[Session]:
        if isinstance(self._session_or_factory, Session):
            yield self._session_or_factory
            return
        session = self._session_or_factory()
        try:
            yield session
        finally:
            session.close()

    def get_relationship(
        self, projection_version: str, tenant_id: str, relationship_id: str
    ) -> ProjectedRelationship | None:
        with self._session_scope() as session:
            r = session.get(
                RelationshipRecord,
                {
                    "projection_version": projection_version,
                    "tenant_id": tenant_id,
                    "relationship_id": relationship_id,
                },
            )
            return _rel(r) if r is not None else None

    def list_relationships(
        self,
        projection_version: str,
        tenant_id: str,
        *,
        relationship_type: RelationshipType | None = None,
        provenance: ProvenanceClass | None = None,
        rule_id: str | None = None,
        source_entity_id: str | None = None,
        target_entity_id: str | None = None,
        after_relationship_id: str | None = None,
        limit: int = 100,
    ) -> ProjectedRelationshipPage:
        limit = _limit(limit)
        with self._session_scope() as session:
            stmt = select(RelationshipRecord).where(
                RelationshipRecord.projection_version == projection_version,
                RelationshipRecord.tenant_id == tenant_id,
            )
            if relationship_type is not None:
                stmt = stmt.where(RelationshipRecord.relationship_type == relationship_type.value)
            if provenance is not None:
                stmt = stmt.where(RelationshipRecord.provenance == provenance.value)
            if rule_id is not None:
                stmt = stmt.where(RelationshipRecord.rule_id == rule_id)
            if source_entity_id is not None:
                stmt = stmt.where(RelationshipRecord.source_entity_id == source_entity_id)
            if target_entity_id is not None:
                stmt = stmt.where(RelationshipRecord.target_entity_id == target_entity_id)
            if after_relationship_id is not None:
                stmt = stmt.where(RelationshipRecord.relationship_id > after_relationship_id)
            rows = list(
                session.scalars(
                    stmt.order_by(RelationshipRecord.relationship_id).limit(limit + 1)
                ).all()
            )
            has_more = len(rows) > limit
            items = [_rel(r) for r in rows[:limit]]
            return ProjectedRelationshipPage(
                items,
                after_relationship_id,
                items[-1].relationship_id if has_more and items else None,
                has_more,
            )

    def get_lineage(
        self,
        projection_version: str,
        tenant_id: str,
        relationship_id: str,
        *,
        observation_limit: int = 100,
        evidence_limit: int = 100,
    ) -> RelationshipLineage | None:
        observation_limit = _limit(observation_limit)
        evidence_limit = _limit(evidence_limit)
        with self._session_scope() as session:
            rr = session.get(
                RelationshipRecord,
                {
                    "projection_version": projection_version,
                    "tenant_id": tenant_id,
                    "relationship_id": relationship_id,
                },
            )
            if rr is None:
                return None
            obs_rows = session.execute(
                select(RelationshipObservationRecord, ObservationRecord)
                .join(
                    ObservationRecord,
                    (ObservationRecord.tenant_id == RelationshipObservationRecord.tenant_id)
                    & (
                        ObservationRecord.observation_id
                        == RelationshipObservationRecord.observation_id
                    ),
                )
                .where(
                    RelationshipObservationRecord.projection_version == projection_version,
                    RelationshipObservationRecord.tenant_id == tenant_id,
                    RelationshipObservationRecord.relationship_id == relationship_id,
                )
                .order_by(
                    RelationshipObservationRecord.ingest_sequence,
                    RelationshipObservationRecord.observation_id,
                )
                .limit(observation_limit)
            ).all()
            observations = tuple(
                RelationshipObservationLineage(
                    o.projection_version,
                    o.tenant_id,
                    o.relationship_id,
                    o.observation_id,
                    int(o.ingest_sequence),
                    o.event_time,
                    r.source,
                    r.sensor_id,
                    r.confidence,
                )
                for o, r in obs_rows
            )
            evidence = tuple(
                RelationshipEvidenceLineage(
                    r.projection_version,
                    r.tenant_id,
                    r.relationship_id,
                    r.evidence_id,
                    r.first_seen,
                    r.last_seen,
                    int(r.first_ingest_sequence),
                    int(r.last_ingest_sequence),
                )
                for r in session.scalars(
                    select(RelationshipEvidenceRecord)
                    .where(
                        RelationshipEvidenceRecord.projection_version == projection_version,
                        RelationshipEvidenceRecord.tenant_id == tenant_id,
                        RelationshipEvidenceRecord.relationship_id == relationship_id,
                    )
                    .order_by(RelationshipEvidenceRecord.evidence_id)
                    .limit(evidence_limit)
                ).all()
            )
            derivation_rows = session.scalars(
                select(RelationshipDerivationRecord)
                .where(
                    RelationshipDerivationRecord.relationship_projection_version
                    == projection_version,
                    RelationshipDerivationRecord.tenant_id == tenant_id,
                    RelationshipDerivationRecord.relationship_id == relationship_id,
                )
                .order_by(
                    RelationshipDerivationRecord.trigger_ingest_sequence,
                    RelationshipDerivationRecord.trigger_observation_id,
                )
            ).all()
            derivations = tuple(
                RelationshipDerivation(
                    d.relationship_projection_version,
                    d.tenant_id,
                    d.relationship_id,
                    d.trigger_observation_id,
                    d.correlation_projection_version,
                    d.rule_id,
                    d.rule_version,
                    int(d.trigger_ingest_sequence),
                    d.event_time,
                    d.confidence,
                    d.reason_code,
                    d.created_at,
                )
                for d in derivation_rows
            )
            support_rows = session.scalars(
                select(RelationshipDerivationSupportRecord)
                .join(
                    RelationshipDerivationRecord,
                    (
                        RelationshipDerivationRecord.relationship_projection_version
                        == RelationshipDerivationSupportRecord.relationship_projection_version
                    )
                    & (
                        RelationshipDerivationRecord.tenant_id
                        == RelationshipDerivationSupportRecord.tenant_id
                    )
                    & (
                        RelationshipDerivationRecord.relationship_id
                        == RelationshipDerivationSupportRecord.relationship_id
                    )
                    & (
                        RelationshipDerivationRecord.trigger_observation_id
                        == RelationshipDerivationSupportRecord.trigger_observation_id
                    ),
                )
                .where(
                    RelationshipDerivationSupportRecord.relationship_projection_version
                    == projection_version,
                    RelationshipDerivationSupportRecord.tenant_id == tenant_id,
                    RelationshipDerivationSupportRecord.relationship_id == relationship_id,
                )
                .order_by(
                    RelationshipDerivationRecord.trigger_ingest_sequence,
                    RelationshipDerivationSupportRecord.support_relationship_id,
                )
            ).all()
            derivation_supports = tuple(
                RelationshipDerivationSupport(
                    s.relationship_projection_version,
                    s.tenant_id,
                    s.relationship_id,
                    s.trigger_observation_id,
                    s.support_relationship_id,
                )
                for s in support_rows
            )
            evidence_rows = session.scalars(
                select(RelationshipDerivationEvidenceRecord)
                .join(
                    RelationshipDerivationRecord,
                    (
                        RelationshipDerivationRecord.relationship_projection_version
                        == RelationshipDerivationEvidenceRecord.relationship_projection_version
                    )
                    & (
                        RelationshipDerivationRecord.tenant_id
                        == RelationshipDerivationEvidenceRecord.tenant_id
                    )
                    & (
                        RelationshipDerivationRecord.relationship_id
                        == RelationshipDerivationEvidenceRecord.relationship_id
                    )
                    & (
                        RelationshipDerivationRecord.trigger_observation_id
                        == RelationshipDerivationEvidenceRecord.trigger_observation_id
                    ),
                )
                .where(
                    RelationshipDerivationEvidenceRecord.relationship_projection_version
                    == projection_version,
                    RelationshipDerivationEvidenceRecord.tenant_id == tenant_id,
                    RelationshipDerivationEvidenceRecord.relationship_id == relationship_id,
                )
                .order_by(
                    RelationshipDerivationRecord.trigger_ingest_sequence,
                    RelationshipDerivationEvidenceRecord.evidence_role,
                    RelationshipDerivationEvidenceRecord.evidence_id,
                )
            ).all()
            derivation_evidence = tuple(
                RelationshipDerivationEvidence(
                    e.relationship_projection_version,
                    e.tenant_id,
                    e.relationship_id,
                    e.trigger_observation_id,
                    e.evidence_id,
                    e.evidence_role,
                )
                for e in evidence_rows
            )
            return RelationshipLineage(
                _rel(rr),
                observations,
                evidence,
                derivations,
                derivation_supports,
                derivation_evidence,
            )
