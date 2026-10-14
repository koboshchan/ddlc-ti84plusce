#ifndef DDLC_PERF_OVERLAY_HISTORY_H
#define DDLC_PERF_OVERLAY_HISTORY_H
#include <stdint.h>
typedef struct {
    uint32_t ticks[5];
    uint8_t count, next;
} perf_history_t;
static inline void perf_history_push(perf_history_t *h, uint32_t ticks) {
    h->ticks[h->next] = ticks;
    h->next = (h->next + 1u) % 5u;
    if (h->count < 5u) ++h->count;
}
static inline uint32_t perf_history_ms(const perf_history_t *h, uint8_t age) {
    uint8_t i = (h->next + 4u - age) % 5u;
    return (uint32_t)(((uint64_t)h->ticks[i] * 1000u + 16384u) / 32768u);
}
#endif
