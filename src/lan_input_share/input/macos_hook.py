from __future__ import annotations

import logging
import threading
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
    Suppression,
)
from lan_input_share.input.macos_keycodes import MODIFIERS, key_event_from_macos
from lan_input_share.input.macos_native import (
    INJECTION_MARKER,
    load_core_foundation,
    load_quartz,
    remote_anchor,
    restore_remote_cursor,
)

_STARTUP_TIMEOUT = 3.0
_LOGGER = logging.getLogger("lan_input_share.input")
_EVENT_TYPES = (1, 2, 3, 4, 5, 6, 7, 10, 11, 12, 22, 25, 26, 27)
_BUTTONS = {
    0: MouseButton.LEFT,
    1: MouseButton.RIGHT,
    2: MouseButton.MIDDLE,
    3: MouseButton.X1,
    4: MouseButton.X2,
}


class MacOSInputHook:
    def __init__(self) -> None:
        self._quartz = load_quartz()
        self._core = load_core_foundation()
        self._callback: Callable[[InputEvent], Suppression] | None = None
        self._remote_active = False
        self._recovery_pending = False
        self._pressed_modifiers: set[int] = set()
        self._local_keys: set[tuple[int, int, bool]] = set()
        self._local_buttons: set[MouseButton] = set()
        self._thread: threading.Thread | None = None
        self._stop_requested = threading.Event()
        self._ready = threading.Event()
        self._startup_error: BaseException | None = None
        self._tap: Any = None
        self._run_loop: Any = None

    @property
    def remote_active(self) -> bool:
        return self._remote_active

    def start(self, callback: Callable[[InputEvent], Suppression]) -> None:
        if self._callback is not None or (self._thread is not None and self._thread.is_alive()):
            raise RuntimeError("输入钩子已经启动")
        self._callback = callback
        self._ready.clear()
        self._stop_requested.clear()
        self._startup_error = None
        self._recovery_pending = False
        self._thread = threading.Thread(
            target=self._hook_thread, name="lan-input-share-mac-tap", daemon=True
        )
        self._thread.start()
        if not self._ready.wait(_STARTUP_TIMEOUT):
            self._stop_requested.set()
            self._callback = None
            self._remote_active = False
            self._stop_native()
            raise TimeoutError("安装 macOS 输入钩子超时")
        if self._startup_error is not None:
            error = self._startup_error
            self.stop()
            raise RuntimeError("安装 macOS 输入钩子失败，请检查辅助功能和输入监控权限") from error

    def stop(self) -> None:
        self._remote_active = False
        self._stop_requested.set()
        self._callback = None
        self._stop_native()
        try:
            thread = self._thread
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=3)
                if thread.is_alive():
                    raise TimeoutError("停止 macOS 输入钩子超时")
            self._thread = None
        finally:
            self._pressed_modifiers.clear()
            self._local_keys.clear()
            self._local_buttons.clear()
            self._recovery_pending = False
            restore_remote_cursor()

    def set_remote_active(self, active: bool) -> None:
        if not active:
            self._recovery_pending = False
        self._remote_active = (
            active and not self._recovery_pending and not self._stop_requested.is_set()
        )

    def _stop_native(self) -> None:
        tap = self._tap
        if tap is not None:
            self._quartz.CGEventTapEnable(tap, False)
        loop = self._run_loop
        if loop is not None:
            self._core.CFRunLoopStop(loop)

    def _hook_thread(self) -> None:
        tap = source = loop = None
        q, core = self._quartz, self._core
        try:
            tap = q.CGEventTapCreate(
                q.kCGSessionEventTap,
                q.kCGHeadInsertEventTap,
                q.kCGEventTapOptionDefault,
                sum(1 << kind for kind in _EVENT_TYPES),
                self._tap_callback,
                None,
            )
            if tap is None:
                raise PermissionError("Quartz 拒绝创建输入 tap")
            self._tap = tap
            if self._stop_requested.is_set():
                return
            source = core.CFMachPortCreateRunLoopSource(None, tap, 0)
            if source is None:
                raise RuntimeError("无法创建输入 run loop source")
            loop = core.CFRunLoopGetCurrent()
            self._run_loop = loop
            core.CFRunLoopAddSource(loop, source, core.kCFRunLoopCommonModes)
            self._pressed_modifiers = {
                code
                for code in MODIFIERS
                if q.CGEventSourceKeyState(q.kCGEventSourceStateCombinedSessionState, code)
            }
            if self._stop_requested.is_set():
                return
            q.CGEventTapEnable(tap, True)
            self._ready.set()
            # Short bounded iterations let a stop/timeout be noticed even if
            # CFRunLoopStop races the first iteration of the native loop.
            while not self._stop_requested.is_set():
                core.CFRunLoopRunInMode(core.kCFRunLoopDefaultMode, 0.05, False)
        except BaseException as exc:
            self._startup_error = exc
            self._recover_local(notify=self._ready.is_set())
        finally:
            self._ready.set()
            if tap is not None:
                q.CGEventTapEnable(tap, False)
                if source is not None and loop is not None:
                    core.CFRunLoopRemoveSource(loop, source, core.kCFRunLoopCommonModes)
                core.CFMachPortInvalidate(tap)
            self._tap = None
            self._run_loop = None

    def _tap_callback(self, proxy: Any, kind: int, native: Any, info: Any) -> Any:
        q = self._quartz
        if kind in (q.kCGEventTapDisabledByTimeout, q.kCGEventTapDisabledByUserInput):
            self._recover_local(notify=True)
            if self._tap is not None and not self._stop_requested.is_set():
                q.CGEventTapEnable(self._tap, True)
            return native
        if self._stop_requested.is_set() or native is None:
            return native
        if q.CGEventGetIntegerValueField(native, q.kCGEventSourceUserData) == INJECTION_MARKER:
            return native
        try:
            events = self._translate(kind, native)
            suppress = False
            for event in events:
                suppress = self._dispatch(event) is Suppression.SUPPRESS or suppress
            return None if suppress else native
        except Exception:
            _LOGGER.exception("macOS 输入 tap 已返回本机控制")
            self._recover_local(notify=True)
            return native

    def _translate(self, kind: int, native: Any) -> tuple[InputEvent, ...]:
        q = self._quartz
        if kind in (q.kCGEventKeyDown, q.kCGEventKeyUp, q.kCGEventFlagsChanged):
            code = q.CGEventGetIntegerValueField(native, q.kCGKeyboardEventKeycode)
            if kind == q.kCGEventFlagsChanged:
                if code == 57:
                    return (KeyEvent(58, 0x14, True), KeyEvent(58, 0x14, False))
                modifier = MODIFIERS.get(code)
                if modifier is None:
                    return ()
                flags = q.CGEventGetFlags(native)
                aggregate, side = modifier
                side_mask = sum(bits for group, bits in MODIFIERS.values() if group == aggregate)
                down = (
                    bool(flags & side)
                    if flags & side_mask
                    else bool(flags & aggregate) and code not in self._pressed_modifiers
                )
                if down:
                    self._pressed_modifiers.add(code)
                else:
                    self._pressed_modifiers.discard(code)
            else:
                down = kind == q.kCGEventKeyDown
            key = key_event_from_macos(code, down)
            return () if key is None else (key,)
        if kind in (
            q.kCGEventMouseMoved,
            q.kCGEventLeftMouseDragged,
            q.kCGEventRightMouseDragged,
            q.kCGEventOtherMouseDragged,
        ):
            anchor = remote_anchor() if self._remote_active else None
            if anchor is not None:
                dx = q.CGEventGetIntegerValueField(native, q.kCGMouseEventDeltaX)
                dy = q.CGEventGetIntegerValueField(native, q.kCGMouseEventDeltaY)
                if dx == 0 and dy == 0:
                    return ()
                position = Point(anchor.x + dx, anchor.y + dy)
            else:
                point = q.CGEventGetLocation(native)
                position = Point(round(point.x), round(point.y))
            return (MouseMoveEvent(position),)
        if kind in (
            q.kCGEventLeftMouseDown,
            q.kCGEventLeftMouseUp,
            q.kCGEventRightMouseDown,
            q.kCGEventRightMouseUp,
            q.kCGEventOtherMouseDown,
            q.kCGEventOtherMouseUp,
        ):
            if kind in (q.kCGEventLeftMouseDown, q.kCGEventLeftMouseUp):
                button = MouseButton.LEFT
            elif kind in (q.kCGEventRightMouseDown, q.kCGEventRightMouseUp):
                button = MouseButton.RIGHT
            else:
                number = q.CGEventGetIntegerValueField(native, q.kCGMouseEventButtonNumber)
                other_button = _BUTTONS.get(number)
                if other_button is None:
                    return ()
                button = other_button
            down = kind in (
                q.kCGEventLeftMouseDown,
                q.kCGEventRightMouseDown,
                q.kCGEventOtherMouseDown,
            )
            return (MouseButtonEvent(button, down),)
        if kind == q.kCGEventScrollWheel:
            continuous = q.CGEventGetIntegerValueField(native, q.kCGScrollWheelEventIsContinuous)
            fields = (
                (q.kCGScrollWheelEventPointDeltaAxis1, q.kCGScrollWheelEventPointDeltaAxis2)
                if continuous
                else (q.kCGScrollWheelEventDeltaAxis1, q.kCGScrollWheelEventDeltaAxis2)
            )
            wheel: list[InputEvent] = []
            for axis, field in enumerate(fields):
                delta = q.CGEventGetIntegerValueField(native, field) * (1 if continuous else 120)
                if axis == 1:
                    # Quartz positive axis2 scrolls left; the canonical Windows
                    # horizontal wheel uses positive values for scrolling right.
                    delta = -delta
                if delta:
                    wheel.append(MouseWheelEvent(delta, horizontal=axis == 1))
            return tuple(wheel)
        return ()

    def _dispatch(self, event: InputEvent) -> Suppression:
        local_release = False
        if isinstance(event, KeyEvent):
            identity = (event.scan_code, event.virtual_key, event.is_extended)
            local_release = not event.is_down and identity in self._local_keys
            if (
                event.virtual_key == 0x1B
                and event.is_down
                and self._pressed_modifiers.intersection((59, 62))
                and self._pressed_modifiers.intersection((58, 61))
            ):
                self._remote_active = False
                restore_remote_cursor()
                if self._callback is not None:
                    self._callback(event)
                return Suppression.PASS_THROUGH
        elif isinstance(event, MouseButtonEvent):
            local_release = not event.is_down and event.button in self._local_buttons
        callback = self._callback
        if callback is None:
            return Suppression.PASS_THROUGH
        requested = callback(event)
        if requested is Suppression.SUPPRESS_ALWAYS:
            return Suppression.SUPPRESS
        if local_release:
            self._remember_local(event)
            return Suppression.PASS_THROUGH
        if not self._remote_active:
            self._remember_local(event)
        return (
            Suppression.SUPPRESS
            if self._remote_active and requested is Suppression.SUPPRESS
            else Suppression.PASS_THROUGH
        )

    def _remember_local(self, event: InputEvent) -> None:
        if isinstance(event, KeyEvent):
            identity = (event.scan_code, event.virtual_key, event.is_extended)
            if event.is_down:
                self._local_keys.add(identity)
            else:
                self._local_keys.discard(identity)
        elif isinstance(event, MouseButtonEvent):
            if event.is_down:
                self._local_buttons.add(event.button)
            else:
                self._local_buttons.discard(event.button)

    def _recover_local(self, *, notify: bool) -> None:
        self._remote_active = False
        self._recovery_pending = True
        try:
            restore_remote_cursor()
        except Exception:
            _LOGGER.exception("macOS 光标恢复失败")
        callback = self._callback
        if notify and callback is not None:
            # Deliver the existing emergency chord so routing also releases the
            # peer's held input instead of only lifting native suppression.
            for event in (
                KeyEvent(29, 0x11, True),
                KeyEvent(56, 0x12, True),
                KeyEvent(1, 0x1B, True),
                KeyEvent(1, 0x1B, False),
                KeyEvent(56, 0x12, False),
                KeyEvent(29, 0x11, False),
            ):
                try:
                    callback(event)
                except Exception:
                    _LOGGER.exception("macOS 紧急返回事件未处理")
                    break


__all__ = ["MacOSInputHook"]
