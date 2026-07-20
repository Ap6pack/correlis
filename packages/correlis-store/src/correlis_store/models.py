from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKeyConstraint,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
    insert,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON

json_type = JSON().with_variant(JSONB, "postgresql")


class Base(DeclarativeBase):
    pass


class ObservationRecord(Base):
    __tablename__ = "observations"

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    observation_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingest_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    sensor_id: Mapped[str] = mapped_column(String(256), nullable=False)
    event_class: Mapped[str] = mapped_column(String(128), nullable=False)
    activity: Mapped[str] = mapped_column(String(128), nullable=False)
    severity: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(json_type, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    inserted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_observations_tenant_event_time", "tenant_id", "event_time"),
        Index("ix_observations_tenant_source_event_time", "tenant_id", "source", "event_time"),
        Index(
            "ix_observations_tenant_event_class_event_time",
            "tenant_id",
            "event_class",
            "event_time",
        ),
    )


class ObservationIngestSequenceStateRecord(Base):
    __tablename__ = "observation_ingest_sequence_state"

    singleton_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    last_sequence: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )

    __table_args__ = (
        CheckConstraint("singleton_id = 1", name="ck_observation_ingest_sequence_state_singleton"),
        CheckConstraint(
            "last_sequence >= 0", name="ck_observation_ingest_sequence_state_nonnegative"
        ),
    )


@event.listens_for(ObservationIngestSequenceStateRecord.__table__, "after_create")
def _insert_initial_observation_ingest_sequence_state(target, connection, **kw):
    connection.execute(insert(target).values(singleton_id=1, last_sequence=0))


class ObservationIngestEntryRecord(Base):
    __tablename__ = "observation_ingest_entries"

    ingest_sequence: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    observation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    inserted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "observation_id", name="uq_observation_ingest_entries_observation"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "observation_id"],
            ["observations.tenant_id", "observations.observation_id"],
        ),
        Index("ix_observation_ingest_entries_tenant_sequence", "tenant_id", "ingest_sequence"),
    )


class EvidenceRefRecord(Base):
    __tablename__ = "evidence_refs"

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    evidence_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    evidence_type: Mapped[str] = mapped_column(String(128), nullable=False)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    locator: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(json_type, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    inserted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("ix_evidence_refs_tenant_sha256", "tenant_id", "sha256"),)


class ObservationEvidenceRecord(Base):
    __tablename__ = "observation_evidence"

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    observation_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    evidence_id: Mapped[str] = mapped_column(String(128), primary_key=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "observation_id"],
            ["observations.tenant_id", "observations.observation_id"],
        ),
        ForeignKeyConstraint(
            ["tenant_id", "evidence_id"],
            ["evidence_refs.tenant_id", "evidence_refs.evidence_id"],
        ),
    )


class CollectorRecord(Base):
    __tablename__ = "collectors"

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    collector_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(json_type, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_authenticated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("ix_collectors_tenant_status", "tenant_id", "status"),
        Index("ix_collectors_tenant_source", "tenant_id", "source"),
    )


class CollectorCredentialRecord(Base):
    __tablename__ = "collector_credentials"

    credential_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    collector_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    token_version: Mapped[str] = mapped_column(String(16), nullable=False)
    secret_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "collector_id"], ["collectors.tenant_id", "collectors.collector_id"]
        ),
        Index("ix_collector_credentials_tenant_collector", "tenant_id", "collector_id"),
        Index(
            "ix_collector_credentials_tenant_collector_revoked",
            "tenant_id",
            "collector_id",
            "revoked_at",
        ),
        Index("ix_collector_credentials_expires_at", "expires_at"),
    )


class CollectorAuthEventRecord(Base):
    __tablename__ = "collector_auth_events"

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    tenant_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    collector_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    credential_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    request_method: Mapped[str] = mapped_column(String(16), nullable=False)
    request_path: Mapped[str] = mapped_column(String(2048), nullable=False)

    __table_args__ = (
        Index("ix_collector_auth_events_occurred_at", "occurred_at"),
        Index("ix_collector_auth_events_tenant_occurred", "tenant_id", "occurred_at"),
        Index(
            "ix_collector_auth_events_tenant_collector_occurred",
            "tenant_id",
            "collector_id",
            "occurred_at",
        ),
        Index("ix_collector_auth_events_outcome_occurred", "outcome", "occurred_at"),
    )
