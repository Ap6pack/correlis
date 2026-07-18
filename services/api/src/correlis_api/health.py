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
