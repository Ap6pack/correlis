from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    host: str = "0.0.0.0"
    port: int = 8080
    log_level: str = "INFO"
    scenario_dir: Path = Path("./scenarios")
    database_url: str | None = None
    alembic_config_path: Path = Path("./alembic.ini")
    credential_pepper: str | None = field(default=None, repr=False)
    ingest_max_body_bytes: int = 2_097_152
    ingest_max_batch_size: int = 100

    def __post_init__(self) -> None:
        try:
            port = int(self.port)
        except (TypeError, ValueError) as exc:
            raise ValueError("CORRELIS_PORT must be an integer from 1 through 65535") from exc
        if not 1 <= port <= 65535:
            raise ValueError("CORRELIS_PORT must be an integer from 1 through 65535")
        object.__setattr__(self, "port", port)
        try:
            ingest_max_body_bytes = int(self.ingest_max_body_bytes)
        except (TypeError, ValueError) as exc:
            raise ValueError("CORRELIS_INGEST_MAX_BODY_BYTES must be a positive integer") from exc
        if ingest_max_body_bytes < 1:
            raise ValueError("CORRELIS_INGEST_MAX_BODY_BYTES must be a positive integer")
        try:
            ingest_max_batch_size = int(self.ingest_max_batch_size)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "CORRELIS_INGEST_MAX_BATCH_SIZE must be an integer from 1 through 500"
            ) from exc
        if not 1 <= ingest_max_batch_size <= 500:
            raise ValueError("CORRELIS_INGEST_MAX_BATCH_SIZE must be an integer from 1 through 500")
        object.__setattr__(self, "ingest_max_body_bytes", ingest_max_body_bytes)
        object.__setattr__(self, "ingest_max_batch_size", ingest_max_batch_size)
        object.__setattr__(self, "scenario_dir", Path(self.scenario_dir))
        object.__setattr__(self, "alembic_config_path", Path(self.alembic_config_path))

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            host=os.getenv("CORRELIS_HOST", "0.0.0.0"),
            port=os.getenv("CORRELIS_PORT", "8080"),
            log_level=os.getenv("CORRELIS_LOG_LEVEL", "INFO"),
            scenario_dir=Path(os.getenv("CORRELIS_SCENARIO_DIR", "./scenarios")),
            database_url=os.getenv("CORRELIS_DATABASE_URL") or None,
            alembic_config_path=Path(os.getenv("CORRELIS_ALEMBIC_CONFIG", "./alembic.ini")),
            credential_pepper=os.getenv("CORRELIS_CREDENTIAL_PEPPER") or None,
            ingest_max_body_bytes=os.getenv("CORRELIS_INGEST_MAX_BODY_BYTES", "2097152"),
            ingest_max_batch_size=os.getenv("CORRELIS_INGEST_MAX_BATCH_SIZE", "100"),
        )
