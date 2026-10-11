"""Process-local task notifications for connected team members."""
from __future__ import annotations

import asyncio
import threading


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[int, set[asyncio.Queue]] = {}
        self._loops: dict[asyncio.Queue, asyncio.AbstractEventLoop] = {}
        self._lock = threading.Lock()

    def subscribe(self, user_id: int) -> asyncio.Queue:
        queue = asyncio.Queue(maxsize=16)
        loop = asyncio.get_running_loop()
        with self._lock:
            self._subscribers.setdefault(user_id, set()).add(queue)
            self._loops[queue] = loop
        return queue

    def unsubscribe(self, user_id: int, queue: asyncio.Queue) -> None:
        with self._lock:
            subscribers = self._subscribers.get(user_id)
            if subscribers is not None:
                subscribers.discard(queue)
                if not subscribers:
                    del self._subscribers[user_id]
            self._loops.pop(queue, None)

    def _deliver(self, queue: asyncio.Queue, event: dict) -> None:
        # Only run on this queue's event loop, including thread-pool publishers.
        with self._lock:
            if queue not in self._loops:
                return
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)

    def publish(self, user_id: int, event: dict) -> None:
        with self._lock:
            targets = [(queue, self._loops[queue])
                       for queue in self._subscribers.get(user_id, ())]
        for queue, loop in targets:
            try:
                loop.call_soon_threadsafe(self._deliver, queue, dict(event))
            except RuntimeError:
                # A connection's loop can close between snapshot and dispatch.
                self.unsubscribe(user_id, queue)


BUS = EventBus()


def notify_tasks_changed(user_id: int, reason: str, task_name: str) -> None:
    BUS.publish(user_id, {"type": "tasks-changed", "reason": reason,
                          "task_name": task_name})
