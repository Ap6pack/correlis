from __future__ import annotations

import logging
from typing import Annotated, Literal

import anyio
from correlis_store import (
    AuthenticatedCollectorPrincipal,
    ObservationSequenceAllocator,
    ObservationSequenceInvariantError,
)
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, sessionmaker
from starlette.background import BackgroundTask

from .collector_auth import get_authenticated_collector_for_stream
from .dependencies import get_database_session_factory
from .observation_stream_runtime import ObservationStreamRuntime
from .request_context import get_request_id
from .stream_connections import ObservationStreamConnectionLimiter
from .stream_cursor import ObservationStreamCursorCodec, ObservationStreamCursorError

router = APIRouter(prefix="/api/v1", tags=["observation-stream"])
logger = logging.getLogger(__name__)
INVALID_CURSOR = {
    "code": "invalid_stream_cursor",
    "message": "The observation stream cursor is invalid.",
}
UNAVAILABLE = {
    "code": "observation_stream_unavailable",
    "message": "The observation stream is temporarily unavailable.",
}
CAPACITY = {
    "code": "observation_stream_capacity_exceeded",
    "message": "Observation stream capacity is currently unavailable.",
}


def _bad_cursor() -> HTTPException:
    return HTTPException(status_code=400, detail=INVALID_CURSOR)


def _check_token_text(value: str | None) -> str | None:
    if value is None:
        return None
    if (
        not value
        or len(value) > 4096
        or value != value.strip()
        or any(ord(ch) < 32 or ch == "\x7f" for ch in value)
    ):
        raise _bad_cursor()
    return value


def _high_watermark(session_factory: sessionmaker[Session]) -> int:
    with session_factory() as session:
        return ObservationSequenceAllocator().high_watermark(session)


@router.get("/streams/observations")
async def stream_observations(
    request: Request,
    principal: Annotated[
        AuthenticatedCollectorPrincipal, Depends(get_authenticated_collector_for_stream)
    ],
    session_factory: Annotated[sessionmaker[Session], Depends(get_database_session_factory)],
    cursor: Annotated[str | None, Query(max_length=4096)] = None,
    start: Annotated[Literal["latest", "earliest"] | None, Query()] = None,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID", max_length=4096)] = None,
    source: Annotated[str | None, Query(max_length=4096)] = None,
    tenant: Annotated[str | None, Query(max_length=4096)] = None,
) -> StreamingResponse:
    if source is not None or tenant is not None:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "stream_scope_override_rejected",
                "message": "Observation stream scope cannot be overridden.",
            },
        )

    settings = request.app.state.settings
    cursor = _check_token_text(cursor)
    last_event_id = _check_token_text(last_event_id)
    if cursor is not None and last_event_id is not None and cursor != last_event_id:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "stream_cursor_conflict",
                "message": "Conflicting observation stream cursors were supplied.",
            },
        )
    supplied = cursor if cursor is not None else last_event_id
    if supplied is not None and start is not None:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "stream_start_conflict",
                "message": "A start mode cannot be combined with a stream cursor.",
            },
        )

    codec = ObservationStreamCursorCodec(settings.credential_pepper)
    if supplied is not None:
        try:
            supplied_position = codec.decode(
                supplied,
                tenant_id=principal.tenant_id,
                collector_id=principal.collector_id,
                source=principal.source,
            )
        except ObservationStreamCursorError as exc:
            raise _bad_cursor() from exc
    else:
        supplied_position = None

    limiter: ObservationStreamConnectionLimiter = request.app.state.observation_stream_limiter
    lease = await limiter.try_acquire(
        tenant_id=principal.tenant_id, collector_id=principal.collector_id
    )
    if lease is None:
        raise HTTPException(status_code=429, detail=CAPACITY, headers={"Retry-After": "5"})

    try:
        try:
            high_watermark = await anyio.to_thread.run_sync(_high_watermark, session_factory)
        except ObservationSequenceInvariantError as exc:
            raise HTTPException(status_code=503, detail=UNAVAILABLE) from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail=UNAVAILABLE) from exc

        if supplied_position is not None:
            position = supplied_position
            if position > high_watermark:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "stream_cursor_ahead_of_store",
                        "message": "The stream cursor is ahead of the current observation store.",
                    },
                )
            start_label = "cursor"
        elif start == "earliest":
            position = 0
            start_label = "earliest"
        else:
            position = high_watermark
            start_label = "latest"

        runtime = ObservationStreamRuntime(
            session_factory=session_factory,
            settings=settings,
            codec=codec,
            lease=lease,
        )
        return StreamingResponse(
            runtime.events(
                request=request,
                principal=principal,
                request_id=get_request_id(request),
                starting_position=position,
                start_label=start_label,
            ),
            media_type="text/event-stream; charset=utf-8",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
            background=BackgroundTask(lease.release),
        )
    except Exception:
        await lease.release()
        raise
