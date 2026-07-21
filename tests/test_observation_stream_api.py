from types import SimpleNamespace

import pytest
from correlis_api.observation_stream import _bad_cursor, _check_token_text
from correlis_api.observation_stream_runtime import ObservationStreamRuntime
from correlis_api.stream_connections import ObservationStreamConnectionLimiter
from correlis_api.stream_cursor import ObservationStreamCursorCodec
from correlis_store import AuthenticatedCollectorPrincipal

PEPPER = "non-production-test-pepper-value-32-bytes"


class Request:
    async def is_disconnected(self):
        return False


class Observation:
    def __init__(self, value):
        self.value = value

    def model_dump(self, mode="json"):
        return {"value": self.value}


def item(sequence, value):
    return SimpleNamespace(ingest_sequence=sequence, observation=Observation(value))


@pytest.mark.parametrize("value", ["", "   ", " ocs1.bad", "ocs1.bad\n", "ocs1.bad\x7f"])
def test_static_cursor_validation_rejects_empty_and_malformed(value):
    with pytest.raises(type(_bad_cursor())):
        _check_token_text(value)


@pytest.mark.asyncio
async def test_runtime_revalidation_interrupts_prefetched_page():
    limiter = ObservationStreamConnectionLimiter(max_connections=1, max_connections_per_collector=1)
    lease = await limiter.try_acquire(tenant_id="tenant", collector_id="collector")
    principal = AuthenticatedCollectorPrincipal(
        tenant_id="tenant",
        collector_id="collector",
        collector_name="Collector",
        credential_id="credential",
        source="src",
    )
    now = 0.0
    active = [False]

    def monotonic():
        return now

    def principal_active(_session_factory, _principal):
        return active.pop(0)

    page = SimpleNamespace(
        observations=(item(1, "first"), item(2, "second"), item(3, "third")),
        next_position=3,
        has_more=False,
    )
    runtime = ObservationStreamRuntime(
        session_factory=lambda: None,
        settings=SimpleNamespace(
            stream_auth_recheck_seconds=5,
            stream_scan_batch_size=100,
            stream_heartbeat_seconds=30,
            stream_poll_interval_seconds=0,
        ),
        codec=ObservationStreamCursorCodec(PEPPER, nonce_factory=lambda: b"0" * 12),
        lease=lease,
        monotonic_clock=monotonic,
        sleep=lambda _delay: None,
        scan_page=lambda *_args: page,
        principal_active=principal_active,
    )
    events = runtime.events(
        request=Request(),
        principal=principal,
        request_id="request-id",
        starting_position=0,
        start_label="earliest",
    )
    assert b"event: ready" in await anext(events)
    assert b"event: observation" in await anext(events)
    now = 6.0
    closed = await anext(events)
    assert b"event: stream_closed" in closed
    assert b"second" not in closed
    with pytest.raises(StopAsyncIteration):
        await anext(events)
    assert (await limiter.snapshot()).total == 0
