from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from enum import StrEnum

from correlis_schema import EvidenceRef, Observation
from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .errors import ImmutableRecordConflict
from .hashing import canonical_model_json, canonical_model_sha256
from .models import EvidenceRefRecord, ObservationEvidenceRecord, ObservationRecord

MAX_LIST_LIMIT = 500


class WriteDisposition(StrEnum):
    CREATED = "created"
    EXISTING = "existing"


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
        retry_after_integrity_error = False
        while True:
            with self._session_scope() as session:
                try:
                    disposition = self._put_with_session(session, observation)
                    session.commit()
                    return disposition
                except IntegrityError:
                    session.rollback()
                    if retry_after_integrity_error:
                        raise
                    retry_after_integrity_error = True
                except Exception:
                    session.rollback()
                    raise

    def _put_with_session(self, session: Session, observation: Observation) -> WriteDisposition:
        tenant_id = observation.tenant_id
        observation_hash = canonical_model_sha256(observation)
        existing_observation = session.get(
            ObservationRecord, {"tenant_id": tenant_id, "observation_id": observation.id}
        )
        if existing_observation is not None:
            if existing_observation.payload_sha256 != observation_hash:
                raise ImmutableRecordConflict("observation", tenant_id, observation.id)
            self._ensure_evidence_matches(session, observation)
            return WriteDisposition.EXISTING

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
        return WriteDisposition.CREATED

    def _ensure_evidence_matches(self, session: Session, observation: Observation) -> None:
        for evidence in observation.evidence:
            existing = session.get(
                EvidenceRefRecord,
                {"tenant_id": observation.tenant_id, "evidence_id": evidence.id},
            )
            if existing is None or existing.payload_sha256 != canonical_model_sha256(evidence):
                raise ImmutableRecordConflict("evidence_ref", observation.tenant_id, evidence.id)

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
                Observation.model_validate(record.payload_json)
                for record in session.scalars(stmt)
            ]

    def get_evidence(self, tenant_id: str, evidence_id: str) -> EvidenceRef | None:
        with self._session_scope() as session:
            record = session.get(
                EvidenceRefRecord, {"tenant_id": tenant_id, "evidence_id": evidence_id}
            )
            return EvidenceRef.model_validate(record.payload_json) if record else None
