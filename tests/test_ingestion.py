from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from correlis_api.app import create_app
from correlis_api.settings import Settings
from correlis_schema import (
    EntityRef,
    EntityType,
    EvidenceRef,
    EvidenceType,
    Observation,
    RelationshipType,
)
from correlis_store import CollectorRepository, ObservationRepository
from correlis_store.models import Base, CollectorAuthEventRecord
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

PEPPER = "non-production-test-pepper-value-32-bytes"
SCENARIOS = Path(__file__).parents[1] / "scenarios"
ALEMBIC_CONFIG = Path(__file__).parents[1] / "alembic.ini"


def api_settings(**overrides) -> Settings:
    values = {
        "scenario_dir": SCENARIOS,
        "alembic_config_path": ALEMBIC_CONFIG,
        "credential_pepper": PEPPER,
        "ingest_max_body_bytes": 8192,
        "ingest_max_batch_size": 3,
    }
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def app_client(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'ingest.sqlite'}", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False, future=True)
    with factory() as session:
        repo = CollectorRepository(session)
        repo.create_collector(
            tenant_id="tenant-a", collector_id="collector-1", name="Outrider", source="outrider"
        )
        token = repo.issue_credential(
            "tenant-a", "collector-1", name="primary", pepper=PEPPER
        ).token
    app = create_app(api_settings(), engine=engine)
    with TestClient(app) as client:
        yield client, factory, token


def evidence(id: str = "ev-1", sha: str = "a" * 64) -> EvidenceRef:
    return EvidenceRef(
        id=id,
        type=EvidenceType.RAW_EVENT,
        source="outrider",
        locator=f"test://{id}",
        sha256=sha,
        collected_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def observation(
    id: str = "obs-1",
    tenant_id: str = "tenant-a",
    source: str = "outrider",
    ev: EvidenceRef | None = None,
    activity: str = "login",
) -> Observation:
    return Observation(
        id=id,
        tenant_id=tenant_id,
        event_time=datetime(2026, 1, 1, 12, tzinfo=UTC),
        ingest_time=datetime(2026, 1, 1, 12, 1, tzinfo=UTC),
        source=source,
        sensor_id="sensor-1",
        event_class="authentication",
        activity=activity,
        subject=EntityRef(id="identity-1", type=EntityType.IDENTITY, label="alice"),
        evidence=[ev or evidence()],
    )


def auth(token: str, request_id: str = "rid-1") -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Request-ID": request_id,
    }


def test_request_id_preserved_and_generated_on_health():
    with TestClient(create_app(api_settings())) as client:
        assert (
            client.get("/health", headers={"X-Request-ID": "demo-1"}).headers["X-Request-ID"]
            == "demo-1"
        )
        generated = client.get("/health", headers={"X-Request-ID": "bad\nvalue"}).headers[
            "X-Request-ID"
        ]
        UUID(generated)


def test_single_ingestion_created_existing_and_audit_request_id(app_client):
    client, factory, token = app_client
    payload = observation().model_dump(mode="json")
    response = client.post("/api/v1/observations", json=payload, headers=auth(token, "rid-ok"))
    assert response.status_code == 201
    assert response.headers["X-Request-ID"] == "rid-ok"
    assert response.json()["disposition"] == "created"
    assert response.json()["request_id"] == "rid-ok"
    with factory() as session:
        assert ObservationRepository(session).get("tenant-a", "obs-1") == observation()
        events = session.scalars(select(CollectorAuthEventRecord)).all()
        assert [event.request_id for event in events] == ["rid-ok"]
    retry = client.post("/api/v1/observations", json=payload, headers=auth(token, "rid-retry"))
    assert retry.status_code == 200
    assert retry.json()["disposition"] == "existing"


def test_single_ingestion_conflicts_and_scope_prevalidation(app_client):
    client, factory, token = app_client
    response = client.post(
        "/api/v1/observations", json=observation().model_dump(mode="json"), headers=auth(token)
    )
    assert response.status_code == 201
    changed = observation(activity="changed").model_dump(mode="json")
    conflict = client.post("/api/v1/observations", json=changed, headers=auth(token))
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "immutable_observation_conflict"
    tenant_mismatch = client.post(
        "/api/v1/observations",
        json=observation(id="obs-tenant", tenant_id="tenant-b", ev=evidence("ev-t")).model_dump(
            mode="json"
        ),
        headers=auth(token),
    )
    assert tenant_mismatch.status_code == 403
    assert tenant_mismatch.json()["detail"]["code"] == "collector_tenant_scope_mismatch"
    source_mismatch = client.post(
        "/api/v1/observations",
        json=observation(id="obs-source", source="other", ev=evidence("ev-s")).model_dump(
            mode="json"
        ),
        headers=auth(token),
    )
    assert source_mismatch.status_code == 403
    with factory() as session:
        repo = ObservationRepository(session)
        assert repo.get("tenant-b", "obs-tenant") is None
        assert repo.get("tenant-a", "obs-source") is None


def test_ontology_validation_writes_nothing(app_client):
    client, factory, token = app_client
    invalid = observation(id="obs-invalid", ev=evidence("ev-invalid")).model_copy(
        update={
            "object": EntityRef(id="asset-1", type=EntityType.ASSET, label="host"),
            "relationship": RelationshipType.SPAWNED,
        }
    )
    response = client.post(
        "/api/v1/observations", json=invalid.model_dump(mode="json"), headers=auth(token)
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "ontology_validation_failed"
    with factory() as session:
        assert ObservationRepository(session).get("tenant-a", "obs-invalid") is None


def test_body_limits_and_media_type_are_ingestion_only(app_client):
    client, _, token = app_client
    too_large = "{" + " " * 9000 + "}"
    response = client.post(
        "/api/v1/observations",
        content=too_large,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    assert response.status_code == 413
    assert response.headers["X-Request-ID"]
    media = client.post(
        "/api/v1/observations", content="{}", headers={"Content-Type": "text/plain"}
    )
    assert media.status_code == 415
    assert client.post("/health", content=too_large).status_code == 405


def test_batch_ingestion_order_conflicts_and_retry(app_client):
    client, _, token = app_client
    items = [
        observation(id="obs-a", ev=evidence("ev-a")),
        observation(id="obs-a", ev=evidence("ev-a")),
        observation(id="obs-b", ev=evidence("ev-b")),
    ]
    response = client.post(
        "/api/v1/observations/batch",
        json={"observations": [item.model_dump(mode="json") for item in items]},
        headers=auth(token, "batch-1"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["request_id"] == "batch-1"
    assert [item["disposition"] for item in body["results"]] == ["created", "existing", "created"]
    retry = client.post(
        "/api/v1/observations/batch",
        json={"observations": [item.model_dump(mode="json") for item in items]},
        headers=auth(token, "batch-2"),
    )
    assert [item["disposition"] for item in retry.json()["results"]] == [
        "existing",
        "existing",
        "existing",
    ]


def test_batch_prevalidation_and_size_limit_write_nothing(app_client):
    client, factory, token = app_client
    too_many = [observation(id=f"obs-{i}", ev=evidence(f"ev-{i}")) for i in range(4)]
    response = client.post(
        "/api/v1/observations/batch",
        json={"observations": [item.model_dump(mode="json") for item in too_many]},
        headers=auth(token),
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "batch_size_exceeded"
    mixed = [
        observation(id="obs-good", ev=evidence("ev-good")),
        observation(id="obs-bad", tenant_id="tenant-b", ev=evidence("ev-bad")),
    ]
    response = client.post(
        "/api/v1/observations/batch",
        json={"observations": [item.model_dump(mode="json") for item in mixed]},
        headers=auth(token),
    )
    assert response.status_code == 403
    assert response.json()["detail"]["item_index"] == 1
    with factory() as session:
        assert ObservationRepository(session).get("tenant-a", "obs-good") is None
