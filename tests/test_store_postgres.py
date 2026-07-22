from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta, timezone
from threading import Barrier, Event

import pytest
from alembic import command
from alembic.config import Config
from correlis_schema import EntityRef, EntityType, EvidenceRef, EvidenceType, Observation
from correlis_store import (
    EntityProjectionHandler,
    EntityRepository,
    ImmutableRecordConflict,
    ObservationRepository,
    ObservationSequenceAllocator,
    ObservationSequenceCursorError,
    ProjectionHandlerError,
    ProjectionInvariantError,
    ProjectionRepository,
    ProjectionRunner,
    ProjectionRunOutcome,
    ProjectorFailureStatus,
    ProjectorIdentity,
    WriteDisposition,
)
from correlis_store.models import (
    EntityRecord,
    EvidenceRefRecord,
    ObservationEvidenceRecord,
    ObservationIngestEntryRecord,
    ObservationIngestSequenceStateRecord,
    ObservationRecord,
    ProjectorFailureRecord,
)
from sqlalchemy import (
    BigInteger,
    Column,
    MetaData,
    String,
    Table,
    create_engine,
    func,
    inspect,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import BIGINT, JSONB, SMALLINT
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.postgres

POSTGRES_URL = os.environ.get("CORRELIS_TEST_DATABASE_URL")

projection_metadata = MetaData()
projection_effects = Table(
    "test_projection_effects",
    projection_metadata,
    Column("projector_name", String(128), primary_key=True),
    Column("projector_version", String(64), primary_key=True),
    Column("ingest_sequence", BigInteger, primary_key=True),
    Column("observation_id", String(128), nullable=False),
    Column("value", String(128), nullable=False),
)


def evidence(id: str = "ev-1", sha: str = "a" * 64, locator: str | None = None) -> EvidenceRef:
    return EvidenceRef(
        id=id,
        type=EvidenceType.RAW_EVENT,
        source="sensor",
        locator=locator or f"test://{id}",
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


@pytest.fixture(scope="session")
def postgres_url() -> str:
    if not POSTGRES_URL:
        pytest.skip("CORRELIS_TEST_DATABASE_URL is required for PostgreSQL integration tests")
    return POSTGRES_URL


@pytest.fixture(scope="session")
def migrated_engine(postgres_url: str):
    os.environ["CORRELIS_DATABASE_URL"] = postgres_url
    config = Config("alembic.ini")
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = create_engine(postgres_url, future=True)
    with engine.begin() as connection:
        projection_effects.create(connection, checkfirst=True)
    try:
        yield engine
    finally:
        with engine.begin() as connection:
            projection_effects.drop(connection, checkfirst=True)
        engine.dispose()
        command.downgrade(config, "base")


def reset_observation_store(connection) -> None:
    connection.execute(
        text(
            """
            TRUNCATE TABLE
                relationship_evidence,
                relationship_observations,
                relationships,
                entity_identity_claims,
                entity_evidence,
                entity_observations,
                entities,
                test_projection_effects,
                projector_failures,
                projector_checkpoints,
                observation_ingest_entries,
                observation_evidence,
                observations,
                evidence_refs
            """
        )
    )
    result = connection.execute(
        text(
            """
            UPDATE observation_ingest_sequence_state
            SET last_sequence = 0
            WHERE singleton_id = 1
            """
        )
    )
    if result.rowcount != 1:
        raise AssertionError("observation ingest sequence singleton is missing or duplicated")
    counts_by_table = {
        table: connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
        for table in (
            "relationship_evidence",
            "relationship_observations",
            "relationships",
            "entity_identity_claims",
            "entity_evidence",
            "entity_observations",
            "entities",
            "test_projection_effects",
            "projector_failures",
            "projector_checkpoints",
            "observation_ingest_entries",
            "observation_evidence",
            "observations",
            "evidence_refs",
        )
    }
    assert counts_by_table == {
        "relationship_evidence": 0,
        "relationship_observations": 0,
        "relationships": 0,
        "entity_identity_claims": 0,
        "entity_evidence": 0,
        "entity_observations": 0,
        "entities": 0,
        "test_projection_effects": 0,
        "projector_failures": 0,
        "projector_checkpoints": 0,
        "observation_ingest_entries": 0,
        "observation_evidence": 0,
        "observations": 0,
        "evidence_refs": 0,
    }
    state_rows = connection.execute(
        text(
            """
            SELECT singleton_id, last_sequence
            FROM observation_ingest_sequence_state
            """
        )
    ).all()
    assert state_rows == [(1, 0)]


@pytest.fixture
def session_factory(migrated_engine):
    with migrated_engine.begin() as connection:
        reset_observation_store(connection)
    return sessionmaker(bind=migrated_engine, class_=Session, expire_on_commit=False, future=True)


def counts(session_factory) -> tuple[int, int, int]:
    with session_factory() as session:
        return (
            session.scalar(select(func.count()).select_from(ObservationRecord)),
            session.scalar(select(func.count()).select_from(EvidenceRefRecord)),
            session.scalar(select(func.count()).select_from(ObservationEvidenceRecord)),
        )


def sequence_state(session_factory) -> tuple[int, int]:
    with session_factory() as session:
        return (
            session.scalar(select(func.count()).select_from(ObservationIngestEntryRecord)),
            session.scalar(
                select(ObservationIngestSequenceStateRecord.last_sequence).where(
                    ObservationIngestSequenceStateRecord.singleton_id == 1
                )
            ),
        )


def run_concurrently(*calls):
    barrier = Barrier(len(calls))

    def wrapped(call):
        barrier.wait()
        return call()

    with ThreadPoolExecutor(max_workers=len(calls)) as executor:
        return [future.result() for future in [executor.submit(wrapped, call) for call in calls]]


def test_alembic_upgrades_successfully_against_postgresql(postgres_url):
    os.environ["CORRELIS_DATABASE_URL"] = postgres_url
    config = Config("alembic.ini")
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = create_engine(postgres_url, future=True)
    try:
        assert {"observations", "evidence_refs", "observation_evidence"}.issubset(
            set(inspect(engine).get_table_names())
        )
    finally:
        engine.dispose()


def test_alembic_downgrades_successfully_against_postgresql(postgres_url):
    os.environ["CORRELIS_DATABASE_URL"] = postgres_url
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    command.downgrade(config, "base")
    engine = create_engine(postgres_url, future=True)
    try:
        assert {"observations", "evidence_refs", "observation_evidence"}.isdisjoint(
            set(inspect(engine).get_table_names())
        )
    finally:
        engine.dispose()
        command.upgrade(config, "head")


def test_postgresql_schema_contracts(session_factory, migrated_engine):
    inspector = inspect(migrated_engine)
    for table in ("observations", "evidence_refs"):
        payload = next(
            column for column in inspector.get_columns(table) if column["name"] == "payload_json"
        )
        assert isinstance(payload["type"], JSONB)
    assert set(inspector.get_pk_constraint("observations")["constrained_columns"]) == {
        "tenant_id",
        "observation_id",
    }
    assert set(inspector.get_pk_constraint("evidence_refs")["constrained_columns"]) == {
        "tenant_id",
        "evidence_id",
    }
    assert set(inspector.get_pk_constraint("observation_evidence")["constrained_columns"]) == {
        "tenant_id",
        "observation_id",
        "evidence_id",
    }
    fks = inspector.get_foreign_keys("observation_evidence")
    assert ["tenant_id", "observation_id"] in [fk["constrained_columns"] for fk in fks]
    assert ["tenant_id", "evidence_id"] in [fk["constrained_columns"] for fk in fks]
    indexes = {index["name"] for index in inspector.get_indexes("observations")}
    assert "ix_observations_tenant_event_time" in indexes
    assert "ix_observations_tenant_source_event_time" in indexes
    assert "ix_observations_tenant_event_class_event_time" in indexes

    assert {
        "observation_ingest_sequence_state",
        "observation_ingest_entries",
    }.issubset(set(inspector.get_table_names()))
    state_pk = inspector.get_pk_constraint("observation_ingest_sequence_state")
    assert state_pk["constrained_columns"] == ["singleton_id"]
    state_columns = {
        column["name"]: column
        for column in inspector.get_columns("observation_ingest_sequence_state")
    }
    assert isinstance(state_columns["singleton_id"]["type"], SMALLINT)
    assert isinstance(state_columns["last_sequence"]["type"], BIGINT)
    assert state_columns["singleton_id"]["nullable"] is False
    assert state_columns["last_sequence"]["nullable"] is False
    state_checks = " ".join(
        check["sqltext"]
        for check in inspector.get_check_constraints("observation_ingest_sequence_state")
    )
    assert "singleton_id = 1" in state_checks
    assert "last_sequence >= 0" in state_checks

    entry_pk = inspector.get_pk_constraint("observation_ingest_entries")
    assert entry_pk["constrained_columns"] == ["ingest_sequence"]
    entry_columns = {
        column["name"]: column for column in inspector.get_columns("observation_ingest_entries")
    }
    assert isinstance(entry_columns["ingest_sequence"]["type"], BIGINT)
    entry_fks = inspector.get_foreign_keys("observation_ingest_entries")
    assert ["tenant_id", "observation_id"] in [fk["constrained_columns"] for fk in entry_fks]
    assert ["tenant_id", "observation_id"] in [fk["referred_columns"] for fk in entry_fks]
    entry_uniques = inspector.get_unique_constraints("observation_ingest_entries")
    assert ["tenant_id", "observation_id"] in [unique["column_names"] for unique in entry_uniques]
    entry_indexes = {
        tuple(index["column_names"])
        for index in inspector.get_indexes("observation_ingest_entries")
    }
    assert ("tenant_id", "ingest_sequence") in entry_indexes

    entity_tables = {"entities", "entity_observations", "entity_evidence", "entity_identity_claims"}
    assert entity_tables.issubset(set(inspector.get_table_names()))

    entity_pk = inspector.get_pk_constraint("entities")
    assert entity_pk["constrained_columns"] == ["projection_version", "tenant_id", "entity_id"]
    entity_columns = {column["name"]: column for column in inspector.get_columns("entities")}
    assert isinstance(entity_columns["attributes_json"]["type"], JSONB)
    for column in ("first_ingest_sequence", "last_ingest_sequence", "latest_claim_ingest_sequence"):
        assert isinstance(entity_columns[column]["type"], BIGINT)
    for column in (
        "first_seen",
        "last_seen",
        "latest_claim_event_time",
        "created_at",
        "updated_at",
    ):
        assert entity_columns[column]["type"].timezone is True
    entity_checks = " ".join(check["name"] for check in inspector.get_check_constraints("entities"))
    for name in (
        "ck_entities_canonical_key_length",
        "ck_entities_sequence_order",
        "ck_entities_seen_order",
    ):
        assert name in entity_checks
    assert ["projection_version", "tenant_id", "canonical_key"] in [
        unique["column_names"] for unique in inspector.get_unique_constraints("entities")
    ]
    entity_indexes = {index["name"] for index in inspector.get_indexes("entities")}
    assert {
        "ix_entities_projection_tenant_type_id",
        "ix_entities_projection_tenant_last_seen",
        "ix_entities_projection_tenant_canonical_key",
    } <= entity_indexes

    obs_pk = inspector.get_pk_constraint("entity_observations")
    assert obs_pk["constrained_columns"] == [
        "projection_version",
        "tenant_id",
        "entity_id",
        "observation_id",
        "role",
    ]
    obs_fks = inspector.get_foreign_keys("entity_observations")
    assert ["projection_version", "tenant_id", "entity_id"] in [
        fk["constrained_columns"] for fk in obs_fks
    ]
    assert ["tenant_id", "observation_id"] in [fk["constrained_columns"] for fk in obs_fks]
    assert ["ingest_sequence"] in [fk["constrained_columns"] for fk in obs_fks]
    obs_checks = " ".join(
        check["name"] for check in inspector.get_check_constraints("entity_observations")
    )
    assert "ck_entity_observations_role" in obs_checks
    obs_indexes = {index["name"] for index in inspector.get_indexes("entity_observations")}
    assert {
        "ix_entity_observations_entity_sequence",
        "ix_entity_observations_observation",
        "ix_entity_observations_sequence",
    } <= obs_indexes

    evidence_pk = inspector.get_pk_constraint("entity_evidence")
    assert evidence_pk["constrained_columns"] == [
        "projection_version",
        "tenant_id",
        "entity_id",
        "evidence_id",
    ]
    evidence_fks = inspector.get_foreign_keys("entity_evidence")
    assert ["projection_version", "tenant_id", "entity_id"] in [
        fk["constrained_columns"] for fk in evidence_fks
    ]
    assert ["tenant_id", "evidence_id"] in [fk["constrained_columns"] for fk in evidence_fks]
    evidence_checks = " ".join(
        check["name"] for check in inspector.get_check_constraints("entity_evidence")
    )
    assert "ck_entity_evidence_seen_order" in evidence_checks
    evidence_indexes = {index["name"] for index in inspector.get_indexes("entity_evidence")}
    assert {"ix_entity_evidence_entity", "ix_entity_evidence_evidence"} <= evidence_indexes

    claim_pk = inspector.get_pk_constraint("entity_identity_claims")
    assert claim_pk["constrained_columns"] == [
        "projection_version",
        "tenant_id",
        "entity_id",
        "identity_key_name",
        "value_sha256",
    ]
    claim_columns = {
        column["name"]: column for column in inspector.get_columns("entity_identity_claims")
    }
    assert isinstance(claim_columns["value_json"]["type"], JSONB)
    claim_fks = inspector.get_foreign_keys("entity_identity_claims")
    assert ["projection_version", "tenant_id", "entity_id"] in [
        fk["constrained_columns"] for fk in claim_fks
    ]
    claim_checks = " ".join(
        check["name"] for check in inspector.get_check_constraints("entity_identity_claims")
    )
    assert "ck_entity_identity_claims_hash_length" in claim_checks
    claim_indexes = {index["name"] for index in inspector.get_indexes("entity_identity_claims")}
    assert "ix_entity_identity_claims_lookup" in claim_indexes
    for table in entity_tables:
        for fk in inspector.get_foreign_keys(table):
            assert (fk.get("options") or {}).get("ondelete") is None

    with migrated_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT COUNT(*) FROM observation_ingest_sequence_state")
            ).scalar_one()
            == 1
        )
        serial_or_identity_columns = connection.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name IN (
                    'observation_ingest_sequence_state',
                    'observation_ingest_entries',
                    'entities',
                    'entity_observations',
                    'entity_evidence',
                    'entity_identity_claims'
                )
                AND (identity_generation IS NOT NULL OR column_default LIKE 'nextval%')
                """
            )
        ).all()
        assert serial_or_identity_columns == []


def test_observation_and_evidence_round_trip(session_factory):
    repo = ObservationRepository(session_factory)
    obs = observation()
    assert repo.put(obs) == WriteDisposition.CREATED
    assert repo.get(obs.tenant_id, obs.id) == obs
    assert repo.get_evidence(obs.tenant_id, obs.evidence[0].id) == obs.evidence[0]


def test_concurrent_identical_observation_writes_are_idempotent(session_factory):
    obs = observation()
    results = run_concurrently(
        lambda: ObservationRepository(session_factory).put(obs),
        lambda: ObservationRepository(session_factory).put(obs),
    )
    assert sorted(results) == [WriteDisposition.CREATED, WriteDisposition.EXISTING]
    assert counts(session_factory) == (1, 1, 1)


def test_concurrent_conflicting_observation_write_raises_conflict(session_factory):
    obs_a = observation(activity="login")
    obs_b = observation(activity="logout")

    def put(obs):
        try:
            return ObservationRepository(session_factory).put(obs)
        except Exception as exc:  # noqa: BLE001 - assert public exception type below.
            return exc

    results = run_concurrently(lambda: put(obs_a), lambda: put(obs_b))
    assert any(result == WriteDisposition.CREATED for result in results)
    assert any(isinstance(result, ImmutableRecordConflict) for result in results)
    assert not any(isinstance(result, IntegrityError) for result in results)
    assert counts(session_factory)[0] == 1


def test_concurrent_shared_evidence_can_be_reused(session_factory):
    shared = evidence()
    results = run_concurrently(
        lambda: ObservationRepository(session_factory).put(observation(id="obs-a", ev=shared)),
        lambda: ObservationRepository(session_factory).put(observation(id="obs-b", ev=shared)),
    )
    assert results == [WriteDisposition.CREATED, WriteDisposition.CREATED]
    assert counts(session_factory) == (2, 1, 2)


def test_concurrent_conflicting_evidence_rolls_back_losing_observation(session_factory):
    ev_a = evidence(id="ev-shared", sha="a" * 64)
    ev_b = evidence(id="ev-shared", sha="b" * 64)

    def put(obs):
        try:
            return ObservationRepository(session_factory).put(obs)
        except Exception as exc:  # noqa: BLE001 - assert public exception type below.
            return exc

    results = run_concurrently(
        lambda: put(observation(id="obs-a", ev=ev_a)),
        lambda: put(observation(id="obs-b", ev=ev_b)),
    )
    assert any(result == WriteDisposition.CREATED for result in results)
    assert any(isinstance(result, ImmutableRecordConflict) for result in results)
    assert not any(isinstance(result, IntegrityError) for result in results)
    assert counts(session_factory) == (1, 1, 1)


def test_tenant_isolation_against_postgresql(session_factory):
    repo = ObservationRepository(session_factory)
    obs = observation(tenant="tenant-a")
    repo.put(obs)
    assert repo.get("tenant-b", obs.id) is None
    assert repo.get_evidence("tenant-b", obs.evidence[0].id) is None


def test_timezone_aware_event_time_survives_round_trip(session_factory):
    repo = ObservationRepository(session_factory)
    when = datetime(2026, 1, 1, 17, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    obs = observation(when=when)
    repo.put(obs)
    stored = repo.get(obs.tenant_id, obs.id)
    assert stored is not None
    assert stored.event_time == when
    assert stored.event_time.tzinfo is not None


def test_sequence_write_behaviour_against_postgresql(session_factory):
    repo = ObservationRepository(session_factory)
    first = observation(id="seq-a", ev=evidence("seq-ev-a"))
    second = observation(id="seq-b", tenant="tenant-b", ev=evidence("seq-ev-b", "b" * 64))

    first_result = repo.put_with_result(first)
    assert first_result.disposition == WriteDisposition.CREATED
    assert first_result.ingest_sequence == 1
    assert repo.put(second) == WriteDisposition.CREATED
    assert repo.get_ingest_sequence(second.tenant_id, second.id) == 2
    assert sequence_state(session_factory) == (2, 2)

    retry = repo.put_with_result(first)
    assert retry.disposition == WriteDisposition.EXISTING
    assert retry.ingest_sequence == 1
    assert sequence_state(session_factory) == (2, 2)

    with pytest.raises(ImmutableRecordConflict):
        repo.put(observation(id="seq-a", ev=evidence("seq-ev-a"), activity="changed"))
    assert sequence_state(session_factory) == (2, 2)

    with pytest.raises(ImmutableRecordConflict):
        repo.put(observation(id="seq-c", ev=evidence("seq-ev-a", "c" * 64)))
    assert sequence_state(session_factory) == (2, 2)

    shared = evidence("shared-seq-ev", "d" * 64)
    repo.put(observation(id="seq-d", ev=shared))
    source_variant = observation(id="seq-e", ev=shared).model_copy(update={"source": "sensor-2"})
    repo.put(source_variant)
    assert sequence_state(session_factory) == (4, 4)
    assert counts(session_factory) == (4, 3, 4)

    page = repo.read_sequence_page(after_sequence=0, limit=10)
    assert [item.ingest_sequence for item in page.items] == [1, 2, 3, 4]
    assert [item.observation.id for item in page.items] == ["seq-a", "seq-b", "seq-d", "seq-e"]


def test_postgresql_allocator_rolls_back_without_visible_gap(session_factory):
    factory = session_factory
    allocator = ObservationSequenceAllocator()
    session_a = factory()
    try:
        session_a.begin()
        assert allocator.allocate(session_a) == 1
        session_a.rollback()
    finally:
        session_a.close()

    with factory() as reader:
        assert allocator.high_watermark(reader) == 0

    with factory() as session_b:
        session_b.begin()
        assert allocator.allocate(session_b) == 1
        session_b.commit()

    assert sequence_state(factory) == (0, 1)


def test_postgresql_allocator_row_lock_serializes_allocations(session_factory):
    allocator = ObservationSequenceAllocator()
    session_a = session_factory()
    session_b = session_factory()
    b_started = Event()
    b_completed = Event()
    release_a = Event()
    b_result: list[int] = []
    b_error: list[BaseException] = []

    def allocate_b() -> None:
        try:
            session_b.begin()
            b_started.set()
            b_result.append(allocator.allocate(session_b))
            b_completed.set()
            session_b.commit()
        except BaseException as exc:  # noqa: BLE001 - propagated after cleanup.
            b_error.append(exc)
            b_completed.set()
            session_b.rollback()
        finally:
            release_a.wait(timeout=5)
            session_b.close()

    try:
        session_a.begin()
        assert allocator.allocate(session_a) == 1
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(allocate_b)
            assert b_started.wait(timeout=5)
            assert not b_completed.wait(timeout=0.5)
            session_a.commit()
            assert b_completed.wait(timeout=5)
            release_a.set()
            future.result(timeout=5)
        assert b_error == []
        assert b_result == [2]
    finally:
        release_a.set()
        session_a.close()

    assert sequence_state(session_factory) == (0, 2)


def test_concurrent_sequence_results_for_repository_writes(session_factory):
    obs = observation(id="same", ev=evidence("same-ev"))
    same_results = run_concurrently(
        lambda: ObservationRepository(session_factory).put_with_result(obs),
        lambda: ObservationRepository(session_factory).put_with_result(obs),
    )
    assert sorted(result.disposition for result in same_results) == [
        WriteDisposition.CREATED,
        WriteDisposition.EXISTING,
    ]
    assert {result.ingest_sequence for result in same_results} == {1}
    assert counts(session_factory) == (1, 1, 1)
    assert sequence_state(session_factory) == (1, 1)


def test_concurrent_distinct_and_conflicting_sequences(session_factory, migrated_engine):
    with migrated_engine.begin() as connection:
        reset_observation_store(connection)
    distinct_results = run_concurrently(
        lambda: ObservationRepository(session_factory).put_with_result(
            observation(id="distinct-a", ev=evidence("distinct-ev-a"))
        ),
        lambda: ObservationRepository(session_factory).put_with_result(
            observation(id="distinct-b", ev=evidence("distinct-ev-b", "b" * 64))
        ),
    )
    assert sorted(result.ingest_sequence for result in distinct_results) == [1, 2]
    assert sequence_state(session_factory) == (2, 2)

    with migrated_engine.begin() as connection:
        reset_observation_store(connection)

    def put_result(obs):
        try:
            return ObservationRepository(session_factory).put_with_result(obs)
        except Exception as exc:  # noqa: BLE001 - assert public exception type below.
            return exc

    conflict_results = run_concurrently(
        lambda: put_result(observation(id="conflict", activity="login")),
        lambda: put_result(observation(id="conflict", activity="logout")),
    )
    assert any(
        getattr(result, "disposition", None) == WriteDisposition.CREATED
        for result in conflict_results
    )
    assert any(isinstance(result, ImmutableRecordConflict) for result in conflict_results)
    assert sequence_state(session_factory) == (1, 1)

    with migrated_engine.begin() as connection:
        reset_observation_store(connection)
    ev_a = evidence(id="conflict-ev", sha="a" * 64)
    ev_b = evidence(id="conflict-ev", sha="b" * 64)
    evidence_results = run_concurrently(
        lambda: put_result(observation(id="ev-conflict-a", ev=ev_a)),
        lambda: put_result(observation(id="ev-conflict-b", ev=ev_b)),
    )
    assert any(
        getattr(result, "disposition", None) == WriteDisposition.CREATED
        for result in evidence_results
    )
    assert any(isinstance(result, ImmutableRecordConflict) for result in evidence_results)
    assert sequence_state(session_factory) == (1, 1)


def test_concurrent_shared_evidence_sequences_against_postgresql(session_factory):
    shared = evidence("concurrent-shared", "e" * 64)
    results = run_concurrently(
        lambda: ObservationRepository(session_factory).put_with_result(
            observation(id="shared-a", ev=shared)
        ),
        lambda: ObservationRepository(session_factory).put_with_result(
            observation(id="shared-b", ev=shared)
        ),
    )
    assert sorted(result.ingest_sequence for result in results) == [1, 2]
    assert counts(session_factory) == (2, 1, 2)
    assert sequence_state(session_factory) == (2, 2)


def test_postgresql_sequence_pages_are_cursor_safe(session_factory):
    repo = ObservationRepository(session_factory)
    repo.put(
        observation(id="page-a", when=datetime(2026, 1, 3, tzinfo=UTC), ev=evidence("page-ev-a"))
    )
    repo.put(
        observation(
            id="page-b", when=datetime(2026, 1, 1, tzinfo=UTC), ev=evidence("page-ev-b", "b" * 64)
        )
    )
    first_page = repo.read_sequence_page(after_sequence=0, limit=1)
    assert [item.ingest_sequence for item in first_page.items] == [1]
    assert first_page.next_sequence == 1
    assert first_page.high_watermark == 2
    assert first_page.has_more is True

    repo.put(observation(id="page-c", ev=evidence("page-ev-c", "c" * 64)))
    second_page = repo.read_sequence_page(after_sequence=first_page.next_sequence, limit=2)
    assert [item.ingest_sequence for item in second_page.items] == [2, 3]
    assert [item.observation.id for item in second_page.items] == ["page-b", "page-c"]
    assert second_page.next_sequence == 3
    assert second_page.has_more is False

    empty_page = repo.read_sequence_page(after_sequence=3, limit=2)
    assert empty_page.items == ()
    assert empty_page.next_sequence == 3
    assert empty_page.has_more is False

    with pytest.raises(ObservationSequenceCursorError):
        repo.read_sequence_page(after_sequence=-1)
    with pytest.raises(ObservationSequenceCursorError):
        repo.read_sequence_page(limit=0)
    with pytest.raises(ObservationSequenceCursorError):
        repo.read_sequence_page(limit=501)


def test_postgresql_sequence_migration_empty_upgrade_and_downgrade(postgres_url):
    os.environ["CORRELIS_DATABASE_URL"] = postgres_url
    config = Config("alembic.ini")
    command.downgrade(config, "base")
    command.upgrade(config, "0002_collector_identity")
    engine = create_engine(postgres_url, future=True)
    try:
        command.upgrade(config, "0003_observation_sequence")
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT COUNT(*) FROM observation_ingest_entries")
                ).scalar_one()
                == 0
            )
            assert (
                connection.execute(
                    text("SELECT last_sequence FROM observation_ingest_sequence_state")
                ).scalar_one()
                == 0
            )
        command.downgrade(config, "0002_collector_identity")
        table_names = set(inspect(engine).get_table_names())
        assert "observation_ingest_entries" not in table_names
        assert "observation_ingest_sequence_state" not in table_names
        assert {"observations", "evidence_refs", "observation_evidence"}.issubset(table_names)
        command.upgrade(config, "0003_observation_sequence")
    finally:
        engine.dispose()
        command.upgrade(config, "head")


def test_postgresql_sequence_migration_backfills_deterministically(postgres_url):
    os.environ["CORRELIS_DATABASE_URL"] = postgres_url
    config = Config("alembic.ini")
    command.downgrade(config, "base")
    command.upgrade(config, "0002_collector_identity")
    engine = create_engine(postgres_url, future=True)
    inserted_at = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    try:
        with engine.begin() as connection:
            for tenant_id, observation_id, offset in [
                ("tenant-b", "obs-2", 0),
                ("tenant-a", "obs-2", 0),
                ("tenant-a", "obs-1", 0),
                ("tenant-a", "obs-0", -1),
            ]:
                ev_id = f"ev-{tenant_id}-{observation_id}"
                connection.execute(
                    text(
                        """
                        INSERT INTO evidence_refs (
                            tenant_id, evidence_id, evidence_type, source, locator,
                            sha256, collected_at, payload_json, payload_sha256
                        ) VALUES (
                            :tenant_id, :evidence_id, 'raw_event', 'sensor', :locator,
                            :sha256, :collected_at, CAST(:payload_json AS jsonb), :payload_sha256
                        )
                        """
                    ),
                    {
                        "tenant_id": tenant_id,
                        "evidence_id": ev_id,
                        "locator": f"test://{ev_id}",
                        "sha256": "a" * 64,
                        "collected_at": inserted_at,
                        "payload_json": '{"kind":"evidence"}',
                        "payload_sha256": "e" * 64,
                    },
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO observations (
                            tenant_id, observation_id, event_time, ingest_time, source,
                            sensor_id, event_class, activity, severity, confidence,
                            payload_json, payload_sha256, inserted_at
                        ) VALUES (
                            :tenant_id, :observation_id, :event_time, :ingest_time, 'sensor',
                            'sensor-1', 'authentication', 'login', 'low', 0.5,
                            CAST(:payload_json AS jsonb), :payload_sha256, :inserted_at
                        )
                        """
                    ),
                    {
                        "tenant_id": tenant_id,
                        "observation_id": observation_id,
                        "event_time": inserted_at,
                        "ingest_time": inserted_at,
                        "payload_json": '{"kind":"observation"}',
                        "payload_sha256": f"{observation_id[-1]}" * 64,
                        "inserted_at": inserted_at + timedelta(hours=offset),
                    },
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO observation_evidence (tenant_id, observation_id, evidence_id)
                        VALUES (:tenant_id, :observation_id, :evidence_id)
                        """
                    ),
                    {
                        "tenant_id": tenant_id,
                        "observation_id": observation_id,
                        "evidence_id": ev_id,
                    },
                )
        before_hashes = {}
        with engine.connect() as connection:
            before_hashes = dict(
                connection.execute(
                    text(
                        """
                        SELECT tenant_id || ':' || observation_id, payload_sha256
                        FROM observations
                        """
                    )
                ).all()
            )
        command.upgrade(config, "0003_observation_sequence")
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT ingest_sequence, tenant_id, observation_id
                    FROM observation_ingest_entries
                    ORDER BY ingest_sequence
                    """
                )
            ).all()
            assert rows == [
                (1, "tenant-a", "obs-0"),
                (2, "tenant-a", "obs-1"),
                (3, "tenant-a", "obs-2"),
                (4, "tenant-b", "obs-2"),
            ]
            assert (
                connection.execute(
                    text("SELECT last_sequence FROM observation_ingest_sequence_state")
                ).scalar_one()
                == 4
            )
            assert (
                connection.execute(text("SELECT COUNT(*) FROM observations")).scalar_one()
                == connection.execute(
                    text("SELECT COUNT(*) FROM observation_ingest_entries")
                ).scalar_one()
            )
            after_hashes = dict(
                connection.execute(
                    text(
                        """
                        SELECT tenant_id || ':' || observation_id, payload_sha256
                        FROM observations
                        """
                    )
                ).all()
            )
            assert after_hashes == before_hashes
    finally:
        engine.dispose()
        command.upgrade(config, "head")


def _projection_identity(name="test-projection", version="1"):
    return ProjectorIdentity(name, version)


def _write_projection_effect(session, identity, item, value="ok"):
    session.execute(
        projection_effects.insert().values(
            projector_name=identity.name,
            projector_version=identity.version,
            ingest_sequence=item.ingest_sequence,
            observation_id=item.observation.id,
            value=value,
        )
    )


def test_postgresql_projection_atomic_success(session_factory):
    identity = _projection_identity()
    ProjectionRepository(session_factory).register_projector(identity)
    ObservationRepository(session_factory).put(
        observation("obs-p1", ev=evidence("ev-p1", "1" * 64))
    )
    ObservationRepository(session_factory).put(
        observation("obs-p2", ev=evidence("ev-p2", "2" * 64))
    )

    result = ProjectionRunner(session_factory).run_batch(
        identity, lambda session, item: _write_projection_effect(session, identity, item)
    )

    assert result.outcome == ProjectionRunOutcome.CAUGHT_UP
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(projection_effects)) == 2
    checkpoint = ProjectionRepository(session_factory).get_checkpoint(identity)
    assert checkpoint.last_processed_sequence == 2
    assert checkpoint.status == "idle"
    assert (
        ProjectionRepository(session_factory).list_failures(
            identity, status=ProjectorFailureStatus.ACTIVE
        )
        == []
    )


def test_postgresql_projection_poison_failure_rolls_back_item(session_factory):
    identity = _projection_identity()
    ProjectionRepository(session_factory).register_projector(identity)
    for idx in range(1, 4):
        ObservationRepository(session_factory).put(
            observation(f"obs-p{idx}", ev=evidence(f"ev-p{idx}", f"{idx}" * 64))
        )

    def handler(session, item):
        _write_projection_effect(session, identity, item)
        if item.ingest_sequence == 2:
            raise ProjectionHandlerError("bad_item", "safe failure")
        if item.ingest_sequence == 3:
            raise AssertionError("sequence 3 must not be called")

    result = ProjectionRunner(session_factory).run_batch(identity, handler)

    assert result.outcome == ProjectionRunOutcome.FAILED
    assert result.processed_count == 1
    with session_factory() as session:
        rows = session.execute(select(projection_effects.c.ingest_sequence)).all()
    assert rows == [(1,)]
    checkpoint = ProjectionRepository(session_factory).get_checkpoint(identity)
    assert checkpoint.last_processed_sequence == 1
    assert checkpoint.status == "failed"
    assert checkpoint.last_failure_sequence == 2
    failure = ProjectionRepository(session_factory).list_failures(
        identity, status=ProjectorFailureStatus.ACTIVE
    )[0]
    assert failure.ingest_sequence == 2
    assert failure.attempt_count == 1


def test_postgresql_projection_infrastructure_error_is_not_poison(session_factory):
    from sqlalchemy.exc import SQLAlchemyError

    identity = _projection_identity()
    repo = ProjectionRepository(session_factory)
    repo.register_projector(identity)
    ObservationRepository(session_factory).put(
        observation("obs-infra", ev=evidence("ev-infra", "f" * 64))
    )

    def broken(session, item):
        _write_projection_effect(session, identity, item)
        session.execute(text("SELECT * FROM deliberately_missing_projection_table"))

    with pytest.raises(SQLAlchemyError):
        ProjectionRunner(session_factory).run_batch(identity, broken)

    checkpoint = repo.get_checkpoint(identity)
    assert checkpoint.status == "idle"
    assert checkpoint.last_processed_sequence == 0
    assert repo.list_failures(identity, status=ProjectorFailureStatus.ACTIVE) == []
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(projection_effects)) == 0


def test_postgresql_projection_missing_failure_record_invariant(session_factory):
    identity = _projection_identity()
    repo = ProjectionRepository(session_factory)
    repo.register_projector(identity)
    ObservationRepository(session_factory).put(
        observation("obs-inv", ev=evidence("ev-inv", "e" * 64))
    )
    ProjectionRunner(session_factory).run_batch(
        identity,
        lambda session, item: (_ for _ in ()).throw(ProjectionHandlerError("bad", "safe")),
    )
    with session_factory.begin() as session:
        session.execute(text("DELETE FROM projector_failures"))

    called = False

    def handler(session, item):
        nonlocal called
        called = True

    with pytest.raises(ProjectionInvariantError):
        ProjectionRunner(session_factory).run_batch(identity, handler, retry_failed=True)
    assert not called
    assert repo.get_checkpoint(identity).status == "failed"


def test_postgresql_entity_projection_behavior_and_isolation(session_factory):
    repo = ObservationRepository(session_factory)
    first = observation("ent-ok", ev=evidence("ent-ev-1", "c" * 64))
    second = observation(
        "ent-newer",
        ev=evidence("ent-ev-2", "d" * 64),
    ).model_copy(
        update={
            "event_time": datetime(2026, 1, 2, 12, tzinfo=UTC),
            "subject": EntityRef(
                id="asset-1",
                type=EntityType.ASSET,
                label="asset newer",
                attributes={"asset_id": "asset-1", "hostname": "HostA", "removed": "gone"},
            ),
            "object": EntityRef(
                id="app-1",
                type=EntityType.APPLICATION,
                label="app",
                attributes={
                    "application_id": "app-1",
                    "scheme": "https",
                    "host": "app",
                    "port": 443,
                },
            ),
        }
    )
    older = observation("ent-older", ev=evidence("ent-ev-3", "e" * 64)).model_copy(
        update={
            "event_time": datetime(2025, 12, 31, 12, tzinfo=UTC),
            "subject": EntityRef(
                id="asset-1",
                type=EntityType.ASSET,
                label="asset old",
                attributes={"asset_id": "asset-1"},
            ),
        }
    )
    conflict = observation("ent-conflict", ev=evidence("ent-ev-4", "f" * 64)).model_copy(
        update={
            "subject": EntityRef(
                id="asset-1",
                type=EntityType.APPLICATION,
                label="bad",
                attributes={"application_id": "asset-1"},
            )
        }
    )
    for item in (first, second, older, conflict):
        repo.put_with_result(item)
    handler = EntityProjectionHandler(clock=lambda: datetime(2026, 2, 1, tzinfo=UTC))
    ProjectionRepository(session_factory).register_projector(handler.projector_identity)
    result = ProjectionRunner(session_factory).run_batch(
        handler.projector_identity, handler, limit=10
    )
    assert result.outcome == ProjectionRunOutcome.FAILED
    assert result.ending_sequence == 3
    entity = EntityRepository(session_factory).get_entity("1", "tenant-a", "asset-1")
    assert entity.label == "asset newer"
    assert entity.first_seen == older.event_time
    assert entity.last_seen == second.event_time
    assert entity.attributes["removed"] == "gone"
    lineage = EntityRepository(session_factory).get_lineage("1", "tenant-a", "asset-1")
    assert {row.observation_id for row in lineage.observations} == {
        "ent-ok",
        "ent-newer",
        "ent-older",
    }
    assert {row.evidence_id for row in lineage.evidence} == {"ent-ev-1", "ent-ev-2", "ent-ev-3"}
    assert {claim.identity_key_name for claim in lineage.identity_claims} >= {
        "asset_id",
        "hostname",
    }
    assert EntityRepository(session_factory).get_entity("1", "tenant-a", "app-1") is not None
    assert EntityRepository(session_factory).get_entity("1", "tenant-b", "asset-1") is None
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ObservationRecord)) == 4
        assert session.scalar(select(func.count()).select_from(EvidenceRefRecord)) == 4
        assert (
            session.scalar(select(func.count()).where(EntityRecord.projection_version == "1")) == 2
        )
        assert (
            session.scalar(
                select(func.count()).where(
                    ProjectorFailureRecord.error_code == "entity_type_conflict"
                )
            )
            == 1
        )

    v2 = EntityProjectionHandler(
        projection_version="2", clock=lambda: datetime(2026, 2, 1, tzinfo=UTC)
    )
    ProjectionRepository(session_factory).register_projector(v2.projector_identity)
    assert (
        ProjectionRunner(session_factory)
        .run_batch(v2.projector_identity, v2, limit=1)
        .ending_sequence
        == 1
    )
    assert EntityRepository(session_factory).get_entity("2", "tenant-a", "asset-1") is not None
