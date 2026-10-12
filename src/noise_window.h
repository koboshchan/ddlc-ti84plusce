#ifndef NOISE_WINDOW_H
#define NOISE_WINDOW_H
#include <stdbool.h>
#include <stdint.h>
#define NOISE_STRIDE 80u
#define NOISE_HALF_BYTES 28800u
#include <string.h>
static inline unsigned noise_coverage(unsigned ms) { return ms >= 3000 ? 4u : ms * 4u / 3000u; }
static inline void noise_compose(uint8_t *dst, const uint8_t *base, const uint8_t *a, const uint8_t *b, const uint8_t *mask, const uint8_t *colors, unsigned ms, uint32_t tick) {
    uint32_t seed = 0xDD1Cu ^ (tick * 2654435761u);
    seed ^= seed << 13; seed ^= seed >> 17; seed ^= seed << 5;
    unsigned x=seed%321u;
    seed ^= seed << 13; seed ^= seed >> 17; seed ^= seed << 5;
    unsigned y=seed%481u, row, col, coverage=noise_coverage(ms);
    if (base != dst) memcpy(dst, base, 76800u);
    if (!coverage) return;
    for(row=0;row<240;row++) {
        unsigned yy=y+row;
        const uint8_t *p=yy<360u?a:b;
        if(yy>=360u) yy-=360u;
        p += yy*NOISE_STRIDE;
        uint8_t *d=dst+row*320u;
        const uint8_t *m=mask+(row/20u)*16u;
        /* Cached random coverage selects texture pixels, never supplies bits. */
        static uint8_t cover[257];
        static bool initialized;
        if (!initialized) {
            uint32_t r=0xC0FEBABEu; unsigned q;
            for(q=0;q<257u;q++) {
                r^=r<<13;r^=r>>17;r^=r<<5;
                cover[q]=(uint8_t)(r&15u);
            }
            initialized=true;
        }
        unsigned ci=(row*113u+(seed&255u))%257u;
        for(col=0;col<320u;col++) {
            unsigned level=cover[ci]; if(++ci==257u) ci=0;
            if (level >= coverage) continue;
            unsigned xx=x+col;
            unsigned bit=(p[xx/8u]>>(7u-xx%8u))&1u;
            d[col]=colors[m[col/20u]+16u*bit];
        }
    }
}
/* Each half has 360 rows. No last-byte lookahead: rightmost source byte
 * is (320+319)/8=79, exactly within the 80-byte row. */
static inline bool noise_origin_valid(unsigned x, unsigned y) {
    return x <= 320u && y <= 480u;
}
static inline uint8_t noise_pixel(const uint8_t *a, const uint8_t *b,
                                 unsigned x, unsigned y) {
    const uint8_t *p = y < 360u ? a : b;
    if (y >= 360u) y -= 360u;
    return (p[y * NOISE_STRIDE + x / 8u] >> (7u - x % 8u)) & 1u;
}
static inline uint32_t noise_next(uint32_t *s) {
    *s ^= *s << 13; *s ^= *s >> 17; *s ^= *s << 5; return *s;
}
#endif
