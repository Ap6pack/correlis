from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class ObservationStreamConnectionSnapshot:
    total: int
    per_collector: MappingProxyType[tuple[str, str], int]


class ObservationStreamConnectionLimiter:
    def __init__(self, *, max_connections: int, max_connections_per_collector: int):
        if max_connections < 1:
            raise ValueError("max_connections must be at least 1")
        if max_connections_per_collector < 1:
            raise ValueError("max_connections_per_collector must be at least 1")
        if max_connections_per_collector > max_connections:
            raise ValueError("max_connections_per_collector must not exceed max_connections")
        self.max_connections = max_connections
        self.max_connections_per_collector = max_connections_per_collector
        self._lock = asyncio.Lock()
        self._total = 0
        self._per: dict[tuple[str, str], int] = {}

    async def try_acquire(
        self, *, tenant_id: str, collector_id: str
    ) -> ObservationStreamLease | None:
        key = (tenant_id, collector_id)
        async with self._lock:
            if self._total >= self.max_connections:
                return None
            if self._per.get(key, 0) >= self.max_connections_per_collector:
                return None
            self._total += 1
            self._per[key] = self._per.get(key, 0) + 1
            return ObservationStreamLease(self, key)

    async def _release(self, key: tuple[str, str]) -> None:
        async with self._lock:
            if self._total > 0:
                self._total -= 1
            count = self._per.get(key, 0)
            if count <= 1:
                self._per.pop(key, None)
            else:
                self._per[key] = count - 1

    async def snapshot(self) -> ObservationStreamConnectionSnapshot:
        async with self._lock:
            return ObservationStreamConnectionSnapshot(
                total=self._total,
                per_collector=MappingProxyType(dict(self._per)),
            )


@dataclass(slots=True)
class ObservationStreamLease:
    _limiter: ObservationStreamConnectionLimiter
    _key: tuple[str, str]
    _released: bool = False

    async def release(self) -> None:
        if self._released:
            return
        self._released = True
        await self._limiter._release(self._key)

    async def __aenter__(self) -> ObservationStreamLease:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.release()
