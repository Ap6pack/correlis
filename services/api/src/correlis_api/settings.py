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
    query_default_page_size: int = 50
    query_max_page_size: int = 200
    stream_scan_batch_size: int = 100
    stream_poll_interval_seconds: float = 0.5
    stream_heartbeat_seconds: float = 15.0
    stream_auth_recheck_seconds: float = 30.0
    stream_max_connections: int = 32
    stream_max_connections_per_collector: int = 2

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
        try:
            query_default_page_size = int(self.query_default_page_size)
            query_max_page_size = int(self.query_max_page_size)
        except (TypeError, ValueError) as exc:
            raise ValueError("query page sizes must be positive integers") from exc
        if query_default_page_size < 1 or query_max_page_size < 1:
            raise ValueError("query page sizes must be positive integers")
        if query_max_page_size > 500:
            raise ValueError("CORRELIS_QUERY_MAX_PAGE_SIZE must not exceed 500")
        if query_default_page_size > query_max_page_size:
            raise ValueError(
                "CORRELIS_QUERY_DEFAULT_PAGE_SIZE must not exceed CORRELIS_QUERY_MAX_PAGE_SIZE"
            )
        object.__setattr__(self, "query_default_page_size", query_default_page_size)
        object.__setattr__(self, "query_max_page_size", query_max_page_size)
        object.__setattr__(self, "scenario_dir", Path(self.scenario_dir))
        for name, minimum, maximum, label in (
            ("stream_scan_batch_size", 1, 500, "CORRELIS_STREAM_SCAN_BATCH_SIZE"),
            ("stream_max_connections", 1, 1000, "CORRELIS_STREAM_MAX_CONNECTIONS"),
        ):
            try:
                value = int(getattr(self, name))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{label} is invalid") from exc
            if not minimum <= value <= maximum:
                raise ValueError(f"{label} is invalid")
            object.__setattr__(self, name, value)
        try:
            per_collector = int(self.stream_max_connections_per_collector)
        except (TypeError, ValueError) as exc:
            raise ValueError("CORRELIS_STREAM_MAX_CONNECTIONS_PER_COLLECTOR is invalid") from exc
        if per_collector < 1 or per_collector > self.stream_max_connections:
            raise ValueError("CORRELIS_STREAM_MAX_CONNECTIONS_PER_COLLECTOR is invalid")
        object.__setattr__(self, "stream_max_connections_per_collector", per_collector)
        for name, minimum, maximum, label in (
            ("stream_poll_interval_seconds", 0.05, 60.0, "CORRELIS_STREAM_POLL_INTERVAL_SECONDS"),
            ("stream_heartbeat_seconds", 5.0, 300.0, "CORRELIS_STREAM_HEARTBEAT_SECONDS"),
            ("stream_auth_recheck_seconds", 5.0, 3600.0, "CORRELIS_STREAM_AUTH_RECHECK_SECONDS"),
        ):
            try:
                value = float(getattr(self, name))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{label} is invalid") from exc
            if not minimum <= value <= maximum:
                raise ValueError(f"{label} is invalid")
            object.__setattr__(self, name, value)
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
            query_default_page_size=os.getenv("CORRELIS_QUERY_DEFAULT_PAGE_SIZE", "50"),
            query_max_page_size=os.getenv("CORRELIS_QUERY_MAX_PAGE_SIZE", "200"),
            stream_scan_batch_size=os.getenv("CORRELIS_STREAM_SCAN_BATCH_SIZE", "100"),
            stream_poll_interval_seconds=os.getenv("CORRELIS_STREAM_POLL_INTERVAL_SECONDS", "0.5"),
            stream_heartbeat_seconds=os.getenv("CORRELIS_STREAM_HEARTBEAT_SECONDS", "15.0"),
            stream_auth_recheck_seconds=os.getenv("CORRELIS_STREAM_AUTH_RECHECK_SECONDS", "30.0"),
            stream_max_connections=os.getenv("CORRELIS_STREAM_MAX_CONNECTIONS", "32"),
            stream_max_connections_per_collector=os.getenv(
                "CORRELIS_STREAM_MAX_CONNECTIONS_PER_COLLECTOR", "2"
            ),
        )
