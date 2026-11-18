from __future__ import annotations

from enum import StrEnum

from lan_input_share.input.events import KeyEvent

_VK_CONTROL = 0x11
_VK_ALT = 0x12
_VK_ESCAPE = 0x1B
_VK_1 = 0x31
_VK_2 = 0x32
_VK_3 = 0x33


class HotkeyAction(StrEnum):
    SELECT_DEVICE_1 = "select_device_1"
    SELECT_DEVICE_2 = "select_device_2"
    SELECT_DEVICE_3 = "select_device_3"
    RETURN_LOCAL = "return_local"


class HotkeyMatcher:
    def __init__(self) -> None:
        self._pressed: dict[tuple[int, int, bool], int] = {}
        self._fired: set[int] = set()

    def feed(self, event: KeyEvent) -> HotkeyAction | None:
        key = event.virtual_key
        canonical = {0xA2: _VK_CONTROL, 0xA3: _VK_CONTROL, 0xA4: _VK_ALT, 0xA5: _VK_ALT}.get(
            key, key
        )
        identity = (event.scan_code, key, event.is_extended)
        if not event.is_down:
            self._pressed.pop(identity, None)
            self._fired.discard(key)
            return None
        self._pressed[identity] = canonical
        if key in self._fired:
            return None
        pressed = self._pressed.values()
        if _VK_CONTROL not in pressed or _VK_ALT not in pressed:
            return None
        actions = {
            _VK_1: HotkeyAction.SELECT_DEVICE_1,
            _VK_2: HotkeyAction.SELECT_DEVICE_2,
            _VK_3: HotkeyAction.SELECT_DEVICE_3,
            _VK_ESCAPE: HotkeyAction.RETURN_LOCAL,
        }
        action = actions.get(key)
        if action is not None:
            self._fired.add(key)
        return action


__all__ = ["HotkeyAction", "HotkeyMatcher"]
