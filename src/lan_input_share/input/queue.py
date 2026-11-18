from __future__ import annotations

import threading
from collections import deque

from lan_input_share.input.events import InputEvent, MouseMoveEvent


class HookEventQueue:
    """Non-blocking hook queue that drops stale moves, never discrete input."""

    def __init__(self, capacity: int = 512) -> None:
        if capacity <= 0:
            raise ValueError("capacity 必须为正数")
        self._capacity = capacity
        self._items: deque[InputEvent] = deque()
        self._lock = threading.Lock()

    def put(self, event: InputEvent, *, coalesce_mouse_moves: bool = True) -> None:
        with self._lock:
            if isinstance(event, MouseMoveEvent) and coalesce_mouse_moves:
                for index in range(len(self._items) - 1, -1, -1):
                    if isinstance(self._items[index], MouseMoveEvent):
                        self._items[index] = event
                        return
            if isinstance(event, MouseMoveEvent) and len(self._items) >= self._capacity:
                return
            self._items.append(event)

    def get_nowait(self) -> InputEvent:
        with self._lock:
            if not self._items:
                raise IndexError("输入队列为空")
            return self._items.popleft()

    def drain(self) -> tuple[InputEvent, ...]:
        with self._lock:
            result = tuple(self._items)
            self._items.clear()
            return result
