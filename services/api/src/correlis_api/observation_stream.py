from __future__ import annotations

import asyncio
import logging
import time
from typing import Annotated, Literal

import anyio
from correlis_store import (
    AuthenticatedCollectorPrincipal,
    ObservationRepository,
    ObservationSequenceCursorError,
    ObservationSequenceInvariantError,
    is_collector_principal_active,
)
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, sessionmaker

from .collector_auth import get_authenticated_collector_for_stream
from .dependencies import get_database_session_factory
from .request_context import get_request_id
from .sse import encode_sse_comment, encode_sse_event
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
        len(value) > 4096
        or value != value.strip()
        or any(ord(ch) < 32 or ch == "\x7f" for ch in value)
    ):
        raise _bad_cursor()
    return value


def _high_watermark(session_factory: sessionmaker[Session]) -> int:
    with session_factory() as session:
        return (
            ObservationRepository(session)
            .read_sequence_page(after_sequence=0, limit=1)
            .high_watermark
        )


def _scan(
    session_factory: sessionmaker[Session], tenant_id: str, source: str, position: int, limit: int
):
    return ObservationRepository(session_factory).scan_scoped_sequence_page(
        tenant_id, source, after_sequence=position, scan_limit=limit
    )


def _active(
    session_factory: sessionmaker[Session], principal: AuthenticatedCollectorPrincipal
) -> bool:
    with session_factory() as session:
        return is_collector_principal_active(session, principal)


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
    limiter: ObservationStreamConnectionLimiter = request.app.state.observation_stream_limiter
    lease = await limiter.try_acquire(
        tenant_id=principal.tenant_id, collector_id=principal.collector_id
    )
    if lease is None:
        raise HTTPException(status_code=429, detail=CAPACITY, headers={"Retry-After": "5"})
    codec = ObservationStreamCursorCodec(settings.credential_pepper)
    cursor = _check_token_text(cursor)
    last_event_id = _check_token_text(last_event_id)
    try:
        if cursor and last_event_id and cursor != last_event_id:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "stream_cursor_conflict",
                    "message": "Conflicting observation stream cursors were supplied.",
                },
            )
        supplied = cursor or last_event_id
        if supplied and start is not None:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "stream_start_conflict",
                    "message": "A start mode cannot be combined with a stream cursor.",
                },
            )
        try:
            high_watermark = await anyio.to_thread.run_sync(_high_watermark, session_factory)
        except Exception as exc:
            await lease.release()
            raise HTTPException(status_code=503, detail=UNAVAILABLE) from exc
        if supplied:
            try:
                position = codec.decode(
                    supplied,
                    tenant_id=principal.tenant_id,
                    collector_id=principal.collector_id,
                    source=principal.source,
                )
            except ObservationStreamCursorError as exc:
                raise _bad_cursor() from exc
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
    except Exception:
        await lease.release()
        raise

    request_id = get_request_id(request)

    async def events():
        nonlocal position
        last_emit = time.monotonic()
        next_auth = last_emit + settings.stream_auth_recheck_seconds
        try:
            ready_cursor = codec.encode(
                position=position,
                tenant_id=principal.tenant_id,
                collector_id=principal.collector_id,
                source=principal.source,
            )
            yield encode_sse_event(
                event="ready",
                event_id=ready_cursor,
                retry_ms=3000,
                data={
                    "request_id": request_id,
                    "tenant_id": principal.tenant_id,
                    "collector_id": principal.collector_id,
                    "source": principal.source,
                    "cursor": ready_cursor,
                    "start": start_label,
                },
            )
            last_emit = time.monotonic()
            while True:
                if await request.is_disconnected():
                    return
                now = time.monotonic()
                if now >= next_auth:
                    try:
                        active = await anyio.to_thread.run_sync(_active, session_factory, principal)
                    except Exception as exc:
                        logger.warning(
                            "observation stream failure",
                            extra={
                                "request_id": request_id,
                                "tenant_id": principal.tenant_id,
                                "collector_id": principal.collector_id,
                                "source": principal.source,
                                "operation": "auth_recheck",
                                "exception_type": type(exc).__name__,
                            },
                        )
                        yield encode_sse_event(
                            event="stream_error",
                            data={
                                "code": UNAVAILABLE["code"],
                                "message": UNAVAILABLE["message"],
                                "request_id": request_id,
                            },
                        )
                        return
                    if not active:
                        yield encode_sse_event(
                            event="stream_closed",
                            data={
                                "code": "collector_authentication_inactive",
                                "message": "Collector authentication is no longer active.",
                                "request_id": request_id,
                            },
                        )
                        return
                    next_auth = now + settings.stream_auth_recheck_seconds
                try:
                    page = await anyio.to_thread.run_sync(
                        _scan,
                        session_factory,
                        principal.tenant_id,
                        principal.source,
                        position,
                        settings.stream_scan_batch_size,
                    )
                except (ObservationSequenceCursorError, ObservationSequenceInvariantError) as exc:
                    logger.warning(
                        "observation stream failure",
                        extra={
                            "request_id": request_id,
                            "tenant_id": principal.tenant_id,
                            "collector_id": principal.collector_id,
                            "source": principal.source,
                            "operation": "scan",
                            "exception_type": type(exc).__name__,
                        },
                    )
                    yield encode_sse_event(
                        event="stream_error",
                        data={
                            "code": UNAVAILABLE["code"],
                            "message": UNAVAILABLE["message"],
                            "request_id": request_id,
                        },
                    )
                    return
                emitted_pos = position
                for item in page.observations:
                    emitted_pos = item.ingest_sequence
                    position = item.ingest_sequence
                    token = codec.encode(
                        position=position,
                        tenant_id=principal.tenant_id,
                        collector_id=principal.collector_id,
                        source=principal.source,
                    )
                    yield encode_sse_event(
                        event="observation",
                        event_id=token,
                        data={
                            "cursor": token,
                            "observation": item.observation.model_dump(mode="json"),
                        },
                    )
                    last_emit = time.monotonic()
                if page.next_position > emitted_pos:
                    position = page.next_position
                    token = codec.encode(
                        position=position,
                        tenant_id=principal.tenant_id,
                        collector_id=principal.collector_id,
                        source=principal.source,
                    )
                    yield encode_sse_event(
                        event="checkpoint", event_id=token, data={"cursor": token}
                    )
                    last_emit = time.monotonic()
                if not page.has_more:
                    if time.monotonic() - last_emit >= settings.stream_heartbeat_seconds:
                        yield encode_sse_comment()
                        last_emit = time.monotonic()
                    await asyncio.sleep(settings.stream_poll_interval_seconds)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "observation stream failure",
                extra={
                    "request_id": request_id,
                    "tenant_id": principal.tenant_id,
                    "collector_id": principal.collector_id,
                    "source": principal.source,
                    "operation": "stream",
                    "exception_type": type(exc).__name__,
                },
            )
            yield encode_sse_event(
                event="stream_error",
                data={
                    "code": UNAVAILABLE["code"],
                    "message": UNAVAILABLE["message"],
                    "request_id": request_id,
                },
            )
        finally:
            await lease.release()

    return StreamingResponse(
        events(),
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
