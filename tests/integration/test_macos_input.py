from __future__ import annotations

import os
import sys
import time

import pytest

from lan_input_share.input.events import KeyEvent, Point, Suppression
from lan_input_share.input.macos_cursor import MacOSCursorController
from lan_input_share.input.macos_hook import MacOSInputHook
from lan_input_share.input.macos_injector import MacOSInputInjector
from lan_input_share.input.macos_native import load_quartz

pytestmark = [
    pytest.mark.macos_integration,
    pytest.mark.skipif(
        sys.platform != "darwin" or os.environ.get("LAN_INPUT_SHARE_RUN_MACOS_INTEGRATION") != "1",
        reason="requires an explicitly enabled interactive Mac with input permissions",
    ),
]


def test_native_cursor_restores_after_repeated_handoff_and_shutdown() -> None:
    quartz = load_quartz()
    cursor = MacOSCursorController()
    original = cursor.position()
    bounds = quartz.CGDisplayBounds(quartz.CGMainDisplayID())
    expected_anchor = Point(
        round(bounds.origin.x + bounds.size.width / 2),
        round(bounds.origin.y + bounds.size.height / 2),
    )
    try:
        assert cursor.confine_for_remote() == expected_anchor
        cursor.recenter_remote()
        assert cursor.position() == expected_anchor
        cursor.restore_local()
        cursor.restore_local()
        assert cursor.position() == original
    finally:
        cursor.restore_local(original)


def test_native_tap_ignores_own_keyboard_injection_and_stops_cleanly() -> None:
    seen: list[object] = []
    hook = MacOSInputHook()
    injector = MacOSInputInjector()
    try:
        hook.start(lambda event: seen.append(event) or Suppression.PASS_THROUGH)
        # An uncommon key release avoids typing into whichever app has focus.
        injector.inject(KeyEvent(107, 0x83, False))
        time.sleep(0.05)
    finally:
        hook.stop()
        hook.stop()
    assert not hook.remote_active
    assert not any(isinstance(event, KeyEvent) and event.virtual_key == 0x83 for event in seen)
