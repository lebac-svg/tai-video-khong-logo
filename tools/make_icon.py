"""Vẽ biểu tượng app (ô vuông xanh ngọc, mũi tên tải xuống trắng, con dấu đỏ ở góc)
bằng Python thuần, xuất static/icon.png và build/icon.ico."""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEAL = (12, 124, 122)
TEAL_DARK = (9, 94, 92)
WHITE = (255, 255, 255)
RED = (215, 38, 61)


def paint(size: int) -> bytes:
    """Trả về ảnh RGBA thô (size x size)."""
    px = bytearray(size * size * 4)
    r = size * 0.22  # bo góc

    def put(x, y, c, a=255):
        i = (y * size + x) * 4
        px[i:i + 4] = bytes((*c, a))

    def inside_rounded(x, y):
        cx = min(max(x, r), size - 1 - r)
        cy = min(max(y, r), size - 1 - r)
        return (x - cx) ** 2 + (y - cy) ** 2 <= r * r

    # nền ô vuông bo góc, hơi tối dần xuống dưới
    for y in range(size):
        t = y / size
        base = tuple(int(TEAL[i] * (1 - t) + TEAL_DARK[i] * t) for i in range(3))
        for x in range(size):
            if inside_rounded(x, y):
                put(x, y, base)

    # mũi tên xuống: thân + đầu tam giác + vạch đáy
    cx = size / 2
    stem_w = size * 0.14
    top, mid, tip = size * 0.20, size * 0.50, size * 0.68
    head_w = size * 0.34
    base_y0, base_y1, base_w = size * 0.76, size * 0.84, size * 0.44
    for y in range(size):
        for x in range(size):
            in_stem = top <= y <= mid and abs(x - cx) <= stem_w / 2
            frac = (y - mid) / (tip - mid) if tip > mid else 0
            in_head = mid <= y <= tip and abs(x - cx) <= head_w / 2 * (1 - frac)
            in_base = base_y0 <= y <= base_y1 and abs(x - cx) <= base_w / 2
            if in_stem or in_head or in_base:
                put(x, y, WHITE)

    # con dấu đỏ nhỏ ở góc dưới phải (vòng tròn viền)
    scx, scy, sr = size * 0.80, size * 0.80, size * 0.13
    for y in range(size):
        for x in range(size):
            d = ((x - scx) ** 2 + (y - scy) ** 2) ** 0.5
            if sr * 0.62 <= d <= sr:
                put(x, y, RED)
            elif d < sr * 0.62 and inside_rounded(x, y):
                put(x, y, WHITE)
    return bytes(px)


def png(size: int, rgba: bytes) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + rgba[y * size * 4:(y + 1) * size * 4] for y in range(size))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def ico(images: list[tuple[int, bytes]]) -> bytes:
    head = struct.pack("<HHH", 0, 1, len(images))
    entries, blobs, offset = b"", b"", 6 + 16 * len(images)
    for size, data in images:
        entries += struct.pack("<BBBBHHII", size if size < 256 else 0, size if size < 256 else 0, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    return head + entries + blobs


def main() -> None:
    sizes = [256, 64, 48, 32, 16]
    pngs = [(s, png(s, paint(s))) for s in sizes]
    (ROOT / "static" / "icon.png").write_bytes(pngs[0][1])
    (ROOT / "build").mkdir(exist_ok=True)
    (ROOT / "build" / "icon.ico").write_bytes(ico(pngs))
    print("static/icon.png và build/icon.ico đã tạo")


if __name__ == "__main__":
    main()
