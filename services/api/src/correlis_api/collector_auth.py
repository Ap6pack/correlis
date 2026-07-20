from __future__ import annotations

from typing import Annotated

from correlis_store import (
    AuthenticatedCollectorPrincipal,
    AuthenticationReasonCode,
    CollectorAuthenticator,
    CredentialPepperConfigurationError,
)
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .dependencies import get_database_session
from .request_context import get_request_id

bearer = HTTPBearer(auto_error=False)
REQUIRED = {
    "code": "collector_credentials_required",
    "message": "Collector credentials are required.",
}
FAILED = {
    "code": "collector_authentication_failed",
    "message": "Collector credentials are invalid or inactive.",
}
NOT_CONFIGURED = {
    "code": "collector_authentication_not_configured",
    "message": "Collector authentication is not configured.",
}


def _bearer_error(detail: dict[str, str]) -> HTTPException:
    return HTTPException(status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"})


def get_authenticated_collector(
    request: Request,
    session: Annotated[Session, Depends(get_database_session)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> AuthenticatedCollectorPrincipal:
    token = (
        credentials.credentials if credentials and credentials.scheme.lower() == "bearer" else None
    )
    settings = request.app.state.settings
    try:
        authenticator = CollectorAuthenticator(session, settings.credential_pepper)
    except CredentialPepperConfigurationError as exc:
        raise HTTPException(status_code=503, detail=NOT_CONFIGURED) from exc
    decision = authenticator.authenticate(
        token,
        request_id=get_request_id(request),
        request_method=request.method,
        request_path=request.url.path,
    )
    if decision.authenticated and decision.principal is not None:
        return decision.principal
    if decision.reason_code == AuthenticationReasonCode.CREDENTIALS_MISSING:
        raise _bearer_error(REQUIRED)
    raise _bearer_error(FAILED)
