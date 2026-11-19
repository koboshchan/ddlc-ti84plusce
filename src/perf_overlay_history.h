#ifndef DDLC_PERF_OVERLAY_HISTORY_H
#define DDLC_PERF_OVERLAY_HISTORY_H

#include <stdint.h>

typedef struct {
    uint32_t ticks[5];
    uint8_t count;
    uint8_t next;
} perf_history_t;

static inline void perf_history_push(perf_history_t *h, uint32_t ticks)
{
    h->ticks[h->next] = ticks;
    h->next = (uint8_t)((h->next + 1u) % 5u);
    if (h->count < 5u) {
        ++h->count;
    }
}

static inline uint32_t perf_history_ms(const perf_history_t *h, uint8_t age)
{
    uint8_t i = (uint8_t)((h->next + 4u - age) % 5u);
    uint32_t t = h->ticks[i];
    return (t >> 15) * 1000u + (((t & 0x7FFFu) * 125u + 2048u) >> 12);
}

#endif
