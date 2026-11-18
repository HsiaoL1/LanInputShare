from __future__ import annotations

import asyncio
import importlib
import sys
import threading
from collections.abc import AsyncIterator, Callable
from types import SimpleNamespace
from typing import Any

import pytest

from lan_input_share.core.models import DeviceInfo, DeviceRole, ScreenInfo
from lan_input_share.input.events import (
    KeyEvent,
    MouseButton,
    MouseButtonEvent,
    MouseMoveEvent,
    MouseWheelEvent,
    Point,
    Suppression,
)
from lan_input_share.network.session import CloseReason, MessageReceived, SessionEvent, SessionState
from lan_input_share.protocol.messages import InputMessage
from lan_input_share.routing.controller import RoutingController
from lan_input_share.routing.layout import LinearLayout
from lan_input_share.runtime.client import ClientRuntime
from lan_input_share.runtime.controller import (
    ControllerRuntime,
    decode_input_event,
    encode_input_event,
)


class FakeQuartz:
    """Only the documented Quartz boundary is faked; backend logic stays real."""

    kCGHIDEventTap = 0
    kCGSessionEventTap = 1
    kCGHeadInsertEventTap = 0
    kCGEventTapOptionDefault = 0
    kCGEventKeyDown = 10
    kCGEventKeyUp = 11
    kCGEventFlagsChanged = 12
    kCGEventMouseMoved = 5
    kCGEventLeftMouseDown = 1
    kCGEventLeftMouseUp = 2
    kCGEventRightMouseDown = 3
    kCGEventRightMouseUp = 4
    kCGEventLeftMouseDragged = 6
    kCGEventRightMouseDragged = 7
    kCGEventOtherMouseDown = 25
    kCGEventOtherMouseUp = 26
    kCGEventOtherMouseDragged = 27
    kCGEventScrollWheel = 22
    kCGEventTapDisabledByTimeout = 0xFFFFFFFE
    kCGEventTapDisabledByUserInput = 0xFFFFFFFF
    kCGKeyboardEventKeycode = 9
    kCGEventSourceUserData = 42
    kCGMouseEventDeltaX = 4
    kCGMouseEventDeltaY = 5
    kCGMouseEventButtonNumber = 3
    kCGMouseEventClickState = 1
    kCGScrollWheelEventDeltaAxis1 = 11
    kCGScrollWheelEventDeltaAxis2 = 12
    kCGScrollWheelEventIsContinuous = 88
    kCGScrollWheelEventPointDeltaAxis1 = 96
    kCGScrollWheelEventPointDeltaAxis2 = 97
    kCGEventFlagMaskShift = 0x20000
    kCGEventFlagMaskControl = 0x40000
    kCGEventFlagMaskAlternate = 0x80000
    kCGEventFlagMaskCommand = 0x100000
    kCGEventFlagMaskAlphaShift = 0x10000
    kCGEventSourceStatePrivate = -1
    kCGEventSourceStateCombinedSessionState = 0
    kCGMouseButtonLeft = 0
    kCGMouseButtonRight = 1
    kCGMouseButtonCenter = 2
    kCGScrollEventUnitPixel = 0
    kCGScrollEventUnitLine = 1

    def __init__(self) -> None:
        self.posted: list[dict[str, Any]] = []
        self.cursor = Point(70, 90)
        self.calls: list[tuple[Any, ...]] = []
        self.tap: object | None = object()
        self.callback: Callable[..., Any] | None = None
        self.create_gate: threading.Event | None = None
        self.warp_error = False
        self.held_keys: set[int] = set()

    def CGEventTapCreate(self, *args: Any) -> object | None:
        assert args[:3] == (1, 0, 0), "session capture must work without root"
        assert args[3] == sum(
            1 << event for event in (1, 2, 3, 4, 5, 6, 7, 10, 11, 12, 22, 25, 26, 27)
        )
        assert args[5] is None
        self.callback = args[4]
        if self.create_gate is not None:
            self.create_gate.wait(1)
        return self.tap

    def CGEventTapEnable(self, tap: object, active: bool) -> None:
        assert tap is self.tap
        self.calls.append(("tap_enable", active))

    def CGEventGetIntegerValueField(self, event: dict[str, Any], field: int) -> int:
        return int(event.get("fields", {}).get(field, 0))

    def CGEventSetIntegerValueField(self, event: dict[str, Any], field: int, value: int) -> None:
        event.setdefault("fields", {})[field] = value

    def CGEventGetFlags(self, event: dict[str, Any]) -> int:
        return int(event.get("flags", 0))

    def CGEventSetFlags(self, event: dict[str, Any], value: int) -> None:
        event["flags"] = value

    def CGEventSetType(self, event: dict[str, Any], value: int) -> None:
        event["type"] = value

    def CGEventGetLocation(self, event: dict[str, Any]) -> SimpleNamespace:
        point = event.get("position", self.cursor)
        return SimpleNamespace(x=point.x, y=point.y)

    def CGEventCreate(self, source: object | None) -> dict[str, Any]:
        return {"position": self.cursor}

    def CGEventSourceCreate(self, state: int) -> object:
        assert state == -1
        return object()

    def CGEventSourceKeyState(self, state: int, key: int) -> bool:
        assert state == 0
        return key in self.held_keys

    def CGEventCreateKeyboardEvent(self, source: object, key: int, down: bool) -> dict[str, Any]:
        assert source is not None
        return {"keycode": key, "type": 10 if down else 11, "fields": {9: key}}

    def CGEventCreateMouseEvent(
        self, source: object, kind: int, point: tuple[int, int], button: int
    ) -> dict[str, Any]:
        assert source is not None
        return {"type": kind, "position": Point(*point), "button": button}

    def CGEventCreateScrollWheelEvent(
        self, source: object, units: int, count: int, *axes: int
    ) -> dict[str, Any]:
        assert source is not None and count == 2 and len(axes) == 2
        return {"type": 22, "units": units, "axes": axes}

    def CGEventPost(self, tap: int, event: dict[str, Any]) -> None:
        assert tap == 0
        self.posted.append(event)

    def CGMainDisplayID(self) -> int:
        return 17

    def CGDisplayBounds(self, display: int) -> SimpleNamespace:
        assert display == 17
        return SimpleNamespace(
            origin=SimpleNamespace(x=0, y=0), size=SimpleNamespace(width=1440, height=900)
        )

    def CGDisplayHideCursor(self, display: int) -> int:
        self.calls.append(("hide",))
        raise AssertionError("a background tray app must not hide the cursor")

    def CGDisplayShowCursor(self, display: int) -> int:
        self.calls.append(("show",))
        raise AssertionError("a background tray app must not alter the hide count")

    def CGAssociateMouseAndMouseCursorPosition(self, active: bool) -> int:
        self.calls.append(("associate", active))
        raise AssertionError("a background tray app must not disassociate the cursor")

    def CGWarpMouseCursorPosition(self, point: tuple[int, int]) -> int:
        self.calls.append(("warp", point))
        if self.warp_error:
            return 1000
        self.cursor = Point(*point)
        return 0

    def emit(
        self,
        kind: int,
        *,
        code: int = 0,
        flags: int = 0,
        fields: dict[int, int] | None = None,
        position: Point | None = None,
    ) -> tuple[dict[str, Any], Any]:
        event = {
            "fields": {9: code, **(fields or {})},
            "flags": flags,
            "position": position or self.cursor,
        }
        assert self.callback is not None
        return event, self.callback(None, kind, event, None)


class FakeCoreFoundation:
    kCFRunLoopCommonModes = "common"
    kCFRunLoopDefaultMode = "default"

    def __init__(self, quartz: FakeQuartz) -> None:
        self.quartz = quartz
        self.loop = threading.Event()
        self.invalidated = False

    def CFMachPortCreateRunLoopSource(self, allocator: object, tap: object, order: int) -> object:
        assert allocator is None and tap is self.quartz.tap and order == 0
        return object()

    def CFRunLoopGetCurrent(self) -> threading.Event:
        return self.loop

    def CFRunLoopAddSource(self, loop: object, source: object, mode: str) -> None:
        assert loop is self.loop and source is not None and mode == "common"

    def CFRunLoopRunInMode(self, mode: str, interval: float, return_after_source: bool) -> int:
        assert mode == "default" and interval <= 0.1 and return_after_source is False
        self.loop.wait(interval)
        return 1

    def CFRunLoopStop(self, loop: threading.Event) -> None:
        assert loop is self.loop
        self.loop.set()

    def CFRunLoopRemoveSource(self, loop: object, source: object, mode: str) -> None:
        assert loop is self.loop and source is not None and mode == "common"
        self.quartz.calls.append(("source_remove",))

    def CFMachPortInvalidate(self, tap: object) -> None:
        assert tap is self.quartz.tap
        self.invalidated = True


@pytest.fixture
def native(monkeypatch: pytest.MonkeyPatch) -> tuple[FakeQuartz, FakeCoreFoundation]:
    quartz = FakeQuartz()
    core = FakeCoreFoundation(quartz)
    monkeypatch.setitem(sys.modules, "Quartz", quartz)
    monkeypatch.setitem(sys.modules, "CoreFoundation", core)
    monkeypatch.setitem(
        sys.modules,
        "AppKit",
        SimpleNamespace(
            NSEvent=SimpleNamespace(doubleClickInterval=lambda: 0.3),
        ),
    )
    return quartz, core


@pytest.mark.parametrize(
    ("apple", "expected"),
    [
        (0, KeyEvent(30, 0x41, True)),
        (8, KeyEvent(46, 0x43, True)),
        (9, KeyEvent(47, 0x56, True)),
        (53, KeyEvent(1, 0x1B, True)),
        (123, KeyEvent(75, 0x25, True, is_extended=True)),
        (76, KeyEvent(28, 0x0D, True, is_extended=True)),
        (56, KeyEvent(42, 0x10, True)),
        (60, KeyEvent(54, 0x10, True)),
        (59, KeyEvent(29, 0x11, True)),
        (62, KeyEvent(29, 0x11, True, is_extended=True)),
        (58, KeyEvent(56, 0x12, True)),
        (61, KeyEvent(56, 0x12, True, is_extended=True)),
        (55, KeyEvent(91, 0x5B, True, is_extended=True)),
        (54, KeyEvent(92, 0x5C, True, is_extended=True)),
        (124, KeyEvent(77, 0x27, True, is_extended=True)),
        (117, KeyEvent(83, 0x2E, True, is_extended=True)),
        (96, KeyEvent(63, 0x74, True)),
        (79, KeyEvent(105, 0x81, True)),
        (80, KeyEvent(106, 0x82, True)),
        (90, KeyEvent(107, 0x83, True)),
    ],
)
def test_key_codes_use_canonical_scancodes_without_swapping_modifiers(
    apple: int, expected: KeyEvent
) -> None:
    codes = importlib.import_module("lan_input_share.input.macos_keycodes")
    assert codes.key_event_from_macos(apple, True) == expected
    assert codes.macos_keycode_from_event(expected) == apple


def test_unsupported_key_mapping_is_explicit() -> None:
    codes = importlib.import_module("lan_input_share.input.macos_keycodes")
    assert codes.key_event_from_macos(255, True) is None
    with pytest.raises(ValueError):
        codes.macos_keycode_from_event(KeyEvent(0, 0xFF, True))


def test_native_tap_suppresses_remote_events_and_balances_stop(
    native: tuple[FakeQuartz, FakeCoreFoundation],
) -> None:
    quartz, core = native
    hook = importlib.import_module("lan_input_share.input.macos_hook").MacOSInputHook()
    seen: list[Any] = []
    hook.start(lambda event: seen.append(event) or Suppression.SUPPRESS)
    try:
        event, result = quartz.emit(10, code=0)
        assert result is event
        hook.set_remote_active(True)
        _, result = quartz.emit(11, code=0)
        assert result is not None  # The key was pressed locally before the switch.
        _, result = quartz.emit(10, code=8)
        assert result is None
        assert seen == [KeyEvent(30, 65, True), KeyEvent(30, 65, False), KeyEvent(46, 67, True)]
    finally:
        hook.stop()
        hook.stop()
    assert core.invalidated and not hook.remote_active
    assert ("tap_enable", False) in quartz.calls
    assert ("source_remove",) in quartz.calls


def test_both_sides_of_modifiers_release_independently(
    native: tuple[FakeQuartz, FakeCoreFoundation],
) -> None:
    quartz, _ = native
    hook = importlib.import_module("lan_input_share.input.macos_hook").MacOSInputHook()
    seen: list[Any] = []
    hook.start(lambda event: seen.append(event) or Suppression.SUPPRESS)
    try:
        hook.set_remote_active(True)
        quartz.emit(12, code=59, flags=0x40001)
        quartz.emit(12, code=62, flags=0x42001)
        quartz.emit(12, code=59, flags=0x42000)
        quartz.emit(12, code=58, flags=0xC2020)
        _, escaped = quartz.emit(10, code=53)
        assert escaped is not None and not hook.remote_active
        quartz.emit(12, code=62, flags=0x80020)
        assert seen[:3] == [
            KeyEvent(29, 17, True),
            KeyEvent(29, 17, True, is_extended=True),
            KeyEvent(29, 17, False),
        ]
        assert seen[-1] == KeyEvent(29, 17, False, is_extended=True)
    finally:
        hook.stop()


def test_modifier_fallback_without_device_flags_tracks_side_toggles(
    native: tuple[FakeQuartz, FakeCoreFoundation],
) -> None:
    quartz, _ = native
    hook = importlib.import_module("lan_input_share.input.macos_hook").MacOSInputHook()
    seen: list[Any] = []
    hook.start(lambda event: seen.append(event) or Suppression.PASS_THROUGH)
    try:
        for code, flags in [(56, 0x20000), (60, 0x20000), (56, 0x20000), (60, 0)]:
            quartz.emit(12, code=code, flags=flags)
        assert seen == [
            KeyEvent(42, 16, True),
            KeyEvent(54, 16, True),
            KeyEvent(42, 16, False),
            KeyEvent(54, 16, False),
        ]
    finally:
        hook.stop()


def test_caps_lock_flags_changed_produces_complete_press_and_release(
    native: tuple[FakeQuartz, FakeCoreFoundation],
) -> None:
    quartz, _ = native
    hook = importlib.import_module("lan_input_share.input.macos_hook").MacOSInputHook()
    seen: list[Any] = []
    hook.start(lambda event: seen.append(event) or Suppression.PASS_THROUGH)
    try:
        quartz.emit(12, code=57, flags=0x10000)
        quartz.emit(12, code=57, flags=0)
        assert seen == [
            KeyEvent(58, 20, True),
            KeyEvent(58, 20, False),
            KeyEvent(58, 20, True),
            KeyEvent(58, 20, False),
        ]
    finally:
        hook.stop()


def test_local_paste_can_be_suppressed_but_emergency_chord_always_passes(
    native: tuple[FakeQuartz, FakeCoreFoundation],
) -> None:
    quartz, _ = native
    hook = importlib.import_module("lan_input_share.input.macos_hook").MacOSInputHook()
    hook.start(lambda _event: Suppression.SUPPRESS_ALWAYS)
    try:
        assert quartz.emit(10, code=9)[1] is None
        hook.set_remote_active(True)
        quartz.emit(12, code=59, flags=0x40001)
        quartz.emit(12, code=58, flags=0xC0021)
        assert quartz.emit(10, code=53)[1] is not None
        assert not hook.remote_active
    finally:
        hook.stop()


def test_drag_buttons_and_both_wheel_axes_are_translated(
    native: tuple[FakeQuartz, FakeCoreFoundation],
) -> None:
    quartz, _ = native
    hook = importlib.import_module("lan_input_share.input.macos_hook").MacOSInputHook()
    seen: list[Any] = []
    hook.start(lambda event: seen.append(event) or Suppression.PASS_THROUGH)
    try:
        quartz.emit(6, position=Point(80, 92))
        quartz.emit(25, fields={3: 3})
        quartz.emit(26, fields={3: 3})
        quartz.emit(22, fields={11: 2, 12: -1})
        assert seen == [
            MouseMoveEvent(Point(80, 92)),
            MouseButtonEvent(MouseButton.X1, True),
            MouseButtonEvent(MouseButton.X1, False),
            MouseWheelEvent(240),
            MouseWheelEvent(120, horizontal=True),
        ]
    finally:
        hook.stop()


def test_continuous_wheel_preserves_small_deltas(
    native: tuple[FakeQuartz, FakeCoreFoundation],
) -> None:
    quartz, _ = native
    hook = importlib.import_module("lan_input_share.input.macos_hook").MacOSInputHook()
    seen: list[Any] = []
    hook.start(lambda event: seen.append(event) or Suppression.PASS_THROUGH)
    try:
        quartz.emit(22, fields={88: 1, 96: -3, 97: 2})
        assert seen == [MouseWheelEvent(-3), MouseWheelEvent(-2, horizontal=True)]
    finally:
        hook.stop()


def test_logical_cursor_anchor_preserves_first_move_and_repeated_recenters(
    native: tuple[FakeQuartz, FakeCoreFoundation],
) -> None:
    quartz, _ = native
    cursor = importlib.import_module("lan_input_share.input.macos_cursor").MacOSCursorController()
    hook = importlib.import_module("lan_input_share.input.macos_hook").MacOSInputHook()
    seen: list[Any] = []

    def capture(event: Any) -> Suppression:
        seen.append(event)
        cursor.recenter_remote()
        return Suppression.SUPPRESS

    hook.start(capture)
    try:
        assert cursor.confine_for_remote() == Point(720, 450)
        cursor.confine_for_remote()
        hook.set_remote_active(True)
        # The still-associated cursor may already be saturated at an edge.
        # Routing must use raw deltas around the anchor, never that position.
        quartz.emit(5, fields={4: 3, 5: -2}, position=Point(1439, 899))
        quartz.emit(5, fields={4: 0, 5: 0})
        quartz.emit(27, fields={4: -5, 5: 4})
        assert seen == [MouseMoveEvent(Point(723, 448)), MouseMoveEvent(Point(715, 454))]
        cursor.restore_local()
        cursor.restore_local()
        assert cursor.position() == Point(70, 90)
        assert all(call[0] == "warp" or call[0] == "tap_enable" for call in quartz.calls)
    finally:
        hook.stop()
        cursor.restore_local()


def test_cursor_restore_warp_failure_preserves_retry_without_foreground_calls(
    native: tuple[FakeQuartz, FakeCoreFoundation],
) -> None:
    quartz, _ = native
    cursor = importlib.import_module("lan_input_share.input.macos_cursor").MacOSCursorController()
    cursor.confine_for_remote()
    quartz.warp_error = True
    with pytest.raises(OSError):
        cursor.restore_local(Point(10, 20))
    assert all(call[0] == "warp" for call in quartz.calls)
    quartz.warp_error = False
    cursor.restore_local()
    assert cursor.position() == Point(10, 20)


def test_tap_disable_forces_local_recovery_before_reenabling(
    native: tuple[FakeQuartz, FakeCoreFoundation],
) -> None:
    quartz, _ = native
    cursor = importlib.import_module("lan_input_share.input.macos_cursor").MacOSCursorController()
    hook = importlib.import_module("lan_input_share.input.macos_hook").MacOSInputHook()
    seen: list[Any] = []
    hook.start(lambda event: seen.append(event) or Suppression.SUPPRESS)
    try:
        cursor.confine_for_remote()
        hook.set_remote_active(True)
        quartz.emit(quartz.kCGEventTapDisabledByTimeout)
        assert not hook.remote_active and cursor.position() == Point(70, 90)
        assert KeyEvent(1, 0x1B, True) in seen
        assert quartz.calls[-1] == ("tap_enable", True)
    finally:
        hook.stop()
        cursor.restore_local()


def test_tap_creation_failure_clears_callback_and_can_retry(
    native: tuple[FakeQuartz, FakeCoreFoundation],
) -> None:
    quartz, _ = native
    hook = importlib.import_module("lan_input_share.input.macos_hook").MacOSInputHook()
    quartz.tap = None
    with pytest.raises(RuntimeError, match="输入钩子"):
        hook.start(lambda _event: Suppression.SUPPRESS)
    quartz.tap = object()
    hook.start(lambda _event: Suppression.PASS_THROUGH)
    hook.stop()


def test_tap_start_timeout_cleans_up_late_created_tap(
    native: tuple[FakeQuartz, FakeCoreFoundation], monkeypatch: pytest.MonkeyPatch
) -> None:
    quartz, core = native
    module = importlib.import_module("lan_input_share.input.macos_hook")
    monkeypatch.setattr(module, "_STARTUP_TIMEOUT", 0.02)
    quartz.create_gate = threading.Event()
    hook = module.MacOSInputHook()
    with pytest.raises(TimeoutError):
        hook.start(lambda _event: Suppression.SUPPRESS)
    quartz.create_gate.set()
    hook.stop()
    assert core.invalidated and ("tap_enable", True) not in quartz.calls


def test_injection_applies_modifier_flags_to_keys_and_releases_both_sides(
    native: tuple[FakeQuartz, FakeCoreFoundation],
) -> None:
    quartz, _ = native
    injector = importlib.import_module("lan_input_share.input.macos_injector").MacOSInputInjector()
    injector.inject(KeyEvent(91, 0x5B, True, is_extended=True))
    injector.inject(KeyEvent(92, 0x5C, True, is_extended=True))
    injector.inject(KeyEvent(91, 0x5B, False, is_extended=True))
    injector.inject(KeyEvent(47, 0x56, True))
    injector.release_all((KeyEvent(92, 0x5C, False, is_extended=True), KeyEvent(47, 0x56, False)))
    assert [event["keycode"] for event in quartz.posted] == [55, 54, 55, 9, 54, 9]
    assert [event["flags"] & 0x100000 for event in quartz.posted] == [0x100000] * 4 + [0, 0]
    assert quartz.posted[0]["type"] == 12
    assert quartz.posted[3]["type"] == 10
    assert all(event["fields"][42] != 0 for event in quartz.posted)


@pytest.mark.asyncio
async def test_mac_to_mac_input_keeps_command_and_mouse_events(
    native: tuple[FakeQuartz, FakeCoreFoundation],
) -> None:
    quartz, _ = native
    mac_hook = importlib.import_module("lan_input_share.input.macos_hook").MacOSInputHook()
    mac_cursor = importlib.import_module(
        "lan_input_share.input.macos_cursor"
    ).MacOSCursorController()
    mac_injector = importlib.import_module(
        "lan_input_share.input.macos_injector"
    ).MacOSInputInjector()
    local = DeviceInfo(
        "local", "Mac 1", DeviceRole.CONTROLLER, ScreenInfo(1440, 900), platform="darwin"
    )
    peer = DeviceInfo("peer", "Mac 2", DeviceRole.CLIENT, ScreenInfo(1440, 900), platform="darwin")
    routing = RoutingController(
        "local", {"local": local, "peer": peer}, LinearLayout(("local", "peer"))
    )

    class Session:
        session_id = "mac-session"
        state = SessionState.ACTIVE

        def __init__(self) -> None:
            self.peer = peer
            self.sent: list[InputMessage] = []

        async def send(self, message: InputMessage) -> None:
            self.sent.append(message)

    session = Session()
    runtime = ControllerRuntime(routing, mac_hook, mac_cursor, local_platform="darwin")
    runtime.attach_session(session)
    routing.activate("peer")
    native_events = (
        (quartz.kCGEventFlagsChanged, {"fields": {9: 55}, "flags": 0x100008}),
        (quartz.kCGEventKeyDown, {"fields": {9: 8}}),
        (quartz.kCGEventMouseMoved, {"position": Point(300, 250)}),
        (quartz.kCGEventLeftMouseDown, {}),
        (quartz.kCGEventScrollWheel, {"fields": {11: 1}}),
    )
    for kind, event in native_events:
        for translated in mac_hook._translate(kind, event):
            await runtime._execute_actions(routing.handle_input(translated))
    for message in session.sent:
        mac_injector.inject(decode_input_event(message.payload))

    assert [event["keycode"] for event in quartz.posted[:2]] == [55, 8]
    assert quartz.posted[1]["flags"] & quartz.kCGEventFlagMaskCommand
    assert quartz.posted[2]["position"] == Point(300, 250)
    assert quartz.posted[3]["type"] == quartz.kCGEventLeftMouseDown
    assert quartz.posted[4]["axes"] == (1, 0)


def test_injected_marker_prevents_hook_feedback(
    native: tuple[FakeQuartz, FakeCoreFoundation],
) -> None:
    quartz, _ = native
    hook = importlib.import_module("lan_input_share.input.macos_hook").MacOSInputHook()
    injector = importlib.import_module("lan_input_share.input.macos_injector").MacOSInputInjector()
    seen: list[Any] = []
    hook.start(lambda event: seen.append(event) or Suppression.SUPPRESS_ALWAYS)
    try:
        injector.inject(KeyEvent(46, 0x43, True))
        event = quartz.posted[-1]
        assert quartz.callback is not None
        assert quartz.callback(None, 10, event, None) is event
        assert seen == []
    finally:
        hook.stop()


def test_injection_generates_drags_and_fractional_wheel_events(
    native: tuple[FakeQuartz, FakeCoreFoundation],
) -> None:
    quartz, _ = native
    injector = importlib.import_module("lan_input_share.input.macos_injector").MacOSInputInjector()
    injector.inject(MouseButtonEvent(MouseButton.LEFT, True))
    injector.inject(MouseMoveEvent(Point(400, 250)))
    injector.inject(MouseButtonEvent(MouseButton.LEFT, False))
    injector.inject(MouseWheelEvent(60, horizontal=True))
    injector.inject(MouseWheelEvent(60, horizontal=True))
    injector.inject(MouseButtonEvent(MouseButton.X2, True))
    assert quartz.posted[1]["type"] == 6 and quartz.posted[1]["position"] == Point(400, 250)
    assert quartz.posted[3]["axes"] == (0, -1)
    assert quartz.posted[4]["button"] == 4
    assert quartz.posted[4]["type"] == 25


def test_cross_platform_queue_module_preserves_discrete_input() -> None:
    queue_module = importlib.import_module("lan_input_share.input.queue")
    queue = queue_module.HookEventQueue(1)
    queue.put(MouseMoveEvent(Point(1, 1)))
    queue.put(KeyEvent(30, 65, True))
    queue.put(MouseMoveEvent(Point(2, 2)))
    assert queue.drain() == (MouseMoveEvent(Point(2, 2)), KeyEvent(30, 65, True))


def test_mouse_button_uses_last_injected_position_before_os_processes_move(
    native: tuple[FakeQuartz, FakeCoreFoundation],
) -> None:
    quartz, _ = native
    injector = importlib.import_module("lan_input_share.input.macos_injector").MacOSInputInjector()
    injector.inject(MouseMoveEvent(Point(400, 250)))
    injector.inject(MouseButtonEvent(MouseButton.LEFT, True))
    assert quartz.posted[-1]["position"] == Point(400, 250)


def test_double_click_sets_same_click_count_on_down_and_up(
    native: tuple[FakeQuartz, FakeCoreFoundation],
) -> None:
    quartz, _ = native
    now = [10.0]
    injector = importlib.import_module("lan_input_share.input.macos_injector").MacOSInputInjector(
        clock=lambda: now[0]
    )
    injector.inject(MouseButtonEvent(MouseButton.LEFT, True))
    injector.inject(MouseButtonEvent(MouseButton.LEFT, False))
    now[0] = 10.2
    injector.inject(MouseButtonEvent(MouseButton.LEFT, True))
    injector.inject(MouseButtonEvent(MouseButton.LEFT, False))
    assert [event["fields"][1] for event in quartz.posted] == [1, 1, 2, 2]


def test_click_count_resets_after_system_interval_and_pointer_moves(
    native: tuple[FakeQuartz, FakeCoreFoundation],
) -> None:
    quartz, _ = native
    now = [10.0]
    injector = importlib.import_module("lan_input_share.input.macos_injector").MacOSInputInjector(
        clock=lambda: now[0]
    )
    injector.inject(MouseButtonEvent(MouseButton.LEFT, True))
    injector.inject(MouseButtonEvent(MouseButton.LEFT, False))
    now[0] = 10.4
    injector.inject(MouseButtonEvent(MouseButton.LEFT, True))
    injector.inject(MouseButtonEvent(MouseButton.LEFT, False))
    now[0] = 10.5
    injector.inject(MouseMoveEvent(Point(90, 90)))
    injector.inject(MouseMoveEvent(Point(70, 90)))
    injector.inject(MouseButtonEvent(MouseButton.LEFT, True))
    assert [event["fields"][1] for event in quartz.posted if event["type"] in (1, 2)] == [
        1,
        1,
        1,
        1,
        1,
    ]


def test_cursor_parks_and_restores_in_background_without_changing_association(
    native: tuple[FakeQuartz, FakeCoreFoundation],
) -> None:
    quartz, _ = native
    cursor = importlib.import_module("lan_input_share.input.macos_cursor").MacOSCursorController()
    assert cursor.confine_for_remote() == Point(720, 450)
    cursor.recenter_remote()
    cursor.restore_local()
    assert cursor.position() == Point(70, 90)
    assert quartz.calls == [
        ("warp", (720, 450)),
        ("warp", (720, 450)),
        ("warp", (70, 90)),
    ]


def test_hook_stop_retries_failed_cursor_restore_warp(
    native: tuple[FakeQuartz, FakeCoreFoundation],
) -> None:
    quartz, _ = native
    cursor = importlib.import_module("lan_input_share.input.macos_cursor").MacOSCursorController()
    hook = importlib.import_module("lan_input_share.input.macos_hook").MacOSInputHook()
    hook.start(lambda _event: Suppression.PASS_THROUGH)
    cursor.confine_for_remote()
    quartz.warp_error = True
    with pytest.raises(OSError):
        cursor.restore_local()
    quartz.warp_error = False
    hook.stop()
    assert quartz.calls.count(("warp", (70, 90))) == 2
    assert cursor.position() == Point(70, 90)


def test_callback_exception_returns_local_and_releases_cursor(
    native: tuple[FakeQuartz, FakeCoreFoundation],
) -> None:
    quartz, _ = native
    cursor = importlib.import_module("lan_input_share.input.macos_cursor").MacOSCursorController()
    hook = importlib.import_module("lan_input_share.input.macos_hook").MacOSInputHook()

    def failing_callback(_event: Any) -> Suppression:
        raise RuntimeError("consumer failed")

    hook.start(failing_callback)
    try:
        cursor.confine_for_remote()
        hook.set_remote_active(True)
        event, result = quartz.emit(10, code=0)
        assert result is event
        assert not hook.remote_active and cursor.position() == Point(70, 90)
    finally:
        hook.stop()
        cursor.restore_local()


@pytest.mark.parametrize(
    "unsupported",
    [
        KeyEvent(55, 0x2C, True, is_extended=True),
        KeyEvent(93, 0x5D, True, is_extended=True),
    ],
)
def test_injector_ignores_windows_only_key_and_next_key_still_works(
    native: tuple[FakeQuartz, FakeCoreFoundation],
    unsupported: KeyEvent,
) -> None:
    quartz, _ = native
    injector = importlib.import_module("lan_input_share.input.macos_injector").MacOSInputInjector()
    injector.inject(unsupported)
    injector.inject(KeyEvent(30, 0x41, True))
    assert [event["keycode"] for event in quartz.posted] == [0]


class NativeClientSession:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[SessionEvent] = asyncio.Queue()
        self.closed: list[CloseReason] = []

    async def events(self) -> AsyncIterator[SessionEvent]:
        while True:
            yield await self.queue.get()

    async def close(self, reason: CloseReason) -> None:
        self.closed.append(reason)


@pytest.mark.asyncio
async def test_client_session_keeps_processing_after_windows_only_key(
    native: tuple[FakeQuartz, FakeCoreFoundation],
) -> None:
    quartz, _ = native
    session = NativeClientSession()
    injector = importlib.import_module("lan_input_share.input.macos_injector").MacOSInputInjector()
    runtime = ClientRuntime(session, injector)
    await runtime.start()
    try:
        for sequence, key in enumerate(
            (KeyEvent(93, 0x5D, True, is_extended=True), KeyEvent(30, 0x41, True))
        ):
            await session.queue.put(
                MessageReceived(InputMessage("controller", sequence, encode_input_event(key)))
            )
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert session.closed == []
        assert [event["keycode"] for event in quartz.posted] == [0]
    finally:
        await runtime.stop()


@pytest.mark.asyncio
async def test_client_session_still_rejects_malformed_input(
    native: tuple[FakeQuartz, FakeCoreFoundation],
) -> None:
    session = NativeClientSession()
    injector = importlib.import_module("lan_input_share.input.macos_injector").MacOSInputInjector()
    runtime = ClientRuntime(session, injector)
    await runtime.start()
    await session.queue.put(MessageReceived(InputMessage("controller", 1, {"kind": "invalid"})))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert session.closed == [CloseReason.PROTOCOL_ERROR]
    await runtime.stop()
