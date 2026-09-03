from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime

from correlis_schema import IncidentState
from sqlalchemy import select
from sqlalchemy.orm import Session

from .attack_scene_policy import AttackScenePolicyNotFound, resolve_attack_scene_policy
from .attack_scene_projection import (
    ATTACK_SCENE_PROJECTOR_NAME,
    AttackSceneProjectionConfig,
    attack_scene_id,
)
from .entity_projection import ENTITY_PROJECTOR_NAME
from .models import (
    EntityRecord,
    ProjectorCheckpointRecord,
    RelationshipDerivationRecord,
    RelationshipDerivationSupportRecord,
    RelationshipObservationRecord,
    RelationshipRecord,
)
from .projections import ProjectionInvariantError
from .relationship_projection import CORRELATION_PROJECTOR_NAME, RELATIONSHIP_PROJECTOR_NAME


class AttackScenePlanningInvariantError(ProjectionInvariantError):
    pass


class AttackSceneDependencyNotReady(ProjectionInvariantError):
    pass


@dataclass(frozen=True, slots=True)
class PlannedAttackSceneRelationship:
    relationship_id: str
    first_seen: datetime
    last_seen: datetime
    first_ingest_sequence: int
    last_ingest_sequence: int


@dataclass(frozen=True, slots=True)
class PlannedAttackSceneEntity:
    entity_id: str
    first_seen: datetime
    last_seen: datetime
    first_ingest_sequence: int
    last_ingest_sequence: int


@dataclass(frozen=True, slots=True)
class PlannedAttackSceneObservation:
    observation_id: str
    ingest_sequence: int
    event_time: datetime


@dataclass(frozen=True, slots=True)
class PlannedAttackSceneStateTransition:
    ingest_sequence: int
    trigger_observation_id: str
    from_state: IncidentState
    to_state: IncidentState
    reason_code: str


@dataclass(frozen=True, slots=True)
class AttackScenePlan:
    projection_version: str
    tenant_id: str
    scene_id: str
    root_relationship_id: str
    entity_projection_version: str
    relationship_projection_version: str
    correlation_projection_version: str
    title: str
    state: IncidentState
    first_seen: datetime
    last_seen: datetime
    first_ingest_sequence: int
    last_ingest_sequence: int
    relationships: tuple[PlannedAttackSceneRelationship, ...]
    entities: tuple[PlannedAttackSceneEntity, ...]
    observations: tuple[PlannedAttackSceneObservation, ...]
    state_transitions: tuple[PlannedAttackSceneStateTransition, ...]


class AttackSceneMembershipPlanner:
    """Reconstruct attack-scene membership solely from bounded durable lineage."""

    _ROOT = ("COR-SEQ-001", "1", "exploited")
    _JOINING = {
        ("COR-SEQ-002", "1", "compromised"),
        ("COR-SEQ-003", "1", "moved_laterally_to"),
    }

    def __init__(self, session: Session, config: AttackSceneProjectionConfig) -> None:
        self._session = session
        self._config = config
        self._validate_config()

    def _validate_config(self) -> None:
        if self._config.identity.name != ATTACK_SCENE_PROJECTOR_NAME:
            raise AttackScenePlanningInvariantError("invalid attack scene projector identity")
        try:
            policy = resolve_attack_scene_policy(
                self._config.policy_name, self._config.policy_version
            )
        except AttackScenePolicyNotFound as exc:
            raise AttackScenePlanningInvariantError("attack scene policy is not installed") from exc
        if (
            policy.policy_name != self._config.policy_name
            or policy.policy_version != self._config.policy_version
            or policy.manifest != self._config.policy_manifest
            or policy.manifest_sha256() != self._config.policy_manifest_sha256
        ):
            raise AttackScenePlanningInvariantError("attack scene policy configuration mismatch")

    def _require_dependencies(self, through_ingest_sequence: int) -> None:
        if through_ingest_sequence < 0:
            raise ValueError("through_ingest_sequence must be nonnegative")
        for name, version in (
            (ENTITY_PROJECTOR_NAME, self._config.entity_projection_version),
            (RELATIONSHIP_PROJECTOR_NAME, self._config.relationship_projection_version),
            (CORRELATION_PROJECTOR_NAME, self._config.correlation_projection_version),
        ):
            checkpoint = self._session.get(
                ProjectorCheckpointRecord,
                {"projector_name": name, "projector_version": version},
            )
            if checkpoint is None or checkpoint.last_processed_sequence < through_ingest_sequence:
                raise AttackSceneDependencyNotReady(
                    f"dependency {name}/{version} is not ready through "
                    f"sequence {through_ingest_sequence}"
                )

    def list_root_relationship_ids(
        self,
        *,
        tenant_id: str,
        through_ingest_sequence: int,
        after_relationship_id: str | None = None,
        limit: int = 100,
    ) -> tuple[str, ...]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        self._require_dependencies(through_ingest_sequence)
        statement = (
            select(RelationshipRecord.relationship_id)
            .join(
                RelationshipDerivationRecord,
                (
                    RelationshipDerivationRecord.relationship_projection_version
                    == RelationshipRecord.projection_version
                )
                & (RelationshipDerivationRecord.tenant_id == RelationshipRecord.tenant_id)
                & (
                    RelationshipDerivationRecord.relationship_id
                    == RelationshipRecord.relationship_id
                ),
            )
            .where(
                RelationshipRecord.projection_version
                == self._config.relationship_projection_version,
                RelationshipRecord.tenant_id == tenant_id,
                RelationshipRecord.relationship_type == self._ROOT[2],
                RelationshipRecord.provenance == "deterministic",
                RelationshipRecord.rule_id == self._ROOT[0],
                RelationshipRecord.rule_version == self._ROOT[1],
                RelationshipDerivationRecord.correlation_projection_version
                == self._config.correlation_projection_version,
                RelationshipDerivationRecord.rule_id == self._ROOT[0],
                RelationshipDerivationRecord.rule_version == self._ROOT[1],
                RelationshipDerivationRecord.trigger_ingest_sequence <= through_ingest_sequence,
            )
            .distinct()
            .order_by(RelationshipRecord.relationship_id)
            .limit(limit)
        )
        if after_relationship_id is not None:
            statement = statement.where(RelationshipRecord.relationship_id > after_relationship_id)
        roots = tuple(self._session.scalars(statement).all())
        for root_id in roots:
            for derivation in self._derivations(
                tenant_id, root_id, through_ingest_sequence, self._ROOT[:2]
            ):
                self._validate_trigger_lineage(tenant_id, derivation)
        return roots

    def plan_scenes(
        self,
        *,
        tenant_id: str,
        through_ingest_sequence: int,
        after_relationship_id: str | None = None,
        limit: int = 100,
    ) -> tuple[AttackScenePlan, ...]:
        roots = self.list_root_relationship_ids(
            tenant_id=tenant_id,
            through_ingest_sequence=through_ingest_sequence,
            after_relationship_id=after_relationship_id,
            limit=limit,
        )
        return tuple(
            plan
            for root in roots
            if (
                plan := self.plan_scene(
                    tenant_id=tenant_id,
                    root_relationship_id=root,
                    through_ingest_sequence=through_ingest_sequence,
                )
            )
            is not None
        )

    def plan_scene(
        self,
        *,
        tenant_id: str,
        root_relationship_id: str,
        through_ingest_sequence: int,
    ) -> AttackScenePlan | None:
        self._require_dependencies(through_ingest_sequence)
        relationship = self._relationship(tenant_id, root_relationship_id)
        if relationship is None or not self._matches(relationship, self._ROOT):
            return None
        root_derivations = self._derivations(
            tenant_id, root_relationship_id, through_ingest_sequence, self._ROOT[:2]
        )
        if not root_derivations:
            return None
        members = {root_relationship_id}
        for derivation in root_derivations:
            self._validate_trigger_lineage(tenant_id, derivation)
            members.update(self._validated_supports(tenant_id, derivation))

        qualifying_derivations: list[RelationshipDerivationRecord] = []
        queue = deque([root_relationship_id])
        traversed = {root_relationship_id}
        while queue:
            support_id = queue.popleft()
            candidates = self._reverse_derivations(tenant_id, support_id, through_ingest_sequence)
            for derived, derivation in candidates:
                if not self._matches(derived, self._JOINING):
                    continue
                if (derivation.rule_id, derivation.rule_version) != (
                    derived.rule_id,
                    derived.rule_version,
                ):
                    raise AttackScenePlanningInvariantError(
                        "derivation rule identity contradicts its relationship"
                    )
                self._validate_trigger_lineage(tenant_id, derivation)
                self._validated_supports(tenant_id, derivation)
                qualifying_derivations.append(derivation)
                members.add(derived.relationship_id)
                if derived.relationship_id not in traversed:
                    traversed.add(derived.relationship_id)
                    queue.append(derived.relationship_id)

        planned_relationships, observations, entity_spans = self._memberships(
            tenant_id, members, through_ingest_sequence
        )
        entities = self._entities(tenant_id, entity_spans)
        observation_values = tuple(
            sorted(
                observations.values(),
                key=lambda value: (value.ingest_sequence, value.observation_id),
            )
        )
        if not observation_values:
            raise AttackScenePlanningInvariantError("qualifying scene has no observation lineage")
        first_root = min(
            root_derivations,
            key=lambda value: (value.trigger_ingest_sequence, value.trigger_observation_id),
        )
        transitions = [
            PlannedAttackSceneStateTransition(
                first_root.trigger_ingest_sequence,
                first_root.trigger_observation_id,
                IncidentState.POTENTIAL,
                IncidentState.OBSERVED,
                "deterministic_exploit_observed",
            )
        ]
        compromises = [value for value in qualifying_derivations if value.rule_id == "COR-SEQ-002"]
        state = IncidentState.OBSERVED
        if compromises:
            first = min(
                compromises,
                key=lambda value: (value.trigger_ingest_sequence, value.trigger_observation_id),
            )
            transitions.append(
                PlannedAttackSceneStateTransition(
                    first.trigger_ingest_sequence,
                    first.trigger_observation_id,
                    IncidentState.OBSERVED,
                    IncidentState.CONFIRMED,
                    "deterministic_compromise_confirmed",
                )
            )
            state = IncidentState.CONFIRMED
        transitions.sort(key=lambda value: (value.ingest_sequence, value.trigger_observation_id))
        return AttackScenePlan(
            projection_version=self._config.identity.version,
            tenant_id=tenant_id,
            scene_id=attack_scene_id(root_relationship_id),
            root_relationship_id=root_relationship_id,
            entity_projection_version=self._config.entity_projection_version,
            relationship_projection_version=self._config.relationship_projection_version,
            correlation_projection_version=self._config.correlation_projection_version,
            title=f"Attack Scene {root_relationship_id}",
            state=state,
            first_seen=min(value.event_time for value in observation_values),
            last_seen=max(value.event_time for value in observation_values),
            first_ingest_sequence=min(value.ingest_sequence for value in observation_values),
            last_ingest_sequence=max(value.ingest_sequence for value in observation_values),
            relationships=planned_relationships,
            entities=entities,
            observations=observation_values,
            state_transitions=tuple(transitions),
        )

    def _relationship(self, tenant_id: str, relationship_id: str) -> RelationshipRecord | None:
        return self._session.get(
            RelationshipRecord,
            {
                "projection_version": self._config.relationship_projection_version,
                "tenant_id": tenant_id,
                "relationship_id": relationship_id,
            },
        )

    @staticmethod
    def _matches(relationship: RelationshipRecord, expected: object) -> bool:
        identity = (relationship.rule_id, relationship.rule_version, relationship.relationship_type)
        return relationship.provenance == "deterministic" and identity in (
            {expected} if isinstance(expected, tuple) else expected
        )

    def _derivations(
        self, tenant_id: str, relationship_id: str, boundary: int, rule: tuple[str, str]
    ):
        return self._session.scalars(
            select(RelationshipDerivationRecord)
            .where(
                RelationshipDerivationRecord.relationship_projection_version
                == self._config.relationship_projection_version,
                RelationshipDerivationRecord.tenant_id == tenant_id,
                RelationshipDerivationRecord.relationship_id == relationship_id,
                RelationshipDerivationRecord.correlation_projection_version
                == self._config.correlation_projection_version,
                RelationshipDerivationRecord.rule_id == rule[0],
                RelationshipDerivationRecord.rule_version == rule[1],
                RelationshipDerivationRecord.trigger_ingest_sequence <= boundary,
            )
            .order_by(
                RelationshipDerivationRecord.trigger_ingest_sequence,
                RelationshipDerivationRecord.trigger_observation_id,
            )
        ).all()

    def _reverse_derivations(self, tenant_id: str, support_id: str, boundary: int):
        return self._session.execute(
            select(RelationshipRecord, RelationshipDerivationRecord)
            .join(
                RelationshipDerivationRecord,
                (
                    RelationshipDerivationRecord.relationship_projection_version
                    == RelationshipRecord.projection_version
                )
                & (RelationshipDerivationRecord.tenant_id == RelationshipRecord.tenant_id)
                & (
                    RelationshipDerivationRecord.relationship_id
                    == RelationshipRecord.relationship_id
                ),
            )
            .join(
                RelationshipDerivationSupportRecord,
                (
                    RelationshipDerivationSupportRecord.relationship_projection_version
                    == RelationshipDerivationRecord.relationship_projection_version
                )
                & (
                    RelationshipDerivationSupportRecord.tenant_id
                    == RelationshipDerivationRecord.tenant_id
                )
                & (
                    RelationshipDerivationSupportRecord.relationship_id
                    == RelationshipDerivationRecord.relationship_id
                )
                & (
                    RelationshipDerivationSupportRecord.trigger_observation_id
                    == RelationshipDerivationRecord.trigger_observation_id
                ),
            )
            .where(
                RelationshipRecord.projection_version
                == self._config.relationship_projection_version,
                RelationshipRecord.tenant_id == tenant_id,
                RelationshipDerivationRecord.correlation_projection_version
                == self._config.correlation_projection_version,
                RelationshipDerivationRecord.trigger_ingest_sequence <= boundary,
                RelationshipDerivationSupportRecord.support_relationship_id == support_id,
            )
            .order_by(
                RelationshipRecord.relationship_id,
                RelationshipDerivationRecord.trigger_ingest_sequence,
                RelationshipDerivationRecord.trigger_observation_id,
            )
        ).all()

    def _validate_trigger_lineage(
        self, tenant_id: str, derivation: RelationshipDerivationRecord
    ) -> None:
        lineage = self._session.get(
            RelationshipObservationRecord,
            {
                "projection_version": self._config.relationship_projection_version,
                "tenant_id": tenant_id,
                "relationship_id": derivation.relationship_id,
                "observation_id": derivation.trigger_observation_id,
            },
        )
        if lineage is None or lineage.ingest_sequence != derivation.trigger_ingest_sequence:
            raise AttackScenePlanningInvariantError("derivation trigger lineage is contradictory")

    def _validated_supports(
        self, tenant_id: str, derivation: RelationshipDerivationRecord
    ) -> set[str]:
        support_ids = set(
            self._session.scalars(
                select(RelationshipDerivationSupportRecord.support_relationship_id).where(
                    RelationshipDerivationSupportRecord.relationship_projection_version
                    == self._config.relationship_projection_version,
                    RelationshipDerivationSupportRecord.tenant_id == tenant_id,
                    RelationshipDerivationSupportRecord.relationship_id
                    == derivation.relationship_id,
                    RelationshipDerivationSupportRecord.trigger_observation_id
                    == derivation.trigger_observation_id,
                )
            ).all()
        )
        for support_id in support_ids:
            exists = self._session.scalar(
                select(RelationshipObservationRecord.observation_id)
                .where(
                    RelationshipObservationRecord.projection_version
                    == self._config.relationship_projection_version,
                    RelationshipObservationRecord.tenant_id == tenant_id,
                    RelationshipObservationRecord.relationship_id == support_id,
                    RelationshipObservationRecord.ingest_sequence
                    < derivation.trigger_ingest_sequence,
                )
                .limit(1)
            )
            if exists is None:
                raise AttackScenePlanningInvariantError("derivation support lacks prior lineage")
        return support_ids

    def _memberships(self, tenant_id: str, members: set[str], boundary: int):
        planned: list[PlannedAttackSceneRelationship] = []
        observations: dict[str, PlannedAttackSceneObservation] = {}
        entity_spans: dict[str, list[object]] = {}
        for relationship_id in sorted(members):
            relationship = self._relationship(tenant_id, relationship_id)
            if relationship is None:
                raise AttackScenePlanningInvariantError("member relationship is missing")
            rows = self._session.scalars(
                select(RelationshipObservationRecord)
                .where(
                    RelationshipObservationRecord.projection_version
                    == self._config.relationship_projection_version,
                    RelationshipObservationRecord.tenant_id == tenant_id,
                    RelationshipObservationRecord.relationship_id == relationship_id,
                    RelationshipObservationRecord.ingest_sequence <= boundary,
                )
                .order_by(RelationshipObservationRecord.ingest_sequence)
            ).all()
            if not rows:
                raise AttackScenePlanningInvariantError(
                    "member relationship has no bounded lineage"
                )
            span = PlannedAttackSceneRelationship(
                relationship_id,
                min(row.event_time for row in rows),
                max(row.event_time for row in rows),
                min(row.ingest_sequence for row in rows),
                max(row.ingest_sequence for row in rows),
            )
            planned.append(span)
            for row in rows:
                value = PlannedAttackSceneObservation(
                    row.observation_id, row.ingest_sequence, row.event_time
                )
                prior = observations.get(row.observation_id)
                if prior is not None and prior != value:
                    raise AttackScenePlanningInvariantError("observation lineage is contradictory")
                observations[row.observation_id] = value
            for entity_id in (relationship.source_entity_id, relationship.target_entity_id):
                values = entity_spans.setdefault(entity_id, [])
                values.append(span)
        return tuple(planned), observations, entity_spans

    def _entities(self, tenant_id: str, spans: dict[str, list[object]]):
        planned = []
        for entity_id in sorted(spans):
            entity = self._session.get(
                EntityRecord,
                {
                    "projection_version": self._config.entity_projection_version,
                    "tenant_id": tenant_id,
                    "entity_id": entity_id,
                },
            )
            if entity is None:
                raise AttackScenePlanningInvariantError("member endpoint entity is missing")
            values = spans[entity_id]
            planned.append(
                PlannedAttackSceneEntity(
                    entity_id,
                    min(value.first_seen for value in values),
                    max(value.last_seen for value in values),
                    min(value.first_ingest_sequence for value in values),
                    max(value.last_ingest_sequence for value in values),
                )
            )
        return tuple(planned)
