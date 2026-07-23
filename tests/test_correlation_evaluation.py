from __future__ import annotations

from dataclasses import FrozenInstanceError
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
    BUILTIN_CORRELATION_RULES,
    CorrelationGraphReader,
    ObservationRepository,
    RelationshipProjectionHandler,
    evaluate_cor_seq_001,
)
from correlis_store.models import Base, RelationshipRecord
from correlis_store.observation_sequence import SequencedObservation
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

T0 = datetime(2026, 1, 1, tzinfo=UTC)
C0 = datetime(2026, 2, 1, tzinfo=UTC)


@pytest.fixture
def sf(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'eval.sqlite'}", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False, future=True)


def ev(id):
    return EvidenceRef(
        id=id,
        type=EvidenceType.RAW_EVENT,
        source="sensor",
        locator=f"secret://{id}",
        sha256="a" * 64,
        collected_at=T0,
    )


def ent(id, type):
    attrs = (
        {"asset_id": id}
        if type == EntityType.ASSET
        else {"vulnerability_id": id}
        if type == EntityType.VULNERABILITY
        else {"address": id}
    )
    return EntityRef(id=id, type=type, label=id, attributes=attrs)


def obs(
    id,
    *,
    tenant="tenant-a",
    activity="finding",
    subject=None,
    object=None,
    relationship=RelationshipType.HAS_VULNERABILITY,
    evidence=None,
):
    return Observation(
        id=id,
        tenant_id=tenant,
        event_time=T0,
        ingest_time=T0 + timedelta(minutes=1),
        source="sensor",
        sensor_id="s1",
        event_class=EventClass.EXPOSURE_FINDING,
        activity=activity,
        confidence=0.6,
        subject=subject or ent("asset-1", EntityType.ASSET),
        object=object,
        relationship=relationship,
        evidence=evidence or [ev("e1")],
    )


def put(sf, o):
    r = ObservationRepository(sf).put_with_result(o)
    return SequencedObservation(r.ingest_sequence, o)


def project(sf, item, version="1"):
    with sf() as s, s.begin():
        RelationshipProjectionHandler(projection_version=version, clock=lambda: C0)(s, item)


def vuln(
    sf, id, *, tenant="tenant-a", asset="asset-1", version="1", evidence=None, deterministic=False
):
    item = put(
        sf,
        obs(
            id,
            tenant=tenant,
            subject=ent(asset, EntityType.ASSET),
            object=ent("vuln-1", EntityType.VULNERABILITY),
            evidence=evidence or [ev(id + "-e")],
        ),
    )
    project(sf, item, version=version)
    if deterministic:
        rid = relationship_id(
            tenant, asset, RelationshipType.HAS_VULNERABILITY, "vuln-1", ProvenanceClass.OBSERVED
        )
        with sf() as s, s.begin():
            r = s.get(
                RelationshipRecord,
                {"projection_version": version, "tenant_id": tenant, "relationship_id": rid},
            )
            r.provenance = ProvenanceClass.DETERMINISTIC.value
            r.rule_id = "x"
            r.rule_version = "1"
    return item


def trigger_observation(
    id="trig",
    *,
    activity="exploit_attempt",
    object_id="asset-1",
    subject_type=EntityType.IP_ADDRESS,
    evidence=None,
):
    return obs(
        id,
        activity=activity,
        subject=ent("1.2.3.4", subject_type),
        object=ent(object_id, EntityType.ASSET) if object_id else None,
        relationship=None,
        evidence=evidence or [ev("te1"), ev("te0")],
    )


def trigger(
    sf,
    id="trig",
    *,
    activity="exploit_attempt",
    object_id="asset-1",
    subject_type=EntityType.IP_ADDRESS,
    evidence=None,
):
    return put(
        sf,
        trigger_observation(
            id,
            activity=activity,
            object_id=object_id,
            subject_type=subject_type,
            evidence=evidence,
        ),
    )


def evaluate(sf, item):
    with sf() as s:
        return evaluate_cor_seq_001(
            CorrelationGraphReader(s), item, relationship_projection_version="1"
        )


def test_prior_observed_vulnerability_produces_complete_immutable_candidate(sf):
    a = vuln(sf, "v1", evidence=[ev("se2"), ev("shared")])
    b = vuln(sf, "v2", asset="asset-1", evidence=[ev("se1"), ev("se2")])
    item = SequencedObservation(
        999, trigger_observation(evidence=[ev("te2"), ev("shared"), ev("te2"), ev("te1")])
    )
    cand = evaluate(sf, item)
    rule = [r for r in BUILTIN_CORRELATION_RULES.definitions() if r.rule_id == "COR-SEQ-001"][0]
    assert cand is not None
    assert (
        cand.rule_id,
        cand.rule_version,
        cand.reason_code,
        cand.relationship_type,
        cand.confidence,
    ) == (
        rule.rule_id,
        rule.rule_version,
        rule.reason_code,
        rule.output_relationship_type,
        rule.confidence,
    )
    assert (cand.source_entity_id, cand.source_entity_type) == ("1.2.3.4", EntityType.IP_ADDRESS)
    assert (cand.target_entity_id, cand.target_entity_type) == ("asset-1", EntityType.ASSET)
    assert cand.supporting_relationship_ids == tuple(sorted(cand.supporting_relationship_ids))
    assert cand.trigger_evidence_ids == ("shared", "te1", "te2")
    assert cand.supporting_evidence_ids == ("se1", "se2", "shared")
    assert "shared" in cand.trigger_evidence_ids and "shared" in cand.supporting_evidence_ids
    with pytest.raises(FrozenInstanceError):
        cand.confidence = 0.1
    assert all(
        "secret://" not in x for x in (*cand.trigger_evidence_ids, *cand.supporting_evidence_ids)
    )
    assert a.ingest_sequence < item.ingest_sequence and b.ingest_sequence < item.ingest_sequence


def test_nonmatches_and_historical_exclusions(sf):
    same_trigger = trigger(sf, "same", object_id="asset-same")
    same_vuln = put(
        sf,
        obs(
            "same-v",
            subject=ent("asset-same", EntityType.ASSET),
            object=ent("vuln-1", EntityType.VULNERABILITY),
        ),
    )
    project(sf, SequencedObservation(same_trigger.ingest_sequence, same_vuln.observation))
    assert evaluate(sf, same_trigger) is None
    future_trigger = trigger(sf, "future", object_id="asset-future")
    vuln(sf, "future-v", asset="asset-future")
    assert evaluate(sf, future_trigger) is None
    vuln(sf, "tenant-v", tenant="tenant-b", asset="asset-tenant")
    vuln(sf, "version-v", version="2", asset="asset-version")
    vuln(sf, "other-v", asset="asset-other")
    vuln(sf, "det-v", asset="asset-3", deterministic=True)
    assert evaluate(sf, trigger(sf, "wrong", activity="login")) is None
    assert evaluate(sf, trigger(sf, "missing", object_id=None)) is None
    assert evaluate(sf, trigger(sf, "badtype", subject_type=EntityType.VULNERABILITY)) is None
    assert evaluate(sf, trigger(sf, "tenant-check", object_id="asset-tenant")) is None
    assert evaluate(sf, trigger(sf, "version-check", object_id="asset-version")) is None
    assert evaluate(sf, trigger(sf, "other-target", object_id="asset-2")) is None
    assert evaluate(sf, trigger(sf, "det-target", object_id="asset-3")) is None


def test_future_aggregate_updates_do_not_alter_historical_results_and_reader_dedupes(sf):
    vuln(sf, "old", evidence=[ev("old-e")])
    trig = trigger(sf, "historical")
    vuln(sf, "future-update", evidence=[ev("future-e")])
    cand = evaluate(sf, trig)
    assert cand is not None
    assert cand.supporting_evidence_ids == ("old-e",)
    with sf() as s:
        facts = CorrelationGraphReader(s).find_prior_observed_vulnerabilities(
            relationship_projection_version="1",
            tenant_id="tenant-a",
            vulnerable_entity_id="asset-1",
            before_ingest_sequence=trig.ingest_sequence,
        )
        assert len(facts) == 1
        assert (
            CorrelationGraphReader(s).evidence_for_prior_relationships(
                relationship_projection_version="1",
                tenant_id="tenant-a",
                relationship_ids=(),
                before_ingest_sequence=trig.ingest_sequence,
            )
            == ()
        )


@pytest.mark.postgres
def test_postgresql_same_sequence_and_future_lineage_exclusions(session_factory):
    sf = session_factory
    trig = trigger(sf, "pg-trigger")
    vuln(sf, "pg-future")
    assert evaluate(sf, trig) is None
