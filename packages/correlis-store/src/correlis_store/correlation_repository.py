from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime

from correlis_ontology import CORE_ONTOLOGY, OntologyRegistry
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .correlation_rules import BUILTIN_CORRELATION_RULES, CorrelationRuleRegistry
from .correlations import CorrelationProjectionConfig
from .models import CorrelationProjectionConfigRecord, ProjectorCheckpointRecord
from .projections import (
    ProjectorIdentity,
    ProjectorNotRegistered,
    ProjectorStateConflict,
    ProjectorStatus,
)
from .relationship_projection import (
    RELATIONSHIP_PROJECTOR_NAME,
    correlation_projector_identity,
)

MAX_LIMIT = 500


def _now() -> datetime:
    return datetime.now(UTC)


def _from_record(r: CorrelationProjectionConfigRecord) -> CorrelationProjectionConfig:
    return CorrelationProjectionConfig(
        identity=ProjectorIdentity(r.projector_name, r.projection_version),
        relationship_projection_version=r.relationship_projection_version,
        ruleset_name=r.ruleset_name,
        ruleset_version=r.ruleset_version,
        rule_manifest_sha256=r.rule_manifest_sha256,
        rule_manifest=deepcopy(r.rule_manifest_json),
        ontology_name=r.ontology_name,
        ontology_version=r.ontology_version,
        created_at=r.created_at
        if r.created_at.tzinfo is not None
        else r.created_at.replace(tzinfo=UTC),
    )


class CorrelationRepository:
    def __init__(
        self,
        session_or_factory: Session | sessionmaker[Session] | Callable[[], Session],
        *,
        clock: Callable[[], datetime] | None = None,
    ):
        self._session_or_factory = session_or_factory
        self._clock = clock or _now

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
        relationship_projection_version: str,
        rule_registry: CorrelationRuleRegistry = BUILTIN_CORRELATION_RULES,
        ontology_registry: OntologyRegistry = CORE_ONTOLOGY,
    ) -> CorrelationProjectionConfig:
        identity = correlation_projector_identity(projection_version)
        manifest = rule_registry.manifest()
        manifest_hash = rule_registry.manifest_sha256()
        with self._session_scope() as session:
            try:
                rel = session.get(
                    ProjectorCheckpointRecord,
                    {
                        "projector_name": RELATIONSHIP_PROJECTOR_NAME,
                        "projector_version": relationship_projection_version,
                    },
                )
                if rel is None:
                    raise ProjectorNotRegistered("relationship projector is not registered")
                existing = session.get(
                    CorrelationProjectionConfigRecord,
                    {"projector_name": identity.name, "projection_version": identity.version},
                )
                if existing is not None:
                    expected = (
                        relationship_projection_version,
                        rule_registry.name,
                        rule_registry.version,
                        manifest_hash,
                        manifest,
                        ontology_registry.name,
                        ontology_registry.version,
                    )
                    actual = (
                        existing.relationship_projection_version,
                        existing.ruleset_name,
                        existing.ruleset_version,
                        existing.rule_manifest_sha256,
                        existing.rule_manifest_json,
                        existing.ontology_name,
                        existing.ontology_version,
                    )
                    if actual != expected:
                        raise ProjectorStateConflict(
                            "correlation projection configuration conflicts with existing "
                            "registration"
                        )
                    cp = session.get(
                        ProjectorCheckpointRecord,
                        {"projector_name": identity.name, "projector_version": identity.version},
                    )
                    if cp is None:
                        raise ProjectorStateConflict(
                            "correlation projection configuration has no checkpoint"
                        )
                    return _from_record(existing)
                claimed = session.scalar(
                    select(CorrelationProjectionConfigRecord).where(
                        CorrelationProjectionConfigRecord.relationship_projector_name
                        == RELATIONSHIP_PROJECTOR_NAME,
                        CorrelationProjectionConfigRecord.relationship_projection_version
                        == relationship_projection_version,
                    )
                )
                if claimed is not None:
                    raise ProjectorStateConflict(
                        "relationship graph is already claimed by another correlation configuration"
                    )
                now = self._clock()
                cp = session.get(
                    ProjectorCheckpointRecord,
                    {"projector_name": identity.name, "projector_version": identity.version},
                )
                if cp is None:
                    cp = ProjectorCheckpointRecord(
                        projector_name=identity.name,
                        projector_version=identity.version,
                        last_processed_sequence=0,
                        status=ProjectorStatus.IDLE,
                        last_failure_sequence=None,
                        created_at=now,
                        updated_at=now,
                        last_processed_at=None,
                    )
                    session.add(cp)
                    session.flush()
                rec = CorrelationProjectionConfigRecord(
                    projector_name=identity.name,
                    projection_version=identity.version,
                    relationship_projector_name=RELATIONSHIP_PROJECTOR_NAME,
                    relationship_projection_version=relationship_projection_version,
                    ruleset_name=rule_registry.name,
                    ruleset_version=rule_registry.version,
                    rule_manifest_sha256=manifest_hash,
                    rule_manifest_json=manifest,
                    ontology_name=ontology_registry.name,
                    ontology_version=ontology_registry.version,
                    created_at=now,
                )
                session.add(rec)
                session.commit()
                return _from_record(rec)
            except IntegrityError:
                session.rollback()
                rec = session.get(
                    CorrelationProjectionConfigRecord,
                    {"projector_name": identity.name, "projection_version": identity.version},
                )
                if (
                    rec is not None
                    and rec.relationship_projection_version == relationship_projection_version
                    and rec.rule_manifest_sha256 == manifest_hash
                ):
                    return _from_record(rec)
                raise
            except Exception:
                session.rollback()
                raise

    def get_projection_config(self, projection_version: str) -> CorrelationProjectionConfig | None:
        identity = correlation_projector_identity(projection_version)
        with self._session_scope() as session:
            rec = session.get(
                CorrelationProjectionConfigRecord,
                {"projector_name": identity.name, "projection_version": identity.version},
            )
            return _from_record(rec) if rec is not None else None

    def list_projection_configs(
        self, *, limit: int = 100
    ) -> tuple[CorrelationProjectionConfig, ...]:
        if limit < 1 or limit > MAX_LIMIT:
            raise ValueError("limit must be between 1 and 500")
        with self._session_scope() as session:
            rows = session.scalars(
                select(CorrelationProjectionConfigRecord)
                .order_by(CorrelationProjectionConfigRecord.projection_version)
                .limit(limit)
            ).all()
            return tuple(_from_record(r) for r in rows)
