from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from correlis_schema import Observation
from sqlalchemy import and_, select
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from .models import (
    ObservationIngestEntryRecord,
    ObservationRecord,
    ProjectorCheckpointRecord,
    ProjectorFailureRecord,
)
from .observation_sequence import ObservationSequenceAllocator, SequencedObservation
from .projections import (
    ProjectionHandler,
    ProjectionHandlerError,
    ProjectionInvariantError,
    ProjectionRunOutcome,
    ProjectionRunResult,
    ProjectorBusy,
    ProjectorFailureStatus,
    ProjectorIdentity,
    ProjectorNotRegistered,
    ProjectorStatus,
)
from .projector_locking import is_lock_not_available

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


def _limit(limit: int) -> int:
    if limit < 1 or limit > 500:
        raise ValueError("limit must be between 1 and 500")
    return limit



class ProjectionRunner:
    def __init__(
        self, session_factory: sessionmaker[Session], *, clock: Callable[[], datetime] | None = None
    ) -> None:
        if isinstance(session_factory, Session) or not callable(session_factory):
            raise TypeError("ProjectionRunner requires a SQLAlchemy session factory")
        self._session_factory = session_factory
        self._clock = clock or _now

    def run_batch(
        self,
        identity: ProjectorIdentity,
        handler: ProjectionHandler,
        *,
        limit: int = 100,
        retry_failed: bool = False,
    ) -> ProjectionRunResult:
        limit = _limit(limit)
        session = self._session_factory()
        try:
            with session.begin():
                rec = self._locked_checkpoint(session, identity)
                self._validate_checkpoint_state(session, rec)
                starting = int(rec.last_processed_sequence)
                if rec.status == ProjectorStatus.PAUSED:
                    return ProjectionRunResult(
                        identity, ProjectionRunOutcome.PAUSED, starting, starting, starting, 0, None
                    )
                high = ObservationSequenceAllocator().high_watermark(session)
                if rec.status == ProjectorStatus.FAILED:
                    if int(rec.last_failure_sequence) > high:
                        raise ProjectionInvariantError(
                            "failed checkpoint exceeds captured high watermark"
                        )
                    failed_item = self._load_item(session, int(rec.last_failure_sequence))
                    self._require_active_failure(session, rec, failed_item)
                    if not retry_failed:
                        return ProjectionRunResult(
                            identity,
                            ProjectionRunOutcome.BLOCKED,
                            starting,
                            starting,
                            high,
                            0,
                            int(rec.last_failure_sequence),
                        )

                processed = 0
                failure_sequence = None
                attempted_failed = False
                while processed < limit and int(rec.last_processed_sequence) < high:
                    next_seq = int(rec.last_processed_sequence) + 1
                    retry_failure = None
                    if rec.status == ProjectorStatus.FAILED:
                        if int(rec.last_failure_sequence) != next_seq or attempted_failed:
                            raise ProjectionInvariantError(
                                "failed checkpoint does not match next sequence"
                            )
                        attempted_failed = True
                    item = self._load_item(session, next_seq)
                    if rec.status == ProjectorStatus.FAILED:
                        retry_failure = self._require_active_failure(session, rec, item)
                    try:
                        with session.begin_nested():
                            handler(session, item)
                    except ProjectionHandlerError as exc:
                        now = self._clock()
                        code, etype, msg = self._safe_error(exc)
                        self._upsert_failure(session, rec, item, code, etype, msg, now)
                        rec.status = ProjectorStatus.FAILED
                        rec.last_failure_sequence = item.ingest_sequence
                        rec.updated_at = now
                        failure_sequence = item.ingest_sequence
                        logger.info(
                            "projection handler failed",
                            extra={
                                "projector_name": identity.name,
                                "projector_version": identity.version,
                                "failure_sequence": failure_sequence,
                                "error_code": code,
                                "error_type": etype,
                                "outcome": "failed",
                            },
                        )
                        break
                    except SQLAlchemyError:
                        raise
                    except Exception as exc:
                        now = self._clock()
                        code, etype, msg = self._safe_error(exc)
                        self._upsert_failure(session, rec, item, code, etype, msg, now)
                        rec.status = ProjectorStatus.FAILED
                        rec.last_failure_sequence = item.ingest_sequence
                        rec.updated_at = now
                        failure_sequence = item.ingest_sequence
                        logger.info(
                            "projection handler failed",
                            extra={
                                "projector_name": identity.name,
                                "projector_version": identity.version,
                                "failure_sequence": failure_sequence,
                                "error_code": code,
                                "error_type": etype,
                                "outcome": "failed",
                            },
                        )
                        break
                    now = self._clock()
                    rec.last_processed_sequence = item.ingest_sequence
                    rec.status = ProjectorStatus.IDLE
                    rec.last_failure_sequence = None
                    rec.last_processed_at = now
                    rec.updated_at = now
                    self._resolve_failure(session, rec, item, now, retry_failure=retry_failure)
                    processed += 1
                ending = int(rec.last_processed_sequence)
                if failure_sequence is not None:
                    outcome = ProjectionRunOutcome.FAILED
                elif ending == high:
                    outcome = ProjectionRunOutcome.CAUGHT_UP
                elif processed > 0:
                    outcome = ProjectionRunOutcome.ADVANCED
                else:
                    raise ProjectionInvariantError("work exists but no observation was processed")
                result = ProjectionRunResult(
                    identity, outcome, starting, ending, high, processed, failure_sequence
                )
            return result
        finally:
            session.close()

    def _locked_checkpoint(
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

    def _validate_checkpoint_state(
        self, session: Session, rec: ProjectorCheckpointRecord
    ) -> None:
        if rec.status in (ProjectorStatus.IDLE, ProjectorStatus.PAUSED):
            if rec.last_failure_sequence is not None:
                raise ProjectionInvariantError(
                    "idle or paused checkpoint cannot retain a failure sequence"
                )
            return
        if rec.status == ProjectorStatus.FAILED:
            if rec.last_failure_sequence is None:
                raise ProjectionInvariantError("failed checkpoint is missing failure sequence")
            if int(rec.last_failure_sequence) <= int(rec.last_processed_sequence):
                raise ProjectionInvariantError("failed checkpoint failure sequence is not ahead")
            if int(rec.last_failure_sequence) != int(rec.last_processed_sequence) + 1:
                raise ProjectionInvariantError("failed checkpoint does not match next sequence")
            failure = session.get(
                ProjectorFailureRecord,
                {
                    "projector_name": rec.projector_name,
                    "projector_version": rec.projector_version,
                    "ingest_sequence": int(rec.last_failure_sequence),
                },
            )
            if failure is None or failure.status != ProjectorFailureStatus.ACTIVE:
                raise ProjectionInvariantError("failed checkpoint is missing active failure")
            return
        raise ProjectionInvariantError("unknown projector checkpoint status")

    def _require_active_failure(
        self,
        session: Session,
        rec: ProjectorCheckpointRecord,
        item: SequencedObservation,
    ) -> ProjectorFailureRecord:
        failure = session.get(
            ProjectorFailureRecord,
            {
                "projector_name": rec.projector_name,
                "projector_version": rec.projector_version,
                "ingest_sequence": item.ingest_sequence,
            },
        )
        if (
            failure is None
            or failure.status != ProjectorFailureStatus.ACTIVE
            or failure.tenant_id != item.observation.tenant_id
            or failure.observation_id != item.observation.id
        ):
            raise ProjectionInvariantError("failed checkpoint does not match active failure")
        return failure

    def _load_item(self, session: Session, sequence: int) -> SequencedObservation:
        row = session.execute(
            select(ObservationIngestEntryRecord, ObservationRecord)
            .outerjoin(
                ObservationRecord,
                and_(
                    ObservationRecord.tenant_id == ObservationIngestEntryRecord.tenant_id,
                    ObservationRecord.observation_id == ObservationIngestEntryRecord.observation_id,
                ),
            )
            .where(ObservationIngestEntryRecord.ingest_sequence == sequence)
        ).first()
        if row is None:
            raise ProjectionInvariantError(f"sequence entry {sequence} is missing")
        entry, record = row
        if record is None:
            raise ProjectionInvariantError(f"sequence entry {sequence} is missing its observation")
        return SequencedObservation(
            int(entry.ingest_sequence), Observation.model_validate(record.payload_json)
        )

    def _safe_error(self, exc: Exception) -> tuple[str, str, str]:
        if isinstance(exc, ProjectionHandlerError):
            return exc.code, exc.__class__.__name__, exc.safe_message
        return (
            "unhandled_projection_error",
            exc.__class__.__name__,
            "Projection handler failed unexpectedly.",
        )

    def _upsert_failure(
        self,
        session: Session,
        rec: ProjectorCheckpointRecord,
        item: SequencedObservation,
        code: str,
        etype: str,
        msg: str,
        now: datetime,
    ) -> None:
        failure = session.get(
            ProjectorFailureRecord,
            {
                "projector_name": rec.projector_name,
                "projector_version": rec.projector_version,
                "ingest_sequence": item.ingest_sequence,
            },
        )
        if failure is None:
            failure = ProjectorFailureRecord(
                projector_name=rec.projector_name,
                projector_version=rec.projector_version,
                ingest_sequence=item.ingest_sequence,
                tenant_id=item.observation.tenant_id,
                observation_id=item.observation.id,
                status=ProjectorFailureStatus.ACTIVE,
                attempt_count=1,
                error_code=code,
                error_type=etype,
                safe_message=msg,
                first_failed_at=now,
                last_failed_at=now,
                resolved_at=None,
            )
            session.add(failure)
        else:
            failure.status = ProjectorFailureStatus.ACTIVE
            failure.attempt_count += 1
            failure.error_code = code
            failure.error_type = etype
            failure.safe_message = msg
            failure.last_failed_at = now
            failure.resolved_at = None
        session.flush()

    def _resolve_failure(
        self,
        session: Session,
        rec: ProjectorCheckpointRecord,
        item: SequencedObservation,
        now: datetime,
        *,
        retry_failure: ProjectorFailureRecord | None,
    ) -> None:
        if retry_failure is None:
            return
        failure = self._require_active_failure(session, rec, item)
        if failure is not retry_failure:
            raise ProjectionInvariantError("retry failure record changed during processing")
        failure.status = ProjectorFailureStatus.RESOLVED
        failure.resolved_at = now
