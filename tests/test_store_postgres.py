from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta, timezone
from threading import Barrier

import pytest
from alembic import command
from alembic.config import Config
from correlis_schema import EntityRef, EntityType, EvidenceRef, EvidenceType, Observation
from correlis_store import ImmutableRecordConflict, ObservationRepository, WriteDisposition
from correlis_store.models import EvidenceRefRecord, ObservationEvidenceRecord, ObservationRecord
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.postgres

POSTGRES_URL = os.environ.get("CORRELIS_TEST_DATABASE_URL")


def evidence(id: str = "ev-1", sha: str = "a" * 64, locator: str | None = None) -> EvidenceRef:
    return EvidenceRef(
        id=id,
        type=EvidenceType.RAW_EVENT,
        source="sensor",
        locator=locator or f"test://{id}",
        sha256=sha,
        collected_at=datetime(2026, 1, 1, tzinfo=UTC),
        metadata={"k": "v"},
    )


def observation(
    id: str = "obs-1",
    tenant: str = "tenant-a",
    when: datetime | None = None,
    ev: EvidenceRef | None = None,
    activity: str = "login",
) -> Observation:
    return Observation(
        id=id,
        tenant_id=tenant,
        event_time=when or datetime(2026, 1, 1, 12, tzinfo=UTC),
        ingest_time=datetime(2026, 1, 1, 12, 1, tzinfo=UTC),
        source="sensor",
        sensor_id="sensor-1",
        event_class="authentication",
        activity=activity,
        subject=EntityRef(id="asset-1", type=EntityType.ASSET, label="asset"),
        evidence=[ev or evidence()],
    )


@pytest.fixture(scope="session")
def postgres_url() -> str:
    if not POSTGRES_URL:
        pytest.skip("CORRELIS_TEST_DATABASE_URL is required for PostgreSQL integration tests")
    return POSTGRES_URL


@pytest.fixture(scope="session")
def migrated_engine(postgres_url: str):
    os.environ["CORRELIS_DATABASE_URL"] = postgres_url
    config = Config("alembic.ini")
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = create_engine(postgres_url, future=True)
    try:
        yield engine
    finally:
        engine.dispose()
        command.downgrade(config, "base")


@pytest.fixture
def session_factory(migrated_engine):
    with migrated_engine.begin() as connection:
        connection.execute(text("TRUNCATE observation_evidence, observations, evidence_refs"))
    return sessionmaker(bind=migrated_engine, class_=Session, expire_on_commit=False, future=True)


def counts(session_factory) -> tuple[int, int, int]:
    with session_factory() as session:
        return (
            session.scalar(select(func.count()).select_from(ObservationRecord)),
            session.scalar(select(func.count()).select_from(EvidenceRefRecord)),
            session.scalar(select(func.count()).select_from(ObservationEvidenceRecord)),
        )


def run_concurrently(*calls):
    barrier = Barrier(len(calls))

    def wrapped(call):
        barrier.wait()
        return call()

    with ThreadPoolExecutor(max_workers=len(calls)) as executor:
        return [future.result() for future in [executor.submit(wrapped, call) for call in calls]]


def test_alembic_upgrades_successfully_against_postgresql(postgres_url):
    os.environ["CORRELIS_DATABASE_URL"] = postgres_url
    config = Config("alembic.ini")
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = create_engine(postgres_url, future=True)
    try:
        assert {"observations", "evidence_refs", "observation_evidence"}.issubset(
            set(inspect(engine).get_table_names())
        )
    finally:
        engine.dispose()


def test_alembic_downgrades_successfully_against_postgresql(postgres_url):
    os.environ["CORRELIS_DATABASE_URL"] = postgres_url
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    command.downgrade(config, "base")
    engine = create_engine(postgres_url, future=True)
    try:
        assert {"observations", "evidence_refs", "observation_evidence"}.isdisjoint(
            set(inspect(engine).get_table_names())
        )
    finally:
        engine.dispose()
        command.upgrade(config, "head")


def test_postgresql_schema_contracts(session_factory, migrated_engine):
    inspector = inspect(migrated_engine)
    for table in ("observations", "evidence_refs"):
        payload = next(
            column for column in inspector.get_columns(table) if column["name"] == "payload_json"
        )
        assert isinstance(payload["type"], JSONB)
    assert set(inspector.get_pk_constraint("observations")["constrained_columns"]) == {
        "tenant_id",
        "observation_id",
    }
    assert set(inspector.get_pk_constraint("evidence_refs")["constrained_columns"]) == {
        "tenant_id",
        "evidence_id",
    }
    assert set(inspector.get_pk_constraint("observation_evidence")["constrained_columns"]) == {
        "tenant_id",
        "observation_id",
        "evidence_id",
    }
    fks = inspector.get_foreign_keys("observation_evidence")
    assert ["tenant_id", "observation_id"] in [fk["constrained_columns"] for fk in fks]
    assert ["tenant_id", "evidence_id"] in [fk["constrained_columns"] for fk in fks]
    indexes = {index["name"] for index in inspector.get_indexes("observations")}
    assert "ix_observations_tenant_event_time" in indexes
    assert "ix_observations_tenant_source_event_time" in indexes
    assert "ix_observations_tenant_event_class_event_time" in indexes


def test_observation_and_evidence_round_trip(session_factory):
    repo = ObservationRepository(session_factory)
    obs = observation()
    assert repo.put(obs) == WriteDisposition.CREATED
    assert repo.get(obs.tenant_id, obs.id) == obs
    assert repo.get_evidence(obs.tenant_id, obs.evidence[0].id) == obs.evidence[0]


def test_concurrent_identical_observation_writes_are_idempotent(session_factory):
    obs = observation()
    results = run_concurrently(
        lambda: ObservationRepository(session_factory).put(obs),
        lambda: ObservationRepository(session_factory).put(obs),
    )
    assert sorted(results) == [WriteDisposition.CREATED, WriteDisposition.EXISTING]
    assert counts(session_factory) == (1, 1, 1)


def test_concurrent_conflicting_observation_write_raises_conflict(session_factory):
    obs_a = observation(activity="login")
    obs_b = observation(activity="logout")

    def put(obs):
        try:
            return ObservationRepository(session_factory).put(obs)
        except Exception as exc:  # noqa: BLE001 - assert public exception type below.
            return exc

    results = run_concurrently(lambda: put(obs_a), lambda: put(obs_b))
    assert any(result == WriteDisposition.CREATED for result in results)
    assert any(isinstance(result, ImmutableRecordConflict) for result in results)
    assert not any(isinstance(result, IntegrityError) for result in results)
    assert counts(session_factory)[0] == 1


def test_concurrent_shared_evidence_can_be_reused(session_factory):
    shared = evidence()
    results = run_concurrently(
        lambda: ObservationRepository(session_factory).put(observation(id="obs-a", ev=shared)),
        lambda: ObservationRepository(session_factory).put(observation(id="obs-b", ev=shared)),
    )
    assert results == [WriteDisposition.CREATED, WriteDisposition.CREATED]
    assert counts(session_factory) == (2, 1, 2)


def test_concurrent_conflicting_evidence_rolls_back_losing_observation(session_factory):
    ev_a = evidence(id="ev-shared", sha="a" * 64)
    ev_b = evidence(id="ev-shared", sha="b" * 64)

    def put(obs):
        try:
            return ObservationRepository(session_factory).put(obs)
        except Exception as exc:  # noqa: BLE001 - assert public exception type below.
            return exc

    results = run_concurrently(
        lambda: put(observation(id="obs-a", ev=ev_a)),
        lambda: put(observation(id="obs-b", ev=ev_b)),
    )
    assert any(result == WriteDisposition.CREATED for result in results)
    assert any(isinstance(result, ImmutableRecordConflict) for result in results)
    assert not any(isinstance(result, IntegrityError) for result in results)
    assert counts(session_factory) == (1, 1, 1)


def test_tenant_isolation_against_postgresql(session_factory):
    repo = ObservationRepository(session_factory)
    obs = observation(tenant="tenant-a")
    repo.put(obs)
    assert repo.get("tenant-b", obs.id) is None
    assert repo.get_evidence("tenant-b", obs.evidence[0].id) is None


def test_timezone_aware_event_time_survives_round_trip(session_factory):
    repo = ObservationRepository(session_factory)
    when = datetime(2026, 1, 1, 17, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    obs = observation(when=when)
    repo.put(obs)
    stored = repo.get(obs.tenant_id, obs.id)
    assert stored is not None
    assert stored.event_time == when
    assert stored.event_time.tzinfo is not None
