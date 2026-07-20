from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from correlis_schema import Observation
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .errors import ObservationSequenceInvariantError
from .models import (
    ObservationIngestEntryRecord,
    ObservationIngestSequenceStateRecord,
    ObservationRecord,
)

SINGLETON_ID = 1
MAX_SEQUENCE_PAGE_LIMIT = 500


class WriteDisposition(StrEnum):
    CREATED = "created"
    EXISTING = "existing"


@dataclass(frozen=True, slots=True)
class ObservationWriteResult:
    disposition: WriteDisposition
    ingest_sequence: int


@dataclass(frozen=True, slots=True)
class SequencedObservation:
    ingest_sequence: int
    observation: Observation


@dataclass(frozen=True, slots=True)
class ObservationSequencePage:
    items: tuple[SequencedObservation, ...]
    after_sequence: int
    next_sequence: int
    high_watermark: int
    has_more: bool


class ObservationSequenceAllocator:
    def allocate(self, session: Session) -> int:
        stmt = (
            update(ObservationIngestSequenceStateRecord)
            .where(ObservationIngestSequenceStateRecord.singleton_id == SINGLETON_ID)
            .values(last_sequence=ObservationIngestSequenceStateRecord.last_sequence + 1)
            .returning(ObservationIngestSequenceStateRecord.last_sequence)
        )
        value = session.scalar(stmt)
        if value is None:
            raise ObservationSequenceInvariantError("observation sequence state is missing")
        if value <= 0:
            raise ObservationSequenceInvariantError("observation sequence allocation was invalid")
        return int(value)

    def high_watermark(self, session: Session) -> int:
        value = session.scalar(
            select(ObservationIngestSequenceStateRecord.last_sequence).where(
                ObservationIngestSequenceStateRecord.singleton_id == SINGLETON_ID
            )
        )
        if value is None:
            raise ObservationSequenceInvariantError("observation sequence state is missing")
        if value < 0:
            raise ObservationSequenceInvariantError("observation sequence state is invalid")
        return int(value)

    def validate_state(self, session: Session) -> None:
        high = self.high_watermark(session)
        max_entry = (
            session.scalar(select(func.max(ObservationIngestEntryRecord.ingest_sequence))) or 0
        )
        if high != int(max_entry):
            raise ObservationSequenceInvariantError("observation sequence state is inconsistent")
        obs_count = session.scalar(select(func.count()).select_from(ObservationRecord)) or 0
        entry_count = (
            session.scalar(select(func.count()).select_from(ObservationIngestEntryRecord)) or 0
        )
        if int(obs_count) != int(entry_count):
            raise ObservationSequenceInvariantError("observation sequence entries are incomplete")
