from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DatabaseCheck:
    ok: bool
    code: str | None = None


@dataclass(frozen=True, slots=True)
class MigrationCheck:
    ok: bool
    code: str
    current: tuple[str, ...] = ()
    expected: tuple[str, ...] = ()


def check_database_connectivity(engine: Engine) -> DatabaseCheck:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:
        logger.exception("Database readiness check failed")
        return DatabaseCheck(ok=False, code="database_unavailable")
    return DatabaseCheck(ok=True)


def check_migration_state(engine: Engine, alembic_config_path: Path) -> MigrationCheck:
    if not alembic_config_path.exists():
        return MigrationCheck(ok=False, code="migration_config_missing")
    try:
        config = Config(str(alembic_config_path))
        script = ScriptDirectory.from_config(config)
        expected = tuple(sorted(script.get_heads()))
        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            current = tuple(sorted(context.get_current_heads()))
    except Exception:
        logger.exception("Migration readiness check failed")
        return MigrationCheck(ok=False, code="migration_state_unavailable")

    if set(current) == set(expected):
        return MigrationCheck(
            ok=True, code="migrations_current", current=current, expected=expected
        )
    return MigrationCheck(
        ok=False, code="migrations_out_of_date", current=current, expected=expected
    )


@dataclass(frozen=True, slots=True)
class ObservationSequenceHealthResult:
    ok: bool
    status: str
    high_watermark: int | None = None
    code: str | None = None


def check_observation_sequence_state(engine: Engine) -> ObservationSequenceHealthResult:
    try:
        with engine.connect() as connection:
            state = connection.execute(
                text(
                    "SELECT last_sequence FROM observation_ingest_sequence_state "
                    "WHERE singleton_id = 1"
                )
            ).scalar_one_or_none()
            if state is None:
                return ObservationSequenceHealthResult(
                    ok=False, status="error", code="observation_sequence_state_missing"
                )
            max_entry = connection.execute(
                text("SELECT COALESCE(MAX(ingest_sequence), 0) FROM observation_ingest_entries")
            ).scalar_one()
            observation_count = connection.execute(
                text("SELECT COUNT(*) FROM observations")
            ).scalar_one()
            entry_count = connection.execute(
                text("SELECT COUNT(*) FROM observation_ingest_entries")
            ).scalar_one()
    except Exception:
        logger.exception("Observation sequence readiness check failed")
        return ObservationSequenceHealthResult(
            ok=False, status="error", code="observation_sequence_unavailable"
        )
    if int(state) != int(max_entry) or int(observation_count) != int(entry_count):
        return ObservationSequenceHealthResult(
            ok=False, status="error", code="observation_sequence_state_inconsistent"
        )
    return ObservationSequenceHealthResult(ok=True, status="ok", high_watermark=int(state))
