from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .attack_scene_policy import (
    BUILTIN_ATTACK_SCENE_POLICY_NAME,
    BUILTIN_ATTACK_SCENE_POLICY_VERSION,
    resolve_attack_scene_policy,
)
from .attack_scene_projection import AttackSceneProjectionConfig, attack_scene_projector_identity
from .entity_projection import ENTITY_PROJECTOR_NAME
from .models import (
    AttackSceneProjectionConfigRecord,
    CorrelationProjectionConfigRecord,
    ProjectorCheckpointRecord,
)
from .projections import (
    ProjectorIdentity,
    ProjectorNotRegistered,
    ProjectorStateConflict,
    ProjectorStatus,
)
from .relationship_projection import CORRELATION_PROJECTOR_NAME, RELATIONSHIP_PROJECTOR_NAME


def _from_record(row: AttackSceneProjectionConfigRecord) -> AttackSceneProjectionConfig:
    return AttackSceneProjectionConfig(
        identity=ProjectorIdentity(row.projector_name, row.projection_version),
        entity_projection_version=row.entity_projection_version,
        relationship_projection_version=row.relationship_projection_version,
        correlation_projection_version=row.correlation_projection_version,
        policy_name=row.policy_name,
        policy_version=row.policy_version,
        policy_manifest_sha256=row.policy_manifest_sha256,
        policy_manifest=deepcopy(row.policy_manifest_json),
        created_at=row.created_at if row.created_at.tzinfo else row.created_at.replace(tzinfo=UTC),
    )


class AttackSceneProjectionRepository:
    def __init__(
        self,
        session_or_factory: Session | sessionmaker[Session] | Callable[[], Session],
        *,
        clock: Callable[[], datetime] | None = None,
    ):
        self._session_or_factory = session_or_factory
        self._clock = clock or (lambda: datetime.now(UTC))

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

    def register_projection(
        self,
        *,
        projection_version: str,
        entity_projection_version: str,
        relationship_projection_version: str,
        correlation_projection_version: str,
        policy_name: str = BUILTIN_ATTACK_SCENE_POLICY_NAME,
        policy_version: str = BUILTIN_ATTACK_SCENE_POLICY_VERSION,
    ) -> AttackSceneProjectionConfig:
        identity = attack_scene_projector_identity(projection_version)
        policy = resolve_attack_scene_policy(policy_name, policy_version)
        manifest = deepcopy(policy.manifest)
        manifest_hash = policy.manifest_sha256()
        expected = (
            entity_projection_version,
            relationship_projection_version,
            correlation_projection_version,
            policy_name,
            policy_version,
            manifest_hash,
            manifest,
        )
        with self._session_scope() as session:
            try:
                for name, version, label in (
                    (ENTITY_PROJECTOR_NAME, entity_projection_version, "entity"),
                    (RELATIONSHIP_PROJECTOR_NAME, relationship_projection_version, "relationship"),
                ):
                    if (
                        session.get(
                            ProjectorCheckpointRecord,
                            {"projector_name": name, "projector_version": version},
                        )
                        is None
                    ):
                        raise ProjectorNotRegistered(f"{label} projector is not registered")
                correlation = session.get(
                    CorrelationProjectionConfigRecord,
                    {
                        "projector_name": CORRELATION_PROJECTOR_NAME,
                        "projection_version": correlation_projection_version,
                    },
                )
                if correlation is None:
                    raise ProjectorNotRegistered(
                        "correlation projection configuration is not registered"
                    )
                if correlation.relationship_projection_version != relationship_projection_version:
                    raise ProjectorStateConflict(
                        "correlation configuration uses a different relationship graph"
                    )
                existing = session.get(
                    AttackSceneProjectionConfigRecord,
                    {"projector_name": identity.name, "projection_version": identity.version},
                )
                if existing is not None:
                    self._verify(existing, expected, session)
                    return _from_record(existing)
                now = self._clock()
                checkpoint = session.get(
                    ProjectorCheckpointRecord,
                    {"projector_name": identity.name, "projector_version": identity.version},
                )
                if checkpoint is None:
                    session.add(
                        ProjectorCheckpointRecord(
                            projector_name=identity.name,
                            projector_version=identity.version,
                            last_processed_sequence=0,
                            status=ProjectorStatus.IDLE,
                            last_failure_sequence=None,
                            created_at=now,
                            updated_at=now,
                            last_processed_at=None,
                        )
                    )
                    session.flush()
                record = AttackSceneProjectionConfigRecord(
                    projector_name=identity.name,
                    projection_version=identity.version,
                    entity_projector_name=ENTITY_PROJECTOR_NAME,
                    entity_projection_version=entity_projection_version,
                    relationship_projector_name=RELATIONSHIP_PROJECTOR_NAME,
                    relationship_projection_version=relationship_projection_version,
                    correlation_projector_name=CORRELATION_PROJECTOR_NAME,
                    correlation_projection_version=correlation_projection_version,
                    policy_name=policy_name,
                    policy_version=policy_version,
                    policy_manifest_sha256=manifest_hash,
                    policy_manifest_json=manifest,
                    created_at=now,
                )
                session.add(record)
                session.commit()
                return _from_record(record)
            except IntegrityError:
                session.rollback()
                existing = session.get(
                    AttackSceneProjectionConfigRecord,
                    {"projector_name": identity.name, "projection_version": identity.version},
                )
                if existing is not None:
                    self._verify(existing, expected, session)
                    return _from_record(existing)
                raise
            except Exception:
                session.rollback()
                raise

    @staticmethod
    def _verify(
        record: AttackSceneProjectionConfigRecord, expected: tuple[object, ...], session: Session
    ) -> None:
        actual = (
            record.entity_projection_version,
            record.relationship_projection_version,
            record.correlation_projection_version,
            record.policy_name,
            record.policy_version,
            record.policy_manifest_sha256,
            record.policy_manifest_json,
        )
        if actual != expected:
            raise ProjectorStateConflict(
                "attack scene projection configuration conflicts with existing registration"
            )
        if (
            session.get(
                ProjectorCheckpointRecord,
                {
                    "projector_name": record.projector_name,
                    "projector_version": record.projection_version,
                },
            )
            is None
        ):
            raise ProjectorStateConflict("attack scene projection configuration has no checkpoint")

    def get_projection_config(self, projection_version: str) -> AttackSceneProjectionConfig | None:
        identity = attack_scene_projector_identity(projection_version)
        with self._session_scope() as session:
            row = session.get(
                AttackSceneProjectionConfigRecord,
                {"projector_name": identity.name, "projection_version": identity.version},
            )
            return _from_record(row) if row else None

    def list_projection_configs(
        self, *, limit: int = 100
    ) -> tuple[AttackSceneProjectionConfig, ...]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        with self._session_scope() as session:
            rows = session.scalars(
                select(AttackSceneProjectionConfigRecord)
                .order_by(AttackSceneProjectionConfigRecord.projection_version)
                .limit(limit)
            ).all()
            return tuple(_from_record(row) for row in rows)
