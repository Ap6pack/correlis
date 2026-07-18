from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .collectors import (
    AuthenticationOutcome,
    AuthenticationReasonCode,
    Collector,
    CollectorAuthEvent,
    CollectorCredential,
    CollectorStatus,
    IssuedCollectorCredential,
)
from .credential_security import (
    TOKEN_VERSION,
    credential_digest,
    generate_collector_token,
    validate_credential_pepper,
)
from .models import CollectorAuthEventRecord, CollectorCredentialRecord, CollectorRecord

MAX_LIST_LIMIT = 500


class CollectorAlreadyExists(Exception):
    pass


class CollectorNotFound(Exception):
    pass


class CollectorDisabled(Exception):
    pass


class CollectorCredentialNotFound(Exception):
    pass


class InvalidCredentialExpiration(Exception):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


def _clean(v: str, name: str) -> str:
    v = (v or "").strip()
    if not v:
        raise ValueError(f"{name} is required")
    return v


def _limit(limit: int) -> None:
    if limit < 1 or limit > MAX_LIST_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIST_LIMIT}")


def _collector(r: CollectorRecord) -> Collector:
    return Collector(
        r.tenant_id,
        r.collector_id,
        r.name,
        r.source,
        CollectorStatus(r.status),
        dict(r.metadata_json or {}),
        r.created_at,
        r.updated_at,
        r.disabled_at,
        r.last_authenticated_at,
    )


def _credential(r: CollectorCredentialRecord) -> CollectorCredential:
    return CollectorCredential(
        r.credential_id,
        r.tenant_id,
        r.collector_id,
        r.name,
        r.token_version,
        r.created_at,
        r.expires_at,
        r.revoked_at,
        r.last_used_at,
    )


def _event(r: CollectorAuthEventRecord) -> CollectorAuthEvent:
    return CollectorAuthEvent(
        r.event_id,
        r.occurred_at,
        AuthenticationOutcome(r.outcome),
        AuthenticationReasonCode(r.reason_code),
        r.tenant_id,
        r.collector_id,
        r.credential_id,
        r.request_id,
        r.request_method,
        r.request_path,
    )


class CollectorRepository:
    def __init__(self, session_or_factory: Session | sessionmaker[Session] | Callable[[], Session]):
        self._session_or_factory = session_or_factory

    @contextmanager
    def _session_scope(self) -> Iterator[Session]:
        if isinstance(self._session_or_factory, Session):
            yield self._session_or_factory
            return
        s = self._session_or_factory()
        try:
            yield s
        finally:
            s.close()

    def create_collector(
        self,
        *,
        tenant_id: str,
        name: str,
        source: str,
        collector_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Collector:
        tenant_id, name, source = (
            _clean(tenant_id, "tenant_id"),
            _clean(name, "name"),
            _clean(source, "source"),
        )
        collector_id = _clean(collector_id or str(uuid.uuid4()), "collector_id")
        ts = _now()
        with self._session_scope() as s:
            rec = CollectorRecord(
                tenant_id=tenant_id,
                collector_id=collector_id,
                name=name,
                source=source,
                status=CollectorStatus.ENABLED.value,
                metadata_json=metadata or {},
                created_at=ts,
                updated_at=ts,
            )
            s.add(rec)
            try:
                s.commit()
            except IntegrityError as exc:
                s.rollback()
                raise CollectorAlreadyExists(collector_id) from exc
            return _collector(rec)

    def get_collector(self, tenant_id: str, collector_id: str) -> Collector | None:
        with self._session_scope() as s:
            r = s.get(CollectorRecord, {"tenant_id": tenant_id, "collector_id": collector_id})
            return _collector(r) if r else None

    def list_collectors(self, *, tenant_id: str | None = None, limit: int = 100) -> list[Collector]:
        _limit(limit)
        with self._session_scope() as s:
            stmt = select(CollectorRecord)
            if tenant_id:
                stmt = stmt.where(CollectorRecord.tenant_id == tenant_id)
            return [
                _collector(r)
                for r in s.scalars(
                    stmt.order_by(CollectorRecord.tenant_id, CollectorRecord.collector_id).limit(
                        limit
                    )
                )
            ]

    def _require_collector_record(self, s, tenant_id, collector_id):
        r = s.get(CollectorRecord, {"tenant_id": tenant_id, "collector_id": collector_id})
        if not r:
            raise CollectorNotFound(collector_id)
        return r

    def disable_collector(self, tenant_id: str, collector_id: str) -> Collector:
        with self._session_scope() as s:
            r = self._require_collector_record(s, tenant_id, collector_id)
            ts = _now()
            if r.status != CollectorStatus.DISABLED.value:
                r.status = CollectorStatus.DISABLED.value
                r.disabled_at = ts
                r.updated_at = ts
            s.commit()
            return _collector(r)

    def enable_collector(self, tenant_id: str, collector_id: str) -> Collector:
        with self._session_scope() as s:
            r = self._require_collector_record(s, tenant_id, collector_id)
            ts = _now()
            if r.status != CollectorStatus.ENABLED.value:
                r.status = CollectorStatus.ENABLED.value
                r.disabled_at = None
                r.updated_at = ts
            s.commit()
            return _collector(r)

    def issue_credential(
        self,
        tenant_id: str,
        collector_id: str,
        *,
        name: str,
        pepper: str,
        expires_at: datetime | None = None,
    ) -> IssuedCollectorCredential:
        validate_credential_pepper(pepper)
        ts = _now()
        if expires_at is not None and expires_at <= ts:
            raise InvalidCredentialExpiration("expires_at must be in the future")
        with self._session_scope() as s:
            c = self._require_collector_record(s, tenant_id, collector_id)
            if c.status != CollectorStatus.ENABLED.value:
                raise CollectorDisabled(collector_id)
            cid, secret, token = generate_collector_token()
            rec = CollectorCredentialRecord(
                credential_id=cid,
                tenant_id=tenant_id,
                collector_id=collector_id,
                name=_clean(name, "name"),
                token_version=TOKEN_VERSION,
                secret_digest=credential_digest(pepper=pepper, credential_id=cid, secret=secret),
                created_at=ts,
                expires_at=expires_at,
            )
            s.add(rec)
            s.commit()
            return IssuedCollectorCredential(_credential(rec), token)

    def list_credentials(self, tenant_id: str, collector_id: str) -> list[CollectorCredential]:
        with self._session_scope() as s:
            return [
                _credential(r)
                for r in s.scalars(
                    select(CollectorCredentialRecord)
                    .where(
                        CollectorCredentialRecord.tenant_id == tenant_id,
                        CollectorCredentialRecord.collector_id == collector_id,
                    )
                    .order_by(
                        CollectorCredentialRecord.created_at,
                        CollectorCredentialRecord.credential_id,
                    )
                )
            ]

    def revoke_credential(self, credential_id: str) -> CollectorCredential:
        with self._session_scope() as s:
            r = s.get(CollectorCredentialRecord, credential_id)
            if not r:
                raise CollectorCredentialNotFound(credential_id)
            if r.revoked_at is None:
                r.revoked_at = _now()
            s.commit()
            return _credential(r)

    def list_auth_events(
        self, *, tenant_id: str, collector_id: str | None = None, limit: int = 100
    ) -> list[CollectorAuthEvent]:
        _limit(limit)
        with self._session_scope() as s:
            stmt = select(CollectorAuthEventRecord).where(
                CollectorAuthEventRecord.tenant_id == tenant_id
            )
            if collector_id:
                stmt = stmt.where(CollectorAuthEventRecord.collector_id == collector_id)
            return [
                _event(r)
                for r in s.scalars(
                    stmt.order_by(
                        CollectorAuthEventRecord.occurred_at.desc(),
                        CollectorAuthEventRecord.event_id.desc(),
                    ).limit(limit)
                )
            ]
