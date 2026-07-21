import asyncio

import pytest
from correlis_api.stream_connections import ObservationStreamConnectionLimiter


def test_constructor_invariants():
    ObservationStreamConnectionLimiter(max_connections=1, max_connections_per_collector=1)
    with pytest.raises(ValueError):
        ObservationStreamConnectionLimiter(max_connections=0, max_connections_per_collector=1)
    with pytest.raises(ValueError):
        ObservationStreamConnectionLimiter(max_connections=1, max_connections_per_collector=0)
    with pytest.raises(ValueError):
        ObservationStreamConnectionLimiter(max_connections=1, max_connections_per_collector=2)


@pytest.mark.asyncio
async def test_release_context_manager_and_snapshot():
    limiter = ObservationStreamConnectionLimiter(max_connections=2, max_connections_per_collector=1)
    lease = await limiter.try_acquire(tenant_id="tenant", collector_id="collector")
    assert lease is not None
    assert await limiter.try_acquire(tenant_id="tenant", collector_id="collector") is None
    snap = await limiter.snapshot()
    assert snap.total == 1
    assert snap.per_collector == {("tenant", "collector"): 1}
    await asyncio.gather(lease.release(), lease.release())
    assert (await limiter.snapshot()).total == 0
    async with (await limiter.try_acquire(tenant_id="tenant", collector_id="collector")):
        assert (await limiter.snapshot()).total == 1
    assert (await limiter.snapshot()).total == 0
