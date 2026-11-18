from __future__ import annotations

import io
import struct

from lan_input_share.clipboard.models import ClipboardImage


def native_to_clipboard_image(data: bytes) -> ClipboardImage:
    """Normalize a native PNG/TIFF to the existing Windows clipboard wire format."""
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            if "A" in image.getbands() or "transparency" in image.info:
                rgba = image.convert("RGBA")
                pixels = rgba.tobytes("raw", "BGRA", 0, -1)
                header = bytearray(124)
                struct.pack_into(
                    "<IiiHHII", header, 0, 124, rgba.width, rgba.height, 1, 32, 3, len(pixels)
                )
                struct.pack_into("<IIII", header, 40, 0xFF0000, 0xFF00, 0xFF, 0xFF000000)
                struct.pack_into("<I", header, 56, 0x73524742)  # LCS_sRGB
                return ClipboardImage("dibv5", bytes(header) + pixels)
            output = io.BytesIO()
            image.convert("RGB").save(output, format="BMP")
            return ClipboardImage("dib", output.getvalue()[14:])
    except (OSError, ValueError, struct.error, Image.DecompressionBombError) as exc:
        raise ValueError("无法转换原生剪贴板图片") from exc


def clipboard_image_to_png(image: ClipboardImage) -> bytes:
    """Decode DIB headers, palettes and bitfields with Pillow, preserving alpha."""
    from PIL import Image

    try:
        # Pillow's DIB reader understands the packed DIB directly, including palettes
        # and BITMAPV5 bit masks. A guessed BMP pixel offset would corrupt these.
        with Image.open(io.BytesIO(image.data), formats=("DIB",)) as decoded:
            decoded.load()
            output = io.BytesIO()
            decoded.save(output, format="PNG")
            return output.getvalue()
    except (OSError, ValueError, struct.error, Image.DecompressionBombError) as exc:
        raise ValueError("无法转换 Windows 剪贴板图片") from exc


__all__ = ["clipboard_image_to_png", "native_to_clipboard_image"]
