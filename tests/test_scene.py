from pathlib import Path

from correlis_api.scenarios import ScenarioRepository
from correlis_api.scene import SceneBuilder, build_scene
from correlis_schema import IncidentState, ProvenanceClass, RelationshipType

SCENARIOS = Path(__file__).parents[1] / "scenarios"


def test_reference_scenario_builds_confirmed_scene():
    observations = ScenarioRepository(SCENARIOS).load("initial-access-demo")
    scene = build_scene("initial-access-demo", observations)

    assert scene.state == IncidentState.CONFIRMED
    assert len(scene.observations) == 7
    assert "asset:web-01" in scene.entities
    assert "asset:db-01" in scene.entities

    relationship_types = {item.type for item in scene.relationships.values()}
    assert RelationshipType.EXPLOITED in relationship_types
    assert RelationshipType.COMPROMISED in relationship_types
    assert RelationshipType.MOVED_LATERALLY_TO in relationship_types

    derived = [
        item
        for item in scene.relationships.values()
        if item.provenance == ProvenanceClass.DETERMINISTIC
    ]
    assert derived
    assert all(item.rule_id for item in derived)
    assert all(item.evidence_refs for item in derived)


def test_scene_builder_is_idempotent_by_observation_id():
    observations = ScenarioRepository(SCENARIOS).load("initial-access-demo")
    builder = SceneBuilder("scene:test", "demo", "Test")
    builder.apply(observations[0])
    builder.apply(observations[0])
    assert len(builder.scene.observations) == 1
