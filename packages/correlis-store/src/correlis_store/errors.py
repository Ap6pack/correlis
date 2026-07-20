from __future__ import annotations


class ImmutableRecordConflict(Exception):
    def __init__(self, resource_type: str, tenant_id: str, record_id: str) -> None:
        self.resource_type = resource_type
        self.tenant_id = tenant_id
        self.record_id = record_id
        super().__init__(
            f"immutable {resource_type} conflict for tenant {tenant_id!r} and id {record_id!r}"
        )


class ObservationSequenceInvariantError(Exception):
    """Raised when durable observation sequence state is missing or inconsistent."""


class ObservationSequenceCursorError(ValueError):
    """Raised when an observation sequence cursor or page size is invalid."""
