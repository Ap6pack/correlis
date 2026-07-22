from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest
from correlis_ontology import CORE_ONTOLOGY, OntologyRegistry, OntologyValidationError
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
    ObservationRepository,
    ProjectionHandlerError,
    ProjectionInvariantError,
    ProjectionRepository,
    ProjectionRunner,
    canonical_entity_key,
)
from correlis_store.models import (
    Base,
    EntityEvidenceRecord,
    EntityIdentityClaimRecord,
    EntityObservationRecord,
    EntityRecord,
    ProjectorFailureRecord,
)
from correlis_store.observation_sequence import SequencedObservation
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

T0 = datetime(2026, 1, 1, 12, tzinfo=UTC)
T1 = datetime(2026, 1, 2, 12, tzinfo=UTC)
T2 = datetime(2026, 1, 3, 12, tzinfo=UTC)
C0 = datetime(2026, 2, 1, tzinfo=UTC)
C1 = datetime(2026, 2, 2, tzinfo=UTC)


@pytest.fixture
def session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'entities.sqlite'}", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False, future=True)


def ev(id: str = "ev-1") -> EvidenceRef:
    return EvidenceRef(
        id=id,
        type=EvidenceType.RAW_EVENT,
        source="sensor",
        locator=f"test://{id}",
        sha256="a" * 64,
        collected_at=T0,
        metadata={"secret": "not copied"},
    )


def ref(
    id: str = "asset-1",
    type: EntityType = EntityType.ASSET,
    label: str = "asset one",
    attrs: dict | None = None,
) -> EntityRef:
    return EntityRef(
        id=id,
        type=type,
        label=label,
        attributes={"asset_id": id} if attrs is None else attrs,
    )


def obs(
    id: str,
    *,
    tenant: str = "tenant-a",
    when: datetime = T0,
    subject: EntityRef | None = None,
    object: EntityRef | None = None,
    evidence: list[EvidenceRef] | None = None,
    source: str = "sensor-a",
) -> Observation:
    return Observation(
        id=id,
        tenant_id=tenant,
        event_time=when,
        ingest_time=when + timedelta(minutes=1),
        source=source,
        sensor_id=f"{source}-sensor",
        event_class=EventClass.AUTHENTICATION,
        activity="login",
        subject=subject or ref(),
        object=object,
        evidence=evidence or [ev()],
    )


def put(sf, observation: Observation) -> SequencedObservation:
    result = ObservationRepository(sf).put_with_result(observation)
    return SequencedObservation(result.ingest_sequence, observation)


def apply(
    sf, item: SequencedObservation, *, version: str = "1", clock=lambda: C0, registry=CORE_ONTOLOGY
):
    with sf() as session, session.begin():
        EntityProjectionHandler(
            projection_version=version, clock=clock, ontology_registry=registry
        )(session, item)


def aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def entity_count(sf) -> int:
    with sf() as session:
        return session.scalar(select(func.count()).select_from(EntityRecord))


def get_entity_row(sf, entity_id="asset-1", tenant="tenant-a", version="1"):
    with sf() as session:
        return session.get(
            EntityRecord,
            {"projection_version": version, "tenant_id": tenant, "entity_id": entity_id},
        )


def test_entity_creation_update_version_tenant_source_and_idempotency(session_factory):
    item1 = put(session_factory, obs("obs-1", when=T1, source="sensor-a"))
    item2 = put(
        session_factory,
        obs(
            "obs-2",
            when=T2,
            subject=ref(label="asset newest", attrs={"asset_id": "asset-1", "new": "yes"}),
            object=ref("app-1", EntityType.APPLICATION, "app", {"application_id": "app-1"}),
            evidence=[ev("ev-1"), ev("ev-2")],
            source="sensor-b",
        ),
    )
    item3 = put(
        session_factory,
        obs("obs-3", when=T0, subject=ref(label="asset old", attrs={"asset_id": "asset-1"})),
    )
    apply(session_factory, item1, clock=lambda: C0)
    apply(session_factory, item2, clock=lambda: C1)
    apply(session_factory, item3, clock=lambda: C1)
    apply(session_factory, item2, clock=lambda: datetime(2026, 2, 3, tzinfo=UTC))

    row = get_entity_row(session_factory)
    assert row.entity_type == "asset"
    assert row.canonical_key == canonical_entity_key(EntityType.ASSET, "asset-1")
    assert row.label == "asset newest"
    assert row.attributes_json == {"asset_id": "asset-1", "new": "yes"}
    assert row.ontology_name == CORE_ONTOLOGY.name
    assert row.ontology_version == CORE_ONTOLOGY.version
    assert aware(row.first_seen) == T0
    assert aware(row.last_seen) == T2
    assert row.first_ingest_sequence == item1.ingest_sequence
    assert row.last_ingest_sequence == item3.ingest_sequence
    assert aware(row.created_at) == C0
    assert aware(row.updated_at) == C1
    assert entity_count(session_factory) == 2

    tenant_item = put(session_factory, obs("obs-tenant", tenant="tenant-b"))
    apply(session_factory, tenant_item)
    apply(session_factory, item1, version="2")
    assert get_entity_row(session_factory, tenant="tenant-b") is not None
    assert get_entity_row(session_factory, version="2") is not None
    assert entity_count(session_factory) == 4

    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(EntityObservationRecord)) == 6
        assert session.scalar(select(func.count()).select_from(EntityEvidenceRecord)) == 6
        assert session.scalar(select(func.count()).select_from(EntityIdentityClaimRecord)) >= 4


def test_latest_claim_tie_breaker_and_complete_attribute_replacement(session_factory):
    i1 = put(
        session_factory,
        obs("tie-1", when=T1, subject=ref(attrs={"asset_id": "asset-1", "old": "x"})),
    )
    i2 = put(
        session_factory,
        obs("tie-2", when=T1, subject=ref(label="tie wins", attrs={"asset_id": "asset-1"})),
    )
    apply(session_factory, i1, clock=lambda: C0)
    apply(session_factory, i2, clock=lambda: C1)
    row = get_entity_row(session_factory)
    assert row.label == "tie wins"
    assert row.attributes_json == {"asset_id": "asset-1"}
    assert "old" not in row.attributes_json
    assert row.latest_claim_ingest_sequence == i2.ingest_sequence


def test_subject_object_equivalent_roles_and_evidence_once(session_factory):
    item = put(session_factory, obs("self", subject=ref(), object=ref(), evidence=[ev("ev-3")]))
    apply(session_factory, item)
    with session_factory() as session:
        roles = session.scalars(
            select(EntityObservationRecord.role).order_by(EntityObservationRecord.role)
        ).all()
        assert roles == ["object", "subject"]
        assert session.scalar(select(func.count()).select_from(EntityEvidenceRecord)) == 1


def assert_handler_error(sf, item, code: str, *, registry=CORE_ONTOLOGY):
    with sf() as session, session.begin():
        before = session.scalar(select(func.count()).select_from(EntityObservationRecord)) or 0
        with pytest.raises(ProjectionHandlerError) as exc, session.begin_nested():
            EntityProjectionHandler(clock=lambda: C0, ontology_registry=registry)(session, item)
        assert exc.value.code == code
        assert session.scalar(select(func.count()).select_from(EntityObservationRecord)) == before


def test_invalid_projection_version_and_conflicts_roll_back_item(session_factory):
    ok = put(session_factory, obs("ok"))
    apply(session_factory, ok)
    original = get_entity_row(session_factory).label
    conflict = put(
        session_factory,
        obs(
            "type-conflict", subject=ref(type=EntityType.APPLICATION, attrs={"application_id": "a"})
        ),
    )
    assert_handler_error(session_factory, conflict, "entity_type_conflict")
    assert get_entity_row(session_factory).label == original

    label_conflict = put(
        session_factory, obs("label-conflict", subject=ref(), object=ref(label="other"))
    )
    assert_handler_error(session_factory, label_conflict, "entity_reference_conflict")
    attr_conflict = put(
        session_factory,
        obs("attr-conflict", subject=ref(), object=ref(attrs={"asset_id": "asset-1", "x": 1})),
    )
    assert_handler_error(session_factory, attr_conflict, "entity_reference_conflict")
    type_same_id = put(
        session_factory,
        obs(
            "same-id-type",
            subject=ref(),
            object=ref(type=EntityType.APPLICATION, attrs={"application_id": "asset-1"}),
        ),
    )
    assert_handler_error(session_factory, type_same_id, "entity_reference_conflict")

    naive = SequencedObservation(999, obs("naive", when=datetime(2026, 1, 1, 12)))
    assert_handler_error(session_factory, naive, "entity_event_time_timezone_required")

    with pytest.raises(ValueError, match="surrounding whitespace|invalid|projector_version"):
        EntityProjectionHandler(projection_version=" 1")
    with pytest.raises(ValueError, match="invalid projection version|projector_version"):
        EntityProjectionHandler(projection_version="")
    with pytest.raises(ValueError):
        EntityProjectionHandler(projection_version="x" * 65)
    with pytest.raises(ValueError):
        EntityProjectionHandler(projection_version="bad/version")


def test_ontology_mismatch_and_validation_errors(session_factory):
    item = put(session_factory, obs("ontology"))
    apply(session_factory, item)
    other_registry = OntologyRegistry(
        name="correlis-core",
        version="different",
        entity_types=CORE_ONTOLOGY.manifest().entity_types,
        relationship_types=CORE_ONTOLOGY.manifest().relationship_types,
        action_types=CORE_ONTOLOGY.manifest().action_types,
    )
    assert_handler_error(
        session_factory, item, "entity_projection_ontology_mismatch", registry=other_registry
    )

    class InvalidRegistry:
        name = CORE_ONTOLOGY.name
        version = CORE_ONTOLOGY.version

        def validate_entity(self, entity):
            raise OntologyValidationError("bad", "raw secret asset-1")

        def get_entity_definition(self, entity_type):
            return CORE_ONTOLOGY.get_entity_definition(entity_type)

    bad_item = put(session_factory, obs("bad-ontology", subject=ref("asset-2")))
    with session_factory() as session, session.begin():
        with pytest.raises(ProjectionHandlerError) as exc, session.begin_nested():
            EntityProjectionHandler(clock=lambda: C0, ontology_registry=InvalidRegistry())(
                session, bad_item
            )
        assert exc.value.code == "entity_ontology_validation_failed"
        assert (
            exc.value.safe_message
            == "Entity reference is incompatible with the configured ontology."
        )


def test_clock_invariant_propagates_without_poison_failure(session_factory):
    put(session_factory, obs("clock"))
    ProjectionRepository(session_factory).register_projector(
        EntityProjectionHandler().projector_identity
    )
    handler = EntityProjectionHandler(clock=lambda: datetime(2026, 1, 1))
    with pytest.raises(ProjectionInvariantError):
        ProjectionRunner(session_factory).run_batch(handler.projector_identity, handler)
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ProjectorFailureRecord)) == 0


def claim_rows(sf, *, entity_id="asset-1", version="1", tenant="tenant-a"):
    with sf() as session:
        return session.scalars(
            select(EntityIdentityClaimRecord).where(
                EntityIdentityClaimRecord.projection_version == version,
                EntityIdentityClaimRecord.tenant_id == tenant,
                EntityIdentityClaimRecord.entity_id == entity_id,
            )
        ).all()


@pytest.mark.parametrize(
    ("entity_type", "entity_id", "attrs", "expected_names"),
    [
        (
            EntityType.ASSET,
            "asset-claims",
            {
                "asset_id": "A",
                "cloud_provider": "aws",
                "cloud_account_id": "acct",
                "instance_id": "i-1",
            },
            {"asset_id", "cloud_instance"},
        ),
        (
            EntityType.APPLICATION,
            "app-claims",
            {"scheme": "https", "host": "Example.COM", "port": 443},
            {"service_endpoint"},
        ),
        (
            EntityType.PROCESS,
            "proc-claims",
            {"host_id": "h", "process_id": 123, "start_time": " 2026-01-01 "},
            {"host_process_start"},
        ),
        (
            EntityType.NETWORK_ENDPOINT,
            "sock-claims",
            {"address": "192.0.2.1", "port": 443, "transport": "TCP"},
            {"socket"},
        ),
        (
            EntityType.CLOUD_RESOURCE,
            "cloud-claims",
            {"provider": "aws", "account_id": "acct", "resource_id": "r"},
            {"cloud_resource"},
        ),
        (EntityType.FILE, "file-claims", {"host_id": "h", "path": "/tmp/X"}, {"host_path"}),
        (
            EntityType.CERTIFICATE,
            "cert-claims",
            {"issuer": "CA", "serial_number": "01"},
            {"issuer_serial"},
        ),
        (EntityType.DATA_STORE, "store-claims", {"host_id": "h", "name": "db"}, {"host_name"}),
    ],
)
def test_identity_claim_representative_ontology_keys(
    session_factory, entity_type, entity_id, attrs, expected_names
):
    item = put(
        session_factory,
        obs(
            f"obs-{entity_id}",
            subject=ref(entity_id, entity_type, entity_id, attrs),
            evidence=[ev(f"ev-{entity_id}")],
        ),
    )
    apply(session_factory, item)
    rows = claim_rows(session_factory, entity_id=entity_id)
    assert expected_names <= {r.identity_key_name for r in rows}
    for row in rows:
        for value in row.value_json.values():
            assert value in attrs.values()
        assert len(row.value_sha256) == 64


def test_identity_claim_scalar_rules_idempotency_isolation_and_no_merging(session_factory):
    good = put(
        session_factory,
        obs(
            "claim-good",
            subject=ref(
                "asset-good",
                attrs={
                    "asset_id": " CasePreserved ",
                    "hostname": "HostA",
                    "cloud_provider": "aws",
                    "cloud_account_id": 123,
                    "instance_id": True,
                },
            ),
        ),
    )
    apply(session_factory, good)
    apply(session_factory, good)
    rows = claim_rows(session_factory, entity_id="asset-good")
    names = {r.identity_key_name for r in rows}
    assert {"asset_id", "hostname", "cloud_instance"} <= names
    asset_claim = next(r for r in rows if r.identity_key_name == "asset_id")
    assert asset_claim.value_json == {"asset_id": " CasePreserved "}
    cloud = next(r for r in rows if r.identity_key_name == "cloud_instance")
    assert isinstance(cloud.value_json["cloud_account_id"], int)
    assert isinstance(cloud.value_json["instance_id"], bool)
    assert len({r.value_sha256 for r in rows}) == len(rows)

    for suffix, value in [
        ("missing", {}),
        ("null", {"asset_id": None}),
        ("blank", {"asset_id": "   "}),
        ("list", {"asset_id": ["x"]}),
        ("object", {"asset_id": {"x": 1}}),
        ("nan", {"asset_id": math.nan}),
        ("inf", {"asset_id": math.inf}),
        ("ninf", {"asset_id": -math.inf}),
    ]:
        item = put(
            session_factory, obs(f"claim-{suffix}", subject=ref(f"asset-{suffix}", attrs=value))
        )
        apply(session_factory, item)
        assert claim_rows(session_factory, entity_id=f"asset-{suffix}") == []

    later = put(
        session_factory,
        obs("claim-later", when=T2, subject=ref("asset-good", attrs={"asset_id": "Changed"})),
    )
    apply(session_factory, later)
    rows = claim_rows(session_factory, entity_id="asset-good")
    assert len([r for r in rows if r.identity_key_name == "asset_id"]) == 2

    same_claim_other = put(
        session_factory,
        obs("claim-other", subject=ref("asset-other", attrs={"asset_id": "Changed"})),
    )
    apply(session_factory, same_claim_other)
    apply(session_factory, same_claim_other, version="2")
    tenant_claim = put(
        session_factory,
        obs(
            "claim-tenant",
            tenant="tenant-b",
            subject=ref("asset-good", attrs={"asset_id": "Changed"}),
        ),
    )
    apply(session_factory, tenant_claim)
    assert get_entity_row(session_factory, entity_id="asset-good") is not None
    assert get_entity_row(session_factory, entity_id="asset-other") is not None
    assert get_entity_row(session_factory, entity_id="asset-other", version="2") is not None
    assert get_entity_row(session_factory, entity_id="asset-good", tenant="tenant-b") is not None


# invalid projection version and surrounding whitespace are tested above.
