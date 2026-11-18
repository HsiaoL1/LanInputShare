from __future__ import annotations

import importlib
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol
from urllib.parse import unquote, urlsplit
from urllib.request import url2pathname

from lan_input_share.clipboard.image_conversion import (
    clipboard_image_to_png,
    native_to_clipboard_image,
)
from lan_input_share.clipboard.models import ClipboardImage

_MARKER_TYPE = "com.laninputshare.marker.v1"


def _load_native() -> tuple[ModuleType, ModuleType, ModuleType]:
    if sys.platform != "darwin":
        raise RuntimeError("macOS 剪贴板只支持 macOS")
    return (
        importlib.import_module("AppKit"),
        importlib.import_module("Foundation"),
        importlib.import_module("objc"),
    )


class PasteboardBackend(Protocol):
    def change_count(self) -> int: ...

    def read_text(self) -> str: ...

    def write_text(self, text: str, marker: str) -> int: ...

    def read_marker(self) -> str: ...

    def read_image(self) -> ClipboardImage | None: ...

    def write_image(self, image: ClipboardImage, marker: str) -> int: ...

    def read_files(self) -> tuple[Path, ...]: ...

    def write_files(self, paths: tuple[Path, ...], marker: str) -> int: ...


class _NativePasteboardBackend:
    def __init__(self) -> None:
        self._appkit, self._foundation, self._objc = _load_native()
        self._lock = threading.RLock()

    @contextmanager
    def _pasteboard(self) -> Iterator[Any]:
        # All Cocoa allocations and calls, including polling on a background thread,
        # happen in a local pool. NSPasteboard itself does not need an AppKit run loop.
        with self._lock, self._objc.autorelease_pool():
            yield self._appkit.NSPasteboard.generalPasteboard()

    def change_count(self) -> int:
        with self._pasteboard() as pasteboard:
            return int(pasteboard.changeCount())

    def read_text(self) -> str:
        with self._pasteboard() as pasteboard:
            return str(pasteboard.stringForType_(self._appkit.NSPasteboardTypeString) or "")

    def read_marker(self) -> str:
        with self._pasteboard() as pasteboard:
            return str(pasteboard.stringForType_(_MARKER_TYPE) or "")

    def write_text(self, text: str, marker: str) -> int:
        with self._pasteboard() as pasteboard:
            item = self._new_item(marker)
            if not item.setString_forType_(text, self._appkit.NSPasteboardTypeString):
                raise RuntimeError("无法准备 macOS 文本剪贴板")
            return self._write_items(pasteboard, [item])

    def read_image(self) -> ClipboardImage | None:
        with self._pasteboard() as pasteboard:
            for type in (self._appkit.NSPasteboardTypePNG, self._appkit.NSPasteboardTypeTIFF):
                data = pasteboard.dataForType_(type)
                if data is not None:
                    try:
                        return native_to_clipboard_image(bytes(data))
                    except ValueError:
                        continue
        return None

    def write_image(self, image: ClipboardImage, marker: str) -> int:
        png = clipboard_image_to_png(image)
        with self._pasteboard() as pasteboard:
            item = self._new_item(marker)
            data = self._foundation.NSData.dataWithBytes_length_(png, len(png))
            if not item.setData_forType_(data, self._appkit.NSPasteboardTypePNG):
                raise RuntimeError("无法准备 macOS 图片剪贴板")
            return self._write_items(pasteboard, [item])

    def read_files(self) -> tuple[Path, ...]:
        with self._pasteboard() as pasteboard:
            paths: list[Path] = []
            for item in pasteboard.pasteboardItems() or ():
                value = item.stringForType_(self._appkit.NSPasteboardTypeFileURL)
                path = _local_file_path(str(value)) if value else None
                if path is not None:
                    paths.append(path)
            return tuple(paths)

    def write_files(self, paths: tuple[Path, ...], marker: str) -> int:
        with self._pasteboard() as pasteboard:
            items: list[Any] = []
            for index, path in enumerate(paths):
                item = self._new_item(marker if index == 0 else None)
                if not item.setString_forType_(
                    path.resolve().as_uri(), self._appkit.NSPasteboardTypeFileURL
                ):
                    raise RuntimeError("无法准备 macOS 文件剪贴板")
                items.append(item)
            # One writeObjects: keeps all file URLs and the first-item marker in one
            # pasteboard generation, so Finder receives the complete selection.
            return self._write_items(pasteboard, items)

    def _new_item(self, marker: str | None) -> Any:
        item = self._appkit.NSPasteboardItem.alloc().init()
        if marker is not None and not item.setString_forType_(marker, _MARKER_TYPE):
            raise RuntimeError("无法准备 macOS 剪贴板标记")
        return item

    @staticmethod
    def _write_items(pasteboard: Any, items: list[Any]) -> int:
        pasteboard.clearContents()
        if not pasteboard.writeObjects_(items):
            raise RuntimeError("无法写入 macOS 剪贴板")
        return int(pasteboard.changeCount())


def _local_file_path(value: str) -> Path | None:
    try:
        url = urlsplit(value)
        if (
            url.scheme != "file"
            or url.netloc.lower() not in {"", "localhost"}
            or not url.path.startswith("/")
            or unquote(url.path).startswith("//")
            or url.query
            or url.fragment
        ):
            return None
        decoded = url2pathname(url.path)
        if "\x00" in decoded or decoded.startswith(("//", "\\\\")):
            return None
        path = Path(decoded)
        return path if path.is_absolute() else None
    except (OSError, ValueError):
        return None


class MacOSClipboardWatcher:
    def __init__(
        self, *, backend: PasteboardBackend | None = None, poll_interval: float = 0.1
    ) -> None:
        if poll_interval <= 0:
            raise ValueError("剪贴板轮询间隔必须为正数")
        self._backend = backend or _NativePasteboardBackend()
        self._poll_interval = poll_interval
        self._operation_lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._stopped = threading.Event()
        self._callback: Callable[[], None] | None = None
        self._image_cache: tuple[int, str, ClipboardImage] | None = None

    def start(self, callback: Callable[[], None]) -> None:
        with self._operation_lock:
            if self._thread is not None:
                raise RuntimeError("剪贴板监听器已经启动")
            initial_count = self._backend.change_count()
            self._stopped.clear()
            self._callback = callback
            self._thread = threading.Thread(
                target=self._poll,
                args=(initial_count,),
                name="lan-input-share-clipboard",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        with self._operation_lock:
            thread = self._thread
            if thread is None:
                return
            self._stopped.set()
            self._callback = None
        if thread is not threading.current_thread():
            thread.join(timeout=3)
            if thread.is_alive():
                raise TimeoutError("停止剪贴板监听器超时")
        with self._operation_lock:
            if self._thread is thread:
                self._thread = None

    def read_unicode_text(self) -> str:
        with self._operation_lock:
            return self._backend.read_text()

    def write_unicode_text(self, text: str, marker: str) -> None:
        with self._operation_lock:
            self._image_cache = None
            self._backend.write_text(text, marker)

    def read_marker(self) -> str:
        with self._operation_lock:
            return self._backend.read_marker()

    def read_image(self) -> ClipboardImage | None:
        with self._operation_lock:
            count = self._backend.change_count()
            cache = self._image_cache
            if cache is not None:
                if cache[0] == count and cache[1] == self._backend.read_marker():
                    return cache[2]
                self._image_cache = None
            return self._backend.read_image()

    def write_image(self, image: ClipboardImage, marker: str) -> None:
        with self._operation_lock:
            self._image_cache = None
            count = self._backend.write_image(image, marker)
            # Native PNG conversion changes DIB header bytes. Preserve the exact
            # received payload while we still own this native pasteboard generation,
            # otherwise the service's digest no longer matches its loop marker.
            self._image_cache = (count, marker, image)

    def read_file_drop(self) -> tuple[Path, ...]:
        with self._operation_lock:
            return self._backend.read_files()

    def write_file_drop(self, paths: tuple[Path, ...], marker: str) -> None:
        if not paths or any(not path.exists() for path in paths):
            raise ValueError("文件剪贴板只能写入存在的本地路径")
        with self._operation_lock:
            self._image_cache = None
            self._backend.write_files(paths, marker)

    def _poll(self, previous_count: int) -> None:
        while not self._stopped.wait(self._poll_interval):
            try:
                with self._operation_lock:
                    count = self._backend.change_count()
                    if count == previous_count:
                        continue
                    previous_count = count
                    if self._image_cache is not None and self._image_cache[0] != count:
                        self._image_cache = None
                    callback = self._callback
                if callback is not None:
                    with suppress(Exception):
                        callback()
            except Exception:
                # A temporary native read failure must not permanently stop polling.
                continue


__all__ = ["MacOSClipboardWatcher", "PasteboardBackend"]
