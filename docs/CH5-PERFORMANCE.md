# Chapter five performance work

This branch separates correctness proofs from performance measurements.
Benchmarks use the supplied CE ROM in fresh headless emulator processes at
48 MHz. CPU timer ticks use CLOCKS_PER_SEC=32768; host elapsed time is not
calculator time. Standalone probes exclude dialogue drawing, presentation
and USB transfers.

## Verified intermediate results

The unchanged sparse noise baseline takes at most 100.281 ms to draw and
69.550 ms to restore across 41 authored samples, with a worst combined time
of 169.830 ms. The unrolled assembly candidate takes 64.026 ms to draw and
38.635 ms to restore, with 102.661 ms combined. Every sampled pixel and
undo restoration matches the reference. Both loaded assembly routines have
verified signatures in target RAM.

Motion scene 35 at authored ages 4000 and 4125 ms takes 10664.795 and
10664.978 ms before the pair cache and opaque assembly row. The initial
candidate takes 4838.165 and 4838.257 ms. These are intermediate measurements,
not a claim that the full mixed frame meets the 100 ms target.

The actual source before/after host comparison covers 1261 frames and
72633600 indexed pixels with zero differences. The randomized regression
also exercises cache collisions, transparent indices, alpha interpolation
and scratch guards. Host checks do not establish target ABI correctness.

## Design and lifetime

The original renderer already draws each whole layer before the next one,
so the hypothesized alternating-layer band-cache thrash is not present.
Layer-local buffers provide isolation but must not be credited with a
speedup without measurements.

Band data pointers are scoped to a read-only draw. No archive collection
or variable mutation is permitted inside that interval. Lower framebuffer
rows are temporary exact palette-pair cache storage; dialogue rendering
must replace those rows before presentation. Cache tags are invalidated
per alpha/layer, and the transparent foreground index cannot create the
invalid all-ones tag.

Noise retains the original sparse ordered coverage and palette mapping.
Replacing it with a full-frame sixteen-level blend LUT would change the
algorithm, rather than merely optimize the existing implementation.

The effect scheduler now composes and presents once per due effect tick.
Before the deadline it leaves the visible frame untouched; typewriter
ticks explicitly request one new composed frame. Failed timed-frame reads
invalidate and clear staging, then the caller reconstructs once. Tests
cover read failures, palette incompatibility, absent media and presentation
counts.

## Reproduction

Run PYTHONPATH=tools python3 -m unittest discover -s tests -v.
Build with the CEdev toolchain using make.
The local benchmark data and supplied ROM/game assets are not committed.
Final motion-only probe, with the same bands implementation on both sides,
takes 4672.943 and 4673.035 ms after the exact 1x/2x axis specialization.
The completed target framebuffer comparison has zero differing pixels
across the first 180 rows of both VRAM buffers. Loaded candidate code and
the opaque assembly routine signatures were independently checked.

These improvements do not meet the 100 ms full-frame goal. No measured
scheduler speedup or complete mixed motion/noise/UI frame timing is claimed.
The full host suite passes 17 tests and the integrated engine builds.

A prebaked exact noise ring was evaluated and rejected. Its byte form alone
requires 65535 bytes; the packed 5-bit ring requires 40960 bytes and a
constant-time seed-to-phase map would require another 131070 bytes. This
does not fit the current heap budget. The shipped unrolled kernel adds
no noise working RAM and preserves sparse coverage instead.

