from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from correlis_schema import IncidentState
from sqlalchemy import select
from sqlalchemy.orm import Session

from .attack_scene_planner import AttackSceneMembershipPlanner, AttackScenePlan
from .attack_scene_projection import ATTACK_SCENE_PROJECTOR_NAME, AttackSceneProjectionConfig
from .models import (
    AttackSceneEntityRecord,
    AttackSceneObservationRecord,
    AttackSceneRecord,
    AttackSceneRelationshipRecord,
    AttackSceneStateTransitionRecord,
)
from .observation_sequence import SequencedObservation
from .projections import ProjectionInvariantError, ProjectorIdentity


class AttackSceneProjectionInvariantError(ProjectionInvariantError):
    pass


class AttackSceneProjectionHandler:
    """Persist the bounded output of the Attack Scene membership planner."""

    _PAGE_SIZE = 500
    _AUTOMATIC_REASONS = {
        "deterministic_exploit_observed",
        "deterministic_compromise_confirmed",
    }
    _STATE_ORDER = {
        IncidentState.POTENTIAL: 0,
        IncidentState.OBSERVED: 1,
        IncidentState.CONFIRMED: 2,
        IncidentState.CONTAINED: 3,
        IncidentState.CLOSED: 4,
    }

    def __init__(
        self,
        *,
        config: AttackSceneProjectionConfig,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if config.identity.name != ATTACK_SCENE_PROJECTOR_NAME:
            raise AttackSceneProjectionInvariantError("invalid attack scene projector identity")
        self._config = config
        self._clock = clock or (lambda: datetime.now(UTC))

    @property
    def projector_identity(self) -> ProjectorIdentity:
        return self._config.identity

    def __call__(self, session: Session, item: SequencedObservation) -> None:
        planner = AttackSceneMembershipPlanner(session, self._config)
        tenant_id = item.observation.tenant_id
        boundary = item.ingest_sequence
        after: str | None = None
        plans: list[AttackScenePlan] = []
        while True:
            roots = planner.list_root_relationship_ids(
                tenant_id=tenant_id,
                through_ingest_sequence=boundary,
                after_relationship_id=after,
                limit=self._PAGE_SIZE,
            )
            for root in roots:
                plan = planner.plan_scene(
                    tenant_id=tenant_id,
                    root_relationship_id=root,
                    through_ingest_sequence=boundary,
                )
                if plan is None:
                    raise AttackSceneProjectionInvariantError(
                        "listed attack scene root could not be planned"
                    )
                plans.append(plan)
            if len(roots) < self._PAGE_SIZE:
                break
            after = roots[-1]

        now: datetime | None = None

        def write_time() -> datetime:
            nonlocal now
            if now is None:
                now = self._clock()
                if now.tzinfo is None or now.utcoffset() is None:
                    raise AttackSceneProjectionInvariantError(
                        "attack scene projection clock must be timezone-aware"
                    )
            return now

        for plan in plans:
            self._persist_plan(session, plan, write_time)
        session.flush()

    def _persist_plan(
        self, session: Session, plan: AttackScenePlan, write_time: Callable[[], datetime]
    ) -> None:
        key = {
            "projection_version": plan.projection_version,
            "tenant_id": plan.tenant_id,
            "scene_id": plan.scene_id,
        }
        scene = session.get(AttackSceneRecord, key)
        scene_changed = False
        if scene is None:
            now = write_time()
            scene = AttackSceneRecord(
                **key,
                entity_projection_version=plan.entity_projection_version,
                relationship_projection_version=plan.relationship_projection_version,
                title=plan.title,
                state=str(plan.state),
                first_seen=plan.first_seen,
                last_seen=plan.last_seen,
                first_ingest_sequence=plan.first_ingest_sequence,
                last_ingest_sequence=plan.last_ingest_sequence,
                summary=None,
                uncertainty_json=[],
                created_at=now,
                updated_at=now,
            )
            session.add(scene)
        else:
            if (
                scene.projection_version != plan.projection_version
                or scene.tenant_id != plan.tenant_id
                or scene.scene_id != plan.scene_id
                or scene.entity_projection_version != plan.entity_projection_version
                or scene.relationship_projection_version != plan.relationship_projection_version
            ):
                raise AttackSceneProjectionInvariantError("attack scene graph binding mismatch")
            for name in (
                "first_seen",
                "last_seen",
                "first_ingest_sequence",
                "last_ingest_sequence",
            ):
                value = getattr(plan, name)
                if getattr(scene, name) != value:
                    setattr(scene, name, value)
                    scene_changed = True
            current = IncidentState(scene.state)
            if self._STATE_ORDER[plan.state] > self._STATE_ORDER[current] and current not in (
                IncidentState.CONTAINED,
                IncidentState.CLOSED,
            ):
                scene.state = str(plan.state)
                scene_changed = True

        scene_changed |= self._persist_relationships(session, plan, write_time)
        scene_changed |= self._persist_entities(session, plan, write_time)
        scene_changed |= self._persist_observations(session, plan, write_time)
        scene_changed |= self._persist_transitions(session, plan, write_time)
        if scene_changed:
            scene.updated_at = write_time()

    @staticmethod
    def _scene_filter(model: type, plan: AttackScenePlan):
        return (
            model.projection_version == plan.projection_version,
            model.tenant_id == plan.tenant_id,
            model.scene_id == plan.scene_id,
        )

    def _persist_relationships(self, session, plan, write_time) -> bool:
        existing = {
            row.relationship_id: row
            for row in session.scalars(
                select(AttackSceneRelationshipRecord).where(
                    *self._scene_filter(AttackSceneRelationshipRecord, plan)
                )
            )
        }
        planned = {value.relationship_id: value for value in plan.relationships}
        if not set(existing) <= set(planned):
            raise AttackSceneProjectionInvariantError("unexpected attack scene relationship")
        changed = False
        for relationship_id, value in planned.items():
            row = existing.get(relationship_id)
            if row is None:
                now = write_time()
                session.add(
                    AttackSceneRelationshipRecord(
                        projection_version=plan.projection_version,
                        tenant_id=plan.tenant_id,
                        scene_id=plan.scene_id,
                        relationship_projection_version=plan.relationship_projection_version,
                        relationship_id=relationship_id,
                        first_seen=value.first_seen,
                        last_seen=value.last_seen,
                        first_ingest_sequence=value.first_ingest_sequence,
                        last_ingest_sequence=value.last_ingest_sequence,
                        created_at=now,
                        updated_at=now,
                    )
                )
                changed = True
                continue
            if row.relationship_projection_version != plan.relationship_projection_version:
                raise AttackSceneProjectionInvariantError("relationship membership graph mismatch")
            fields = ("first_seen", "last_seen", "first_ingest_sequence", "last_ingest_sequence")
            if any(getattr(row, f) != getattr(value, f) for f in fields):
                for field in fields:
                    setattr(row, field, getattr(value, field))
                row.updated_at = write_time()
                changed = True
        return changed

    def _persist_entities(self, session, plan, write_time) -> bool:
        existing = {
            row.entity_id: row
            for row in session.scalars(
                select(AttackSceneEntityRecord).where(
                    *self._scene_filter(AttackSceneEntityRecord, plan)
                )
            )
        }
        planned = {value.entity_id: value for value in plan.entities}
        if not set(existing) <= set(planned):
            raise AttackSceneProjectionInvariantError("unexpected attack scene entity")
        changed = False
        for entity_id, value in planned.items():
            row = existing.get(entity_id)
            if row is None:
                now = write_time()
                session.add(
                    AttackSceneEntityRecord(
                        projection_version=plan.projection_version,
                        tenant_id=plan.tenant_id,
                        scene_id=plan.scene_id,
                        entity_projection_version=plan.entity_projection_version,
                        entity_id=entity_id,
                        first_seen=value.first_seen,
                        last_seen=value.last_seen,
                        first_ingest_sequence=value.first_ingest_sequence,
                        last_ingest_sequence=value.last_ingest_sequence,
                        created_at=now,
                        updated_at=now,
                    )
                )
                changed = True
                continue
            if row.entity_projection_version != plan.entity_projection_version:
                raise AttackSceneProjectionInvariantError("entity membership graph mismatch")
            fields = ("first_seen", "last_seen", "first_ingest_sequence", "last_ingest_sequence")
            if any(getattr(row, f) != getattr(value, f) for f in fields):
                for field in fields:
                    setattr(row, field, getattr(value, field))
                row.updated_at = write_time()
                changed = True
        return changed

    def _persist_observations(self, session, plan, write_time) -> bool:
        existing = {
            row.observation_id: row
            for row in session.scalars(
                select(AttackSceneObservationRecord).where(
                    *self._scene_filter(AttackSceneObservationRecord, plan)
                )
            )
        }
        planned = {value.observation_id: value for value in plan.observations}
        if not set(existing) <= set(planned):
            raise AttackSceneProjectionInvariantError("unexpected attack scene observation")
        changed = False
        for observation_id, value in planned.items():
            row = existing.get(observation_id)
            if row is None:
                session.add(
                    AttackSceneObservationRecord(
                        projection_version=plan.projection_version,
                        tenant_id=plan.tenant_id,
                        scene_id=plan.scene_id,
                        observation_id=observation_id,
                        ingest_sequence=value.ingest_sequence,
                        event_time=value.event_time,
                        created_at=write_time(),
                    )
                )
                changed = True
            elif row.ingest_sequence != value.ingest_sequence or row.event_time != value.event_time:
                raise AttackSceneProjectionInvariantError("observation membership mismatch")
        return changed

    def _persist_transitions(self, session, plan, write_time) -> bool:
        rows = session.scalars(
            select(AttackSceneStateTransitionRecord).where(
                *self._scene_filter(AttackSceneStateTransitionRecord, plan)
            )
        ).all()
        existing = {(row.ingest_sequence, row.trigger_observation_id): row for row in rows}
        changed = False
        for value in plan.state_transitions:
            if value.reason_code not in self._AUTOMATIC_REASONS:
                raise AttackSceneProjectionInvariantError(
                    "planner produced nonautomatic transition"
                )
            key = (value.ingest_sequence, value.trigger_observation_id)
            row = existing.get(key)
            expected = (str(value.from_state), str(value.to_state), value.reason_code)
            if row is None:
                session.add(
                    AttackSceneStateTransitionRecord(
                        projection_version=plan.projection_version,
                        tenant_id=plan.tenant_id,
                        scene_id=plan.scene_id,
                        ingest_sequence=value.ingest_sequence,
                        trigger_observation_id=value.trigger_observation_id,
                        from_state=expected[0],
                        to_state=expected[1],
                        reason_code=expected[2],
                        created_at=write_time(),
                    )
                )
                changed = True
            elif (row.from_state, row.to_state, row.reason_code) != expected:
                raise AttackSceneProjectionInvariantError("state transition mismatch")
        return changed
