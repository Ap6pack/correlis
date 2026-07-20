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
    ImmutableRecordConflict,
    ObservationRepository,
    ObservationSequenceAllocator,
    ObservationSequenceCursorError,
    WriteDisposition,
)
from correlis_store.models import (
    EvidenceRefRecord,
    ObservationEvidenceRecord,
    ObservationIngestEntryRecord,
    ObservationIngestSequenceStateRecord,
    ObservationRecord,
)
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.dialects.postgresql import BIGINT, JSONB, SMALLINT
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.postgres

POSTGRES_URL = os.environ.get("CORRELIS_TEST_DATABASE_URL")


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
    try:
        yield engine
    finally:
        engine.dispose()
        command.downgrade(config, "base")


def reset_observation_store(connection) -> None:
    connection.execute(
        text(
            """
            TRUNCATE TABLE
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
            "observation_ingest_entries",
            "observation_evidence",
            "observations",
            "evidence_refs",
        )
    }
    assert counts_by_table == {
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
                    'observation_ingest_entries'
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
