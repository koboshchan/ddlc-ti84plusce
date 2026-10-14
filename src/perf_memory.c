/* Debug engine-allocation ledger, not a guess at allocator internals.
 * Requested payload bytes only. Allocator metadata, libc/USB allocations,
 * executable code and stack are not included. BSS reports the ledger itself.
 * A bounded table avoids allocating memory just to measure memory.
 * Tracking starts at startup even with the panel OFF, so later activation
 * and asset unloading/reloading see current ownership rather than deltas.
 */
#include <stdlib.h>
#define DDLC_PERF_MEMORY_IMPL
#include "perf_memory.h"
#ifdef DDLC_PERF_OVERLAY
enum { CAPACITY = 64 };
static struct { void *ptr; size_t size; } slots[CAPACITY];
static size_t live, peak;
static unsigned count;
static bool complete = true;
void *perf_memory_malloc(size_t bytes) {
    void *ptr = malloc(bytes);
    if (!ptr) return NULL;
    for (unsigned i=0; i<CAPACITY; ++i) {
        if (!slots[i].ptr) {
            slots[i].ptr=ptr; slots[i].size=bytes;
            live+=bytes; ++count;
            if (live>peak) peak=live;
            return ptr;
        }
    }
    complete=false; /* Never claim an exact total after ledger overflow. */
    return ptr;
}
void perf_memory_free(void *ptr) {
    if (ptr) for (unsigned i=0; i<CAPACITY; ++i) {
        if (slots[i].ptr==ptr) {
            live-=slots[i].size; --count; slots[i].ptr=NULL;
            break;
        }
    }
    free(ptr);
}
perf_memory_t perf_memory_snapshot(void) {
    perf_memory_t s={live,peak,count,complete};
    return s;
}
#endif
