from __future__ import annotations

import io
import struct
import threading
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from lan_input_share.clipboard.models import ClipboardImage, ClipboardItem


class FakeNative:
    """NSPasteboard API boundary double: native calls require an autorelease pool."""

    def __init__(self) -> None:
        self.local = threading.local()
        self.native_threads: set[int] = set()
        self.count = 0
        self.items: list[FakeItem] = []
        self.writes: list[tuple[FakeItem, ...]] = []
        self.clears = 0
        self.fail_writes = False

    def check_pool(self) -> None:
        assert getattr(self.local, "depth", 0) > 0
        self.native_threads.add(threading.get_ident())

    @contextmanager
    def pool(self):  # type: ignore[no-untyped-def]
        self.local.depth = getattr(self.local, "depth", 0) + 1
        try:
            yield
        finally:
            self.local.depth -= 1

    def generalPasteboard(self):  # type: ignore[no-untyped-def]
        self.check_pool()
        return self

    def changeCount(self) -> int:
        self.check_pool()
        return self.count

    def clearContents(self) -> int:
        self.check_pool()
        self.items = []
        self.count += 1
        self.clears += 1
        return self.count

    def writeObjects_(self, items: list[FakeItem]) -> bool:
        self.check_pool()
        if self.fail_writes:
            return False
        self.items = list(items)
        self.count += 1
        self.writes.append(tuple(items))
        return True

    def stringForType_(self, type: str) -> str | None:
        self.check_pool()
        return self.items[0].stringForType_(type) if self.items else None

    def dataForType_(self, type: str) -> bytes | None:
        self.check_pool()
        return self.items[0].dataForType_(type) if self.items else None

    def pasteboardItems(self) -> list[FakeItem]:
        self.check_pool()
        return self.items

    def local_change(self, values: list[dict[str, str | bytes]]) -> None:
        self.items = [FakeItem(self, value) for value in values]
        self.count += 1


class FakeItem:
    def __init__(self, native: FakeNative, values: dict[str, str | bytes] | None = None) -> None:
        self.native = native
        self.values = values or {}

    def init(self):  # type: ignore[no-untyped-def]
        self.native.check_pool()
        return self

    def setString_forType_(self, value: str, type: str) -> bool:
        self.native.check_pool()
        self.values[type] = value
        return True

    def setData_forType_(self, value: bytes, type: str) -> bool:
        self.native.check_pool()
        self.values[type] = value
        return True

    def stringForType_(self, type: str) -> str | None:
        self.native.check_pool()
        value = self.values.get(type)
        return value if isinstance(value, str) else None

    def dataForType_(self, type: str) -> bytes | None:
        self.native.check_pool()
        value = self.values.get(type)
        return value if isinstance(value, bytes) else None


@pytest.fixture
def native(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    from lan_input_share.clipboard import macos_clipboard

    boundary = FakeNative()
    appkit = SimpleNamespace(
        NSPasteboard=boundary,
        NSPasteboardItem=SimpleNamespace(alloc=lambda: FakeItem(boundary)),
        NSPasteboardTypeString="public.utf8-plain-text",
        NSPasteboardTypePNG="public.png",
        NSPasteboardTypeTIFF="public.tiff",
        NSPasteboardTypeFileURL="public.file-url",
    )
    foundation = SimpleNamespace(
        NSData=SimpleNamespace(dataWithBytes_length_=lambda data, length: bytes(data[:length]))
    )
    objc = SimpleNamespace(autorelease_pool=boundary.pool)
    monkeypatch.setattr(macos_clipboard, "_load_native", lambda: (appkit, foundation, objc))
    return boundary


def _watcher():  # type: ignore[no-untyped-def]
    from lan_input_share.clipboard.macos_clipboard import MacOSClipboardWatcher

    return MacOSClipboardWatcher(poll_interval=0.01)


def _dib() -> ClipboardImage:
    header = struct.pack("<IiiHHIIiiII", 40, 1, 1, 1, 24, 0, 4, 1234, 4321, 0, 0)
    return ClipboardImage("dib", header + bytes([0, 0, 255, 0]))


def _png(*, format: str = "PNG", color: str = "blue") -> bytes:
    image = Image.new("RGB", (1, 1), color)
    output = io.BytesIO()
    image.save(output, format=format)
    return output.getvalue()


def test_text_and_marker_are_written_on_one_native_item(native: FakeNative) -> None:
    watcher = _watcher()

    watcher.write_unicode_text("你好\nclipboard 🖱", "origin:sequence:digest")

    assert watcher.read_unicode_text() == "你好\nclipboard 🖱"
    assert watcher.read_marker() == "origin:sequence:digest"
    assert len(native.writes) == 1
    assert len(native.writes[0]) == 1
    assert native.clears == 1
    assert getattr(native.local, "depth", 0) == 0


def test_empty_clipboard_reads_missing_formats_without_errors(native: FakeNative) -> None:
    watcher = _watcher()

    assert watcher.read_marker() == ""
    assert watcher.read_unicode_text() == ""
    assert watcher.read_image() is None
    assert watcher.read_file_drop() == ()


def test_remote_image_keeps_original_bytes_and_marker_digest(native: FakeNative) -> None:
    from lan_input_share.clipboard.coordinator import ClipboardCoordinator
    from lan_input_share.runtime.service import _image_item_from_marker

    watcher = _watcher()
    original = _dib()
    item = ClipboardItem.image("remote", 3, original, generation="session")

    watcher.write_image(original, item.marker)
    reread = watcher.read_image()

    assert reread is original
    assert ClipboardItem.image("remote", 3, reread, generation="session").marker == (
        watcher.read_marker()
    )
    coordinator = ClipboardCoordinator("mac", forward_remote=False)
    coordinator.remote_received(item)
    captured = _image_item_from_marker(watcher.read_marker(), reread)
    assert captured == item
    assert coordinator.local_changed(captured) == ()
    written = native.writes[0][0].values["public.png"]
    assert isinstance(written, bytes)
    with Image.open(io.BytesIO(written)) as decoded:
        assert decoded.getpixel((0, 0)) == (255, 0, 0)


def test_real_local_change_invalidates_image_cache_even_if_marker_survives(
    native: FakeNative,
) -> None:
    watcher = _watcher()
    original = _dib()
    watcher.write_image(original, "remote:3:original")
    # Some applications preserve private pasteboard types when replacing an image.
    native.local_change(
        [{"public.png": _png(), "com.laninputshare.marker.v1": "remote:3:original"}]
    )

    result = watcher.read_image()

    assert result is not None
    assert result != original
    with Image.open(io.BytesIO(result.data)) as image:
        assert image.getpixel((0, 0)) == (0, 0, 255)


def test_marker_change_invalidates_original_image_cache(native: FakeNative) -> None:
    watcher = _watcher()
    original = _dib()
    watcher.write_image(original, "remote:3:original")
    native.items[0].values.pop("com.laninputshare.marker.v1")

    assert watcher.read_image() != original


def test_reads_native_tiff_when_png_is_missing_or_invalid(native: FakeNative) -> None:
    watcher = _watcher()
    native.local_change([{"public.png": b"broken", "public.tiff": _png(format="TIFF")}])

    result = watcher.read_image()

    assert result is not None
    with Image.open(io.BytesIO(result.data)) as image:
        assert image.getpixel((0, 0)) == (0, 0, 255)


def test_multiple_file_urls_are_atomic_with_marker_only_on_first_item(
    native: FakeNative, tmp_path: Path
) -> None:
    first = tmp_path / "中文 space #%.txt"
    first.write_text("source", encoding="utf-8")
    folder = tmp_path / "folder"
    folder.mkdir()
    watcher = _watcher()

    watcher.write_file_drop((first, folder), "files:4:marker")

    assert watcher.read_file_drop() == (first.resolve(), folder.resolve())
    assert watcher.read_marker() == "files:4:marker"
    assert native.clears == 1
    assert len(native.writes) == 1
    assert len(native.writes[0]) == 2
    assert native.items[0].values["public.file-url"] == first.resolve().as_uri()
    assert "com.laninputshare.marker.v1" not in native.items[1].values


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/file.txt",
        "file://server/share/file.txt",
        "file://user@localhost/tmp/file.txt",
        "file:relative.txt",
        "file:///tmp/file.txt?unsafe=true",
        "file:///tmp/file.txt#fragment",
        "file:////server/share/file.txt",
        "file:///%2Fserver/share/file.txt",
        "file:///tmp/%00name",
    ],
)
def test_rejects_nonlocal_or_malformed_file_urls(native: FakeNative, url: str) -> None:
    native.local_change([{"public.file-url": url}])

    assert _watcher().read_file_drop() == ()


def test_accepts_explicit_localhost_file_url(native: FakeNative, tmp_path: Path) -> None:
    native.local_change([{"public.file-url": tmp_path.as_uri().replace("file:///", "file://localhost/")}])

    assert _watcher().read_file_drop() == (tmp_path,)


def test_invalid_file_write_does_not_destroy_clipboard(native: FakeNative, tmp_path: Path) -> None:
    watcher = _watcher()
    watcher.write_unicode_text("keep", "keep:marker")

    for paths in ((), (tmp_path / "missing",)):
        with pytest.raises(ValueError, match="本地路径"):
            watcher.write_file_drop(paths, "invalid")

    assert watcher.read_unicode_text() == "keep"
    assert native.clears == 1


def test_failed_native_write_raises_and_does_not_cache_image(native: FakeNative) -> None:
    native.fail_writes = True
    watcher = _watcher()

    with pytest.raises(RuntimeError, match="剪贴板"):
        watcher.write_image(_dib(), "failed")

    assert watcher.read_image() is None


def test_polling_notifies_changes_off_main_thread_and_stops(native: FakeNative) -> None:
    watcher = _watcher()
    received: list[str] = []
    changed = threading.Event()

    def callback() -> None:
        received.append(watcher.read_unicode_text())
        changed.set()

    watcher.start(callback)
    try:
        with pytest.raises(RuntimeError, match="已经启动"):
            watcher.start(callback)
        assert not changed.wait(0.03)
        native.local_change([{"public.utf8-plain-text": "local copy"}])
        assert changed.wait(1)
        assert received == ["local copy"]
        assert any(ident != threading.get_ident() for ident in native.native_threads)
    finally:
        watcher.stop()
        watcher.stop()
    native.local_change([{"public.utf8-plain-text": "after stop"}])
    assert received == ["local copy"]


def test_callback_failure_does_not_stop_clipboard_polling(native: FakeNative) -> None:
    watcher = _watcher()
    attempts = 0
    failed = threading.Event()
    recovered = threading.Event()

    def callback() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            failed.set()
            raise RuntimeError("application callback failed")
        recovered.set()

    watcher.start(callback)
    try:
        native.local_change([{"public.utf8-plain-text": "first"}])
        assert failed.wait(1)
        native.local_change([{"public.utf8-plain-text": "second"}])
        assert recovered.wait(1)
    finally:
        watcher.stop()
