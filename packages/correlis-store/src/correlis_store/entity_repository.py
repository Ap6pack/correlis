from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager

from correlis_schema import EntityType
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .entities import (
    EntityEvidenceLineage,
    EntityIdentityClaim,
    EntityLineage,
    EntityObservationLineage,
    ProjectedEntity,
    ProjectedEntityPage,
)
from .models import (
    EntityEvidenceRecord,
    EntityIdentityClaimRecord,
    EntityObservationRecord,
    EntityRecord,
    ObservationRecord,
)


def _limit(v: int) -> int:
    if v < 1 or v > 500:
        raise ValueError("limit must be between 1 and 500")
    return v


def _entity(r: EntityRecord) -> ProjectedEntity:
    return ProjectedEntity(
        r.projection_version,
        r.tenant_id,
        r.entity_id,
        r.canonical_key,
        EntityType(r.entity_type),
        r.label,
        dict(r.attributes_json),
        r.ontology_name,
        r.ontology_version,
        r.first_seen,
        r.last_seen,
        int(r.first_ingest_sequence),
        int(r.last_ingest_sequence),
        r.latest_claim_event_time,
        int(r.latest_claim_ingest_sequence),
        r.created_at,
        r.updated_at,
    )


class EntityRepository:
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

    def get_entity(
        self, projection_version: str, tenant_id: str, entity_id: str
    ) -> ProjectedEntity | None:
        with self._session_scope() as session:
            r = session.get(
                EntityRecord,
                {
                    "projection_version": projection_version,
                    "tenant_id": tenant_id,
                    "entity_id": entity_id,
                },
            )
            return _entity(r) if r is not None else None

    def list_entities(
        self,
        projection_version: str,
        tenant_id: str,
        *,
        entity_type: EntityType | None = None,
        after_entity_id: str | None = None,
        limit: int = 100,
    ) -> ProjectedEntityPage:
        limit = _limit(limit)
        with self._session_scope() as session:
            stmt = select(EntityRecord).where(
                EntityRecord.projection_version == projection_version,
                EntityRecord.tenant_id == tenant_id,
            )
            if entity_type is not None:
                stmt = stmt.where(EntityRecord.entity_type == entity_type.value)
            if after_entity_id is not None:
                stmt = stmt.where(EntityRecord.entity_id > after_entity_id)
            rows = list(
                session.scalars(stmt.order_by(EntityRecord.entity_id).limit(limit + 1)).all()
            )
            has_more = len(rows) > limit
            items = [_entity(r) for r in rows[:limit]]
            return ProjectedEntityPage(
                items,
                after_entity_id,
                items[-1].entity_id if has_more and items else None,
                has_more,
            )

    def get_lineage(
        self,
        projection_version: str,
        tenant_id: str,
        entity_id: str,
        *,
        observation_limit: int = 100,
        evidence_limit: int = 100,
        identity_claim_limit: int = 100,
    ) -> EntityLineage | None:
        observation_limit = _limit(observation_limit)
        evidence_limit = _limit(evidence_limit)
        identity_claim_limit = _limit(identity_claim_limit)
        with self._session_scope() as session:
            ent = session.get(
                EntityRecord,
                {
                    "projection_version": projection_version,
                    "tenant_id": tenant_id,
                    "entity_id": entity_id,
                },
            )
            if ent is None:
                return None
            obs_rows = session.execute(
                select(EntityObservationRecord, ObservationRecord)
                .join(
                    ObservationRecord,
                    (ObservationRecord.tenant_id == EntityObservationRecord.tenant_id)
                    & (ObservationRecord.observation_id == EntityObservationRecord.observation_id),
                )
                .where(
                    EntityObservationRecord.projection_version == projection_version,
                    EntityObservationRecord.tenant_id == tenant_id,
                    EntityObservationRecord.entity_id == entity_id,
                )
                .order_by(EntityObservationRecord.ingest_sequence, EntityObservationRecord.role)
                .limit(observation_limit)
            ).all()
            observations = [
                EntityObservationLineage(
                    o.projection_version,
                    o.tenant_id,
                    o.entity_id,
                    o.observation_id,
                    o.role,
                    int(o.ingest_sequence),
                    o.event_time,
                    r.source,
                    r.sensor_id,
                )
                for o, r in obs_rows
            ]
            evidence = [
                EntityEvidenceLineage(
                    r.projection_version,
                    r.tenant_id,
                    r.entity_id,
                    r.evidence_id,
                    r.first_seen,
                    r.last_seen,
                    int(r.first_ingest_sequence),
                    int(r.last_ingest_sequence),
                )
                for r in session.scalars(
                    select(EntityEvidenceRecord)
                    .where(
                        EntityEvidenceRecord.projection_version == projection_version,
                        EntityEvidenceRecord.tenant_id == tenant_id,
                        EntityEvidenceRecord.entity_id == entity_id,
                    )
                    .order_by(EntityEvidenceRecord.evidence_id)
                    .limit(evidence_limit)
                ).all()
            ]
            claims = [
                EntityIdentityClaim(
                    r.projection_version,
                    r.tenant_id,
                    r.entity_id,
                    EntityType(r.entity_type),
                    r.identity_key_name,
                    r.value_sha256,
                    dict(r.value_json),
                    r.first_seen,
                    r.last_seen,
                    int(r.first_ingest_sequence),
                    int(r.last_ingest_sequence),
                )
                for r in session.scalars(
                    select(EntityIdentityClaimRecord)
                    .where(
                        EntityIdentityClaimRecord.projection_version == projection_version,
                        EntityIdentityClaimRecord.tenant_id == tenant_id,
                        EntityIdentityClaimRecord.entity_id == entity_id,
                    )
                    .order_by(
                        EntityIdentityClaimRecord.identity_key_name,
                        EntityIdentityClaimRecord.value_sha256,
                    )
                    .limit(identity_claim_limit)
                ).all()
            ]
            return EntityLineage(_entity(ent), observations, evidence, claims)
