from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

REPLAY_BUFFER_SIZE = 5000


@dataclass(frozen=True)
class SequencedEvent:
    id: str
    seq: int
    event: dict[str, Any]


class EventBus:
    def __init__(self, buffer_size: int = REPLAY_BUFFER_SIZE) -> None:
        self._buffer_size = buffer_size
        self._boot_id = _base36(int(time.time() * 1000))
        self._seq = 0
        self._buffer: list[SequencedEvent] = []
        self._subscribers: set[tuple[asyncio.AbstractEventLoop, asyncio.Queue[SequencedEvent]]] = set()

    def publish(self, event: dict[str, Any]) -> SequencedEvent:
        self._seq += 1
        entry = SequencedEvent(id=f"{self._boot_id}-{self._seq}", seq=self._seq, event=event)
        self._buffer.append(entry)
        if len(self._buffer) > self._buffer_size * 2:
            self._buffer = self._buffer[-self._buffer_size :]

        dead: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue[SequencedEvent]]] = []
        for loop, queue in list(self._subscribers):
            if loop.is_closed():
                dead.append((loop, queue))
                continue
            try:
                loop.call_soon_threadsafe(queue.put_nowait, entry)
            except Exception:
                dead.append((loop, queue))
        for item in dead:
            self._subscribers.discard(item)
        return entry

    def subscribe(self) -> tuple[asyncio.Queue[SequencedEvent], callable]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[SequencedEvent] = asyncio.Queue()
        token = (loop, queue)
        self._subscribers.add(token)

        def unsubscribe() -> None:
            self._subscribers.discard(token)

        return queue, unsubscribe

    def last_event_id(self) -> str:
        return f"{self._boot_id}-{self._seq}"

    def replay_since(self, last_event_id: str) -> tuple[str, list[SequencedEvent]]:
        sep = last_event_id.rfind("-")
        if sep <= 0:
            return ("resync", [])
        boot_id = last_event_id[:sep]
        try:
            seq = int(last_event_id[sep + 1 :])
        except ValueError:
            return ("resync", [])
        if boot_id != self._boot_id or seq < 0 or seq > self._seq:
            return ("resync", [])
        oldest = self._buffer[0].seq if self._buffer else self._seq + 1
        if seq + 1 < oldest:
            return ("resync", [])
        return ("replay", [e for e in self._buffer if e.seq > seq])


def _base36(num: int) -> str:
    chars = "0123456789abcdefghijklmnopqrstuvwxyz"
    if num == 0:
        return "0"
    out: list[str] = []
    n = abs(num)
    while n:
        n, rem = divmod(n, 36)
        out.append(chars[rem])
    return "".join(reversed(out))


event_bus = EventBus()
