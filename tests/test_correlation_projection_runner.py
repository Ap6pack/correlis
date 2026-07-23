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
    CorrelationDependencyNotReady,
    CorrelationProjectionHandler,
    CorrelationProjectionNotConfigured,
    CorrelationRepository,
    ObservationRepository,
    ProjectionInvariantError,
    ProjectionRepository,
    ProjectionRunner,
    ProjectorStatus,
    RelationshipProjectionHandler,
    relationship_projector_identity,
    resolve_correlation_rule_registry,
)
from correlis_store.models import (
    Base,
    ProjectorCheckpointRecord,
    ProjectorFailureRecord,
    RelationshipDerivationEvidenceRecord,
    RelationshipDerivationRecord,
    RelationshipDerivationSupportRecord,
    RelationshipEvidenceRecord,
    RelationshipObservationRecord,
    RelationshipRecord,
)
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

T0 = datetime(2026, 1, 1, tzinfo=UTC)
T1 = datetime(2026, 1, 2, tzinfo=UTC)
T2 = datetime(2026, 1, 3, tzinfo=UTC)
C0 = datetime(2026, 2, 1, tzinfo=UTC)


@pytest.fixture
def sf(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'corr.sqlite'}", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False, future=True)


def ev(id):
    return EvidenceRef(
        id=id,
        type=EvidenceType.RAW_EVENT,
        source="sensor",
        locator=f"test://{id}",
        sha256="b" * 64,
        collected_at=T0,
    )


def ref(id, type):
    attrs = {"asset_id": id} if type == EntityType.ASSET else {"vulnerability_id": id}
    return EntityRef(id=id, type=type, label=id, attributes=attrs)


def vuln_obs(id="vuln", *, when=T0, evidence_id="ev-support"):
    return Observation(
        id=id,
        tenant_id="tenant-a",
        event_time=when,
        ingest_time=when + timedelta(minutes=1),
        source="sensor",
        sensor_id="s1",
        event_class=EventClass.EXPOSURE_FINDING,
        activity="finding",
        severity="medium",
        confidence=0.7,
        subject=ref("asset-1", EntityType.ASSET),
        object=ref("vuln-1", EntityType.VULNERABILITY),
        relationship=RelationshipType.HAS_VULNERABILITY,
        evidence=[ev(evidence_id)],
    )


def exploit_obs(id="exploit", *, when=T1, evidence_id="ev-trigger"):
    return Observation(
        id=id,
        tenant_id="tenant-a",
        event_time=when,
        ingest_time=when + timedelta(minutes=1),
        source="sensor",
        sensor_id="s2",
        event_class=EventClass.NETWORK_ACTIVITY,
        activity="exploit_attempt",
        severity="high",
        confidence=0.9,
        subject=ref("asset-1", EntityType.ASSET),
        object=ref("asset-1", EntityType.ASSET),
        evidence=[ev(evidence_id)],
    )


def register(sf):
    ProjectionRepository(sf).register_projector(relationship_projector_identity("1"))
    CorrelationRepository(sf, clock=lambda: C0).register_projection(
        projection_version="1", relationship_projection_version="1"
    )


def run_relationship(sf, limit=100):
    h = RelationshipProjectionHandler(projection_version="1", clock=lambda: C0)
    return ProjectionRunner(sf, clock=lambda: C0).run_batch(h.projector_identity, h, limit=limit)


def run_correlation(sf, version="1", rel_version="1", limit=100):
    h = CorrelationProjectionHandler(
        projection_version=version,
        relationship_projection_version=rel_version,
        rule_registry=resolve_correlation_rule_registry("correlis-sequence", "1"),
        clock=lambda: C0,
    )
    return ProjectionRunner(sf, clock=lambda: C0).run_batch(h.projector_identity, h, limit=limit)


def put(sf, obs):
    return ObservationRepository(sf).put_with_result(obs).ingest_sequence


def test_missing_config_and_dependency_lag_have_no_poison_or_partial_writes(sf):
    with sf() as s, s.begin():
        s.add(
            ProjectorCheckpointRecord(
                projector_name="correlation-projection",
                projector_version="1",
                last_processed_sequence=0,
                status=ProjectorStatus.IDLE,
                last_failure_sequence=None,
                created_at=C0,
                updated_at=C0,
                last_processed_at=None,
            )
        )
    put(sf, exploit_obs())
    with pytest.raises(CorrelationProjectionNotConfigured):
        run_correlation(sf)

    register(sf)
    with pytest.raises(CorrelationDependencyNotReady):
        run_correlation(sf)
    with sf() as s:
        assert s.scalar(select(func.count()).select_from(ProjectorFailureRecord)) == 0
        assert s.scalar(select(func.count()).select_from(RelationshipRecord)) == 0
        cp = s.get(
            ProjectorCheckpointRecord,
            {"projector_name": "correlation-projection", "projector_version": "1"},
        )
        assert cp.last_processed_sequence == 0


def test_nonmatching_observation_advances_checkpoint(sf):
    register(sf)
    put(sf, vuln_obs())
    run_relationship(sf)
    out = run_correlation(sf)
    assert out.ending_sequence == 1
    assert out.outcome == "caught_up"
    with sf() as s:
        assert (
            s.scalar(
                select(func.count())
                .select_from(RelationshipRecord)
                .where(RelationshipRecord.provenance == "deterministic")
            )
            == 0
        )


def test_matching_trigger_persists_deterministic_relationship_and_lineage_idempotently(sf):
    register(sf)
    put(sf, vuln_obs())
    trigger_seq = put(sf, exploit_obs())
    run_relationship(sf)
    out = run_correlation(sf)
    assert out.ending_sequence == trigger_seq
    rid = relationship_id(
        "tenant-a",
        "asset-1",
        RelationshipType.EXPLOITED,
        "asset-1",
        ProvenanceClass.DETERMINISTIC,
        "COR-SEQ-001",
    )
    run_correlation(sf)  # caught up/idempotent
    with sf() as s:
        rel = s.get(
            RelationshipRecord,
            {"projection_version": "1", "tenant_id": "tenant-a", "relationship_id": rid},
        )
        assert rel.relationship_type == "exploited"
        assert rel.provenance == "deterministic"
        assert rel.rule_id == "COR-SEQ-001"
        assert rel.rule_version == "1"
        assert rel.confidence == 0.85
        assert rel.first_seen.replace(tzinfo=UTC) == T1
        assert rel.last_seen.replace(tzinfo=UTC) == T1
        assert rel.first_ingest_sequence == trigger_seq
        assert rel.last_ingest_sequence == trigger_seq
        assert (
            s.scalar(
                select(func.count())
                .select_from(RelationshipObservationRecord)
                .where(RelationshipObservationRecord.relationship_id == rid)
            )
            == 1
        )
        assert (
            s.scalar(
                select(func.count())
                .select_from(RelationshipEvidenceRecord)
                .where(RelationshipEvidenceRecord.relationship_id == rid)
            )
            == 2
        )
        assert s.scalar(select(func.count()).select_from(RelationshipDerivationRecord)) == 1
        assert s.scalar(select(func.count()).select_from(RelationshipDerivationSupportRecord)) == 1
        roles = set(
            s.execute(
                select(
                    RelationshipDerivationEvidenceRecord.evidence_id,
                    RelationshipDerivationEvidenceRecord.evidence_role,
                )
            ).all()
        )
        assert roles == {("ev-trigger", "trigger"), ("ev-support", "support")}


def test_multiple_triggers_aggregate_and_corruption_raises(sf):
    register(sf)
    put(sf, vuln_obs())
    seq1 = put(sf, exploit_obs("exploit-1", when=T2, evidence_id="ev-t1"))
    seq2 = put(sf, exploit_obs("exploit-2", when=T1, evidence_id="ev-t2"))
    run_relationship(sf)
    run_correlation(sf)
    rid = relationship_id(
        "tenant-a",
        "asset-1",
        RelationshipType.EXPLOITED,
        "asset-1",
        ProvenanceClass.DETERMINISTIC,
        "COR-SEQ-001",
    )
    with sf() as s:
        rel = s.get(
            RelationshipRecord,
            {"projection_version": "1", "tenant_id": "tenant-a", "relationship_id": rid},
        )
        assert rel.first_seen.replace(tzinfo=UTC) == T1
        assert rel.last_seen.replace(tzinfo=UTC) == T2
        assert rel.first_ingest_sequence == seq1
        assert rel.last_ingest_sequence == seq2
        rel.rule_version = "bad"
        cp = s.get(
            ProjectorCheckpointRecord,
            {"projector_name": "correlation-projection", "projector_version": "1"},
        )
        cp.last_processed_sequence = 1
        s.commit()
    with pytest.raises(ProjectionInvariantError):
        run_correlation(sf)
