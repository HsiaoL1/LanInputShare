from __future__ import annotations

import os
import struct
import sys
import threading
from pathlib import Path

import pytest

from lan_input_share.clipboard.macos_clipboard import MacOSClipboardWatcher
from lan_input_share.clipboard.models import ClipboardImage, ClipboardItem

pytestmark = [
    pytest.mark.macos_integration,
    pytest.mark.skipif(
        sys.platform != "darwin" or os.environ.get("LAN_INPUT_SHARE_RUN_MACOS_INTEGRATION") != "1",
        reason="Requires macOS and explicit native clipboard test opt-in",
    ),
]


def test_native_text_image_files_and_background_notification(tmp_path: Path) -> None:
    watcher = MacOSClipboardWatcher(poll_interval=0.05)
    original_text = watcher.read_unicode_text()
    original_marker = watcher.read_marker()
    original_image = watcher.read_image()
    original_files = watcher.read_file_drop()
    changed = threading.Event()
    first = tmp_path / "中文 space #%.txt"
    first.write_text("source", encoding="utf-8")
    folder = tmp_path / "folder"
    folder.mkdir()
    header = struct.pack("<IiiHHIIiiII", 40, 1, 1, 1, 24, 0, 4, 0, 0, 0, 0)
    image = ClipboardImage("dib", header + b"\x00\x00\xff\x00")
    item = ClipboardItem.image("integration", 1, image, generation="native")
    try:
        watcher.start(changed.set)
        watcher.write_unicode_text("中文 clipboard 🖱", "integration-text")
        assert changed.wait(2)
        assert watcher.read_unicode_text() == "中文 clipboard 🖱"
        assert watcher.read_marker() == "integration-text"

        watcher.write_image(image, item.marker)
        assert watcher.read_image() == image
        # A fresh reader exercises actual NSData/PNG conversion without the cache.
        native_image = MacOSClipboardWatcher().read_image()
        assert native_image is not None
        assert watcher.read_marker() == item.marker

        watcher.write_file_drop((first, folder), "integration-files")
        assert watcher.read_file_drop() == (first.resolve(), folder.resolve())
        assert watcher.read_marker() == "integration-files"
    finally:
        watcher.stop()
        if original_files and all(path.exists() for path in original_files):
            watcher.write_file_drop(original_files, original_marker)
        elif original_image is not None:
            watcher.write_image(original_image, original_marker)
        else:
            watcher.write_unicode_text(original_text, original_marker)
