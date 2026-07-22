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
    Severity,
)
from correlis_store import (
    ImmutableRecordConflict,
    ObservationPageAnchor,
    ObservationQueryFilters,
    ObservationRepository,
    WriteDisposition,
    entity_projector_identity,
)
from correlis_store.hashing import canonical_model_sha256
from correlis_store.models import Base, ObservationEvidenceRecord, ObservationRecord
from sqlalchemy import create_engine, func, inspect, select, text
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
        event_class=EventClass.AUTHENTICATION,
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


def test_alembic_revision_ids_fit_default_version_table():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config("alembic.ini"))
    revisions = list(script.walk_revisions())
    revision_ids = [revision.revision for revision in revisions]

    assert revision_ids
    assert all(revision_id for revision_id in revision_ids)
    assert len(set(revision_ids)) == len(revision_ids)
    assert all(len(revision_id) <= 32 for revision_id in revision_ids)
    assert script.get_current_head() == "0006_relationship_projection"


def test_alembic_upgrade_and_downgrade_create_expected_tables(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config

    db = tmp_path / "migration.sqlite"
    monkeypatch.setenv("CORRELIS_DATABASE_URL", f"sqlite:///{db}")
    config = Config("alembic.ini")
    command.upgrade(config, "0004_projection_state")
    engine = create_engine(f"sqlite:///{db}", future=True)
    sf = sessionmaker(bind=engine, class_=Session, expire_on_commit=False, future=True)
    obs = observation("obs-migration")
    ObservationRepository(sf).put(obs)
    from correlis_store import ProjectionRepository, ProjectorIdentity

    ProjectionRepository(sf).register_projector(ProjectorIdentity("other-projector", "1"))
    command.upgrade(config, "head")
    inspector = inspect(engine)
    entity_tables = {"entities", "entity_observations", "entity_evidence", "entity_identity_claims"}
    assert entity_tables.issubset(set(inspector.get_table_names()))
    with engine.begin() as connection:
        assert all(
            connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one() == 0
            for table in entity_tables
        )
        assert connection.execute(text("SELECT COUNT(*) FROM observations")).scalar_one() == 1
        assert connection.execute(text("SELECT COUNT(*) FROM evidence_refs")).scalar_one() == 1
        assert (
            connection.execute(
                text(
                    "SELECT last_sequence FROM observation_ingest_sequence_state "
                    "WHERE singleton_id = 1"
                )
            ).scalar_one()
            == 1
        )
        assert (
            connection.execute(
                text(
                    "SELECT COUNT(*) FROM projector_checkpoints "
                    "WHERE projector_name = 'entity-projection'"
                )
            ).scalar_one()
            == 0
        )
        connection.execute(
            text(
                "INSERT INTO projector_checkpoints "
                "(projector_name, projector_version, last_processed_sequence, status, "
                "last_failure_sequence, created_at, updated_at, last_processed_at) "
                "VALUES ('entity-projection', '1', 0, 'failed', 1, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, NULL)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO projector_failures "
                "(projector_name, projector_version, ingest_sequence, tenant_id, "
                "observation_id, status, attempt_count, error_code, error_type, "
                "safe_message, first_failed_at, last_failed_at, resolved_at) "
                "VALUES ('entity-projection', '1', 1, 'tenant-a', 'obs-migration', "
                "'active', 1, 'entity_type_conflict', 'ProjectionHandlerError', "
                "'safe', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, NULL)"
            )
        )
    command.downgrade(config, "0004_projection_state")
    names_after_downgrade = set(inspect(engine).get_table_names())
    assert entity_tables.isdisjoint(names_after_downgrade)
    with engine.begin() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT COUNT(*) FROM projector_checkpoints "
                    "WHERE projector_name = 'entity-projection'"
                )
            ).scalar_one()
            == 0
        )
        assert (
            connection.execute(
                text(
                    "SELECT COUNT(*) FROM projector_failures "
                    "WHERE projector_name = 'entity-projection'"
                )
            ).scalar_one()
            == 0
        )
        assert (
            connection.execute(
                text(
                    "SELECT COUNT(*) FROM projector_checkpoints "
                    "WHERE projector_name = 'other-projector'"
                )
            ).scalar_one()
            == 1
        )
        assert connection.execute(text("SELECT COUNT(*) FROM observations")).scalar_one() == 1
        assert connection.execute(text("SELECT COUNT(*) FROM evidence_refs")).scalar_one() == 1
    command.upgrade(config, "head")
    assert entity_projector_identity().version == "1"
    from correlis_store import ProjectionRepository

    assert (
        ProjectionRepository(sf)
        .register_projector(entity_projector_identity())
        .last_processed_sequence
        == 0
    )
    command.downgrade(config, "base")
    assert {"observations", "evidence_refs", "observation_evidence"}.isdisjoint(
        set(inspect(engine).get_table_names())
    )


def scoped_observation(
    id: str,
    tenant: str = "tenant-a",
    source: str = "sensor",
    when: datetime | None = None,
    sensor: str = "sensor-1",
    event_class: EventClass = EventClass.AUTHENTICATION,
    severity: Severity = Severity.LOW,
    ev: EvidenceRef | None = None,
) -> Observation:
    obs = observation(
        id=id, tenant=tenant, when=when, ev=ev or evidence(f"ev-{id}", (id[-1:] or "a") * 64)
    )
    return obs.model_copy(
        update={
            "source": source,
            "sensor_id": sensor,
            "event_class": event_class,
            "severity": severity,
        }
    )


def test_source_scoped_direct_lookup_and_unscoped_compatibility(session_factory):
    repo = ObservationRepository(session_factory)
    obs = scoped_observation("obs-1", source="source-a")
    repo.put(obs)
    assert repo.get_scoped("tenant-a", "source-a", "obs-1") == obs
    assert repo.get_scoped("tenant-b", "source-a", "obs-1") is None
    assert repo.get_scoped("tenant-a", "source-b", "obs-1") is None
    assert repo.get("tenant-a", "obs-1") == obs


def test_source_scoped_list_keyset_filters_and_limits(session_factory):
    repo = ObservationRepository(session_factory)
    t = datetime(2026, 1, 1, 12, tzinfo=UTC)
    for obs in [
        scoped_observation(
            "obs-a",
            when=t,
            source="source-a",
            sensor="s1",
            event_class=EventClass.AUTHENTICATION,
            severity=Severity.LOW,
        ),
        scoped_observation(
            "obs-b",
            when=t,
            source="source-a",
            sensor="s1",
            event_class=EventClass.AUTHENTICATION,
            severity=Severity.HIGH,
        ),
        scoped_observation(
            "obs-c",
            when=t + timedelta(hours=1),
            source="source-a",
            sensor="s2",
            event_class=EventClass.NETWORK_ACTIVITY,
            severity=Severity.HIGH,
        ),
        scoped_observation(
            "obs-d",
            when=t + timedelta(hours=2),
            source="source-b",
            sensor="s1",
            event_class=EventClass.AUTHENTICATION,
            severity=Severity.LOW,
        ),
    ]:
        repo.put(obs)
    page1 = repo.list_page("tenant-a", "source-a", limit=2)
    assert [o.id for o in page1.observations] == ["obs-c", "obs-b"]
    assert page1.has_more is True
    assert page1.next_anchor == ObservationPageAnchor(t, "obs-b")
    page2 = repo.list_page("tenant-a", "source-a", limit=2, anchor=page1.next_anchor)
    assert [o.id for o in page2.observations] == ["obs-a"]
    assert page2.has_more is False
    filt = ObservationQueryFilters(
        event_time_from=t,
        event_time_to=t,
        event_class=EventClass.AUTHENTICATION,
        severity=Severity.HIGH,
        sensor_id="s1",
    )
    assert [
        o.id for o in repo.list_page("tenant-a", "source-a", limit=10, filters=filt).observations
    ] == ["obs-b"]
    with pytest.raises(ValueError):
        repo.list_page("tenant-a", "source-a", limit=0)


def test_source_scoped_evidence_lookup_uses_visible_associations(session_factory):
    repo = ObservationRepository(session_factory)
    shared = evidence("ev-shared", "d" * 64)
    source_a = scoped_observation("obs-a", source="source-a", ev=shared)
    source_b = scoped_observation("obs-b", source="source-b", ev=shared)
    hidden = scoped_observation("obs-c", source="source-b", ev=evidence("ev-hidden", "e" * 64))
    other_tenant = scoped_observation(
        "obs-d", tenant="tenant-b", source="source-a", ev=evidence("ev-other", "f" * 64)
    )
    for obs in [source_a, source_b, hidden, other_tenant]:
        repo.put(obs)
    assert repo.get_evidence_scoped("tenant-a", "source-a", "ev-shared") == shared
    assert repo.get_evidence_scoped("tenant-a", "source-b", "ev-shared") == shared
    assert repo.get_evidence_scoped("tenant-a", "source-a", "ev-hidden") is None
    assert repo.get_evidence_scoped("tenant-a", "source-a", "ev-other") is None


def test_put_with_result_assigns_and_reuses_ingest_sequence(session_factory):
    repo = ObservationRepository(session_factory)
    first = observation(id="seq-1", ev=evidence("seq-ev-1"))
    second = observation(id="seq-2", ev=evidence("seq-ev-2", "2" * 64))

    first_result = repo.put_with_result(first)
    second_result = repo.put_with_result(second)

    assert first_result.disposition == WriteDisposition.CREATED
    assert first_result.ingest_sequence == 1
    assert second_result.ingest_sequence == 2
    retry = repo.put_with_result(first)
    assert retry.disposition == WriteDisposition.EXISTING
    assert retry.ingest_sequence == 1
    assert repo.get_ingest_sequence(first.tenant_id, first.id) == 1
    assert repo.get_ingest_sequence("tenant-b", first.id) is None


def test_sequence_page_is_ascending_and_cursor_safe(session_factory):
    repo = ObservationRepository(session_factory)
    newer_event = datetime(2026, 1, 3, tzinfo=UTC)
    older_event = datetime(2026, 1, 1, tzinfo=UTC)
    first = observation(id="page-1", when=newer_event, ev=evidence("page-ev-1"))
    second = observation(id="page-2", when=older_event, ev=evidence("page-ev-2", "3" * 64))
    repo.put(first)
    repo.put(second)

    page = repo.read_sequence_page(after_sequence=0, limit=1)
    assert page.high_watermark == 2
    assert page.has_more is True
    assert page.next_sequence == 1
    assert [item.observation.id for item in page.items] == ["page-1"]

    next_page = repo.read_sequence_page(after_sequence=page.next_sequence, limit=10)
    assert next_page.has_more is False
    assert next_page.next_sequence == 2
    assert [item.ingest_sequence for item in next_page.items] == [2]
    assert [item.observation.id for item in next_page.items] == ["page-2"]


def test_sequence_cursor_validation(session_factory):
    from correlis_store import ObservationSequenceCursorError

    repo = ObservationRepository(session_factory)
    with pytest.raises(ObservationSequenceCursorError):
        repo.read_sequence_page(after_sequence=-1)
    with pytest.raises(ObservationSequenceCursorError):
        repo.read_sequence_page(limit=0)
    with pytest.raises(ObservationSequenceCursorError):
        repo.read_sequence_page(limit=501)
