"""Offline bounded approximate palette blend tables (pure Python)."""

from __future__ import annotations


def generate_blend_luts(
    palette: list[int] | tuple[int, ...],
    nearest: bytes | bytearray | list[int],
) -> tuple[bytes, list[bytes]]:
    if len(palette) != 256 or len(nearest) != 32768:
        raise ValueError("Expected 256 RGB1555 colors and 32768 nearest entries")
    if any(v < 0 or v > 32767 for v in palette):
        raise ValueError("Palette color outside RGB1555 range")
    if any(v > 254 for v in nearest):
        raise ValueError("Nearest lookup must exclude transparent index")

    channels = [((v >> 10) & 31, (v >> 5) & 31, v & 31) for v in palette]

    def dist(a: tuple[int, int, int], b: tuple[int, int, int]) -> int:
        return sum((x - y) ** 2 for x, y in zip(a, b))

    centers = [channels[0]]
    for _ in range(31):
        centers.append(
            max(channels[:255], key=lambda c: min(dist(c, p) for p in centers))
        )

    for _ in range(12):
        groups_idx = [
            min(range(32), key=lambda j: dist(c, centers[j])) for c in channels
        ]
        new_centers = []
        for j in range(32):
            members = [c for c, g in zip(channels[:255], groups_idx[:255]) if g == j]
            if members:
                avg = tuple(
                    (sum(c[k] for c in members) + len(members) // 2) // len(members)
                    for k in range(3)
                )
                new_centers.append(avg)
            else:
                new_centers.append(centers[j])
        if new_centers == centers:
            break
        centers = new_centers

    groups = bytes(min(range(32), key=lambda j: dist(c, centers[j])) for c in channels)
    tables: list[bytes] = []
    for level in range(16):
        alpha = (level * 1024 + 7) // 15
        out = bytearray()
        for fg in channels:
            for bg in centers:
                r, g, b = [
                    (f * alpha + d * (1024 - alpha) + 512) >> 10
                    for f, d in zip(fg, bg)
                ]
                out.append(nearest[(r << 10) | (g << 5) | b])
        tables.append(bytes(out))

    return groups, tables
