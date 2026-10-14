# Ch5 performance overlay
Build with make clean && make PERF_OVERLAY=1. Normal builds compile the hooks
to no-ops and both diagnostic modules to empty translation units.
The existing Debug Menu contains Performance overlay: OFF/ON. Default OFF;
there is no new key binding. The toggle is available from title and gameplay.
The setting is session-only. No new AppVars or assets.

Five newest whole noise_tick render-work measurements start before noise
restore and include scene/timed-CG fallback, noise and dialogue composition.
Recording ends before presentation and panel ink. These are render times,
not intervals between frames. A skipped scheduler tick records no sample. An admitted tick whose scene draw
is lazy/no-op also records no sample, even if noise/dialogue composition runs.
Only successful ch5_motion_draw or a successful timed ch5 frame marks an
actual frame; failure and non-ch5 fallback do not append history.

Timer 3 uses the 32768 Hz clock with CPU interrupts unchanged. It is borrowed
only when disabled; an occupied timer shows TIMER BUSY with no fabricated
samples. Toggle-off and render_end restore its original counter/configuration.
Other timer bits remain untouched. Integer milliseconds are rounded, and
unsigned subtraction handles wrap.

BSS is the actual linker-measured static program footprint, including the
diagnostic ledger. HEAP is current requested payload bytes owned by instrumented
engine malloc/free sites, with ALLOCS the live allocation count. Tracking runs
from startup even with the panel OFF so asset unload/reload is reflected
immediately. This is NOT allocator overhead, libc/USB allocation, executable
code, or stack usage, and is not represented as total RAM utilization.
OS FREE is os_MemChk's actual free user-RAM region, not C-heap free capacity.
The bounded 64-allocation ledger allocates no memory itself; overflow marks
the heap reading PART permanently rather than falsely claiming exactness.
All engine owners assets/main/noise/persist/render are instrumented in debug
builds only. Normal builds have no accounting overhead.

VRAM RESERVED 150 KiB is graphx's two 320x240 indexed buffers. Scratch overlays
those buffers, not a second allocation, and is not counted twice. No free-VRAM
claim is made. Library scratch outside this region is not labelled framebuffer
memory. Heap requested bytes and BSS must not be summed as total system RAM.

The panel writes only the displayed framebuffer after normal presentation,
leaving the canonical inactive buffer clean. Panel ink selects the darkest and brightest current palette entries without changing the palette. No backup allocation, palette,
clipping, fontlib or graphx text changes. Its work and memory query are excluded
from recorded render time. The next presentation clears it after toggle-off.

Portable tests cover lifecycle, allocation accounting/unload/reload/overflow,
release no-ops, timer ownership/restoration/wrap, and clean inactive framebuffer.
CEmu screenshot under the shared lock and fresh bundle fit are required before
coordinated integration/push.
