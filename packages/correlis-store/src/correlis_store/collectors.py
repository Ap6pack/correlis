from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class CollectorStatus(StrEnum):
    ENABLED = "enabled"
    DISABLED = "disabled"


class AuthenticationOutcome(StrEnum):
    SUCCESS = "success"
    REJECTED = "rejected"


class AuthenticationReasonCode(StrEnum):
    AUTHENTICATED = "authenticated"
    CREDENTIALS_MISSING = "credentials_missing"
    TOKEN_MALFORMED = "token_malformed"
    CREDENTIAL_NOT_FOUND = "credential_not_found"
    CREDENTIAL_SECRET_MISMATCH = "credential_secret_mismatch"
    CREDENTIAL_REVOKED = "credential_revoked"
    CREDENTIAL_EXPIRED = "credential_expired"
    COLLECTOR_NOT_FOUND = "collector_not_found"
    COLLECTOR_DISABLED = "collector_disabled"


@dataclass(frozen=True, slots=True)
class Collector:
    tenant_id: str
    collector_id: str
    name: str
    source: str
    status: CollectorStatus
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    disabled_at: datetime | None
    last_authenticated_at: datetime | None


@dataclass(frozen=True, slots=True)
class CollectorCredential:
    credential_id: str
    tenant_id: str
    collector_id: str
    name: str
    token_version: str
    created_at: datetime
    expires_at: datetime | None
    revoked_at: datetime | None
    last_used_at: datetime | None


@dataclass(frozen=True, slots=True)
class IssuedCollectorCredential:
    credential: CollectorCredential
    token: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class AuthenticatedCollectorPrincipal:
    tenant_id: str
    collector_id: str
    collector_name: str
    source: str
    credential_id: str


@dataclass(frozen=True, slots=True)
class CollectorAuthEvent:
    event_id: str
    occurred_at: datetime
    outcome: AuthenticationOutcome
    reason_code: AuthenticationReasonCode
    tenant_id: str | None
    collector_id: str | None
    credential_id: str | None
    request_id: str | None
    request_method: str
    request_path: str


@dataclass(frozen=True, slots=True)
class CollectorAuthenticationDecision:
    authenticated: bool
    principal: AuthenticatedCollectorPrincipal | None
    reason_code: AuthenticationReasonCode
