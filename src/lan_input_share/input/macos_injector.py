from __future__ import annotations

import importlib
import time
from collections.abc import Callable
from typing import Any

from lan_input_share.input.events import (
    InputEvent,
    KeyEvent,
    MouseButton,
    MouseButtonEvent,
    MouseMoveEvent,
    MouseWheelEvent,
    Point,
)
from lan_input_share.input.macos_keycodes import MODIFIERS, macos_keycode_from_event
from lan_input_share.input.macos_native import INJECTION_MARKER, load_quartz

_BUTTON_NUMBERS = {
    MouseButton.LEFT: 0,
    MouseButton.RIGHT: 1,
    MouseButton.MIDDLE: 2,
    MouseButton.X1: 3,
    MouseButton.X2: 4,
}


class MacOSInputInjector:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._quartz = load_quartz()
        self._source = self._quartz.CGEventSourceCreate(self._quartz.kCGEventSourceStatePrivate)
        if self._source is None:
            raise RuntimeError("无法创建 macOS 输入源")
        self._modifiers: set[int] = set()
        self._buttons: set[MouseButton] = set()
        self._wheel_remainder = [0, 0]
        self._caps_lock = False
        self._last_position: Point | None = None
        self._clock = clock
        self._double_click_interval = float(
            importlib.import_module("AppKit").NSEvent.doubleClickInterval()
        )
        self._clicks: dict[MouseButton, tuple[float, Point, int]] = {}

    def inject(self, event: InputEvent) -> None:
        q = self._quartz
        modifiers = self._modifiers.copy()
        buttons = self._buttons.copy()
        caps_lock = self._caps_lock
        clicks = self._clicks.copy()
        native: Any
        if isinstance(event, KeyEvent):
            try:
                code = macos_keycode_from_event(event)
            except ValueError:
                # Windows-only keys have no native Mac equivalent. This is an
                # unsupported input, not a broken session or malformed protocol.
                return
            native = q.CGEventCreateKeyboardEvent(self._source, code, event.is_down)
            if code in MODIFIERS:
                if event.is_down:
                    modifiers.add(code)
                else:
                    modifiers.discard(code)
                if native is not None:
                    q.CGEventSetType(native, q.kCGEventFlagsChanged)
            elif code == 57 and event.is_down:
                caps_lock = not caps_lock
        elif isinstance(event, MouseMoveEvent):
            if MouseButton.LEFT in buttons:
                kind, button = q.kCGEventLeftMouseDragged, 0
            elif MouseButton.RIGHT in buttons:
                kind, button = q.kCGEventRightMouseDragged, 1
            elif buttons:
                kind, button = q.kCGEventOtherMouseDragged, _BUTTON_NUMBERS[next(iter(buttons))]
            else:
                kind, button = q.kCGEventMouseMoved, 0
            native = q.CGEventCreateMouseEvent(
                self._source, kind, (event.position.x, event.position.y), button
            )
            clicks = {
                button: record
                for button, record in clicks.items()
                if _same_click_position(event.position, record[1])
            }
        elif isinstance(event, MouseButtonEvent):
            button = _BUTTON_NUMBERS[event.button]
            if button == 0:
                kind = q.kCGEventLeftMouseDown if event.is_down else q.kCGEventLeftMouseUp
            elif button == 1:
                kind = q.kCGEventRightMouseDown if event.is_down else q.kCGEventRightMouseUp
            else:
                kind = q.kCGEventOtherMouseDown if event.is_down else q.kCGEventOtherMouseUp
            if self._last_position is None:
                location = q.CGEventGetLocation(q.CGEventCreate(None))
                position = Point(round(location.x), round(location.y))
            else:
                position = self._last_position
            native = q.CGEventCreateMouseEvent(self._source, kind, (position.x, position.y), button)
            previous = clicks.get(event.button)
            click_count = previous[2] if previous is not None else 1
            if event.is_down:
                buttons.add(event.button)
                now = self._clock()
                click_count = (
                    previous[2] + 1
                    if previous is not None
                    and 0 <= now - previous[0] <= self._double_click_interval
                    and _same_click_position(position, previous[1])
                    else 1
                )
                clicks[event.button] = (now, position, click_count)
            else:
                buttons.discard(event.button)
            if native is not None:
                q.CGEventSetIntegerValueField(native, q.kCGMouseEventButtonNumber, button)
                q.CGEventSetIntegerValueField(native, q.kCGMouseEventClickState, click_count)
        elif isinstance(event, MouseWheelEvent):
            axis = int(event.horizontal)
            accumulated = self._wheel_remainder[axis] + event.delta
            lines = abs(accumulated) // 120 * (1 if accumulated >= 0 else -1)
            self._wheel_remainder[axis] = accumulated - lines * 120
            if lines == 0:
                return
            native = q.CGEventCreateScrollWheelEvent(
                self._source,
                q.kCGScrollEventUnitLine,
                2,
                0 if event.horizontal else lines,
                -lines if event.horizontal else 0,
            )
        else:
            raise TypeError(f"不支持的输入事件：{type(event).__name__}")
        if native is None:
            raise RuntimeError("无法创建 macOS 输入事件")
        flags = q.kCGEventFlagMaskAlphaShift if caps_lock else 0
        for code in modifiers:
            aggregate, side = MODIFIERS[code]
            flags |= aggregate | side
        q.CGEventSetFlags(native, flags)
        q.CGEventSetIntegerValueField(native, q.kCGEventSourceUserData, INJECTION_MARKER)
        q.CGEventPost(q.kCGHIDEventTap, native)
        self._modifiers, self._buttons, self._caps_lock = modifiers, buttons, caps_lock
        self._clicks = clicks
        if isinstance(event, MouseMoveEvent):
            self._last_position = event.position

    def release_all(self, events: tuple[InputEvent, ...]) -> None:
        error: Exception | None = None
        for event in events:
            try:
                self.inject(event)
            except Exception as exc:
                error = exc
        self._clicks.clear()
        self._wheel_remainder = [0, 0]
        self._last_position = None
        if error is not None:
            raise error


def _same_click_position(first: Point, second: Point) -> bool:
    return bool(abs(first.x - second.x) <= 4 and abs(first.y - second.y) <= 4)


__all__ = ["MacOSInputInjector"]
