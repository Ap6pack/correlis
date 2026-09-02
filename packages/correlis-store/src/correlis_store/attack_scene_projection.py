from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime

from .projections import ProjectorIdentity

ATTACK_SCENE_PROJECTOR_NAME = "attack-scene-projection"
DEFAULT_ATTACK_SCENE_PROJECTOR_VERSION = "1"


def attack_scene_projector_identity(
    version: str = DEFAULT_ATTACK_SCENE_PROJECTOR_VERSION,
) -> ProjectorIdentity:
    return ProjectorIdentity(ATTACK_SCENE_PROJECTOR_NAME, version)


def attack_scene_id(root_relationship_id: str) -> str:
    if not root_relationship_id.strip():
        raise ValueError("root relationship id must be nonblank")
    value = f"scene:{root_relationship_id}"
    if len(value) > 128:
        raise ValueError("attack scene id exceeds 128 characters")
    return value


@dataclass(frozen=True, slots=True)
class AttackSceneProjectionConfig:
    identity: ProjectorIdentity
    entity_projection_version: str
    relationship_projection_version: str
    correlation_projection_version: str
    policy_name: str
    policy_version: str
    policy_manifest_sha256: str
    policy_manifest: dict[str, object] = field(repr=False)
    created_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_manifest", deepcopy(self.policy_manifest))
