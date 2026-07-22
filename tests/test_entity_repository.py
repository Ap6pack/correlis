from __future__ import annotations

from datetime import UTC, datetime, timedelta

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
    ProjectionRepository,
    ProjectorIdentity,
)
from correlis_store.models import Base, EntityRecord, ProjectorCheckpointRecord
from correlis_store.observation_sequence import SequencedObservation
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

T0 = datetime(2026, 1, 1, 12, tzinfo=UTC)


@pytest.fixture
def session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'repo.sqlite'}", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False, future=True)


def ev(id="ev-1"):
    return EvidenceRef(
        id=id,
        type=EvidenceType.RAW_EVENT,
        source="sensor",
        locator=f"test://{id}",
        sha256="a" * 64,
        collected_at=T0,
        metadata={"payload": True},
    )


def observation(id: str, entity_id: str, *, tenant="tenant-a", version="1", type=EntityType.ASSET):
    attrs = {"asset_id": entity_id} if type == EntityType.ASSET else {"application_id": entity_id}
    return Observation(
        id=id,
        tenant_id=tenant,
        event_time=T0 + timedelta(minutes=len(id)),
        ingest_time=T0,
        source="src",
        sensor_id="sensor",
        event_class=EventClass.AUTHENTICATION,
        activity="a",
        subject=EntityRef(id=entity_id, type=type, label=entity_id, attributes=attrs),
        evidence=[ev(f"ev-{id}")],
    )


def project(sf, obs: Observation, *, version="1"):
    result = ObservationRepository(sf).put_with_result(obs)
    with sf() as session, session.begin():
        EntityProjectionHandler(
            projection_version=version, clock=lambda: datetime(2026, 2, 1, tzinfo=UTC)
        )(session, SequencedObservation(result.ingest_sequence, obs))


def seed(sf):
    project(sf, observation("obs-a", "ent-a"))
    project(sf, observation("obs-b", "ent-b"))
    project(sf, observation("obs-c", "ent-c", type=EntityType.APPLICATION))
    project(sf, observation("obs-other-tenant", "ent-a", tenant="tenant-b"))
    project(sf, observation("obs-v2", "ent-a"), version="2")


def test_repository_scope_listing_pagination_and_limits(session_factory):
    seed(session_factory)
    repo = EntityRepository(session_factory)
    assert repo.get_entity("1", "tenant-a", "ent-a").entity_id == "ent-a"
    assert repo.get_entity("1", "tenant-b", "ent-b") is None
    assert repo.get_entity("2", "tenant-a", "ent-b") is None
    assert [e.entity_id for e in repo.list_entities("1", "tenant-a").items] == [
        "ent-a",
        "ent-b",
        "ent-c",
    ]
    assert [
        e.entity_id
        for e in repo.list_entities("1", "tenant-a", entity_type=EntityType.APPLICATION).items
    ] == ["ent-c"]
    first = repo.list_entities("1", "tenant-a", limit=2)
    assert [e.entity_id for e in first.items] == ["ent-a", "ent-b"]
    assert first.has_more is True
    assert first.next_entity_id == "ent-b"
    second = repo.list_entities("1", "tenant-a", after_entity_id=first.next_entity_id, limit=2)
    assert [e.entity_id for e in second.items] == ["ent-c"]
    assert second.has_more is False
    assert second.next_entity_id is None
    empty = repo.list_entities("1", "tenant-a", after_entity_id="z", limit=2)
    assert empty.items == [] and empty.next_entity_id is None
    with pytest.raises(ValueError):
        repo.list_entities("1", "tenant-a", limit=0)
    with pytest.raises(ValueError):
        repo.list_entities("1", "tenant-a", limit=501)


def test_repository_lineage_limits_no_locator_no_mutation_or_commit(session_factory):
    seed(session_factory)
    ProjectionRepository(session_factory).register_projector(ProjectorIdentity("other", "1"))
    repo = EntityRepository(session_factory)
    before = repo.get_entity("1", "tenant-a", "ent-a").updated_at
    lineage = repo.get_lineage(
        "1", "tenant-a", "ent-a", observation_limit=1, evidence_limit=1, identity_claim_limit=1
    )
    assert lineage is not None
    assert len(lineage.observations) == 1
    assert lineage.observations[0].source == "src"
    assert lineage.observations[0].sensor_id == "sensor"
    assert len(lineage.evidence) == 1
    assert not hasattr(lineage.evidence[0], "locator")
    assert len(lineage.identity_claims) == 1
    assert repo.get_lineage("1", "tenant-a", "missing") is None
    with session_factory() as session:
        assert session.scalar(select(ProjectorCheckpointRecord.last_processed_sequence)) == 0
        assert (
            session.get(
                EntityRecord,
                {"projection_version": "1", "tenant_id": "tenant-a", "entity_id": "ent-a"},
            ).updated_at
            == before
        )
    for kwargs in ({"observation_limit": 0}, {"evidence_limit": 501}, {"identity_claim_limit": 0}):
        with pytest.raises(ValueError):
            repo.get_lineage("1", "tenant-a", "ent-a", **kwargs)
