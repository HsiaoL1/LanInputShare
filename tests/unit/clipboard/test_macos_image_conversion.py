from __future__ import annotations

import io
import struct

import pytest
from PIL import Image

from lan_input_share.clipboard.models import ClipboardImage


def _native_image(mode: str, pixels: list[tuple[int, ...]], *, format: str = "PNG") -> bytes:
    image = Image.new(mode, (2, 1))
    image.putdata(pixels)
    stream = io.BytesIO()
    image.save(stream, format=format)
    return stream.getvalue()


def _rgb_dib() -> bytes:
    # Bottom-up, 24-bit BGR: red then green, padded to a four-byte boundary.
    return struct.pack("<IiiHHIIiiII", 40, 2, 1, 1, 24, 0, 8, 0, 0, 0, 0) + bytes(
        [0, 0, 255, 0, 255, 0, 0, 0]
    )


def test_reads_native_png_and_tiff_into_windows_wire_image() -> None:
    from lan_input_share.clipboard.image_conversion import native_to_clipboard_image

    for format in ("PNG", "TIFF"):
        result = native_to_clipboard_image(
            _native_image("RGB", [(255, 0, 0), (0, 255, 0)], format=format)
        )
        assert result.format == "dib"
        with Image.open(io.BytesIO(result.data)) as image:
            assert image.size == (2, 1)
            rgb = image.convert("RGB")
            assert [rgb.getpixel((x, 0)) for x in range(2)] == [(255, 0, 0), (0, 255, 0)]


def test_converts_rgb_dib_to_native_png() -> None:
    from lan_input_share.clipboard.image_conversion import clipboard_image_to_png

    result = clipboard_image_to_png(ClipboardImage("dib", _rgb_dib()))

    assert result.startswith(b"\x89PNG\r\n\x1a\n")
    with Image.open(io.BytesIO(result)) as image:
        rgb = image.convert("RGB")
        assert [rgb.getpixel((x, 0)) for x in range(2)] == [(255, 0, 0), (0, 255, 0)]


def test_converts_palette_dib_without_treating_palette_as_pixels() -> None:
    from lan_input_share.clipboard.image_conversion import clipboard_image_to_png

    header = struct.pack("<IiiHHIIiiII", 40, 2, 1, 1, 8, 0, 4, 0, 0, 2, 0)
    palette = bytes([0, 0, 255, 0, 255, 0, 0, 0])
    result = clipboard_image_to_png(ClipboardImage("dib", header + palette + b"\x00\x01\0\0"))

    with Image.open(io.BytesIO(result)) as image:
        rgb = image.convert("RGB")
        assert [rgb.getpixel((x, 0)) for x in range(2)] == [(255, 0, 0), (0, 0, 255)]


def test_converts_dibv5_bitfields_and_preserves_alpha() -> None:
    from lan_input_share.clipboard.image_conversion import clipboard_image_to_png

    header = bytearray(124)
    struct.pack_into("<IiiHHII", header, 0, 124, 2, -1, 1, 32, 3, 8)
    struct.pack_into("<IIII", header, 40, 0xFF0000, 0xFF00, 0xFF, 0xFF000000)
    result = clipboard_image_to_png(
        ClipboardImage("dibv5", bytes(header) + bytes([0, 0, 255, 128, 0, 255, 0, 255]))
    )

    with Image.open(io.BytesIO(result)) as image:
        rgba = image.convert("RGBA")
        assert [rgba.getpixel((x, 0)) for x in range(2)] == [(255, 0, 0, 128), (0, 255, 0, 255)]


def test_native_alpha_image_uses_dibv5_and_preserves_transparency() -> None:
    from lan_input_share.clipboard.image_conversion import (
        clipboard_image_to_png,
        native_to_clipboard_image,
    )

    result = native_to_clipboard_image(_native_image("RGBA", [(255, 0, 0, 80), (0, 0, 255, 0)]))

    assert result.format == "dibv5"
    with Image.open(io.BytesIO(clipboard_image_to_png(result))) as image:
        rgba = image.convert("RGBA")
        assert [rgba.getpixel((x, 0)) for x in range(2)] == [(255, 0, 0, 80), (0, 0, 255, 0)]


@pytest.mark.parametrize("data", [b"", b"not an image", b"\x28\x00\x00\x00"])
def test_rejects_invalid_native_image(data: bytes) -> None:
    from lan_input_share.clipboard.image_conversion import native_to_clipboard_image

    with pytest.raises(ValueError, match="图片"):
        native_to_clipboard_image(data)


def test_rejects_truncated_dib_before_writing_native_clipboard() -> None:
    from lan_input_share.clipboard.image_conversion import clipboard_image_to_png

    with pytest.raises(ValueError, match="图片"):
        clipboard_image_to_png(ClipboardImage("dib", _rgb_dib()[:-4]))
