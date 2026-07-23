from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime

from .projections import ProjectorIdentity


@dataclass(frozen=True, slots=True)
class CorrelationProjectionConfig:
    identity: ProjectorIdentity
    relationship_projection_version: str
    ruleset_name: str
    ruleset_version: str
    rule_manifest_sha256: str
    rule_manifest: dict[str, object] = field(repr=False)
    ontology_name: str
    ontology_version: str
    created_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "rule_manifest", deepcopy(self.rule_manifest))
