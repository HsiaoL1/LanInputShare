from __future__ import annotations

from lan_input_share.input.events import KeyEvent

# Apple kVK_* hardware codes -> Windows set-1 scan code, VK, extended bit.
# Physical keys stay physical; cross-platform shortcut adaptation is in the runtime.
_KEYS: dict[int, tuple[int, int, bool]] = {
    0: (30, 0x41, False),
    1: (31, 0x53, False),
    2: (32, 0x44, False),
    3: (33, 0x46, False),
    4: (35, 0x48, False),
    5: (34, 0x47, False),
    6: (44, 0x5A, False),
    7: (45, 0x58, False),
    8: (46, 0x43, False),
    9: (47, 0x56, False),
    10: (86, 0xE2, False),
    11: (48, 0x42, False),
    12: (16, 0x51, False),
    13: (17, 0x57, False),
    14: (18, 0x45, False),
    15: (19, 0x52, False),
    16: (21, 0x59, False),
    17: (20, 0x54, False),
    18: (2, 0x31, False),
    19: (3, 0x32, False),
    20: (4, 0x33, False),
    21: (5, 0x34, False),
    22: (7, 0x36, False),
    23: (6, 0x35, False),
    24: (13, 0xBB, False),
    25: (10, 0x39, False),
    26: (8, 0x37, False),
    27: (12, 0xBD, False),
    28: (9, 0x38, False),
    29: (11, 0x30, False),
    30: (27, 0xDD, False),
    31: (24, 0x4F, False),
    32: (22, 0x55, False),
    33: (26, 0xDB, False),
    34: (23, 0x49, False),
    35: (25, 0x50, False),
    36: (28, 0x0D, False),
    37: (38, 0x4C, False),
    38: (36, 0x4A, False),
    39: (40, 0xDE, False),
    40: (37, 0x4B, False),
    41: (39, 0xBA, False),
    42: (43, 0xDC, False),
    43: (51, 0xBC, False),
    44: (53, 0xBF, False),
    45: (49, 0x4E, False),
    46: (50, 0x4D, False),
    47: (52, 0xBE, False),
    48: (15, 0x09, False),
    49: (57, 0x20, False),
    50: (41, 0xC0, False),
    51: (14, 0x08, False),
    53: (1, 0x1B, False),
    54: (92, 0x5C, True),
    55: (91, 0x5B, True),
    56: (42, 0x10, False),
    57: (58, 0x14, False),
    58: (56, 0x12, False),
    59: (29, 0x11, False),
    60: (54, 0x10, False),
    61: (56, 0x12, True),
    62: (29, 0x11, True),
    64: (104, 0x80, False),
    65: (83, 0x6E, False),
    67: (55, 0x6A, False),
    69: (78, 0x6B, False),
    71: (69, 0x90, False),
    75: (53, 0x6F, True),
    76: (28, 0x0D, True),
    78: (74, 0x6D, False),
    79: (105, 0x81, False),
    80: (106, 0x82, False),
    81: (89, 0xBB, False),
    82: (82, 0x60, False),
    83: (79, 0x61, False),
    84: (80, 0x62, False),
    85: (81, 0x63, False),
    86: (75, 0x64, False),
    87: (76, 0x65, False),
    88: (77, 0x66, False),
    89: (71, 0x67, False),
    90: (107, 0x83, False),
    91: (72, 0x68, False),
    92: (73, 0x69, False),
    96: (63, 0x74, False),
    97: (64, 0x75, False),
    98: (65, 0x76, False),
    99: (61, 0x72, False),
    100: (66, 0x77, False),
    101: (67, 0x78, False),
    103: (87, 0x7A, False),
    105: (100, 0x7C, False),
    106: (103, 0x7F, False),
    107: (101, 0x7D, False),
    109: (68, 0x79, False),
    111: (88, 0x7B, False),
    113: (102, 0x7E, False),
    114: (82, 0x2D, True),
    115: (71, 0x24, True),
    116: (73, 0x21, True),
    117: (83, 0x2E, True),
    118: (62, 0x73, False),
    119: (79, 0x23, True),
    120: (60, 0x71, False),
    121: (81, 0x22, True),
    122: (59, 0x70, False),
    123: (75, 0x25, True),
    124: (77, 0x27, True),
    125: (80, 0x28, True),
    126: (72, 0x26, True),
}
_SCAN_TO_MAC = {(scan, extended): apple for apple, (scan, _, extended) in _KEYS.items()}
_VK_TO_MAC = {vk: apple for apple, (_, vk, _) in reversed(tuple(_KEYS.items()))}
_VK_TO_MAC.update({0xA0: 56, 0xA1: 60, 0xA2: 59, 0xA3: 62, 0xA4: 58, 0xA5: 61})

# The low bits are NX_DEVICE*KEYMASK values from Apple's IOLLEvent.h.
# Retain the native identity: Command is Windows, Control is Control, Option is Alt.
MODIFIERS: dict[int, tuple[int, int]] = {
    56: (0x20000, 0x0002),
    60: (0x20000, 0x0004),
    59: (0x40000, 0x0001),
    62: (0x40000, 0x2000),
    58: (0x80000, 0x0020),
    61: (0x80000, 0x0040),
    55: (0x100000, 0x0008),
    54: (0x100000, 0x0010),
}


def key_event_from_macos(keycode: int, is_down: bool) -> KeyEvent | None:
    key = _KEYS.get(keycode)
    if key is None:
        return None
    scan, vk, extended = key
    return KeyEvent(scan, vk, is_down, is_extended=extended)


def macos_keycode_from_event(event: KeyEvent) -> int:
    if event.scan_code:
        keycode = _SCAN_TO_MAC.get((event.scan_code, event.is_extended))
    else:
        keycode = _VK_TO_MAC.get(event.virtual_key)
    if keycode is None:
        raise ValueError(f"macOS 不支持此键：scan={event.scan_code} vk={event.virtual_key}")
    return keycode


__all__ = ["MODIFIERS", "key_event_from_macos", "macos_keycode_from_event"]
