"""Shared ~64 KB TI-84 Plus CE AppVar asset packer (DPKIDX + DPK0..DPKn).

Packs heterogeneous read-only assets (script chunks, sprites, scenes, title art,
palettes, poem tables, UI boxes, Ch5 motion data) into densely filled ~64 KB
AppVar volumes (`DPK0`, `DPK1`, ...) indexed by `DPKIDX` and the existing
compact group LUTs (`DSPRLUT`, `DSCNLUT`, `DTILLUT`, `DCGPLUT`).

Layout invariants:
  - Every `DPK<n>` volume is at most `vol_max` bytes (default 65,300 B, safely
    below TI-OS's 65,505 B variable cap and 65,536 B Flash sector size).
  - Assets that require a direct contiguous Flash pointer (`contiguous=True`,
    e.g. ZX0 scenes/sprites, RLET title/sticker art, Ch5 motion tables) are
    placed whole within a single `DPK<n>` volume via First-Fit Decreasing.
  - Streamable / RAM-loaded assets (`contiguous=False`, e.g. `DSCR*` script
    chunks, LUTs, palettes, UI textboxes, metadata tables) fill the tail gap
    of every volume right up to `vol_max`, spanning seamlessly into offset 0
    of `DPK<n+1>`.
  - `DPKIDX` format:
      [4B magic "DPK1"][u16 count][u16 vol_max]
      count * [8B NUL-padded name][u8 vol][u8 flags][u16 offset][u32 length]
    Entries are sorted lexicographically by the 8-byte name for O(log N) lookup.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Callable

PAK_MAGIC = b"DPK1"
PAK_VOL_MAX = 65200
PAK_FLAG_SPANNING = 1


def read_span(volumes: list[bytes], vol: int, offset: int, length: int) -> bytes:
    """Reference spanning reader matching src/pak.c's pak_read_span()."""
    out = bytearray()
    cur_vol = vol
    cur_off = offset
    rem = length
    while rem > 0:
        if cur_vol >= len(volumes):
            raise ValueError(f"span overruns volume list at DPK{cur_vol}")
        vdata = volumes[cur_vol]
        if cur_off >= len(vdata):
            cur_off -= len(vdata)
            cur_vol += 1
            continue
        take = min(rem, len(vdata) - cur_off)
        out.extend(vdata[cur_off : cur_off + take])
        rem -= take
        cur_off = 0
        cur_vol += 1
    return bytes(out)


@dataclass
class _Item:
    kind: str       # "named" or "group"
    key: object     # str for "named", (group_name, index) for "group"
    data: bytes
    contiguous: bool


@dataclass
class PackResult:
    index_bytes: bytes
    volumes: list[bytes]
    lut_bytes: dict[str, bytes]
    named_locations: dict[str, tuple[int, int, int, int]]  # name -> (vol, off, len, flags)
    group_locations: dict[str, list[tuple[int, int, int]]] # group -> [(vol, off, len), ...]
    spanning_count: int


@dataclass
class AssetPacker:
    vol_max: int = PAK_VOL_MAX
    _named: dict[str, _Item] = field(default_factory=dict)
    _groups: dict[str, list[_Item]] = field(default_factory=dict)

    def add_named(self, name: str, data: bytes, *, contiguous: bool = False) -> None:
        if not name or len(name) > 8 or not name.isascii():
            raise ValueError(f"invalid TI AppVar asset name: {name!r}")
        if name in self._named:
            raise ValueError(f"duplicate packed asset name: {name!r}")
        if contiguous and len(data) > self.vol_max:
            raise ValueError(
                f"contiguous asset {name!r} ({len(data)} B) exceeds vol_max ({self.vol_max} B)"
            )
        self._named[name] = _Item("named", name, data, contiguous)

    def add_group(self, group_name: str, items: list[bytes], *, contiguous: bool = True) -> None:
        if group_name in self._groups:
            raise ValueError(f"duplicate packed group: {group_name!r}")
        wrapped: list[_Item] = []
        for idx, data in enumerate(items):
            if contiguous and len(data) > self.vol_max:
                raise ValueError(
                    f"contiguous group item {group_name}[{idx}] ({len(data)} B) "
                    f"exceeds vol_max ({self.vol_max} B)"
                )
            wrapped.append(_Item("group", (group_name, idx), data, contiguous))
        self._groups[group_name] = wrapped

    def build(
        self,
        lut_names: dict[str, str],
        lut_builder: Callable[[list[tuple[int, int, int]]], bytes],
    ) -> PackResult:
        contig_pool: list[_Item] = []
        span_queue: list[_Item] = []
        # Content-alias identical items within each group (matching legacy pack_group).
        group_aliases: dict[tuple[str, int], int] = {}

        for item in self._named.values():
            (contig_pool if item.contiguous else span_queue).append(item)
        for gname, group_items in self._groups.items():
            seen: dict[bytes, int] = {}
            for idx, item in enumerate(group_items):
                if item.data in seen:
                    group_aliases[(gname, idx)] = seen[item.data]
                else:
                    seen[item.data] = idx
                    (contig_pool if item.contiguous else span_queue).append(item)

        # Deterministic FFD order for contiguous items: largest first, tie-break by key.
        contig_pool.sort(key=lambda it: (-len(it.data), it.kind, str(it.key)))
        # Put group spannables (e.g. DCGPAL) first, then named spannables sorted by name.
        span_queue.sort(key=lambda it: (0 if it.kind == "group" else 1, str(it.key)))

        named_loc: dict[str, tuple[int, int, int, int]] = {}
        group_loc: dict[str, list[tuple[int, int, int] | None]] = {
            g: [None] * len(items) for g, items in self._groups.items()
        }

        def record(item: _Item, vol: int, off: int, flags: int) -> None:
            if item.kind == "named":
                named_loc[item.key] = (vol, off, len(item.data), flags)
            else:
                gname, idx = item.key
                group_loc[gname][idx] = (vol, off, len(item.data))

        volumes: list[bytes] = []
        cur = bytearray()
        carry = b""
        span_idx = 0

        def flush_vol() -> None:
            nonlocal cur
            volumes.append(bytes(cur))
            cur = bytearray()

        def stream_item(item: _Item) -> None:
            nonlocal carry
            assert not carry
            rem = self.vol_max - len(cur)
            if rem == 0:
                flush_vol()
                rem = self.vol_max
            vol_idx = len(volumes)
            off = len(cur)
            data = item.data
            if len(data) <= rem:
                record(item, vol_idx, off, 0)
                cur.extend(data)
            else:
                record(item, vol_idx, off, PAK_FLAG_SPANNING)
                cur.extend(data[:rem])
                carry = data[rem:]
                flush_vol()
                while len(carry) >= self.vol_max:
                    volumes.append(carry[: self.vol_max])
                    carry = carry[self.vol_max :]
                if carry:
                    cur.extend(carry)
                    carry = b""

        # Main packing loop: fill each volume with FFD contiguous items, then top off
        # to exact vol_max using spannable items whose tails land at offset 0 of vol+1.
        while contig_pool or span_idx < len(span_queue) or carry:
            if carry:
                take = min(len(carry), self.vol_max - len(cur))
                cur.extend(carry[:take])
                carry = carry[take:]
                if len(cur) == self.vol_max:
                    flush_vol()
                    continue

            # Place largest fitting contiguous items into current volume.
            if contig_pool:
                i = 0
                while i < len(contig_pool):
                    rem = self.vol_max - len(cur)
                    if rem == 0:
                        break
                    it = contig_pool[i]
                    if len(it.data) <= rem:
                        record(it, len(volumes), len(cur), 0)
                        cur.extend(it.data)
                        contig_pool.pop(i)
                    else:
                        i += 1

            # Top off the remaining bytes of this volume with spannable items.
            while len(cur) < self.vol_max and span_idx < len(span_queue):
                it = span_queue[span_idx]
                span_idx += 1
                rem = self.vol_max - len(cur)
                vol_idx = len(volumes)
                off = len(cur)
                if len(it.data) <= rem:
                    record(it, vol_idx, off, 0)
                    cur.extend(it.data)
                else:
                    record(it, vol_idx, off, PAK_FLAG_SPANNING)
                    cur.extend(it.data[:rem])
                    carry = it.data[rem:]
                    break

            if contig_pool or span_idx < len(span_queue) or carry:
                if len(cur) > 0:
                    flush_vol()

        # Finalize group LUTs now that all group items have final (vol, off, len),
        # and append them into the tail volume (spanning if needed).
        final_group_loc: dict[str, list[tuple[int, int, int]]] = {}
        lut_blobs: dict[str, bytes] = {}
        for gname, locs in group_loc.items():
            resolved = []
            for idx, entry in enumerate(locs):
                if entry is None and (gname, idx) in group_aliases:
                    entry = locs[group_aliases[(gname, idx)]]
                if entry is None:
                    raise AssertionError(f"missing location for {gname}[{idx}]")
                resolved.append(entry)
            final_group_loc[gname] = resolved
            if gname in lut_names:
                lname = lut_names[gname]
                lbytes = lut_builder(resolved)
                lut_blobs[lname] = lbytes
                lut_item = _Item("named", lname, lbytes, contiguous=False)
                stream_item(lut_item)

        if len(cur) > 0:
            flush_vol()

        if len(volumes) > 255:
            raise ValueError(f"pack produced {len(volumes)} volumes (u8 limit is 255)")

        # Build sorted DPKIDX binary table.
        sorted_names = sorted(named_loc.keys(), key=lambda s: s.encode("ascii").ljust(8, b"\x00"))
        idx_out = bytearray(PAK_MAGIC)
        idx_out += struct.pack("<HH", len(sorted_names), self.vol_max)
        spanning_count = 0
        for name in sorted_names:
            vol, off, length, flags = named_loc[name]
            if flags & PAK_FLAG_SPANNING:
                spanning_count += 1
            idx_out += struct.pack(
                "<8sBBHI",
                name.encode("ascii").ljust(8, b"\x00"),
                vol,
                flags,
                off,
                length,
            )

        # Bit-exact self-verification of every packed asset and LUT entry.
        for name, item in self._named.items():
            vol, off, length, flags = named_loc[name]
            assert length == len(item.data)
            if item.contiguous:
                assert (flags & PAK_FLAG_SPANNING) == 0
                assert off + length <= len(volumes[vol])
            got = read_span(volumes, vol, off, length)
            if got != item.data:
                raise AssertionError(f"pack roundtrip mismatch for {name!r}")
        for lname, lbytes in lut_blobs.items():
            vol, off, length, _ = named_loc[lname]
            if read_span(volumes, vol, off, length) != lbytes:
                raise AssertionError(f"pack roundtrip mismatch for LUT {lname!r}")
        for gname, items in self._groups.items():
            for idx, item in enumerate(items):
                vol, off, length = final_group_loc[gname][idx]
                assert length == len(item.data)
                if item.contiguous:
                    assert off + length <= len(volumes[vol])
                if read_span(volumes, vol, off, length) != item.data:
                    raise AssertionError(f"pack roundtrip mismatch for {gname}[{idx}]")

        return PackResult(
            index_bytes=bytes(idx_out),
            volumes=volumes,
            lut_bytes=lut_blobs,
            named_locations=named_loc,
            group_locations=final_group_loc,
            spanning_count=spanning_count,
        )
