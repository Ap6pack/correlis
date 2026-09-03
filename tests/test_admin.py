from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from correlis_schema import (
    EntityRef,
    EntityType,
    EventClass,
    EvidenceRef,
    EvidenceType,
    IncidentState,
    Observation,
)
from correlis_store import (
    EntityProjectionHandler,
    ObservationRepository,
    ProjectionRepository,
    create_session_factory,
)
from correlis_store.models import AttackSceneRecord, Base
from sqlalchemy import create_engine

from services.api.src.correlis_api import admin

T = datetime(2026, 1, 1, 12, tzinfo=UTC)


class EngineHandle:
    def __init__(self, engine):
        self.engine = engine
        self.disposed = False

    def dispose(self):
        self.disposed = True
        self.engine.dispose()


def make_resources(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'admin.sqlite'}", future=True)
    Base.metadata.create_all(engine)
    handle = EngineHandle(engine)
    sf = create_session_factory(engine)
    monkeypatch.setattr(admin, "_session_resources", lambda: (handle, sf))
    return handle, sf


def ev(id="ev-1"):
    return EvidenceRef(
        id=id,
        type=EvidenceType.RAW_EVENT,
        source="s",
        locator=f"test://{id}",
        sha256="c" * 64,
        collected_at=T,
        metadata={"hidden": True},
    )


def obs(id="obs-1", *, type=EntityType.ASSET, label="asset"):
    attrs = {"asset_id": "asset-1"} if type == EntityType.ASSET else {"application_id": "asset-1"}
    return Observation(
        id=id,
        tenant_id="tenant-a",
        event_time=T,
        ingest_time=T,
        source="src",
        sensor_id="sensor",
        event_class=EventClass.AUTHENTICATION,
        activity="a",
        subject=EntityRef(id="asset-1", type=type, label=label, attributes=attrs),
        evidence=[ev(f"ev-{id}")],
    )


def add_attack_scenes(sf):
    with sf.begin() as session:
        for scene_id, state in (("scene:a", "observed"), ("scene:b", "confirmed")):
            session.add(
                AttackSceneRecord(
                    projection_version="1",
                    tenant_id="tenant-a",
                    scene_id=scene_id,
                    entity_projection_version="1",
                    relationship_projection_version="1",
                    title=f"Scene {scene_id}",
                    state=state,
                    first_seen=T,
                    last_seen=T,
                    first_ingest_sequence=1,
                    last_ingest_sequence=1,
                    summary=None,
                    uncertainty_json=("bounded",),
                    created_at=T,
                    updated_at=T,
                )
            )


def test_attack_scene_parser_contract():
    parsed = admin.build_parser().parse_args(
        ["attack-scenes", "list", "--projection-version", "1", "--tenant-id", "tenant-a"]
    )
    assert parsed.limit == 100
    assert parsed.state is None
    parsed = admin.build_parser().parse_args(
        [
            "attack-scenes",
            "list",
            "--projection-version",
            "1",
            "--tenant-id",
            "tenant-a",
            "--state",
            "confirmed",
        ]
    )
    assert IncidentState(parsed.state) is IncidentState.CONFIRMED
    for command in ("list", "show", "lineage"):
        with pytest.raises(SystemExit):
            admin.build_parser().parse_args(["attack-scenes", command, "--tenant-id", "tenant-a"])
        with pytest.raises(SystemExit):
            admin.build_parser().parse_args(
                ["attack-scenes", command, "--projection-version", "1"]
            )
    with pytest.raises(SystemExit):
        admin.build_parser().parse_args(
            [
                "attack-scenes",
                "list",
                "--projection-version",
                "1",
                "--tenant-id",
                "tenant-a",
                "--state",
                "invalid",
            ]
        )


def test_attack_scene_read_cli_paths(tmp_path, monkeypatch, capsys):
    handle, sf = make_resources(tmp_path, monkeypatch)
    add_attack_scenes(sf)
    assert (
        admin.main(
            [
                "attack-scenes",
                "list",
                "--projection-version",
                "1",
                "--tenant-id",
                "tenant-a",
                "--state",
                "observed",
                "--after-scene-id",
                "scene:0",
                "--limit",
                "1",
            ]
        )
        == 0
    )
    page = json.loads(capsys.readouterr().out)
    assert page["items"][0]["scene_id"] == "scene:a"
    assert page["items"][0]["state"] == "observed"
    assert page["items"][0]["first_seen"] == T.replace(tzinfo=None).isoformat()
    assert page["items"][0]["uncertainty"] == ["bounded"]
    assert page["has_more"] is False
    assert page["next_after_scene_id"] is None

    common = ["--projection-version", "1", "--tenant-id", "tenant-a", "--scene-id", "scene:a"]
    assert admin.main(["attack-scenes", "show", *common]) == 0
    assert json.loads(capsys.readouterr().out)["scene_id"] == "scene:a"
    assert admin.main(["attack-scenes", "lineage", *common]) == 0
    lineage = json.loads(capsys.readouterr().out)
    assert lineage["scene"]["scene_id"] == "scene:a"
    assert lineage["entities"] == lineage["relationships"] == lineage["observations"] == []
    assert lineage["state_transitions"] == []

    for wrong_scope in (
        ["--projection-version", "1", "--tenant-id", "tenant-b"],
        ["--projection-version", "2", "--tenant-id", "tenant-a"],
    ):
        for command in ("show", "lineage"):
            assert (
                admin.main(["attack-scenes", command, *wrong_scope, "--scene-id", "scene:a"])
                == 1
            )
            assert capsys.readouterr().err.strip() == "attack scene not found"
    assert handle.disposed is True


def test_entity_projection_and_entities_cli_paths(tmp_path, monkeypatch, capsys):
    handle, sf = make_resources(tmp_path, monkeypatch)
    assert admin.main(["entity-projection", "show", "--version", "1"]) == 1
    assert handle.disposed is True

    handle, sf = make_resources(tmp_path, monkeypatch)
    assert admin.main(["entity-projection", "register", "--version", "1"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["identity"] == {"name": "entity-projection", "version": "1"}
    assert admin.main(["entity-projection", "register", "--version", "1"]) == 0
    assert admin.main(["entity-projection", "show", "--version", "1"]) == 0

    assert admin.main(["entity-projection", "run", "--version", "2"]) == 1
    assert "not registered" in capsys.readouterr().err
    ObservationRepository(sf).put_with_result(obs("obs-1"))
    assert admin.main(["entity-projection", "run", "--version", "1", "--limit", "1"]) == 0
    run = json.loads(capsys.readouterr().out)
    assert run["outcome"] == "caught_up"
    assert run["processed_count"] == 1
    assert (
        admin.main(["entities", "list", "--projection-version", "1", "--tenant-id", "tenant-a"])
        == 0
    )
    assert json.loads(capsys.readouterr().out)["items"][0]["entity_id"] == "asset-1"
    assert (
        admin.main(
            [
                "entities",
                "show",
                "--projection-version",
                "1",
                "--tenant-id",
                "tenant-a",
                "--entity-id",
                "asset-1",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["entity_id"] == "asset-1"
    assert (
        admin.main(
            [
                "entities",
                "lineage",
                "--projection-version",
                "1",
                "--tenant-id",
                "tenant-a",
                "--entity-id",
                "asset-1",
            ]
        )
        == 0
    )
    lineage = json.loads(capsys.readouterr().out)
    assert lineage["observations"][0]["source"] == "src"
    assert "locator" not in json.dumps(lineage)
    assert (
        admin.main(
            [
                "entities",
                "show",
                "--projection-version",
                "1",
                "--tenant-id",
                "tenant-a",
                "--entity-id",
                "missing",
            ]
        )
        == 1
    )
    assert handle.disposed is True


def test_entity_projection_cli_failed_blocked_paused_and_retry(tmp_path, monkeypatch, capsys):
    _, sf = make_resources(tmp_path, monkeypatch)
    ProjectionRepository(sf).register_projector(EntityProjectionHandler().projector_identity)
    ObservationRepository(sf).put_with_result(obs("ok"))
    ObservationRepository(sf).put_with_result(obs("bad", type=EntityType.APPLICATION, label="bad"))
    assert admin.main(["entity-projection", "run", "--version", "1", "--limit", "10"]) == 1
    failed = json.loads(capsys.readouterr().out)
    assert failed["outcome"] == "failed"
    assert admin.main(["entity-projection", "run", "--version", "1"]) == 1
    assert json.loads(capsys.readouterr().out)["outcome"] == "blocked"
    assert admin.main(["entity-projection", "run", "--version", "1", "--retry-failed"]) == 1
    assert json.loads(capsys.readouterr().out)["outcome"] == "failed"

    paused_identity = EntityProjectionHandler(projection_version="9").projector_identity
    ProjectionRepository(sf).register_projector(paused_identity)
    ProjectionRepository(sf).pause_projector(paused_identity)
    assert admin.main(["entity-projection", "run", "--version", "9"]) == 1
    assert json.loads(capsys.readouterr().out)["outcome"] == "paused"

    assert admin.main(["projectors", "list"]) == 0
    assert admin.main(["collectors", "list"]) == 0


def test_correlation_projection_cli_paths(tmp_path, monkeypatch, capsys):
    handle, sf = make_resources(tmp_path, monkeypatch)
    assert admin.main(["correlation-projection", "show", "--version", "1"]) == 1
    assert handle.disposed is True
    handle, sf = make_resources(tmp_path, monkeypatch)
    ProjectionRepository(sf).register_projector(
        __import__("correlis_store").relationship_projector_identity("1")
    )
    assert (
        admin.main(
            [
                "correlation-projection",
                "register",
                "--version",
                "1",
                "--relationship-projection-version",
                "1",
            ]
        )
        == 0
    )
    registered = json.loads(capsys.readouterr().out)
    assert registered["config"]["identity"] == {"name": "correlation-projection", "version": "1"}
    assert registered["checkpoint"]["last_processed_sequence"] == 0

    ProjectionRepository(sf).register_projector(
        __import__("correlis_store").relationship_projector_identity("2")
    )
    assert (
        admin.main(
            [
                "correlation-projection",
                "register",
                "--version",
                "2",
                "--relationship-projection-version",
                "2",
                "--ruleset-name",
                "correlis-sequence",
                "--ruleset-version",
                "1",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert (
        admin.main(
            [
                "correlation-projection",
                "register",
                "--version",
                "3",
                "--relationship-projection-version",
                "2",
                "--ruleset-name",
                "correlis-sequence",
                "--ruleset-version",
                "999",
            ]
        )
        == 1
    )
    assert "correlation ruleset not found" in capsys.readouterr().err
    assert admin.main(["correlation-projection", "show", "--version", "1"]) == 0
    assert json.loads(capsys.readouterr().out)["config"]["ruleset_name"] == "correlis-sequence"
    assert admin.main(["correlation-projection", "rules", "--version", "1"]) == 0
    assert json.loads(capsys.readouterr().out)["rules"][0]["rule_id"] == "COR-SEQ-001"
    assert (
        admin.main(["projectors", "register", "--name", "correlation-projection", "--version", "9"])
        == 1
    )
    assert "correlation-projection register" in capsys.readouterr().err


def test_correlation_projection_run_cli_uses_stored_graph_and_exit_codes(
    tmp_path, monkeypatch, capsys
):
    _, sf = make_resources(tmp_path, monkeypatch)
    ProjectionRepository(sf).register_projector(
        __import__("correlis_store").relationship_projector_identity("1")
    )
    assert (
        admin.main(
            [
                "correlation-projection",
                "register",
                "--version",
                "1",
                "--relationship-projection-version",
                "1",
            ]
        )
        == 0
    )
    capsys.readouterr()
    ObservationRepository(sf).put_with_result(obs("corr-noop"))
    assert admin.main(["correlation-projection", "run", "--version", "1", "--limit", "1"]) == 1
    assert "dependency" in capsys.readouterr().err
    assert admin.main(["relationship-projection", "run", "--version", "1", "--limit", "1"]) == 0
    capsys.readouterr()
    assert (
        admin.main(
            ["correlation-projection", "run", "--version", "1", "--limit", "1", "--retry-failed"]
        )
        == 0
    )
    out = json.loads(capsys.readouterr().out)
    assert out["outcome"] == "caught_up"
    assert out["processed_count"] == 1
