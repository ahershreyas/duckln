"""Plan 65 Phase 4 — In-memory message bus for harness agents.

Agents publish findings to topics; other agents subscribe. The bus keeps a
bounded history per topic so a subscriber arriving late can still see what
was published before it joined.

Asyncio-friendly: ``subscribe`` returns an async iterator that yields new
messages as they arrive AND replays history if the subscriber wants it.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator


@dataclass(frozen=True)
class Message:
    """One message published to the bus."""

    topic: str
    sender: str
    payload: Any
    timestamp: float


@dataclass
class _TopicQueue:
    """Per-topic state: bounded history + the set of live subscriber queues."""

    history: list[Message] = field(default_factory=list)
    subscribers: list[asyncio.Queue] = field(default_factory=list)


class MessageBus:
    """In-memory publish/subscribe.

    - ``publish`` is synchronous so non-async producers (tool handlers) can
      call it. Subscribers are async iterators that receive via per-subscriber
      asyncio.Queue.
    - History is bounded to ``max_history_per_topic`` (default 100). Older
      messages are evicted to keep memory tight.
    - ``close`` signals all subscribers to stop iterating.
    """

    def __init__(self, *, max_history_per_topic: int = 100) -> None:
        self._topics: dict[str, _TopicQueue] = {}
        self._max_history = max_history_per_topic
        self._closed = False
        self._lock = asyncio.Lock()

    def publish(self, topic: str, payload: Any, *, sender: str) -> Message:
        if self._closed:
            raise RuntimeError("MessageBus is closed")
        message = Message(topic=topic, sender=sender, payload=payload, timestamp=time.monotonic())
        tq = self._topics.setdefault(topic, _TopicQueue())
        tq.history.append(message)
        if len(tq.history) > self._max_history:
            tq.history = tq.history[-self._max_history:]
        # Fan out to live subscribers. put_nowait is safe because Queue has no
        # maxsize; we want non-blocking delivery so publish stays synchronous.
        for q in list(tq.subscribers):
            try:
                q.put_nowait(message)
            except Exception:
                pass
        return message

    def history(self, topic: str, *, limit: int = 50) -> tuple[Message, ...]:
        tq = self._topics.get(topic)
        if tq is None:
            return ()
        return tuple(tq.history[-limit:])

    def topics(self) -> tuple[str, ...]:
        return tuple(sorted(self._topics.keys()))

    async def subscribe(
        self,
        topic: str,
        *,
        replay_history: bool = True,
    ) -> AsyncIterator[Message]:
        """Yield messages for ``topic`` as an async iterator.

        Iteration stops when the bus is closed via ``close()``.
        """
        queue: asyncio.Queue = asyncio.Queue()
        async with self._lock:
            tq = self._topics.setdefault(topic, _TopicQueue())
            if replay_history:
                for m in tq.history:
                    queue.put_nowait(m)
            tq.subscribers.append(queue)
        try:
            while True:
                if self._closed and queue.empty():
                    return
                # Use wait_for so close() can unblock the iterator.
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    if self._closed:
                        return
                    continue
                yield msg
        finally:
            async with self._lock:
                tq = self._topics.get(topic)
                if tq is not None and queue in tq.subscribers:
                    tq.subscribers.remove(queue)

    def close(self) -> None:
        self._closed = True
