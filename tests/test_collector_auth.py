from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from correlis_store import AuthenticationReasonCode, CollectorRepository
from correlis_store.collector_auth import CollectorAuthenticator
from correlis_store.collectors import CollectorStatus
from correlis_store.credential_security import (
    CredentialPepperConfigurationError,
    credential_digest,
    generate_collector_token,
    parse_collector_token,
    verify_credential_digest,
)
from correlis_store.models import Base, CollectorAuthEventRecord, CollectorCredentialRecord
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

PEPPER = "non-production-test-pepper-value-32-bytes"


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine, future=True, expire_on_commit=False) as s:
        yield s


def test_credential_security_round_trip_and_repr():
    cid, secret, token = generate_collector_token()
    assert token.startswith("correlis_v1.")
    assert len(secret) >= 43
    assert parse_collector_token(token).credential_id == cid
    digest = credential_digest(pepper=PEPPER, credential_id=cid, secret=secret)
    assert len(digest) == 64 and digest == digest.lower()
    assert verify_credential_digest(
        pepper=PEPPER, credential_id=cid, secret=secret, expected_digest=digest
    )
    assert not verify_credential_digest(
        pepper=PEPPER, credential_id=cid, secret=secret + "x", expected_digest=digest
    )
    assert credential_digest(pepper=PEPPER + "x", credential_id=cid, secret=secret) != digest
    with pytest.raises(CredentialPepperConfigurationError):
        credential_digest(pepper="weak", credential_id=cid, secret=secret)


def test_repository_issue_authenticate_revoke_and_audit(session):
    repo = CollectorRepository(session)
    repo.create_collector(
        tenant_id="tenant-a", collector_id="collector-1", name="Outrider", source="outrider"
    )
    issued = repo.issue_credential("tenant-a", "collector-1", name="primary", pepper=PEPPER)
    assert issued.token not in repr(issued)
    decision = CollectorAuthenticator(session, PEPPER).authenticate(
        issued.token, request_id="rid", request_method="GET", request_path="/api/v1/collectors/me"
    )
    assert decision.authenticated
    assert decision.principal and decision.principal.tenant_id == "tenant-a"
    cred_row = session.get(CollectorCredentialRecord, issued.credential.credential_id)
    assert cred_row and cred_row.last_used_at is not None
    assert issued.token not in " ".join(str(v) for v in vars(cred_row).values())
    repo.revoke_credential(issued.credential.credential_id)
    rejected = CollectorAuthenticator(session, PEPPER).authenticate(
        issued.token, request_id=None, request_method="GET", request_path="/api/v1/collectors/me"
    )
    assert not rejected.authenticated
    assert rejected.reason_code == AuthenticationReasonCode.CREDENTIAL_REVOKED
    events = session.scalars(
        select(CollectorAuthEventRecord).order_by(CollectorAuthEventRecord.occurred_at)
    ).all()
    assert [e.outcome for e in events] == ["success", "rejected"]
    assert all("correlis_v1" not in " ".join(str(v) for v in vars(e).values()) for e in events)


def test_expired_and_disabled_collectors_are_rejected(session):
    repo = CollectorRepository(session)
    repo.create_collector(
        tenant_id="tenant-a", collector_id="collector-1", name="Outrider", source="outrider"
    )
    expired = repo.issue_credential(
        "tenant-a",
        "collector-1",
        name="expired",
        pepper=PEPPER,
        expires_at=datetime.now(UTC) + timedelta(seconds=5),
    )
    auth = CollectorAuthenticator(
        session, PEPPER, now=lambda: datetime.now(UTC) + timedelta(days=1)
    )
    assert (
        auth.authenticate(
            expired.token, request_id=None, request_method="GET", request_path="/"
        ).reason_code
        == AuthenticationReasonCode.CREDENTIAL_EXPIRED
    )
    active = repo.issue_credential("tenant-a", "collector-1", name="active", pepper=PEPPER)
    disabled = repo.disable_collector("tenant-a", "collector-1")
    assert disabled.status == CollectorStatus.DISABLED
    assert (
        CollectorAuthenticator(session, PEPPER)
        .authenticate(active.token, request_id=None, request_method="GET", request_path="/")
        .reason_code
        == AuthenticationReasonCode.COLLECTOR_DISABLED
    )
