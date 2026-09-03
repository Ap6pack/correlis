from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager

from correlis_schema import IncidentState
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .attack_scenes import (
    AttackSceneEntityMembership,
    AttackSceneLineage,
    AttackSceneObservationMembership,
    AttackSceneRelationshipMembership,
    AttackSceneStateTransition,
    ProjectedAttackScene,
    ProjectedAttackScenePage,
)
from .models import (
    AttackSceneEntityRecord,
    AttackSceneObservationRecord,
    AttackSceneRecord,
    AttackSceneRelationshipRecord,
    AttackSceneStateTransitionRecord,
)


def _scene(row: AttackSceneRecord) -> ProjectedAttackScene:
    return ProjectedAttackScene(
        row.projection_version,
        row.tenant_id,
        row.scene_id,
        row.entity_projection_version,
        row.relationship_projection_version,
        row.title,
        IncidentState(row.state),
        row.first_seen,
        row.last_seen,
        int(row.first_ingest_sequence),
        int(row.last_ingest_sequence),
        row.summary,
        tuple(str(item) for item in row.uncertainty_json),
        row.created_at,
        row.updated_at,
    )


class AttackSceneRepository:
    """Read-only access to tenant- and projection-scoped Attack Scene membership."""

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

    def get_scene(
        self, *, projection_version: str, tenant_id: str, scene_id: str
    ) -> ProjectedAttackScene | None:
        with self._session_scope() as session:
            row = session.get(
                AttackSceneRecord,
                {
                    "projection_version": projection_version,
                    "tenant_id": tenant_id,
                    "scene_id": scene_id,
                },
            )
            return None if row is None else _scene(row)

    def list_scenes(
        self,
        *,
        projection_version: str,
        tenant_id: str,
        state: IncidentState | None = None,
        after_scene_id: str | None = None,
        limit: int = 100,
    ) -> tuple[ProjectedAttackScene, ...]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        return self._list_scenes(
            projection_version=projection_version,
            tenant_id=tenant_id,
            state=state,
            after_scene_id=after_scene_id,
            limit=limit,
        )

    def list_scene_page(
        self,
        *,
        projection_version: str,
        tenant_id: str,
        state: IncidentState | None = None,
        after_scene_id: str | None = None,
        limit: int = 100,
    ) -> ProjectedAttackScenePage:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        rows = self._list_scenes(
            projection_version=projection_version,
            tenant_id=tenant_id,
            state=state,
            after_scene_id=after_scene_id,
            limit=limit + 1,
        )
        has_more = len(rows) > limit
        items = rows[:limit]
        return ProjectedAttackScenePage(
            items=items,
            next_after_scene_id=items[-1].scene_id if has_more else None,
            has_more=has_more,
        )

    def _list_scenes(
        self,
        *,
        projection_version: str,
        tenant_id: str,
        state: IncidentState | None,
        after_scene_id: str | None,
        limit: int,
    ) -> tuple[ProjectedAttackScene, ...]:
        stmt = select(AttackSceneRecord).where(
            AttackSceneRecord.projection_version == projection_version,
            AttackSceneRecord.tenant_id == tenant_id,
        )
        if state is not None:
            stmt = stmt.where(AttackSceneRecord.state == state.value)
        if after_scene_id is not None:
            stmt = stmt.where(AttackSceneRecord.scene_id > after_scene_id)
        with self._session_scope() as session:
            return tuple(
                _scene(row)
                for row in session.scalars(stmt.order_by(AttackSceneRecord.scene_id).limit(limit))
            )

    def get_lineage(
        self, *, projection_version: str, tenant_id: str, scene_id: str
    ) -> AttackSceneLineage | None:
        scope = (projection_version, tenant_id, scene_id)
        with self._session_scope() as session:
            row = session.get(
                AttackSceneRecord,
                {
                    "projection_version": projection_version,
                    "tenant_id": tenant_id,
                    "scene_id": scene_id,
                },
            )
            if row is None:
                return None

            def rows(model, *ordering):
                return session.scalars(
                    select(model)
                    .where(
                        model.projection_version == scope[0],
                        model.tenant_id == scope[1],
                        model.scene_id == scope[2],
                    )
                    .order_by(*ordering)
                ).all()

            entities = tuple(
                AttackSceneEntityMembership(
                    r.projection_version,
                    r.tenant_id,
                    r.scene_id,
                    r.entity_projection_version,
                    r.entity_id,
                    r.first_seen,
                    r.last_seen,
                    int(r.first_ingest_sequence),
                    int(r.last_ingest_sequence),
                )
                for r in rows(AttackSceneEntityRecord, AttackSceneEntityRecord.entity_id)
            )
            relationships = tuple(
                AttackSceneRelationshipMembership(
                    r.projection_version,
                    r.tenant_id,
                    r.scene_id,
                    r.relationship_projection_version,
                    r.relationship_id,
                    r.first_seen,
                    r.last_seen,
                    int(r.first_ingest_sequence),
                    int(r.last_ingest_sequence),
                )
                for r in rows(
                    AttackSceneRelationshipRecord, AttackSceneRelationshipRecord.relationship_id
                )
            )
            observations = tuple(
                AttackSceneObservationMembership(
                    r.projection_version,
                    r.tenant_id,
                    r.scene_id,
                    r.observation_id,
                    int(r.ingest_sequence),
                    r.event_time,
                )
                for r in rows(
                    AttackSceneObservationRecord,
                    AttackSceneObservationRecord.ingest_sequence,
                    AttackSceneObservationRecord.observation_id,
                )
            )
            transitions = tuple(
                AttackSceneStateTransition(
                    r.projection_version,
                    r.tenant_id,
                    r.scene_id,
                    int(r.ingest_sequence),
                    r.trigger_observation_id,
                    IncidentState(r.from_state),
                    IncidentState(r.to_state),
                    r.reason_code,
                    r.created_at,
                )
                for r in rows(
                    AttackSceneStateTransitionRecord,
                    AttackSceneStateTransitionRecord.ingest_sequence,
                    AttackSceneStateTransitionRecord.trigger_observation_id,
                )
            )
            return AttackSceneLineage(
                _scene(row), entities, relationships, observations, transitions
            )
