from __future__ import annotations

from sqlalchemy.exc import OperationalError

POSTGRES_LOCK_NOT_AVAILABLE = "55P03"


def is_lock_not_available(exc: OperationalError) -> bool:
    """Return True only for PostgreSQL NOWAIT row-lock contention."""

    orig = getattr(exc, "orig", None)
    return (
        getattr(orig, "pgcode", None) == POSTGRES_LOCK_NOT_AVAILABLE
        or getattr(orig, "sqlstate", None) == POSTGRES_LOCK_NOT_AVAILABLE
    )
