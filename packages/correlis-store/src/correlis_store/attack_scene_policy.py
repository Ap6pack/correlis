from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


class AttackScenePolicyNotFound(LookupError):
    """Raised when an exact scene policy identity is not installed."""


@dataclass(frozen=True, slots=True, init=False)
class AttackScenePolicyDefinition:
    policy_name: str
    policy_version: str
    _manifest_json: str

    def __init__(self, policy_name: str, policy_version: str, manifest: dict[str, object]) -> None:
        if not policy_name.strip() or not policy_version.strip():
            raise ValueError("policy name and version must be nonblank")
        object.__setattr__(self, "policy_name", policy_name)
        object.__setattr__(self, "policy_version", policy_version)
        object.__setattr__(
            self, "_manifest_json", json.dumps(manifest, sort_keys=True, separators=(",", ":"))
        )

    @property
    def manifest(self) -> dict[str, object]:
        return json.loads(self._manifest_json)

    def manifest_sha256(self) -> str:
        return hashlib.sha256(self._manifest_json.encode()).hexdigest()


BUILTIN_ATTACK_SCENE_POLICY_NAME = "correlis-root-chain"
BUILTIN_ATTACK_SCENE_POLICY_VERSION = "1"

BUILTIN_ATTACK_SCENE_POLICY = AttackScenePolicyDefinition(
    BUILTIN_ATTACK_SCENE_POLICY_NAME,
    BUILTIN_ATTACK_SCENE_POLICY_VERSION,
    {
        "anchor": {
            "relationship_type": "exploited",
            "provenance": "deterministic",
            "rule_id": "COR-SEQ-001",
            "rule_version": "1",
            "only_automatic_scene_root": True,
        },
        "scene_identity": {
            "strategy": "root_relationship",
            "format": "scene:<root_relationship_id>",
        },
        "relationship_membership": {
            "recognized": [
                {
                    "rule_id": "COR-SEQ-001",
                    "rule_version": "1",
                    "relationship_type": "exploited",
                    "semantics": "root_and_immutable_derivation_support_context",
                },
                {
                    "rule_id": "COR-SEQ-002",
                    "rule_version": "1",
                    "relationship_type": "compromised",
                    "semantics": (
                        "join_every_root_reachable_through_immutable_derivation_support_lineage"
                    ),
                },
                {
                    "rule_id": "COR-SEQ-003",
                    "rule_version": "1",
                    "relationship_type": "moved_laterally_to",
                    "semantics": (
                        "join_every_root_reachable_through_immutable_derivation_support_lineage"
                    ),
                },
            ],
            "multiple_root_memberships": "permitted",
            "automatic_scene_merge": False,
        },
        "entity_membership": "source_and_target_endpoints_of_member_relationships_only",
        "observation_membership": {
            "source": "relationship_observations_lineage_of_member_relationships_only",
            "ingest_sequence_boundary_required": True,
            "event_time_windows": False,
        },
        "incident_state": {
            "COR-SEQ-001": {"state": "observed", "reason_code": "deterministic_exploit_observed"},
            "COR-SEQ-002": {
                "state": "confirmed",
                "reason_code": "deterministic_compromise_confirmed",
            },
            "COR-SEQ-003": {"state": "confirmed", "transition": False},
            "automatic_potential": False,
            "automatic_states_forbidden": ["contained", "closed"],
        },
    },
)


class AttackScenePolicyCatalog:
    def __init__(self, policies: tuple[AttackScenePolicyDefinition, ...]) -> None:
        values: dict[tuple[str, str], AttackScenePolicyDefinition] = {}
        for policy in policies:
            key = (policy.policy_name, policy.policy_version)
            if key in values:
                raise ValueError(f"duplicate attack scene policy: {key[0]}/{key[1]}")
            values[key] = policy
        self._policies = tuple(values[key] for key in sorted(values))

    def get(self, policy_name: str, policy_version: str) -> AttackScenePolicyDefinition | None:
        return next(
            (
                p
                for p in self._policies
                if (p.policy_name, p.policy_version) == (policy_name, policy_version)
            ),
            None,
        )

    def require(self, policy_name: str, policy_version: str) -> AttackScenePolicyDefinition:
        policy = self.get(policy_name, policy_version)
        if policy is None:
            raise AttackScenePolicyNotFound(
                f"attack scene policy not found: {policy_name}/{policy_version}"
            )
        return policy

    def list(self) -> tuple[AttackScenePolicyDefinition, ...]:
        return self._policies


BUILTIN_ATTACK_SCENE_POLICY_CATALOG = AttackScenePolicyCatalog((BUILTIN_ATTACK_SCENE_POLICY,))


def resolve_attack_scene_policy(
    policy_name: str, policy_version: str
) -> AttackScenePolicyDefinition:
    return BUILTIN_ATTACK_SCENE_POLICY_CATALOG.require(policy_name, policy_version)
