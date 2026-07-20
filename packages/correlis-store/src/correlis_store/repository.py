from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime

from correlis_schema import EvidenceRef, Observation
from sqlalchemy import Select, and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .errors import (
    ImmutableRecordConflict,
    ObservationSequenceCursorError,
    ObservationSequenceInvariantError,
)
from .hashing import canonical_model_json, canonical_model_sha256
from .models import (
    EvidenceRefRecord,
    ObservationEvidenceRecord,
    ObservationIngestEntryRecord,
    ObservationRecord,
)
from .observation_queries import (
    ObservationPageAnchor,
    ObservationQueryFilters,
    ObservationQueryPage,
)
from .observation_sequence import (
    MAX_SEQUENCE_PAGE_LIMIT,
    ObservationSequenceAllocator,
    ObservationSequencePage,
    ObservationWriteResult,
    SequencedObservation,
    WriteDisposition,
)

MAX_LIST_LIMIT = 500


class ObservationRepository:
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

    def put(self, observation: Observation) -> WriteDisposition:
        return self.put_with_result(observation).disposition

    def put_with_result(self, observation: Observation) -> ObservationWriteResult:
        retry_after_integrity_error = False
        while True:
            with self._session_scope() as session:
                try:
                    result = self._put_with_session(session, observation)
                    session.commit()
                    return result
                except IntegrityError:
                    session.rollback()
                    if retry_after_integrity_error:
                        raise
                    retry_after_integrity_error = True
                except Exception:
                    session.rollback()
                    raise

    def _put_with_session(
        self, session: Session, observation: Observation
    ) -> ObservationWriteResult:
        tenant_id = observation.tenant_id
        observation_hash = canonical_model_sha256(observation)
        existing_observation = session.get(
            ObservationRecord, {"tenant_id": tenant_id, "observation_id": observation.id}
        )
        if existing_observation is not None:
            if existing_observation.payload_sha256 != observation_hash:
                raise ImmutableRecordConflict("observation", tenant_id, observation.id)
            self._ensure_evidence_matches(session, observation)
            sequence = self._existing_sequence(session, tenant_id, observation.id)
            return ObservationWriteResult(WriteDisposition.EXISTING, sequence)

        for evidence in observation.evidence:
            evidence_hash = canonical_model_sha256(evidence)
            existing_evidence = session.get(
                EvidenceRefRecord, {"tenant_id": tenant_id, "evidence_id": evidence.id}
            )
            if existing_evidence is not None:
                if existing_evidence.payload_sha256 != evidence_hash:
                    raise ImmutableRecordConflict("evidence_ref", tenant_id, evidence.id)
                continue
            session.add(
                EvidenceRefRecord(
                    tenant_id=tenant_id,
                    evidence_id=evidence.id,
                    evidence_type=str(evidence.type),
                    source=evidence.source,
                    locator=evidence.locator,
                    sha256=evidence.sha256.lower(),
                    collected_at=evidence.collected_at,
                    payload_json=canonical_model_json(evidence),
                    payload_sha256=evidence_hash,
                )
            )

        session.add(
            ObservationRecord(
                tenant_id=tenant_id,
                observation_id=observation.id,
                event_time=observation.event_time,
                ingest_time=observation.ingest_time,
                source=observation.source,
                sensor_id=observation.sensor_id,
                event_class=str(observation.event_class),
                activity=observation.activity,
                severity=str(observation.severity),
                confidence=observation.confidence,
                payload_json=canonical_model_json(observation),
                payload_sha256=observation_hash,
            )
        )
        session.flush()
        for evidence in observation.evidence:
            session.add(
                ObservationEvidenceRecord(
                    tenant_id=tenant_id, observation_id=observation.id, evidence_id=evidence.id
                )
            )
        session.flush()
        sequence = ObservationSequenceAllocator().allocate(session)
        session.add(
            ObservationIngestEntryRecord(
                ingest_sequence=sequence, tenant_id=tenant_id, observation_id=observation.id
            )
        )
        session.flush()
        return ObservationWriteResult(WriteDisposition.CREATED, sequence)

    def _existing_sequence(self, session: Session, tenant_id: str, observation_id: str) -> int:
        sequence = session.scalar(
            select(ObservationIngestEntryRecord.ingest_sequence).where(
                ObservationIngestEntryRecord.tenant_id == tenant_id,
                ObservationIngestEntryRecord.observation_id == observation_id,
            )
        )
        if sequence is None:
            raise ObservationSequenceInvariantError(
                "existing observation is missing a sequence entry"
            )
        return int(sequence)

    def get_ingest_sequence(self, tenant_id: str, observation_id: str) -> int | None:
        with self._session_scope() as session:
            value = session.scalar(
                select(ObservationIngestEntryRecord.ingest_sequence).where(
                    ObservationIngestEntryRecord.tenant_id == tenant_id,
                    ObservationIngestEntryRecord.observation_id == observation_id,
                )
            )
            return int(value) if value is not None else None

    def read_sequence_page(
        self, *, after_sequence: int = 0, limit: int = 100
    ) -> ObservationSequencePage:
        if after_sequence < 0:
            raise ObservationSequenceCursorError("after_sequence must be zero or positive")
        if limit < 1 or limit > MAX_SEQUENCE_PAGE_LIMIT:
            raise ObservationSequenceCursorError(
                f"limit must be between 1 and {MAX_SEQUENCE_PAGE_LIMIT}"
            )
        with self._session_scope() as session:
            allocator = ObservationSequenceAllocator()
            high_watermark = allocator.high_watermark(session)
            stmt = (
                select(ObservationIngestEntryRecord, ObservationRecord)
                .outerjoin(
                    ObservationRecord,
                    and_(
                        ObservationRecord.tenant_id == ObservationIngestEntryRecord.tenant_id,
                        ObservationRecord.observation_id
                        == ObservationIngestEntryRecord.observation_id,
                    ),
                )
                .where(
                    ObservationIngestEntryRecord.ingest_sequence > after_sequence,
                    ObservationIngestEntryRecord.ingest_sequence <= high_watermark,
                )
                .order_by(ObservationIngestEntryRecord.ingest_sequence.asc())
                .limit(limit + 1)
            )
            rows = list(session.execute(stmt))
            returned = rows[:limit]
            items_list: list[SequencedObservation] = []
            for entry, record in returned:
                if record is None:
                    raise ObservationSequenceInvariantError(
                        "sequence entry is missing its observation"
                    )
                items_list.append(
                    SequencedObservation(
                        int(entry.ingest_sequence),
                        Observation.model_validate(record.payload_json),
                    )
                )
            items = tuple(items_list)
            next_sequence = items[-1].ingest_sequence if items else after_sequence
            return ObservationSequencePage(
                items=items,
                after_sequence=after_sequence,
                next_sequence=next_sequence,
                high_watermark=high_watermark,
                has_more=len(rows) > limit,
            )

    def _ensure_evidence_matches(self, session: Session, observation: Observation) -> None:
        for evidence in observation.evidence:
            existing = session.get(
                EvidenceRefRecord,
                {"tenant_id": observation.tenant_id, "evidence_id": evidence.id},
            )
            if existing is None or existing.payload_sha256 != canonical_model_sha256(evidence):
                raise ImmutableRecordConflict("evidence_ref", observation.tenant_id, evidence.id)

    def get_scoped(self, tenant_id: str, source: str, observation_id: str) -> Observation | None:
        with self._session_scope() as session:
            stmt = select(ObservationRecord).where(
                ObservationRecord.tenant_id == tenant_id,
                ObservationRecord.source == source,
                ObservationRecord.observation_id == observation_id,
            )
            record = session.scalars(stmt).first()
            return Observation.model_validate(record.payload_json) if record else None

    def list_page(
        self,
        tenant_id: str,
        source: str,
        *,
        limit: int,
        anchor: ObservationPageAnchor | None = None,
        filters: ObservationQueryFilters | None = None,
    ) -> ObservationQueryPage:
        if limit < 1 or limit > MAX_LIST_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_LIST_LIMIT}")
        filters = filters or ObservationQueryFilters()
        with self._session_scope() as session:
            stmt: Select[tuple[ObservationRecord]] = select(ObservationRecord).where(
                ObservationRecord.tenant_id == tenant_id,
                ObservationRecord.source == source,
            )
            if filters.event_time_from is not None:
                stmt = stmt.where(ObservationRecord.event_time >= filters.event_time_from)
            if filters.event_time_to is not None:
                stmt = stmt.where(ObservationRecord.event_time <= filters.event_time_to)
            if filters.event_class is not None:
                stmt = stmt.where(ObservationRecord.event_class == str(filters.event_class))
            if filters.severity is not None:
                stmt = stmt.where(ObservationRecord.severity == str(filters.severity))
            if filters.sensor_id is not None:
                stmt = stmt.where(ObservationRecord.sensor_id == filters.sensor_id)
            if anchor is not None:
                stmt = stmt.where(
                    or_(
                        ObservationRecord.event_time < anchor.event_time,
                        and_(
                            ObservationRecord.event_time == anchor.event_time,
                            ObservationRecord.observation_id < anchor.observation_id,
                        ),
                    )
                )
            stmt = stmt.order_by(
                ObservationRecord.event_time.desc(), ObservationRecord.observation_id.desc()
            ).limit(limit + 1)
            records = list(session.scalars(stmt))
            returned = records[:limit]
            observations = tuple(Observation.model_validate(r.payload_json) for r in returned)
            has_more = len(records) > limit
            next_anchor = None
            if has_more and observations:
                final = observations[-1]
                next_anchor = ObservationPageAnchor(
                    event_time=final.event_time, observation_id=final.id
                )
            return ObservationQueryPage(
                observations=observations, has_more=has_more, next_anchor=next_anchor
            )

    def get_evidence_scoped(
        self, tenant_id: str, source: str, evidence_id: str
    ) -> EvidenceRef | None:
        with self._session_scope() as session:
            stmt = (
                select(EvidenceRefRecord)
                .join(
                    ObservationEvidenceRecord,
                    and_(
                        EvidenceRefRecord.tenant_id == ObservationEvidenceRecord.tenant_id,
                        EvidenceRefRecord.evidence_id == ObservationEvidenceRecord.evidence_id,
                    ),
                )
                .outerjoin(
                    ObservationRecord,
                    and_(
                        ObservationRecord.tenant_id == ObservationEvidenceRecord.tenant_id,
                        ObservationRecord.observation_id
                        == ObservationEvidenceRecord.observation_id,
                    ),
                )
                .where(
                    EvidenceRefRecord.tenant_id == tenant_id,
                    EvidenceRefRecord.evidence_id == evidence_id,
                    ObservationRecord.tenant_id == tenant_id,
                    ObservationRecord.source == source,
                )
                .distinct()
            )
            record = session.scalars(stmt).first()
            return EvidenceRef.model_validate(record.payload_json) if record else None

    def get(self, tenant_id: str, observation_id: str) -> Observation | None:
        with self._session_scope() as session:
            record = session.get(
                ObservationRecord, {"tenant_id": tenant_id, "observation_id": observation_id}
            )
            return Observation.model_validate(record.payload_json) if record else None

    def list(
        self, tenant_id: str, *, limit: int = 100, before: datetime | None = None
    ) -> list[Observation]:
        if limit < 1 or limit > MAX_LIST_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_LIST_LIMIT}")
        with self._session_scope() as session:
            stmt: Select[tuple[ObservationRecord]] = select(ObservationRecord).where(
                ObservationRecord.tenant_id == tenant_id
            )
            if before is not None:
                stmt = stmt.where(ObservationRecord.event_time < before)
            stmt = stmt.order_by(
                ObservationRecord.event_time.desc(), ObservationRecord.observation_id.desc()
            ).limit(limit)
            return [
                Observation.model_validate(record.payload_json) for record in session.scalars(stmt)
            ]

    def get_evidence(self, tenant_id: str, evidence_id: str) -> EvidenceRef | None:
        with self._session_scope() as session:
            record = session.get(
                EvidenceRefRecord, {"tenant_id": tenant_id, "evidence_id": evidence_id}
            )
            return EvidenceRef.model_validate(record.payload_json) if record else None
