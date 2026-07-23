from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from .models import ProjectorCheckpointRecord, ProjectorFailureRecord
from .projections import (
    ProjectorBusy,
    ProjectorCheckpoint,
    ProjectorFailed,
    ProjectorFailure,
    ProjectorFailureStatus,
    ProjectorIdentity,
    ProjectorNotRegistered,
    ProjectorStateConflict,
    ProjectorStatus,
)
from .projector_locking import is_lock_not_available

MAX_LIMIT = 500


def _now() -> datetime:
    return datetime.now(UTC)


def _limit(limit: int) -> int:
    if limit < 1 or limit > MAX_LIMIT:
        raise ValueError("limit must be between 1 and 500")
    return limit


def checkpoint_from_record(r: ProjectorCheckpointRecord) -> ProjectorCheckpoint:
    return ProjectorCheckpoint(
        ProjectorIdentity(r.projector_name, r.projector_version),
        int(r.last_processed_sequence),
        ProjectorStatus(r.status),
        int(r.last_failure_sequence) if r.last_failure_sequence is not None else None,
        r.created_at,
        r.updated_at,
        r.last_processed_at,
    )


def failure_from_record(r: ProjectorFailureRecord) -> ProjectorFailure:
    return ProjectorFailure(
        ProjectorIdentity(r.projector_name, r.projector_version),
        int(r.ingest_sequence),
        r.tenant_id,
        r.observation_id,
        ProjectorFailureStatus(r.status),
        int(r.attempt_count),
        r.error_code,
        r.error_type,
        r.safe_message,
        r.first_failed_at,
        r.last_failed_at,
        r.resolved_at,
    )


class ProjectionRepository:
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

    def register_projector(self, identity: ProjectorIdentity) -> ProjectorCheckpoint:
        if identity.name == "correlation-projection":
            raise RuntimeError(
                "correlation-projection is reserved; use correlis-admin "
                "correlation-projection register"
            )
        with self._session_scope() as session:
            rec = session.get(
                ProjectorCheckpointRecord,
                {"projector_name": identity.name, "projector_version": identity.version},
            )
            if rec is not None:
                return checkpoint_from_record(rec)
            now = self._clock()
            rec = ProjectorCheckpointRecord(
                projector_name=identity.name,
                projector_version=identity.version,
                last_processed_sequence=0,
                status=ProjectorStatus.IDLE,
                last_failure_sequence=None,
                created_at=now,
                updated_at=now,
                last_processed_at=None,
            )
            session.add(rec)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                rec = session.get(
                    ProjectorCheckpointRecord,
                    {"projector_name": identity.name, "projector_version": identity.version},
                )
                if rec is None:
                    raise
            return checkpoint_from_record(rec)

    def get_checkpoint(self, identity: ProjectorIdentity) -> ProjectorCheckpoint | None:
        with self._session_scope() as session:
            rec = session.get(
                ProjectorCheckpointRecord,
                {"projector_name": identity.name, "projector_version": identity.version},
            )
            return checkpoint_from_record(rec) if rec is not None else None

    def list_checkpoints(self, *, limit: int = 100) -> list[ProjectorCheckpoint]:
        with self._session_scope() as session:
            rows = session.scalars(
                select(ProjectorCheckpointRecord)
                .order_by(
                    ProjectorCheckpointRecord.projector_name,
                    ProjectorCheckpointRecord.projector_version,
                )
                .limit(_limit(limit))
            ).all()
            return [checkpoint_from_record(r) for r in rows]

    def _required(self, session: Session, identity: ProjectorIdentity) -> ProjectorCheckpointRecord:
        rec = session.get(
            ProjectorCheckpointRecord,
            {"projector_name": identity.name, "projector_version": identity.version},
        )
        if rec is None:
            raise ProjectorNotRegistered(
                f"projector {identity.name}/{identity.version} is not registered"
            )
        return rec

    def _locked_required(
        self, session: Session, identity: ProjectorIdentity
    ) -> ProjectorCheckpointRecord:
        stmt = (
            select(ProjectorCheckpointRecord)
            .where(
                ProjectorCheckpointRecord.projector_name == identity.name,
                ProjectorCheckpointRecord.projector_version == identity.version,
            )
            .with_for_update(nowait=True)
        )
        try:
            rec = session.scalar(stmt)
        except OperationalError as exc:
            if is_lock_not_available(exc):
                raise ProjectorBusy(
                    f"projector {identity.name}/{identity.version} is busy"
                ) from exc
            raise
        if rec is None:
            raise ProjectorNotRegistered(
                f"projector {identity.name}/{identity.version} is not registered"
            )
        return rec

    def pause_projector(self, identity: ProjectorIdentity) -> ProjectorCheckpoint:
        with self._session_scope() as session:
            rec = self._locked_required(session, identity)
            if rec.status == ProjectorStatus.FAILED:
                raise ProjectorStateConflict("failed projectors cannot be paused")
            if rec.status != ProjectorStatus.PAUSED:
                rec.status = ProjectorStatus.PAUSED
                rec.updated_at = self._clock()
                session.commit()
            return checkpoint_from_record(rec)

    def resume_projector(self, identity: ProjectorIdentity) -> ProjectorCheckpoint:
        with self._session_scope() as session:
            rec = self._locked_required(session, identity)
            if rec.status == ProjectorStatus.FAILED:
                raise ProjectorFailed("failed projectors require explicit retry")
            if rec.status != ProjectorStatus.IDLE:
                rec.status = ProjectorStatus.IDLE
                rec.updated_at = self._clock()
                session.commit()
            return checkpoint_from_record(rec)

    def list_failures(
        self,
        identity: ProjectorIdentity,
        *,
        status: ProjectorFailureStatus | None = None,
        limit: int = 100,
    ) -> list[ProjectorFailure]:
        with self._session_scope() as session:
            self._required(session, identity)
            stmt = select(ProjectorFailureRecord).where(
                ProjectorFailureRecord.projector_name == identity.name,
                ProjectorFailureRecord.projector_version == identity.version,
            )
            if status is not None:
                stmt = stmt.where(ProjectorFailureRecord.status == status)
            rows = session.scalars(
                stmt.order_by(
                    ProjectorFailureRecord.last_failed_at.desc(),
                    ProjectorFailureRecord.ingest_sequence.desc(),
                ).limit(_limit(limit))
            ).all()
            return [failure_from_record(r) for r in rows]
