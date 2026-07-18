from __future__ import annotations

import hashlib
import json
from pathlib import Path

from correlis_schema import EvidenceRef, EvidenceType, Observation


class ScenarioNotFoundError(FileNotFoundError):
    pass


class ScenarioRepository:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir

    def list(self) -> list[str]:
        if not self.base_dir.exists():
            return []
        return sorted(path.name for path in self.base_dir.iterdir() if path.is_dir())

    def load(self, name: str) -> list[Observation]:
        scenario_file = self.base_dir / name / "events.jsonl"
        if not scenario_file.is_file():
            raise ScenarioNotFoundError(name)

        observations: list[Observation] = []
        for line_number, raw_line in enumerate(scenario_file.read_bytes().splitlines(), start=1):
            if not raw_line.strip():
                continue
            digest = hashlib.sha256(raw_line).hexdigest()
            payload = json.loads(raw_line)
            payload.setdefault(
                "evidence",
                [
                    EvidenceRef(
                        type=EvidenceType.RAW_EVENT,
                        source=payload.get("source", "scenario"),
                        locator=f"scenario://{name}/events.jsonl#{line_number}",
                        sha256=digest,
                        metadata={"scenario": name, "line": line_number},
                    ).model_dump(mode="json")
                ],
            )
            observations.append(Observation.model_validate(payload))

        observations.sort(key=lambda item: (item.event_time, item.id))
        return observations
