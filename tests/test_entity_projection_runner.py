from __future__ import annotations

from datetime import UTC, datetime

import pytest
from correlis_schema import (
    EntityRef,
    EntityType,
    EventClass,
    EvidenceRef,
    EvidenceType,
    Observation,
)
from correlis_store import (
    EntityProjectionHandler,
    EntityRepository,
    ObservationRepository,
    ProjectionInvariantError,
    ProjectionRepository,
    ProjectionRunner,
    ProjectionRunOutcome,
    ProjectorIdentity,
)
from correlis_store.models import (
    Base,
    EntityObservationRecord,
    EvidenceRefRecord,
    ObservationRecord,
    ProjectorFailureRecord,
)
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

T = datetime(2026, 1, 1, 12, tzinfo=UTC)


@pytest.fixture
def session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'runner.sqlite'}", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False, future=True)


def ev(id="ev-1"):
    return EvidenceRef(
        id=id,
        type=EvidenceType.RAW_EVENT,
        source="s",
        locator=f"test://{id}",
        sha256="b" * 64,
        collected_at=T,
        metadata={"m": "v"},
    )


def obs(id: str, entity_id="asset-1", *, type=EntityType.ASSET, label="asset"):
    attrs = {"asset_id": entity_id} if type == EntityType.ASSET else {"application_id": entity_id}
    return Observation(
        id=id,
        tenant_id="tenant-a",
        event_time=T,
        ingest_time=T,
        source="s",
        sensor_id="sensor",
        event_class=EventClass.AUTHENTICATION,
        activity="a",
        subject=EntityRef(id=entity_id, type=type, label=label, attributes=attrs),
        evidence=[ev(f"ev-{id}")],
    )


def put(sf, *observations):
    repo = ObservationRepository(sf)
    for observation in observations:
        repo.put_with_result(observation)


def test_projection_runner_checkpoint_atomicity_retry_versions_and_invariants(session_factory):
    put(
        session_factory,
        obs("obs-1"),
        obs("obs-2", label="second"),
        obs("obs-3", type=EntityType.APPLICATION, label="bad"),
    )
    projections = ProjectionRepository(session_factory)
    handler = EntityProjectionHandler(clock=lambda: datetime(2026, 2, 1, tzinfo=UTC))
    checkpoint = projections.register_projector(handler.projector_identity)
    assert checkpoint.last_processed_sequence == 0

    first = ProjectionRunner(session_factory).run_batch(
        handler.projector_identity, handler, limit=1
    )
    assert first.outcome == ProjectionRunOutcome.ADVANCED
    assert first.ending_sequence == 1
    assert EntityRepository(session_factory).get_entity("1", "tenant-a", "asset-1") is not None

    failed = ProjectionRunner(session_factory).run_batch(
        handler.projector_identity, handler, limit=10
    )
    assert failed.outcome == ProjectionRunOutcome.FAILED
    assert failed.failure_sequence == 3
    checkpoint = projections.get_checkpoint(handler.projector_identity)
    assert checkpoint.last_processed_sequence == 2
    assert checkpoint.last_failure_sequence == 3
    assert (
        EntityRepository(session_factory).get_entity("1", "tenant-a", "asset-1").label == "second"
    )
    assert (
        EntityRepository(session_factory)
        .get_entity("1", "tenant-a", "asset-1")
        .last_ingest_sequence
        == 2
    )
    assert (
        EntityRepository(session_factory).get_entity("1", "tenant-a", "asset-1").entity_type
        == EntityType.ASSET
    )

    blocked = ProjectionRunner(session_factory).run_batch(handler.projector_identity, handler)
    assert blocked.outcome == ProjectionRunOutcome.BLOCKED
    retry = ProjectionRunner(session_factory).run_batch(
        handler.projector_identity, handler, retry_failed=True
    )
    assert retry.outcome == ProjectionRunOutcome.FAILED
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ProjectorFailureRecord)) == 1
        assert session.scalar(select(func.count()).select_from(ObservationRecord)) == 3
        assert session.scalar(select(func.count()).select_from(EvidenceRefRecord)) == 3

    v2 = EntityProjectionHandler(
        projection_version="2", clock=lambda: datetime(2026, 2, 1, tzinfo=UTC)
    )
    projections.register_projector(v2.projector_identity)
    assert projections.get_checkpoint(v2.projector_identity).last_processed_sequence == 0
    v2_result = ProjectionRunner(session_factory).run_batch(v2.projector_identity, v2, limit=1)
    assert v2_result.ending_sequence == 1
    assert EntityRepository(session_factory).get_entity("2", "tenant-a", "asset-1") is not None

    other = projections.register_projector(ProjectorIdentity("other-projector", "1"))
    assert other.last_processed_sequence == 0

    bad_clock = EntityProjectionHandler(
        projection_version="clock", clock=lambda: datetime(2026, 1, 1)
    )
    projections.register_projector(bad_clock.projector_identity)
    with pytest.raises(ProjectionInvariantError):
        ProjectionRunner(session_factory).run_batch(
            bad_clock.projector_identity, bad_clock, limit=1
        )
    with session_factory() as session:
        assert (
            session.scalar(
                select(func.count()).where(ProjectorFailureRecord.projector_version == "clock")
            )
            == 0
        )
        assert session.scalar(select(func.count()).select_from(EntityObservationRecord)) >= 3
