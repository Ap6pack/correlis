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
from sqlalchemy.orm import Session, sessionmaker

from .dependencies import get_database_session, get_database_session_factory
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


def _authenticate_with_session(
    request: Request, session: Session, token: str | None
) -> AuthenticatedCollectorPrincipal:
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


def _token_from_credentials(credentials: HTTPAuthorizationCredentials | None) -> str | None:
    return (
        credentials.credentials if credentials and credentials.scheme.lower() == "bearer" else None
    )


def get_authenticated_collector(
    request: Request,
    session: Annotated[Session, Depends(get_database_session)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> AuthenticatedCollectorPrincipal:
    return _authenticate_with_session(request, session, _token_from_credentials(credentials))


def get_authenticated_collector_for_stream(
    request: Request,
    session_factory: Annotated[sessionmaker[Session], Depends(get_database_session_factory)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> AuthenticatedCollectorPrincipal:
    with session_factory() as session:
        return _authenticate_with_session(request, session, _token_from_credentials(credentials))
