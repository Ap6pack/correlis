from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from correlis_schema import EntityRef, EntityType, EvidenceRef, EvidenceType, Observation
from correlis_store import (
    ObservationRepository,
    ProjectionHandlerError,
    ProjectionRepository,
    ProjectionRunner,
    ProjectionRunOutcome,
    ProjectorFailed,
    ProjectorFailureStatus,
    ProjectorIdentity,
    ProjectorStateConflict,
)
from correlis_store.models import Base
from sqlalchemy import BigInteger, Column, String, create_engine
from sqlalchemy.orm import Session, sessionmaker


class TestProjectionEffect(Base):
    __tablename__ = "test_projection_effects"
    ingest_sequence = Column(BigInteger, primary_key=True, autoincrement=False)
    observation_id = Column(String(128), nullable=False)
    value = Column(String(128), nullable=False)


def evidence(id="ev-1", sha="a" * 64):
    return EvidenceRef(
        id=id,
        type=EvidenceType.RAW_EVENT,
        source="sensor",
        locator=f"test://{id}",
        sha256=sha,
        collected_at=datetime(2026, 1, 1, tzinfo=UTC),
        metadata={},
    )


def observation(id="obs-1", tenant="tenant-a", when=None, ev=None):
    return Observation(
        id=id,
        tenant_id=tenant,
        event_time=when or datetime(2026, 1, 1, 12, tzinfo=UTC),
        ingest_time=datetime(2026, 1, 1, 12, 1, tzinfo=UTC),
        source="sensor",
        sensor_id="s",
        event_class="authentication",
        activity="login",
        subject=EntityRef(id="asset", type=EntityType.ASSET, label="asset"),
        evidence=[ev or evidence()],
    )


@pytest.fixture
def sf(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'db.sqlite'}", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False, future=True)


@pytest.fixture
def clock():
    values = [datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=i) for i in range(100)]
    return lambda: values.pop(0)


def ident(v="1"):
    return ProjectorIdentity("entity-projection", v)


def test_register_lifecycle_and_versions(sf, clock):
    repo = ProjectionRepository(sf, clock=clock)
    cp = repo.register_projector(ident())
    assert cp.last_processed_sequence == 0
    assert cp.status == "idle"
    assert repo.register_projector(ident()).last_processed_sequence == cp.last_processed_sequence
    assert repo.register_projector(ident("2")).identity.version == "2"
    assert [c.identity.version for c in repo.list_checkpoints()] == ["1", "2"]
    assert repo.pause_projector(ident()).status == "paused"
    assert repo.pause_projector(ident()).status == "paused"
    assert repo.resume_projector(ident()).status == "idle"
    with pytest.raises(ValueError):
        ProjectorIdentity("Bad", "1")
    with pytest.raises(ValueError):
        ProjectorIdentity("ok", " bad")
    with pytest.raises(ValueError):
        repo.list_checkpoints(limit=0)


def test_successful_runner_commits_effect_and_checkpoint(sf, clock):
    ProjectionRepository(sf, clock=clock).register_projector(ident())
    ObservationRepository(sf).put(observation())

    def handler(session, item):
        session.add(
            TestProjectionEffect(
                ingest_sequence=item.ingest_sequence, observation_id=item.observation.id, value="ok"
            )
        )

    result = ProjectionRunner(sf, clock=clock).run_batch(ident(), handler)
    assert result.outcome == ProjectionRunOutcome.CAUGHT_UP
    assert result.processed_count == 1
    assert ProjectionRepository(sf).get_checkpoint(ident()).last_processed_sequence == 1
    with sf() as session:
        assert session.get(TestProjectionEffect, 1).observation_id == "obs-1"


def test_failure_blocks_and_retry_resolves(sf, clock):
    repo = ProjectionRepository(sf, clock=clock)
    repo.register_projector(ident())
    ObservationRepository(sf).put(observation())

    def failing(session, item):
        session.add(
            TestProjectionEffect(
                ingest_sequence=item.ingest_sequence,
                observation_id=item.observation.id,
                value="bad",
            )
        )
        raise ProjectionHandlerError("bad_item", "safe failure")

    result = ProjectionRunner(sf, clock=clock).run_batch(ident(), failing)
    assert result.outcome == ProjectionRunOutcome.FAILED
    cp = repo.get_checkpoint(ident())
    assert (
        cp.status == "failed" and cp.last_processed_sequence == 0 and cp.last_failure_sequence == 1
    )
    with sf() as session:
        assert session.get(TestProjectionEffect, 1) is None
    failures = repo.list_failures(ident(), status=ProjectorFailureStatus.ACTIVE)
    assert failures[0].attempt_count == 1 and failures[0].safe_message == "safe failure"
    called = False

    def ok(session, item):
        nonlocal called
        called = True
        session.add(
            TestProjectionEffect(
                ingest_sequence=item.ingest_sequence, observation_id=item.observation.id, value="ok"
            )
        )

    assert (
        ProjectionRunner(sf, clock=clock).run_batch(ident(), ok).outcome
        == ProjectionRunOutcome.BLOCKED
    )
    assert not called
    assert (
        ProjectionRunner(sf, clock=clock).run_batch(ident(), ok, retry_failed=True).outcome
        == ProjectionRunOutcome.CAUGHT_UP
    )
    assert repo.get_checkpoint(ident()).status == "idle"
    assert repo.list_failures(ident(), status=ProjectorFailureStatus.RESOLVED)[0].attempt_count == 1


def test_failed_lifecycle_controls_rejected(sf, clock):
    repo = ProjectionRepository(sf, clock=clock)
    repo.register_projector(ident())
    ObservationRepository(sf).put(observation())
    ProjectionRunner(sf, clock=clock).run_batch(
        ident(), lambda s, i: (_ for _ in ()).throw(ProjectionHandlerError("bad", "safe"))
    )
    with pytest.raises(ProjectorStateConflict):
        repo.pause_projector(ident())
    with pytest.raises(ProjectorFailed):
        repo.resume_projector(ident())
