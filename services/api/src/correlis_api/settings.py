from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    host: str = os.getenv("CORRELIS_HOST", "0.0.0.0")
    port: int = int(os.getenv("CORRELIS_PORT", "8080"))
    log_level: str = os.getenv("CORRELIS_LOG_LEVEL", "INFO")
    scenario_dir: Path = Path(os.getenv("CORRELIS_SCENARIO_DIR", "scenarios"))


settings = Settings()
