from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from correlis_schema import EventClass, Observation, Severity


def _require_aware(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value


@dataclass(frozen=True, slots=True)
class ObservationPageAnchor:
    event_time: datetime
    observation_id: str

    def __post_init__(self) -> None:
        _require_aware(self.event_time, "event_time")
        if not self.observation_id:
            raise ValueError("observation_id must be non-empty")


@dataclass(frozen=True, slots=True)
class ObservationQueryFilters:
    event_time_from: datetime | None = None
    event_time_to: datetime | None = None
    event_class: EventClass | None = None
    severity: Severity | None = None
    sensor_id: str | None = None

    def __post_init__(self) -> None:
        if self.event_time_from is not None:
            _require_aware(self.event_time_from, "event_time_from")
        if self.event_time_to is not None:
            _require_aware(self.event_time_to, "event_time_to")
        if self.sensor_id is not None and not 1 <= len(self.sensor_id) <= 256:
            raise ValueError("sensor_id must be between 1 and 256 characters")


@dataclass(frozen=True, slots=True)
class ObservationQueryPage:
    observations: tuple[Observation, ...]
    has_more: bool
    next_anchor: ObservationPageAnchor | None
