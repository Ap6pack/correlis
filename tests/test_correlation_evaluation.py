from __future__ import annotations

import os
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from alembic.config import Config
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
    BUILTIN_CORRELATION_RULE_CATALOG,
    BUILTIN_CORRELATION_RULES,
    COR_SEQ_002,
    CorrelationGraphInvariantError,
    CorrelationGraphReader,
    ObservationRepository,
    RelationshipProjectionHandler,
    evaluate_cor_seq_001,
    evaluate_cor_seq_002,
)
from correlis_store.models import (
    Base,
    RelationshipObservationRecord,
    RelationshipRecord,
)
from correlis_store.observation_sequence import SequencedObservation
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

T0 = datetime(2026, 1, 1, tzinfo=UTC)
C0 = datetime(2026, 2, 1, tzinfo=UTC)
POSTGRES_URL = os.environ.get("CORRELIS_TEST_DATABASE_URL")


@pytest.fixture(scope="session")
def postgres_url() -> str:
    if not POSTGRES_URL:
        pytest.skip(
            "CORRELIS_TEST_DATABASE_URL is required for PostgreSQL integration tests"
        )
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


def reset_postgres_store(connection) -> None:
    connection.execute(text("""
            TRUNCATE TABLE
                relationship_derivation_evidence,
                relationship_derivation_supports,
                relationship_derivations,
                relationship_evidence,
                relationship_observations,
                relationships,
                entity_identity_claims,
                entity_evidence,
                entity_observations,
                entities,
                projector_failures,
                correlation_projection_configs,
                projector_checkpoints,
                observation_ingest_entries,
                observation_evidence,
                observations,
                evidence_refs
            """))
    result = connection.execute(text("""
            UPDATE observation_ingest_sequence_state
            SET last_sequence = 0
            WHERE singleton_id = 1
            """))
    if result.rowcount != 1:
        raise AssertionError(
            "observation ingest sequence singleton is missing or duplicated"
        )


@pytest.fixture
def session_factory(migrated_engine):
    with migrated_engine.begin() as connection:
        reset_postgres_store(connection)
    return sessionmaker(
        bind=migrated_engine, class_=Session, expire_on_commit=False, future=True
    )


@pytest.fixture
def sf(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'eval.sqlite'}", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(
        bind=engine, class_=Session, expire_on_commit=False, future=True
    )


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
        else (
            {"vulnerability_id": id}
            if type == EntityType.VULNERABILITY
            else {"address": id}
        )
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
        RelationshipProjectionHandler(projection_version=version, clock=lambda: C0)(
            s, item
        )


def vuln(
    sf,
    id,
    *,
    tenant="tenant-a",
    asset="asset-1",
    version="1",
    evidence=None,
    deterministic=False,
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
            tenant,
            asset,
            RelationshipType.HAS_VULNERABILITY,
            "vuln-1",
            ProvenanceClass.OBSERVED,
        )
        with sf() as s, s.begin():
            r = s.get(
                RelationshipRecord,
                {
                    "projection_version": version,
                    "tenant_id": tenant,
                    "relationship_id": rid,
                },
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
        999,
        trigger_observation(evidence=[ev("te2"), ev("shared"), ev("te2"), ev("te1")]),
    )
    cand = evaluate(sf, item)
    rule = [
        r for r in BUILTIN_CORRELATION_RULES.definitions() if r.rule_id == "COR-SEQ-001"
    ][0]
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
    assert (cand.source_entity_id, cand.source_entity_type) == (
        "1.2.3.4",
        EntityType.IP_ADDRESS,
    )
    assert (cand.target_entity_id, cand.target_entity_type) == (
        "asset-1",
        EntityType.ASSET,
    )
    assert cand.supporting_relationship_ids == tuple(
        sorted(cand.supporting_relationship_ids)
    )
    assert cand.trigger_evidence_ids == ("shared", "te1", "te2")
    assert cand.supporting_evidence_ids == ("se1", "se2", "shared")
    assert (
        "shared" in cand.trigger_evidence_ids
        and "shared" in cand.supporting_evidence_ids
    )
    with pytest.raises(FrozenInstanceError):
        cand.confidence = 0.1
    assert all(
        "secret://" not in x
        for x in (*cand.trigger_evidence_ids, *cand.supporting_evidence_ids)
    )
    assert (
        a.ingest_sequence < item.ingest_sequence
        and b.ingest_sequence < item.ingest_sequence
    )


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
    project(
        sf, SequencedObservation(same_trigger.ingest_sequence, same_vuln.observation)
    )
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
    assert (
        evaluate(sf, trigger(sf, "badtype", subject_type=EntityType.VULNERABILITY))
        is None
    )
    assert evaluate(sf, trigger(sf, "tenant-check", object_id="asset-tenant")) is None
    assert evaluate(sf, trigger(sf, "version-check", object_id="asset-version")) is None
    assert evaluate(sf, trigger(sf, "other-target", object_id="asset-2")) is None
    assert evaluate(sf, trigger(sf, "det-target", object_id="asset-3")) is None


def test_future_aggregate_updates_do_not_alter_historical_results_and_reader_dedupes(
    sf,
):
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


def exploit_obs(
    id, *, tenant="tenant-a", attacker="1.2.3.4", target="asset-1", evidence=None
):
    return obs(
        id,
        tenant=tenant,
        activity="exploit_attempt",
        subject=ent(attacker, EntityType.IP_ADDRESS),
        object=ent(target, EntityType.ASSET),
        relationship=RelationshipType.EXPLOITED,
        evidence=evidence or [ev(id + "-e")],
    )


def proc_obs(
    id="proc",
    *,
    tenant="tenant-a",
    target="asset-1",
    attack_source="1.2.3.4",
    suspicious=True,
    target_type=EntityType.ASSET,
    evidence=None,
):
    attrs = {} if suspicious is None else {"suspicious_child": suspicious}
    keys = {} if attack_source is None else {"attack_source": attack_source}
    return Observation(
        id=id,
        tenant_id=tenant,
        event_time=T0,
        ingest_time=T0 + timedelta(minutes=1),
        source="sensor",
        sensor_id="s1",
        event_class=EventClass.PROCESS_ACTIVITY,
        activity="process_start",
        confidence=0.6,
        subject=ent("proc-1", EntityType.PROCESS),
        object=ent(target, target_type),
        relationship=None,
        evidence=evidence or [ev("te2"), ev("shared"), ev("te1")],
        correlation_keys=keys,
        attributes=attrs,
    )


def add_exploit(sf, id, **kwargs):
    item = put(sf, exploit_obs(id, **kwargs))
    project(sf, item)
    return item


def eval_002(sf, item, version="1"):
    with sf() as s:
        return evaluate_cor_seq_002(
            CorrelationGraphReader(s), item, relationship_projection_version=version
        )


def add_manual_exploit(
    sf,
    id,
    *,
    attacker="1.2.3.4",
    target="asset-1",
    evidence=None,
    source_type=EntityType.IP_ADDRESS,
    target_type=EntityType.ASSET,
    provenance=ProvenanceClass.DETERMINISTIC,
):
    item = put(sf, exploit_obs(id, attacker=attacker, target=target, evidence=evidence))
    rid = relationship_id(
        "tenant-a", attacker, RelationshipType.EXPLOITED, target, provenance, id
    )
    with sf() as s, s.begin():
        s.add(
            RelationshipRecord(
                projection_version="1",
                tenant_id="tenant-a",
                relationship_id=rid,
                relationship_type=RelationshipType.EXPLOITED.value,
                provenance=provenance.value,
                rule_id=id if provenance == ProvenanceClass.DETERMINISTIC else None,
                rule_version=(
                    "1" if provenance == ProvenanceClass.DETERMINISTIC else None
                ),
                source_entity_id=attacker,
                source_entity_type=source_type.value,
                target_entity_id=target,
                target_entity_type=target_type.value,
                confidence=0.8,
                ontology_name="core",
                ontology_version="1",
                first_seen=T0,
                last_seen=T0,
                first_ingest_sequence=item.ingest_sequence,
                last_ingest_sequence=item.ingest_sequence,
                created_at=C0,
                updated_at=C0,
            )
        )
        s.add(
            RelationshipObservationRecord(
                projection_version="1",
                tenant_id="tenant-a",
                relationship_id=rid,
                observation_id=item.observation.id,
                ingest_sequence=item.ingest_sequence,
                event_time=item.observation.event_time,
                created_at=C0,
            )
        )
    return item, rid


def mutate_exploit(sf, rid, *, source_type=None, target_type=None, provenance=None):
    with sf() as s, s.begin():
        r = s.get(
            RelationshipRecord,
            {
                "projection_version": "1",
                "tenant_id": "tenant-a",
                "relationship_id": rid,
            },
        )
        if source_type is not None:
            r.source_entity_type = source_type.value
        if target_type is not None:
            r.target_entity_type = target_type.value
        if provenance is not None:
            r.provenance = provenance.value
            if provenance == ProvenanceClass.DETERMINISTIC:
                r.rule_id = "support-rule"
                r.rule_version = "1"


def test_cor_seq_002_prior_support_candidate_identity_evidence_and_immutability(sf):
    first = add_exploit(sf, "x2", evidence=[ev("se2"), ev("shared")])
    rid1 = relationship_id(
        "tenant-a",
        "1.2.3.4",
        RelationshipType.EXPLOITED,
        "asset-1",
        ProvenanceClass.OBSERVED,
    )
    mutate_exploit(sf, rid1, provenance=ProvenanceClass.DETERMINISTIC)
    second = add_exploit(sf, "x1", attacker="5.6.7.8", evidence=[ev("noise")])
    add_manual_exploit(
        sf, "x3", attacker="1.2.3.4", target="asset-1", evidence=[ev("se1"), ev("se2")]
    )
    item = SequencedObservation(
        999, proc_obs(evidence=[ev("te2"), ev("shared"), ev("te1"), ev("te2")])
    )
    cand = eval_002(sf, item)
    assert cand is not None
    assert (cand.rule_id, cand.rule_version) == ("COR-SEQ-002", "1")
    assert cand.reason_code == "suspicious_process_after_exploit"
    assert cand.relationship_type == RelationshipType.COMPROMISED
    assert cand.confidence == 0.92
    assert (cand.source_entity_id, cand.source_entity_type) == (
        "1.2.3.4",
        EntityType.IP_ADDRESS,
    )
    assert (cand.target_entity_id, cand.target_entity_type) == (
        "asset-1",
        EntityType.ASSET,
    )
    assert cand.supporting_relationship_ids == tuple(
        sorted(cand.supporting_relationship_ids)
    )
    assert len(cand.supporting_relationship_ids) == 2
    assert cand.trigger_evidence_ids == ("shared", "te1", "te2")
    assert cand.supporting_evidence_ids == ("se1", "se2", "shared")
    assert (
        "shared" in cand.trigger_evidence_ids
        and "shared" in cand.supporting_evidence_ids
    )
    with pytest.raises(FrozenInstanceError):
        cand.confidence = 0.1
    assert all(
        "secret://" not in x
        for x in (*cand.trigger_evidence_ids, *cand.supporting_evidence_ids)
    )
    assert (
        first.ingest_sequence < item.ingest_sequence
        and second.ingest_sequence < item.ingest_sequence
    )


@pytest.mark.parametrize("bad", [None, False, 1, "true", "yes", object()])
def test_cor_seq_002_suspicious_child_must_be_exact_boolean_true(sf, bad):
    add_exploit(sf, "xflag")
    assert eval_002(sf, SequencedObservation(999, proc_obs(suspicious=bad))) is None


@pytest.mark.parametrize("attack_source", [None, "", "   "])
def test_cor_seq_002_attack_source_required(sf, attack_source):
    add_exploit(sf, "xsrc")
    assert (
        eval_002(sf, SequencedObservation(999, proc_obs(attack_source=attack_source)))
        is None
    )


def test_cor_seq_002_historical_scope_and_endpoint_nonmatches(sf):
    same_trigger = put(
        sf, proc_obs("same", target="same-asset", evidence=[ev("same-e")])
    )
    same_exp = put(sf, exploit_obs("same-exp", target="same-asset"))
    project(
        sf, SequencedObservation(same_trigger.ingest_sequence, same_exp.observation)
    )
    future_trigger = put(
        sf, proc_obs("future", target="future-asset", evidence=[ev("future-e")])
    )
    add_exploit(sf, "future-exp", target="future-asset")
    add_exploit(sf, "tenant-exp", tenant="tenant-b", target="tenant-asset")
    project(
        sf, put(sf, exploit_obs("version-exp2", target="version-asset")), version="2"
    )
    add_exploit(sf, "wrong-source", attacker="9.9.9.9", target="wrong-source-asset")
    add_exploit(sf, "wrong-target", target="wrong-target-asset")
    assert (
        eval_002(sf, SequencedObservation(999, proc_obs("no-prior", target="nope")))
        is None
    )
    assert eval_002(sf, same_trigger) is None
    assert eval_002(sf, future_trigger) is None
    assert (
        eval_002(
            sf, SequencedObservation(999, proc_obs("tenant", target="tenant-asset"))
        )
        is None
    )
    assert (
        eval_002(
            sf, SequencedObservation(999, proc_obs("version", target="version-asset"))
        )
        is None
    )
    assert (
        eval_002(
            sf, SequencedObservation(999, proc_obs("wsrc", target="wrong-source-asset"))
        )
        is None
    )
    assert (
        eval_002(sf, SequencedObservation(999, proc_obs("wtgt", target="asset-2")))
        is None
    )


def test_cor_seq_002_invalid_output_types_return_none(sf):
    add_exploit(sf, "xtype", target="proc-asset")
    rid = relationship_id(
        "tenant-a",
        "1.2.3.4",
        RelationshipType.EXPLOITED,
        "proc-asset",
        ProvenanceClass.OBSERVED,
    )
    mutate_exploit(
        sf, rid, source_type=EntityType.VULNERABILITY, target_type=EntityType.PROCESS
    )
    assert (
        eval_002(
            sf,
            SequencedObservation(
                999, proc_obs(target="proc-asset", target_type=EntityType.PROCESS)
            ),
        )
        is None
    )


def test_cor_seq_002_conflicting_support_types_raise_invariant(sf):
    add_exploit(sf, "conflict-a")
    add_manual_exploit(sf, "conflict-b", attacker="1.2.3.4", target="asset-1")
    rids = []
    with sf() as s:
        facts = CorrelationGraphReader(s).find_prior_exploits(
            relationship_projection_version="1",
            tenant_id="tenant-a",
            attack_source_entity_id="1.2.3.4",
            target_entity_id="asset-1",
            before_ingest_sequence=999,
        )
        rids = [f.relationship_id for f in facts]
    mutate_exploit(sf, rids[0], source_type=EntityType.DOMAIN)
    with pytest.raises(CorrelationGraphInvariantError):
        eval_002(sf, SequencedObservation(999, proc_obs()))
    mutate_exploit(
        sf,
        rids[0],
        source_type=EntityType.IP_ADDRESS,
        target_type=EntityType.APPLICATION,
    )
    with pytest.raises(CorrelationGraphInvariantError):
        eval_002(sf, SequencedObservation(999, proc_obs()))


def test_cor_seq_002_future_evidence_excluded_and_reader_dedupes(sf):
    add_exploit(sf, "old-exp", evidence=[ev("old-e")])
    trig = put(sf, proc_obs("hist", evidence=[ev("hist-e")]))
    add_exploit(sf, "future-evidence", evidence=[ev("future-e")])
    cand = eval_002(sf, trig)
    assert cand is not None
    assert cand.supporting_evidence_ids == ("old-e",)
    with sf() as s:
        facts = CorrelationGraphReader(s).find_prior_exploits(
            relationship_projection_version="1",
            tenant_id="tenant-a",
            attack_source_entity_id="1.2.3.4",
            target_entity_id="asset-1",
            before_ingest_sequence=trig.ingest_sequence,
        )
        assert len(facts) == 1


def test_cor_seq_002_not_in_version_one_registry_catalog_or_execution(sf):
    assert [d.rule_id for d in BUILTIN_CORRELATION_RULES.definitions()] == [
        "COR-SEQ-001"
    ]
    assert (
        BUILTIN_CORRELATION_RULES.manifest_sha256()
        == "10268cfa7db0510e60fa14049a9d1227cab19cd164e044d643236e5a9d3f93e9"
    )
    assert BUILTIN_CORRELATION_RULE_CATALOG.get("correlis-sequence", "2") is None
    assert COR_SEQ_002.rule_id == "COR-SEQ-002"


@pytest.mark.postgres
def test_postgresql_cor_seq_002_strict_cutoff_support_scope_and_future_evidence(
    session_factory,
):
    sf = session_factory
    add_exploit(sf, "pg-old", evidence=[ev("pg-old-e")])
    rid = relationship_id(
        "tenant-a",
        "1.2.3.4",
        RelationshipType.EXPLOITED,
        "asset-1",
        ProvenanceClass.OBSERVED,
    )
    mutate_exploit(sf, rid, provenance=ProvenanceClass.DETERMINISTIC)
    add_exploit(sf, "pg-obs", attacker="5.6.7.8", evidence=[ev("pg-obs-e")])
    add_exploit(sf, "pg-other-tenant", tenant="tenant-b", evidence=[ev("pg-tenant-e")])
    trig = put(sf, proc_obs("pg-trigger", evidence=[ev("pg-trigger-e")]))
    # Insert future deterministic lineage directly so the test exercises the COR-SEQ-002
    # reader cutoff without re-projecting over the observed support mutated above.
    add_manual_exploit(sf, "pg-future", evidence=[ev("pg-future-e")])
    cand = eval_002(sf, trig)
    assert cand is not None
    assert cand.supporting_evidence_ids == ("pg-old-e",)
