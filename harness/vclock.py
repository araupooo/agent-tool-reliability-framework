"""Deterministic virtual clock for the parallel tool replay harness.

The clock advances only to the next scheduled event tick. No wall-clock
time is read anywhere in the harness, so a recorded trace replays
byte-identically on any machine.
"""

from __future__ import annotations

import asyncio
import heapq
import itertools


class VirtualClock:
    """Integer-tick virtual clock with a deterministic event queue.

    Events are ordered by (tick, insertion sequence), so ties resolve in
    the order they were scheduled. The clock never reads wall-clock time.
    """

    def __init__(self) -> None:
        self.now: int = 0
        self._heap: list = []
        self._seq = itertools.count()

    def schedule(self, tick: int, kind: str, payload) -> int:
        seq = next(self._seq)
        heapq.heappush(self._heap, (tick, seq, kind, payload))
        return seq

    def peek(self):
        return self._heap[0] if self._heap else None

    def pop(self):
        return heapq.heappop(self._heap)

    def __len__(self) -> int:
        return len(self._heap)

    async def sleep(self, ticks: int) -> None:
        """Suspend the caller until ``ticks`` virtual ticks have elapsed."""
        if ticks <= 0:
            return
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self.schedule(self.now + ticks, "wake", fut)
        await fut
