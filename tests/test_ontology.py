from __future__ import annotations

from pathlib import Path

import pytest
from correlis_api.scenarios import ScenarioRepository
from correlis_api.scene import SceneBuilder, build_scene
from correlis_ontology import (
    CORE_ONTOLOGY,
    OntologyConfigurationError,
    OntologyRegistry,
    OntologyValidationError,
)
from correlis_ontology.core import ACTION_DEFINITIONS, ENTITY_DEFINITIONS, RELATIONSHIP_DEFINITIONS
from correlis_ontology.definitions import EntityTypeDefinition, IdentityKeyDefinition
from correlis_schema import (
    EntityRef,
    EntityType,
    IncidentState,
    OperationalActionType,
    RelationshipType,
)
from pydantic import ValidationError

SCENARIOS = Path(__file__).parents[1] / "scenarios"


def entity(t: EntityType) -> EntityRef:
    return EntityRef(id=f"{t.value}:test", type=t, label=t.value)


def test_registry_completeness_and_manifest_determinism():
    manifest = CORE_ONTOLOGY.manifest()
    assert {item.type for item in manifest.entity_types} == set(EntityType)
    assert {item.type for item in manifest.relationship_types} == set(RelationshipType)
    assert {item.type for item in manifest.action_types} == set(OperationalActionType)
    assert [item.type.value for item in manifest.entity_types] == sorted(
        i.value for i in EntityType
    )
    assert manifest.model_dump(mode="json") == CORE_ONTOLOGY.manifest().model_dump(mode="json")


def test_registry_rejects_duplicate_and_missing_definitions():
    with pytest.raises(OntologyConfigurationError):
        OntologyRegistry(
            name="bad",
            version="0",
            entity_types=(*ENTITY_DEFINITIONS, ENTITY_DEFINITIONS[0]),
            relationship_types=RELATIONSHIP_DEFINITIONS,
            action_types=ACTION_DEFINITIONS,
        )
    with pytest.raises(OntologyConfigurationError):
        OntologyRegistry(
            name="bad",
            version="0",
            entity_types=ENTITY_DEFINITIONS[:-1],
            relationship_types=RELATIONSHIP_DEFINITIONS,
            action_types=ACTION_DEFINITIONS,
        )


def test_identity_key_validation_and_duplicate_names():
    with pytest.raises(ValueError):
        IdentityKeyDefinition(name="bad", fields=(), description="bad")
    duplicate = EntityTypeDefinition(
        type=EntityType.ASSET,
        display_name="Asset",
        description="bad",
        identity_keys=(
            IdentityKeyDefinition(name="x", fields=("a",), description="a"),
            IdentityKeyDefinition(name="x", fields=("b",), description="b"),
        ),
    )
    replacements = tuple(
        duplicate if item.type == EntityType.ASSET else item for item in ENTITY_DEFINITIONS
    )
    with pytest.raises(OntologyConfigurationError):
        OntologyRegistry(
            name="bad",
            version="0",
            entity_types=replacements,
            relationship_types=RELATIONSHIP_DEFINITIONS,
            action_types=ACTION_DEFINITIONS,
        )


def test_default_registry_public_manifest_is_immutable():
    manifest = CORE_ONTOLOGY.manifest()
    with pytest.raises(TypeError):
        manifest.entity_types[0] = manifest.entity_types[0]  # type: ignore[index]
    with pytest.raises(ValidationError):
        manifest.entity_types[0].display_name = "Changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    "source,target,relationship",
    [
        (EntityType.ASSET, EntityType.VULNERABILITY, RelationshipType.HAS_VULNERABILITY),
        (EntityType.DOMAIN, EntityType.IP_ADDRESS, RelationshipType.RESOLVED_TO),
        (EntityType.PROCESS, EntityType.ASSET, RelationshipType.RUNS_ON),
        (EntityType.ASSET, EntityType.ASSET, RelationshipType.AUTHENTICATED_TO),
    ],
)
def test_valid_edges(source, target, relationship):
    CORE_ONTOLOGY.validate_edge(relationship, entity(source), entity(target))


@pytest.mark.parametrize(
    "source,target,relationship,code",
    [
        (
            EntityType.VULNERABILITY,
            EntityType.ASSET,
            RelationshipType.HAS_VULNERABILITY,
            "relationship_source_type_not_allowed",
        ),
        (
            EntityType.IP_ADDRESS,
            EntityType.DOMAIN,
            RelationshipType.RESOLVED_TO,
            "relationship_source_type_not_allowed",
        ),
        (
            EntityType.ASSET,
            EntityType.PROCESS,
            RelationshipType.RUNS_ON,
            "relationship_source_type_not_allowed",
        ),
        (
            EntityType.ASSET,
            EntityType.DOMAIN,
            RelationshipType.HAS_VULNERABILITY,
            "relationship_target_type_not_allowed",
        ),
    ],
)
def test_invalid_edges_have_stable_codes(source, target, relationship, code):
    with pytest.raises(OntologyValidationError) as exc:
        CORE_ONTOLOGY.validate_edge(relationship, entity(source), entity(target))
    assert exc.value.code == code


def test_scenario_builds_with_same_relationship_ids_and_directions():
    observations = ScenarioRepository(SCENARIOS).load("initial-access-demo")
    scene = build_scene("initial-access-demo", observations)
    assert scene.state == IncidentState.CONFIRMED
    pairs = {(r.source_entity_id, r.target_entity_id, r.type) for r in scene.relationships.values()}
    assert ("asset:web-01", "vuln:LAB-CVE-001", RelationshipType.HAS_VULNERABILITY) in pairs
    assert ("ip:203.0.113.44", "asset:web-01", RelationshipType.TARGETED) in pairs
    assert ("asset:web-01", "asset:db-01", RelationshipType.MOVED_LATERALLY_TO) in pairs
    ids = sorted(scene.relationships)
    assert ids == sorted(build_scene("initial-access-demo", observations).relationships)


def test_invalid_direct_relationship_fails_loudly_without_mutation():
    observation = ScenarioRepository(SCENARIOS).load("initial-access-demo")[0]
    invalid = observation.model_copy(
        update={"subject": observation.object, "object": observation.subject}
    )
    builder = SceneBuilder("scene:test", observation.tenant_id, "Test")
    with pytest.raises(OntologyValidationError):
        builder.apply(invalid)
    assert builder.scene.observations == []
