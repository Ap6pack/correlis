from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from correlis_api.app import create_app
from correlis_api.dependencies import get_database_session
from correlis_api.settings import Settings
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

SCENARIOS = Path(__file__).parents[1] / "scenarios"
ALEMBIC_CONFIG = Path(__file__).parents[1] / "alembic.ini"


@pytest.fixture(scope="session")
def postgres_url() -> str:
    url = os.environ.get("CORRELIS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("CORRELIS_TEST_DATABASE_URL is required for PostgreSQL integration tests")
    return url


@pytest.fixture(scope="session")
def migrated_engine(postgres_url: str):
    os.environ["CORRELIS_DATABASE_URL"] = postgres_url
    config = Config(str(ALEMBIC_CONFIG))
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = create_engine(postgres_url, future=True)
    try:
        yield engine
    finally:
        engine.dispose()
        command.downgrade(config, "base")


def api_settings(**overrides) -> Settings:
    values = {
        "scenario_dir": SCENARIOS,
        "database_url": None,
        "alembic_config_path": ALEMBIC_CONFIG,
    }
    values.update(overrides)
    return Settings(**values)


def sqlite_engine(tmp_path: Path, name: str = "ready.sqlite") -> Engine:
    return create_engine(f"sqlite:///{tmp_path / name}", future=True)


def stamp_sqlite_database(tmp_path: Path, revision: str) -> Engine:
    db = tmp_path / f"{revision}.sqlite"
    os.environ["CORRELIS_DATABASE_URL"] = f"sqlite:///{db}"
    config = Config(str(ALEMBIC_CONFIG))
    command.upgrade(config, "head")
    if revision != "head":
        command.stamp(config, revision)
    return create_engine(f"sqlite:///{db}", future=True)


def alembic_heads() -> list[str]:
    return sorted(ScriptDirectory.from_config(Config(str(ALEMBIC_CONFIG))).get_heads())


def test_create_app_returns_fastapi_application():
    app = create_app(api_settings())
    assert isinstance(app, FastAPI)


def test_explicit_settings_override_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("CORRELIS_SCENARIO_DIR", str(tmp_path / "unused"))
    app = create_app(api_settings())
    with TestClient(app) as client:
        assert client.get("/api/v1/scenarios").json()["scenarios"] == ["initial-access-demo"]


def test_injected_scenario_repository_is_used(tmp_path):
    (tmp_path / "custom").mkdir()
    repository = type("InjectedRepository", (), {"list": lambda self: ["custom"]})()
    app = create_app(
        api_settings(scenario_dir=Path("missing")),
        scenario_repository=repository,
    )
    with TestClient(app) as client:
        assert client.get("/api/v1/scenarios").json() == {"scenarios": ["custom"]}


def test_health_remains_200():
    with TestClient(create_app(api_settings())) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "correlis-api", "version": "0.1.0"}


def test_health_live_returns_200():
    with TestClient(create_app(api_settings())) as client:
        response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_liveness_does_not_attempt_database_connection():
    class ExplodingEngine:
        def connect(self):
            raise AssertionError("liveness must not connect")

    with TestClient(create_app(api_settings(), engine=ExplodingEngine())) as client:  # type: ignore[arg-type]
        assert client.get("/health/live").status_code == 200


def test_ready_returns_503_when_no_database_configured():
    with TestClient(create_app(api_settings())) as client:
        response = client.get("/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["checks"]["database"] == {"status": "error", "code": "database_not_configured"}
    assert body["checks"]["migrations"] == {
        "status": "not_checked",
        "code": None,
        "current": None,
        "expected": None,
    }


def test_ready_database_not_configured_does_not_leak_connection_string():
    secret = "postgresql+psycopg://user:secret@db.example/correlis"
    with TestClient(create_app(api_settings(database_url=None))) as client:
        body = client.get("/health/ready").text
    assert "database_not_configured" in body
    assert secret not in body
    assert "secret" not in body


def test_ready_returns_503_when_database_connection_fails(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'missing' / 'db.sqlite'}", future=True)
    with TestClient(create_app(api_settings(), engine=engine)) as client:
        response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["database"] == {
        "status": "error",
        "code": "database_unavailable",
    }


def test_failed_readiness_does_not_include_raw_driver_exception_text(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'missing' / 'db.sqlite'}", future=True)
    with TestClient(create_app(api_settings(), engine=engine)) as client:
        body = client.get("/health/ready").text
    assert "unable to open database file" not in body
    assert "OperationalError" not in body


def test_ready_returns_503_for_uninitialized_database(tmp_path):
    with TestClient(create_app(api_settings(), engine=sqlite_engine(tmp_path))) as client:
        response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["migrations"]["code"] == "migrations_out_of_date"


def test_ready_returns_503_when_database_revision_is_behind_alembic_head(tmp_path):
    engine = stamp_sqlite_database(tmp_path, "base")
    with TestClient(create_app(api_settings(), engine=engine)) as client:
        response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["migrations"]["expected"] == alembic_heads()


def test_ready_returns_200_when_database_is_at_alembic_head(tmp_path):
    engine = stamp_sqlite_database(tmp_path, "head")
    with TestClient(create_app(api_settings(), engine=engine)) as client:
        response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["checks"]["migrations"]["current"] == alembic_heads()


def test_startup_creates_session_factory_when_database_is_configured(tmp_path):
    settings = api_settings(database_url=f"sqlite:///{tmp_path / 'app.sqlite'}")
    app = create_app(settings)
    with TestClient(app):
        assert app.state.database_session_factory is not None


def test_shutdown_disposes_application_owned_engine(tmp_path):
    settings = api_settings(database_url=f"sqlite:///{tmp_path / 'owned.sqlite'}")
    app = create_app(settings)
    with TestClient(app):
        engine = app.state.database_engine
    assert app.state.database_engine is None
    assert engine.pool is not None


def test_shutdown_does_not_dispose_externally_injected_engine(tmp_path):
    engine = sqlite_engine(tmp_path)
    app = create_app(api_settings(), engine=engine)
    with TestClient(app):
        pass
    with engine.connect() as connection:
        assert connection.execute(text("SELECT 1")).scalar_one() == 1


def test_get_database_session_opens_and_closes_session(tmp_path):
    engine = sqlite_engine(tmp_path)
    app = create_app(api_settings(), engine=engine)

    @app.get("/uses-session")
    def uses_session(session: Annotated[object, Depends(get_database_session)]):
        return {"selected": session.execute(text("SELECT 1")).scalar_one()}

    with TestClient(app) as client:
        response = client.get("/uses-session")
    assert response.status_code == 200
    assert response.json() == {"selected": 1}


def test_get_database_session_returns_503_when_no_session_factory_exists():
    app = create_app(api_settings())

    @app.get("/uses-session")
    def uses_session(session: Annotated[object, Depends(get_database_session)]):
        return {"unused": bool(session)}

    with TestClient(app) as client:
        response = client.get("/uses-session")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "database_not_configured"


def test_scenario_listing_still_works():
    with TestClient(create_app(api_settings())) as client:
        assert client.get("/api/v1/scenarios").json() == {"scenarios": ["initial-access-demo"]}


def test_scene_generation_still_works():
    with TestClient(create_app(api_settings())) as client:
        response = client.get("/api/v1/scenarios/initial-access-demo/scene")
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "confirmed"
    assert len(body["observations"]) == 7


def test_websocket_replay_still_works():
    with (
        TestClient(create_app(api_settings())) as client,
        client.websocket_connect("/ws/scenarios/initial-access-demo/replay?speed=0") as websocket,
    ):
        started = websocket.receive_json()
        assert started["type"] == "replay_started"
        for _ in range(7):
            assert websocket.receive_json()["type"] == "scene_delta"
        complete = websocket.receive_json()
        assert complete["type"] == "replay_complete"
        assert complete["data"]["state"] == "confirmed"


def test_scenario_not_found_handling_is_unchanged():
    with TestClient(create_app(api_settings())) as client:
        response = client.get("/api/v1/scenarios/missing/scene")
    assert response.status_code == 404
    assert response.json() == {"detail": "scenario not found"}


@pytest.mark.postgres
def test_postgres_ready_reports_current_head(migrated_engine):
    app = create_app(api_settings(), engine=migrated_engine)
    with TestClient(app) as client:
        response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["checks"]["migrations"]["current"] == alembic_heads()
    with migrated_engine.connect() as connection:
        assert connection.execute(text("SELECT 1")).scalar_one() == 1


def test_ontology_endpoint_returns_core_manifest_without_database():
    with TestClient(create_app(api_settings())) as client:
        response = client.get("/api/v1/ontology")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "correlis-core"
    assert body["version"] == "0.1.0"
    assert body["entity_types"]
    assert body["relationship_types"]
    assert body["action_types"]
    assert [item["type"] for item in body["entity_types"]] == sorted(
        item["type"] for item in body["entity_types"]
    )


def test_injected_ontology_registry_is_returned_by_endpoint():
    from correlis_ontology import CORE_ONTOLOGY

    app = create_app(api_settings(), ontology_registry=CORE_ONTOLOGY)
    with TestClient(app) as client:
        assert client.get("/api/v1/ontology").json()["version"] == CORE_ONTOLOGY.version
