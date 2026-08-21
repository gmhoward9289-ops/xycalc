"""Deterministic RGB PNG writer. Stdlib only; no tIME / random chunks."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _chunk(tag: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(tag + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)


def write_rgb_png(path: Path, width: int, height: int, rgb: bytes) -> None:
    """Write an 8-bit RGB PNG. `rgb` is width*height*3 bytes, row-major."""
    expected = width * height * 3
    if len(rgb) != expected:
        raise ValueError(f"rgb buffer {len(rgb)} bytes, expected {expected}")
    raw = bytearray()
    stride = width * 3
    for y in range(height):
        raw.append(0)  # filter None
        raw.extend(rgb[y * stride : (y + 1) * stride])
    compressed = zlib.compress(bytes(raw), 9)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    path.write_bytes(
        _PNG_MAGIC + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", compressed) + _chunk(b"IEND", b"")
    )
