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
    ProvenanceClass,
    RelationshipType,
    relationship_id,
)
from correlis_store import (
    ObservationRepository,
    ProjectionHandlerError,
    ProjectionInvariantError,
    ProjectionRepository,
    ProjectionRunner,
    RelationshipProjectionHandler,
    RelationshipRepository,
    relationship_projector_identity,
)
from correlis_store.models import (
    Base,
    RelationshipEvidenceRecord,
    RelationshipObservationRecord,
    RelationshipRecord,
)
from correlis_store.observation_sequence import SequencedObservation
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

T0 = datetime(2026, 1, 1, tzinfo=UTC)
T1 = datetime(2026, 1, 2, tzinfo=UTC)
T2 = datetime(2026, 1, 3, tzinfo=UTC)
C0 = datetime(2026, 2, 1, tzinfo=UTC)
C1 = datetime(2026, 2, 2, tzinfo=UTC)


@pytest.fixture
def sf(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'rel.sqlite'}", future=True)
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
    )


def ref(id="asset-1", type=EntityType.ASSET):
    return EntityRef(
        id=id,
        type=type,
        label=id,
        attributes={"asset_id": id} if type == EntityType.ASSET else {"vulnerability_id": id},
    )


def obs(
    id,
    *,
    tenant="tenant-a",
    when=T1,
    rel=RelationshipType.HAS_VULNERABILITY,
    subject=None,
    object=None,
    evidence=None,
    conf=0.5,
):
    return Observation(
        id=id,
        tenant_id=tenant,
        event_time=when,
        ingest_time=when + timedelta(minutes=1),
        source="sensor-a",
        sensor_id="sensor-1",
        event_class=EventClass.EXPOSURE_FINDING,
        activity="finding",
        confidence=conf,
        subject=subject or ref(),
        object=object or ref("vuln-1", EntityType.VULNERABILITY),
        relationship=rel,
        evidence=evidence or [ev()],
    )


def put(sf, o):
    r = ObservationRepository(sf).put_with_result(o)
    return SequencedObservation(r.ingest_sequence, o)


def apply(sf, item, version="1", clock=lambda: C0):
    with sf() as s, s.begin():
        RelationshipProjectionHandler(projection_version=version, clock=clock)(s, item)


def row(sf, rid=None, tenant="tenant-a", version="1"):
    with sf() as s:
        if rid is None:
            return s.scalar(select(RelationshipRecord))
        return s.get(
            RelationshipRecord,
            {"projection_version": version, "tenant_id": tenant, "relationship_id": rid},
        )


def test_shared_relationship_id_compatibility_and_variation():
    assert (
        relationship_id(
            "tenant-a",
            "asset-1",
            RelationshipType.HAS_VULNERABILITY,
            "vuln-1",
            ProvenanceClass.OBSERVED,
            None,
        )
        == "f5459ffa0d3e22db2cb833db0694fcd8"
    )
    assert relationship_id(
        "tenant-a",
        "asset-1",
        RelationshipType.HAS_VULNERABILITY,
        "vuln-1",
        ProvenanceClass.OBSERVED,
        None,
    ) == relationship_id(
        "tenant-a",
        "asset-1",
        RelationshipType.HAS_VULNERABILITY,
        "vuln-1",
        ProvenanceClass.OBSERVED,
    )
    assert relationship_id(
        "tenant-b",
        "asset-1",
        RelationshipType.HAS_VULNERABILITY,
        "vuln-1",
        ProvenanceClass.OBSERVED,
    ) != relationship_id(
        "tenant-a",
        "asset-1",
        RelationshipType.HAS_VULNERABILITY,
        "vuln-1",
        ProvenanceClass.OBSERVED,
    )
    assert relationship_id(
        "tenant-a",
        "asset-1",
        RelationshipType.HAS_VULNERABILITY,
        "vuln-1",
        ProvenanceClass.DETERMINISTIC,
        "r1",
    ) != relationship_id(
        "tenant-a",
        "asset-1",
        RelationshipType.HAS_VULNERABILITY,
        "vuln-1",
        ProvenanceClass.DETERMINISTIC,
        "r2",
    )


def test_no_relationship_is_noop(sf):
    o = obs("no", rel=None, object=None)
    item = put(sf, o)
    apply(sf, item)
    with sf() as s:
        assert s.scalar(select(func.count()).select_from(RelationshipRecord)) == 0


def test_relationship_creation_idempotency_aggregation_isolation_and_repository(sf):
    i1 = put(sf, obs("o1", when=T1, evidence=[ev("e1")], conf=0.4))
    i2 = put(sf, obs("o2", when=T2, evidence=[ev("e1"), ev("e2")], conf=0.8))
    i3 = put(sf, obs("o3", when=T0, evidence=[ev("e2")], conf=0.6))
    apply(sf, i1, clock=lambda: C0)
    apply(sf, i2, clock=lambda: C1)
    apply(sf, i3, clock=lambda: C1)
    apply(sf, i2, clock=lambda: datetime(2026, 3, 1, tzinfo=UTC))
    r = row(sf)
    assert r.relationship_type == "has_vulnerability"
    assert r.provenance == "observed"
    assert r.source_entity_type == "asset"
    assert r.target_entity_type == "vulnerability"
    assert r.confidence == 0.8
    assert r.first_seen.replace(tzinfo=UTC) == T0
    assert r.last_seen.replace(tzinfo=UTC) == T2
    assert r.first_ingest_sequence == i1.ingest_sequence
    assert r.last_ingest_sequence == i3.ingest_sequence
    assert r.updated_at.replace(tzinfo=UTC) == C1
    with sf() as s:
        assert s.scalar(select(func.count()).select_from(RelationshipObservationRecord)) == 3
        assert s.scalar(select(func.count()).select_from(RelationshipEvidenceRecord)) == 2
    repo = RelationshipRepository(sf)
    got = repo.get_relationship("1", "tenant-a", r.relationship_id)
    assert got.relationship_type is RelationshipType.HAS_VULNERABILITY
    page = repo.list_relationships(
        "1",
        "tenant-a",
        relationship_type=RelationshipType.HAS_VULNERABILITY,
        source_entity_id="asset-1",
        target_entity_id="vuln-1",
        limit=1,
    )
    assert len(page.items) == 1
    lin = repo.get_lineage("1", "tenant-a", r.relationship_id)
    assert [o.observation_id for o in lin.observations] == ["o1", "o2", "o3"]
    assert [e.evidence_id for e in lin.evidence] == ["e1", "e2"]
    apply(sf, put(sf, obs("tenant", tenant="tenant-b")))
    apply(sf, i1, version="2")
    assert repo.list_relationships("1", "tenant-b").items
    assert repo.get_relationship("2", "tenant-a", r.relationship_id) is not None


def test_validation_and_conflict(sf):
    with pytest.raises(ProjectionHandlerError) as e:
        apply(sf, SequencedObservation(1, obs("naive", when=datetime(2026, 1, 1))))
    assert e.value.code == "relationship_event_time_timezone_required"
    bad = obs(
        "bad",
        subject=ref("vuln-2", EntityType.VULNERABILITY),
        object=ref("asset-2", EntityType.ASSET),
    )
    with pytest.raises(ProjectionHandlerError) as e:
        apply(sf, SequencedObservation(2, bad))
    assert e.value.code == "relationship_ontology_validation_failed"
    assert "test://" not in e.value.safe_message
    ok = put(sf, obs("ok"))
    apply(sf, ok)
    r = row(sf)
    with sf() as s, s.begin():
        s.get(
            RelationshipRecord,
            {
                "projection_version": "1",
                "tenant_id": "tenant-a",
                "relationship_id": r.relationship_id,
            },
        ).target_entity_type = "asset"
    with pytest.raises(ProjectionInvariantError):
        apply(sf, ok)


def test_runner_independent_versions_and_no_entity_dependency(sf):
    ProjectionRepository(sf).register_projector(relationship_projector_identity("1"))
    ProjectionRepository(sf).register_projector(relationship_projector_identity("2"))
    put(sf, obs("run"))
    h = RelationshipProjectionHandler()
    out = ProjectionRunner(sf).run_batch(h.projector_identity, h, limit=10)
    assert str(out.outcome) == "caught_up" and out.ending_sequence == 1
    out2 = ProjectionRunner(sf).run_batch(
        relationship_projector_identity("2"),
        RelationshipProjectionHandler(projection_version="2"),
        limit=10,
    )
    assert out2.ending_sequence == 1
    with sf() as s:
        assert s.scalar(select(func.count()).select_from(RelationshipRecord)) == 2


def test_repository_limit_validation_and_missing(sf):
    repo = RelationshipRepository(sf)
    assert repo.get_relationship("1", "tenant-a", "missing") is None
    with pytest.raises(ValueError):
        repo.list_relationships("1", "tenant-a", limit=0)
    with pytest.raises(ValueError):
        repo.get_lineage("1", "tenant-a", "x", evidence_limit=501)
