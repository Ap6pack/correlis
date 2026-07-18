from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from correlis_schema import EntityRef, EntityType, EvidenceRef, EvidenceType, Observation
from correlis_store import ImmutableRecordConflict, ObservationRepository, WriteDisposition
from correlis_store.hashing import canonical_model_sha256
from correlis_store.models import Base, ObservationEvidenceRecord, ObservationRecord
from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.orm import Session, sessionmaker


def evidence(id: str = "ev-1", sha: str = "a" * 64) -> EvidenceRef:
    return EvidenceRef(
        id=id,
        type=EvidenceType.RAW_EVENT,
        source="sensor",
        locator=f"test://{id}",
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


@pytest.fixture
def session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'store.sqlite'}", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False, future=True)


def test_canonical_hashing_is_deterministic():
    assert canonical_model_sha256(evidence()) == canonical_model_sha256(evidence())


def test_observation_creation_and_round_trip(session_factory):
    repo = ObservationRepository(session_factory)
    obs = observation()
    assert repo.put(obs) == WriteDisposition.CREATED
    assert repo.get(obs.tenant_id, obs.id) == obs


def test_evidence_reference_round_trip(session_factory):
    repo = ObservationRepository(session_factory)
    obs = observation()
    repo.put(obs)
    assert repo.get_evidence(obs.tenant_id, obs.evidence[0].id) == obs.evidence[0]


def test_identical_retry_is_existing_and_does_not_duplicate(session_factory):
    repo = ObservationRepository(session_factory)
    obs = observation()
    assert repo.put(obs) == WriteDisposition.CREATED
    assert repo.put(obs) == WriteDisposition.EXISTING
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ObservationRecord)) == 1
        assert session.scalar(select(func.count()).select_from(ObservationEvidenceRecord)) == 1


def test_observation_conflict_raises(session_factory):
    repo = ObservationRepository(session_factory)
    repo.put(observation())
    with pytest.raises(ImmutableRecordConflict) as exc:
        repo.put(observation(activity="changed"))
    assert exc.value.resource_type == "observation"
    assert exc.value.tenant_id == "tenant-a"
    assert exc.value.record_id == "obs-1"


def test_evidence_conflict_raises(session_factory):
    repo = ObservationRepository(session_factory)
    repo.put(observation(ev=evidence()))
    with pytest.raises(ImmutableRecordConflict):
        repo.put(observation(id="obs-2", ev=evidence(sha="b" * 64)))


def test_evidence_conflict_rolls_back_observation(session_factory):
    repo = ObservationRepository(session_factory)
    repo.put(observation(ev=evidence()))
    with pytest.raises(ImmutableRecordConflict):
        repo.put(observation(id="obs-2", ev=evidence(sha="b" * 64)))
    assert repo.get("tenant-a", "obs-2") is None


def test_tenant_reads_cannot_cross_boundaries(session_factory):
    repo = ObservationRepository(session_factory)
    obs = observation(tenant="tenant-a")
    repo.put(obs)
    assert repo.get("tenant-b", obs.id) is None
    assert repo.get_evidence("tenant-b", obs.evidence[0].id) is None


def test_listing_newest_first_and_tie_breaker(session_factory):
    repo = ObservationRepository(session_factory)
    t1 = datetime(2026, 1, 1, 12, tzinfo=UTC)
    t2 = t1 + timedelta(hours=1)
    repo.put(observation(id="obs-a", when=t1, ev=evidence("ev-a")))
    repo.put(observation(id="obs-b", when=t2, ev=evidence("ev-b", "b" * 64)))
    repo.put(observation(id="obs-c", when=t2, ev=evidence("ev-c", "c" * 64)))
    assert [obs.id for obs in repo.list("tenant-a")] == ["obs-c", "obs-b", "obs-a"]


def test_invalid_listing_limits_are_rejected(session_factory):
    repo = ObservationRepository(session_factory)
    with pytest.raises(ValueError):
        repo.list("tenant-a", limit=0)
    with pytest.raises(ValueError):
        repo.list("tenant-a", limit=501)


def test_before_filter(session_factory):
    repo = ObservationRepository(session_factory)
    t1 = datetime(2026, 1, 1, 12, tzinfo=UTC)
    t2 = t1 + timedelta(hours=1)
    repo.put(observation(id="obs-a", when=t1, ev=evidence("ev-a")))
    repo.put(observation(id="obs-b", when=t2, ev=evidence("ev-b", "b" * 64)))
    assert [obs.id for obs in repo.list("tenant-a", before=t2)] == ["obs-a"]


def test_alembic_upgrade_and_downgrade_create_expected_tables(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config

    db = tmp_path / "migration.sqlite"
    monkeypatch.setenv("CORRELIS_DATABASE_URL", f"sqlite:///{db}")
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{db}", future=True)
    assert {"observations", "evidence_refs", "observation_evidence"}.issubset(
        set(inspect(engine).get_table_names())
    )
    command.downgrade(config, "base")
    assert {"observations", "evidence_refs", "observation_evidence"}.isdisjoint(
        set(inspect(engine).get_table_names())
    )
