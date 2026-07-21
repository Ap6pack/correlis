from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable

import anyio
from correlis_store import (
    AuthenticatedCollectorPrincipal,
    ObservationRepository,
    ObservationSequenceCursorError,
    ObservationSequenceInvariantError,
    is_collector_principal_active,
)
from sqlalchemy.orm import Session, sessionmaker

from .sse import encode_sse_comment, encode_sse_event
from .stream_connections import ObservationStreamLease
from .stream_cursor import ObservationStreamCursorCodec

logger = logging.getLogger(__name__)

UNAVAILABLE = {
    "code": "observation_stream_unavailable",
    "message": "The observation stream is temporarily unavailable.",
}


def scan_observation_stream_page(
    session_factory: sessionmaker[Session], tenant_id: str, source: str, position: int, limit: int
):
    return ObservationRepository(session_factory).scan_scoped_sequence_page(
        tenant_id, source, after_sequence=position, scan_limit=limit
    )


def collector_principal_active(
    session_factory: sessionmaker[Session], principal: AuthenticatedCollectorPrincipal
) -> bool:
    with session_factory() as session:
        return is_collector_principal_active(session, principal)


class ObservationStreamRuntime:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        settings,
        codec: ObservationStreamCursorCodec,
        lease: ObservationStreamLease,
        monotonic_clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        scan_page=scan_observation_stream_page,
        principal_active=collector_principal_active,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._codec = codec
        self._lease = lease
        self._monotonic = monotonic_clock
        self._sleep = sleep
        self._scan_page = scan_page
        self._principal_active = principal_active

    async def events(
        self,
        *,
        request,
        principal: AuthenticatedCollectorPrincipal,
        request_id: str,
        starting_position: int,
        start_label: str,
    ) -> AsyncIterator[bytes]:
        position = starting_position
        last_emit = self._monotonic()
        next_auth = last_emit + self._settings.stream_auth_recheck_seconds

        async def revalidate_if_due(operation: str) -> bytes | None:
            nonlocal next_auth
            now = self._monotonic()
            if now < next_auth:
                return None
            try:
                active = await anyio.to_thread.run_sync(
                    self._principal_active, self._session_factory, principal
                )
            except Exception as exc:
                logger.warning(
                    "observation stream failure",
                    extra={
                        "request_id": request_id,
                        "tenant_id": principal.tenant_id,
                        "collector_id": principal.collector_id,
                        "source": principal.source,
                        "operation": operation,
                        "exception_type": type(exc).__name__,
                    },
                )
                return encode_sse_event(
                    event="stream_error",
                    data={
                        "code": UNAVAILABLE["code"],
                        "message": UNAVAILABLE["message"],
                        "request_id": request_id,
                    },
                )
            if not active:
                return encode_sse_event(
                    event="stream_closed",
                    data={
                        "code": "collector_authentication_inactive",
                        "message": "Collector authentication is no longer active.",
                        "request_id": request_id,
                    },
                )
            next_auth = now + self._settings.stream_auth_recheck_seconds
            return None

        try:
            ready_cursor = self._codec.encode(
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
            last_emit = self._monotonic()
            while True:
                if await request.is_disconnected():
                    return
                auth_event = await revalidate_if_due("auth_recheck")
                if auth_event is not None:
                    yield auth_event
                    return
                try:
                    page = await anyio.to_thread.run_sync(
                        self._scan_page,
                        self._session_factory,
                        principal.tenant_id,
                        principal.source,
                        position,
                        self._settings.stream_scan_batch_size,
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
                    auth_event = await revalidate_if_due("auth_recheck")
                    if auth_event is not None:
                        yield auth_event
                        return
                    emitted_pos = item.ingest_sequence
                    position = item.ingest_sequence
                    token = self._codec.encode(
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
                    last_emit = self._monotonic()
                if page.next_position > emitted_pos:
                    auth_event = await revalidate_if_due("auth_recheck")
                    if auth_event is not None:
                        yield auth_event
                        return
                    position = page.next_position
                    token = self._codec.encode(
                        position=position,
                        tenant_id=principal.tenant_id,
                        collector_id=principal.collector_id,
                        source=principal.source,
                    )
                    yield encode_sse_event(
                        event="checkpoint", event_id=token, data={"cursor": token}
                    )
                    last_emit = self._monotonic()
                if not page.has_more:
                    if self._monotonic() - last_emit >= self._settings.stream_heartbeat_seconds:
                        auth_event = await revalidate_if_due("auth_recheck")
                        if auth_event is not None:
                            yield auth_event
                            return
                        yield encode_sse_comment()
                        last_emit = self._monotonic()
                    await self._sleep(self._settings.stream_poll_interval_seconds)
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
            await self._lease.release()
