from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from sqlalchemy.orm import Session

from .observation_sequence import SequencedObservation

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_CODE_RE = re.compile(r"^[a-z0-9_]{1,64}$")


def _validate_value(value: str, *, field: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    trimmed = value.strip()
    if value != trimmed:
        raise ValueError(f"{field} must not contain surrounding whitespace")
    if not pattern.fullmatch(value):
        raise ValueError(f"invalid {field}")
    return value


@dataclass(frozen=True, slots=True)
class ProjectorIdentity:
    name: str
    version: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "name", _validate_value(self.name, field="projector_name", pattern=_NAME_RE)
        )
        object.__setattr__(
            self,
            "version",
            _validate_value(self.version, field="projector_version", pattern=_VERSION_RE),
        )


class ProjectorStatus(StrEnum):
    IDLE = "idle"
    FAILED = "failed"
    PAUSED = "paused"


class ProjectorFailureStatus(StrEnum):
    ACTIVE = "active"
    RESOLVED = "resolved"


@dataclass(frozen=True, slots=True)
class ProjectorCheckpoint:
    identity: ProjectorIdentity
    last_processed_sequence: int
    status: ProjectorStatus
    last_failure_sequence: int | None
    created_at: datetime
    updated_at: datetime
    last_processed_at: datetime | None


@dataclass(frozen=True, slots=True)
class ProjectorFailure:
    identity: ProjectorIdentity
    ingest_sequence: int
    tenant_id: str
    observation_id: str
    status: ProjectorFailureStatus
    attempt_count: int
    error_code: str
    error_type: str
    safe_message: str
    first_failed_at: datetime
    last_failed_at: datetime
    resolved_at: datetime | None


class ProjectionRunOutcome(StrEnum):
    ADVANCED = "advanced"
    CAUGHT_UP = "caught_up"
    FAILED = "failed"
    PAUSED = "paused"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class ProjectionRunResult:
    identity: ProjectorIdentity
    outcome: ProjectionRunOutcome
    starting_sequence: int
    ending_sequence: int
    captured_high_watermark: int
    processed_count: int
    failure_sequence: int | None


class ProjectionHandler(Protocol):
    """Database-only projection handler owned by ProjectionRunner's transaction.

    Handlers must use the supplied Session for projection state and must not commit,
    roll back, close, open independent projection transactions, mutate observations,
    or perform external side effects.
    """

    def __call__(self, session: Session, item: SequencedObservation) -> None: ...


class ProjectionHandlerError(Exception):
    def __init__(self, code: str, safe_message: str) -> None:
        self.code = _validate_value(code, field="error_code", pattern=_CODE_RE)
        if not isinstance(safe_message, str) or not safe_message or len(safe_message) > 2048:
            raise ValueError("safe_message must be 1-2048 characters")
        self.safe_message = safe_message
        super().__init__(safe_message)


class ProjectorAlreadyRegistered(Exception):
    pass


class ProjectorNotRegistered(Exception):
    pass


class ProjectorBusy(Exception):
    pass


class ProjectorPaused(Exception):
    pass


class ProjectorFailed(Exception):
    pass


class ProjectorStateConflict(Exception):
    pass


class ProjectionInvariantError(Exception):
    pass
