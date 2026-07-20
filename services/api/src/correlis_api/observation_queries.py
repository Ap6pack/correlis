from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated

from correlis_schema import EventClass, EvidenceRef, Observation, Severity
from correlis_store import (
    AuthenticatedCollectorPrincipal,
    ObservationQueryFilters,
    ObservationRepository,
)
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .collector_auth import get_authenticated_collector
from .dependencies import get_database_session
from .pagination import (
    PaginationCursorError,
    decode_cursor,
    encode_cursor,
    query_filter_fingerprint,
)
from .request_context import get_request_id

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["observation-queries"])


class PageMetadata(BaseModel):
    limit: int
    returned: int
    has_more: bool
    next_cursor: str | None


class ObservationReadResponse(BaseModel):
    request_id: str
    observation: Observation


class ObservationListResponse(BaseModel):
    request_id: str
    tenant_id: str
    source: str
    items: list[Observation]
    page: PageMetadata


class EvidenceReadResponse(BaseModel):
    request_id: str
    evidence: EvidenceRef


def _aware(value: datetime | None) -> None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "timezone_required",
                "message": "Observation query timestamps must include a timezone.",
            },
        )


def _unavailable(
    exc: Exception,
    request_id: str,
    principal: AuthenticatedCollectorPrincipal,
    operation: str,
    **extra: str,
) -> HTTPException:
    logger.exception(
        "observation query unavailable",
        extra={
            "request_id": request_id,
            "tenant_id": principal.tenant_id,
            "collector_id": principal.collector_id,
            "source": principal.source,
            "operation": operation,
            **extra,
        },
    )
    return HTTPException(
        status_code=503,
        detail={
            "code": "observation_query_unavailable",
            "message": "The observation query service is temporarily unavailable.",
        },
    )


@router.get(
    "/observations", response_model=ObservationListResponse, operation_id="list_observations"
)
def list_observations(
    request: Request,
    principal: Annotated[AuthenticatedCollectorPrincipal, Depends(get_authenticated_collector)],
    session: Annotated[Session, Depends(get_database_session)],
    event_time_from: Annotated[datetime | None, Query()] = None,
    event_time_to: Annotated[datetime | None, Query()] = None,
    event_class: Annotated[EventClass | None, Query()] = None,
    severity: Annotated[Severity | None, Query()] = None,
    sensor_id: Annotated[str | None, Query(min_length=1, max_length=256)] = None,
    source: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    limit: Annotated[int | None, Query(ge=1)] = None,
    cursor: Annotated[str | None, Query(max_length=4096)] = None,
) -> ObservationListResponse:
    request_id = get_request_id(request)
    effective_source = principal.source
    if source is not None and source != principal.source:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "collector_source_scope_mismatch",
                "message": (
                    "The requested source is outside the authenticated collector source scope."
                ),
            },
        )
    max_limit = request.app.state.settings.query_max_page_size
    page_limit = limit if limit is not None else request.app.state.settings.query_default_page_size
    if page_limit > max_limit:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "query_validation_failed",
                "message": "The observation query parameters failed validation.",
                "errors": [
                    {
                        "location": ["query", "limit"],
                        "type": "less_than_equal",
                        "message": f"Input should be less than or equal to {max_limit}",
                    }
                ],
            },
        )
    _aware(event_time_from)
    _aware(event_time_to)
    if (
        event_time_from is not None
        and event_time_to is not None
        and event_time_from > event_time_to
    ):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_event_time_range",
                "message": "event_time_from must not be later than event_time_to.",
            },
        )
    fingerprint = query_filter_fingerprint(
        tenant_id=principal.tenant_id,
        source=effective_source,
        event_time_from=event_time_from,
        event_time_to=event_time_to,
        event_class=event_class,
        severity=severity,
        sensor_id=sensor_id,
    )
    anchor = None
    if cursor is not None:
        try:
            anchor, cursor_fingerprint = decode_cursor(cursor)
        except PaginationCursorError as exc:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "invalid_pagination_cursor",
                    "message": "The pagination cursor is invalid.",
                },
            ) from exc
        if cursor_fingerprint != fingerprint:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "pagination_cursor_filter_mismatch",
                    "message": (
                        "The pagination cursor does not match the active observation filters."
                    ),
                },
            )
    try:
        page = ObservationRepository(session).list_page(
            principal.tenant_id,
            effective_source,
            limit=page_limit,
            anchor=anchor,
            filters=ObservationQueryFilters(
                event_time_from=event_time_from,
                event_time_to=event_time_to,
                event_class=event_class,
                severity=severity,
                sensor_id=sensor_id,
            ),
        )
    except Exception as exc:
        raise _unavailable(exc, request_id, principal, "list_observations") from exc
    next_cursor = encode_cursor(page.next_anchor, fingerprint) if page.next_anchor else None
    return ObservationListResponse(
        request_id=request_id,
        tenant_id=principal.tenant_id,
        source=effective_source,
        items=list(page.observations),
        page=PageMetadata(
            limit=page_limit,
            returned=len(page.observations),
            has_more=page.has_more,
            next_cursor=next_cursor,
        ),
    )


@router.get(
    "/observations/{observation_id}",
    response_model=ObservationReadResponse,
    operation_id="get_observation",
)
def get_observation(
    observation_id: Annotated[str, Path(min_length=1, max_length=128)],
    request: Request,
    principal: Annotated[AuthenticatedCollectorPrincipal, Depends(get_authenticated_collector)],
    session: Annotated[Session, Depends(get_database_session)],
) -> ObservationReadResponse:
    request_id = get_request_id(request)
    try:
        observation = ObservationRepository(session).get_scoped(
            principal.tenant_id, principal.source, observation_id
        )
    except Exception as exc:
        raise _unavailable(
            exc, request_id, principal, "get_observation", observation_id=observation_id
        ) from exc
    if observation is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "observation_not_found", "message": "The observation was not found."},
        )
    return ObservationReadResponse(request_id=request_id, observation=observation)


@router.get(
    "/evidence/{evidence_id}", response_model=EvidenceReadResponse, operation_id="get_evidence"
)
def get_evidence(
    evidence_id: Annotated[str, Path(min_length=1, max_length=128)],
    request: Request,
    principal: Annotated[AuthenticatedCollectorPrincipal, Depends(get_authenticated_collector)],
    session: Annotated[Session, Depends(get_database_session)],
) -> EvidenceReadResponse:
    request_id = get_request_id(request)
    try:
        evidence = ObservationRepository(session).get_evidence_scoped(
            principal.tenant_id, principal.source, evidence_id
        )
    except Exception as exc:
        raise _unavailable(
            exc, request_id, principal, "get_evidence", evidence_id=evidence_id
        ) from exc
    if evidence is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "evidence_not_found",
                "message": "The evidence reference was not found.",
            },
        )
    return EvidenceReadResponse(request_id=request_id, evidence=evidence)
