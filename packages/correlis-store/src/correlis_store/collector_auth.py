from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from .collectors import (
    AuthenticatedCollectorPrincipal,
    AuthenticationOutcome,
    AuthenticationReasonCode,
    CollectorAuthenticationDecision,
    CollectorStatus,
)
from .credential_security import (
    TokenParseError,
    parse_collector_token,
    validate_credential_pepper,
    verify_credential_digest,
)
from .models import CollectorAuthEventRecord, CollectorCredentialRecord, CollectorRecord


def _utcnow() -> datetime:
    return datetime.now(UTC)


class CollectorAuthenticator:
    def __init__(self, session: Session, pepper: str, *, now: Callable[[], datetime] | None = None):
        validate_credential_pepper(pepper)
        self.session = session
        self.pepper = pepper
        self.now = now or _utcnow

    def authenticate(
        self,
        presented_token: str | None,
        *,
        request_id: str | None,
        request_method: str,
        request_path: str,
    ) -> CollectorAuthenticationDecision:
        ts = self.now()
        tenant_id = collector_id = credential_id = None

        def reject(reason: AuthenticationReasonCode):
            self.session.rollback()
            self.session.add(
                CollectorAuthEventRecord(
                    event_id=str(uuid.uuid4()),
                    occurred_at=ts,
                    outcome=AuthenticationOutcome.REJECTED.value,
                    reason_code=reason.value,
                    tenant_id=tenant_id,
                    collector_id=collector_id,
                    credential_id=credential_id,
                    request_id=request_id,
                    request_method=request_method[:16],
                    request_path=request_path[:2048],
                )
            )
            self.session.commit()
            return CollectorAuthenticationDecision(False, None, reason)

        if not presented_token:
            return reject(AuthenticationReasonCode.CREDENTIALS_MISSING)
        try:
            parsed = parse_collector_token(presented_token)
            credential_id = parsed.credential_id
        except TokenParseError:
            return reject(AuthenticationReasonCode.TOKEN_MALFORMED)
        cred = self.session.get(CollectorCredentialRecord, credential_id)
        if cred is None:
            return reject(AuthenticationReasonCode.CREDENTIAL_NOT_FOUND)
        tenant_id, collector_id = cred.tenant_id, cred.collector_id
        collector = self.session.get(
            CollectorRecord, {"tenant_id": tenant_id, "collector_id": collector_id}
        )
        if collector is None:
            return reject(AuthenticationReasonCode.COLLECTOR_NOT_FOUND)
        if cred.token_version != parsed.token_version:
            return reject(AuthenticationReasonCode.TOKEN_MALFORMED)
        if not verify_credential_digest(
            pepper=self.pepper,
            credential_id=credential_id,
            secret=parsed.secret,
            expected_digest=cred.secret_digest,
            token_version=cred.token_version,
        ):
            return reject(AuthenticationReasonCode.CREDENTIAL_SECRET_MISMATCH)
        if cred.revoked_at is not None:
            return reject(AuthenticationReasonCode.CREDENTIAL_REVOKED)
        expires_at = cred.expires_at
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at is not None and expires_at <= ts:
            return reject(AuthenticationReasonCode.CREDENTIAL_EXPIRED)
        if collector.status != CollectorStatus.ENABLED.value:
            return reject(AuthenticationReasonCode.COLLECTOR_DISABLED)
        cred.last_used_at = ts
        collector.last_authenticated_at = ts
        self.session.add(
            CollectorAuthEventRecord(
                event_id=str(uuid.uuid4()),
                occurred_at=ts,
                outcome=AuthenticationOutcome.SUCCESS.value,
                reason_code=AuthenticationReasonCode.AUTHENTICATED.value,
                tenant_id=tenant_id,
                collector_id=collector_id,
                credential_id=credential_id,
                request_id=request_id,
                request_method=request_method[:16],
                request_path=request_path[:2048],
            )
        )
        self.session.commit()
        return CollectorAuthenticationDecision(
            True,
            AuthenticatedCollectorPrincipal(
                tenant_id, collector_id, collector.name, collector.source, credential_id
            ),
            AuthenticationReasonCode.AUTHENTICATED,
        )


def is_collector_principal_active(
    session: Session, principal: AuthenticatedCollectorPrincipal, *, now: datetime | None = None
) -> bool:
    ts = now or _utcnow()
    cred = session.get(CollectorCredentialRecord, principal.credential_id)
    if cred is None:
        return False
    if cred.tenant_id != principal.tenant_id or cred.collector_id != principal.collector_id:
        return False
    if cred.revoked_at is not None:
        return False
    expires_at = cred.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at is not None and expires_at <= ts:
        return False
    collector = session.get(
        CollectorRecord, {"tenant_id": principal.tenant_id, "collector_id": principal.collector_id}
    )
    if collector is None:
        return False
    if (
        collector.tenant_id != principal.tenant_id
        or collector.collector_id != principal.collector_id
    ):
        return False
    if collector.status != CollectorStatus.ENABLED.value:
        return False
    return collector.source == principal.source
