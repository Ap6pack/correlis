from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
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


class ProjectorCheckpointRecord(Base):
    __tablename__ = "projector_checkpoints"

    projector_name: Mapped[str] = mapped_column(String(128), primary_key=True)
    projector_version: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_processed_sequence: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    last_failure_sequence: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "last_processed_sequence >= 0", name="ck_projector_checkpoints_sequence_nonnegative"
        ),
        CheckConstraint(
            "status IN ('idle', 'failed', 'paused')", name="ck_projector_checkpoints_status"
        ),
        CheckConstraint(
            "((status = 'failed' AND last_failure_sequence IS NOT NULL "
            "AND last_failure_sequence > last_processed_sequence) OR "
            "(status <> 'failed' AND last_failure_sequence IS NULL))",
            name="ck_projector_checkpoints_failure_state",
        ),
    )


class ProjectorFailureRecord(Base):
    __tablename__ = "projector_failures"

    projector_name: Mapped[str] = mapped_column(String(128), primary_key=True)
    projector_version: Mapped[str] = mapped_column(String(64), primary_key=True)
    ingest_sequence: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("observation_ingest_entries.ingest_sequence"),
        primary_key=True,
        autoincrement=False,
    )
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    observation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt_count: Mapped[int] = mapped_column(nullable=False)
    error_code: Mapped[str] = mapped_column(String(64), nullable=False)
    error_type: Mapped[str] = mapped_column(String(256), nullable=False)
    safe_message: Mapped[str] = mapped_column(String(2048), nullable=False)
    first_failed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_failed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["projector_name", "projector_version"],
            ["projector_checkpoints.projector_name", "projector_checkpoints.projector_version"],
        ),
        ForeignKeyConstraint(
            ["tenant_id", "observation_id"],
            ["observations.tenant_id", "observations.observation_id"],
        ),
        CheckConstraint("status IN ('active', 'resolved')", name="ck_projector_failures_status"),
        CheckConstraint("attempt_count >= 1", name="ck_projector_failures_attempt_count"),
        CheckConstraint("error_code GLOB '[a-z0-9_]*'", name="ck_projector_failures_error_code"),
        CheckConstraint(
            "((status = 'active' AND resolved_at IS NULL) OR "
            "(status = 'resolved' AND resolved_at IS NOT NULL))",
            name="ck_projector_failures_resolved_state",
        ),
        Index(
            "ix_projector_failures_projector_status_last_failed",
            "projector_name",
            "projector_version",
            "status",
            "last_failed_at",
        ),
        Index("ix_projector_failures_status_last_failed", "status", "last_failed_at"),
        Index("ix_projector_failures_ingest_sequence", "ingest_sequence"),
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


class EntityRecord(Base):
    __tablename__ = "entities"

    projection_version: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    entity_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    canonical_key: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(256), nullable=False)
    attributes_json: Mapped[dict[str, Any]] = mapped_column(json_type, nullable=False)
    ontology_name: Mapped[str] = mapped_column(String(128), nullable=False)
    ontology_version: Mapped[str] = mapped_column(String(64), nullable=False)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    first_ingest_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_ingest_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    latest_claim_event_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    latest_claim_ingest_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("length(canonical_key) = 64", name="ck_entities_canonical_key_length"),
        CheckConstraint("first_ingest_sequence >= 1", name="ck_entities_first_sequence_positive"),
        CheckConstraint(
            "last_ingest_sequence >= first_ingest_sequence", name="ck_entities_sequence_order"
        ),
        CheckConstraint(
            "latest_claim_ingest_sequence >= first_ingest_sequence",
            name="ck_entities_latest_sequence_min",
        ),
        CheckConstraint(
            "latest_claim_ingest_sequence <= last_ingest_sequence",
            name="ck_entities_latest_sequence_max",
        ),
        CheckConstraint("first_seen <= last_seen", name="ck_entities_seen_order"),
        CheckConstraint(
            "latest_claim_event_time <= last_seen", name="ck_entities_latest_claim_seen"
        ),
        UniqueConstraint(
            "projection_version",
            "tenant_id",
            "canonical_key",
            name="uq_entities_projection_tenant_canonical_key",
        ),
        Index(
            "ix_entities_projection_tenant_type_id",
            "projection_version",
            "tenant_id",
            "entity_type",
            "entity_id",
        ),
        Index(
            "ix_entities_projection_tenant_last_seen",
            "projection_version",
            "tenant_id",
            "last_seen",
        ),
        Index(
            "ix_entities_projection_tenant_canonical_key",
            "projection_version",
            "tenant_id",
            "canonical_key",
        ),
    )


class EntityObservationRecord(Base):
    __tablename__ = "entity_observations"

    projection_version: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    entity_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    observation_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    role: Mapped[str] = mapped_column(String(16), primary_key=True)
    ingest_sequence: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("observation_ingest_entries.ingest_sequence"), nullable=False
    )
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["projection_version", "tenant_id", "entity_id"],
            ["entities.projection_version", "entities.tenant_id", "entities.entity_id"],
        ),
        ForeignKeyConstraint(
            ["tenant_id", "observation_id"],
            ["observations.tenant_id", "observations.observation_id"],
        ),
        CheckConstraint("role IN ('subject', 'object')", name="ck_entity_observations_role"),
        Index(
            "ix_entity_observations_entity_sequence",
            "projection_version",
            "tenant_id",
            "entity_id",
            "ingest_sequence",
        ),
        Index(
            "ix_entity_observations_observation",
            "projection_version",
            "tenant_id",
            "observation_id",
        ),
        Index(
            "ix_entity_observations_sequence", "projection_version", "tenant_id", "ingest_sequence"
        ),
    )


class EntityEvidenceRecord(Base):
    __tablename__ = "entity_evidence"

    projection_version: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    entity_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    evidence_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    first_ingest_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_ingest_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["projection_version", "tenant_id", "entity_id"],
            ["entities.projection_version", "entities.tenant_id", "entities.entity_id"],
        ),
        ForeignKeyConstraint(
            ["tenant_id", "evidence_id"], ["evidence_refs.tenant_id", "evidence_refs.evidence_id"]
        ),
        CheckConstraint("first_seen <= last_seen", name="ck_entity_evidence_seen_order"),
        CheckConstraint(
            "first_ingest_sequence >= 1", name="ck_entity_evidence_first_sequence_positive"
        ),
        CheckConstraint(
            "last_ingest_sequence >= first_ingest_sequence",
            name="ck_entity_evidence_sequence_order",
        ),
        Index("ix_entity_evidence_entity", "projection_version", "tenant_id", "entity_id"),
        Index("ix_entity_evidence_evidence", "projection_version", "tenant_id", "evidence_id"),
    )


class EntityIdentityClaimRecord(Base):
    __tablename__ = "entity_identity_claims"

    projection_version: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    entity_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    identity_key_name: Mapped[str] = mapped_column(String(128), primary_key=True)
    value_sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    value_json: Mapped[dict[str, Any]] = mapped_column(json_type, nullable=False)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    first_ingest_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_ingest_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["projection_version", "tenant_id", "entity_id"],
            ["entities.projection_version", "entities.tenant_id", "entities.entity_id"],
        ),
        CheckConstraint("length(value_sha256) = 64", name="ck_entity_identity_claims_hash_length"),
        CheckConstraint("first_seen <= last_seen", name="ck_entity_identity_claims_seen_order"),
        CheckConstraint(
            "first_ingest_sequence >= 1", name="ck_entity_identity_claims_first_sequence_positive"
        ),
        CheckConstraint(
            "last_ingest_sequence >= first_ingest_sequence",
            name="ck_entity_identity_claims_sequence_order",
        ),
        Index(
            "ix_entity_identity_claims_lookup",
            "projection_version",
            "tenant_id",
            "entity_type",
            "identity_key_name",
            "value_sha256",
        ),
    )
