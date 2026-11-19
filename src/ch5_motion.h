#ifndef CH5_MOTION_H
#define CH5_MOTION_H
#include <stdint.h>
#include <string.h>
/* Authored viewport pixels in Q8; source cgs.rpy 231-311.
 * Sampling quantized to 250ms (4fps), no accumulated delta or frame assets. */
typedef struct { int32_t x, y; uint32_t zoom_q10; uint16_t alpha_q10; } ch5_motion_t;

/* Authored RenPy cosine warpers evaluated at the permitted 250ms steps.
 * Each offset rounded ONCE to Q8, background amplitude before rounding. */
static const int16_t ch5_x_fg[24] = {0,640,1920,2560,2240,1600,1280,640,-640,-1280,-1152,-896,-768,-1216,-2112,-2560,-1920,-640,0,320,960,1280,960,320};
static const int16_t ch5_x_bg[24] = {0,160,480,640,560,400,320,160,-160,-320,-288,-224,-192,-304,-528,-640,-480,-160,0,80,240,320,240,80};
static const int16_t ch5_y_fg[16] = {0,187,640,1093,1280,1183,905,490,0,-490,-905,-1183,-1280,-790,-375,-97};
static const int16_t ch5_y_bg[16] = {0,47,160,273,320,296,226,122,0,-122,-226,-296,-320,-198,-94,-24};

/* Exact 32-bit computation of (a * 125 * b * 256) / 10^12 = (a * b) / 31,250,000,
 * matching truncated 64-bit signed division without pulling in __lldivs. */
static int32_t ch5_muldiv_1e12_256(int32_t a, uint32_t b) {
    uint32_t u = (a < 0) ? (uint32_t)(-a) : (uint32_t)a;
    uint32_t u_q = u / 125u, u_r = u % 125u;
    uint32_t b_q = b / 125u, b_r = b % 125u;
    uint32_t mid = u_q * b_r + u_r * b_q + (u_r * b_r) / 125u;
    uint32_t mag = (u_q * b_q + mid / 125u) / 2000u;
    return (a < 0) ? -(int32_t)mag : (int32_t)mag;
}

static ch5_motion_t ch5_motion(unsigned ms, unsigned mode, unsigned width, unsigned height) {
    static const uint8_t bg_alpha[10] = {20,20,25,20,30,25,35,30,35,20};
    static const uint8_t fg_alpha[10] = {30,30,40,30,50,40,60,50,60,40};
    ch5_motion_t p;
    unsigned t = (ms / 250u) * 250u;
    uint32_t xa = 0, ya = 0;
    p.zoom_q10 = 1000000UL;
    p.alpha_q10 = 1024;
    p.x = 0;
    p.y = 0;

    if (mode == 1) {
        uint32_t q = t > 4000u ? 4000u : t;
        p.zoom_q10 = 1100000UL - 25UL * q;
        xa = ya = 500000UL;
    } else if (mode == 2) {
        uint32_t q = t > 4000u ? 4000u : t;
        p.zoom_q10 = 800000UL - 50000UL * q / 4000UL;
        xa = 300000UL + 15000UL * q / 4000UL;
        ya = 250000UL;
        p.y = (int32_t)(10L * 256L * (int32_t)q / 4000L);
    } else if (mode >= 3) {
        p.zoom_q10 = 2000000UL;
        xa = (mode == 3 || mode == 5) ? 200000UL : 500000UL;
        ya = (mode == 3 || mode == 5) ? 300000UL : 50000UL;
    }

    int32_t z125 = (int32_t)(p.zoom_q10 / 125UL);
    p.x = ch5_muldiv_1e12_256(10240000L - (int32_t)width * z125, xa);
    p.y += ch5_muldiv_1e12_256(5760000L - (int32_t)height * z125, ya);

    if (mode >= 3) {
        unsigned bg = (mode == 3 || mode == 5);
        unsigned dt = (mode == 7) ? (t >= 500u ? t - 500u : 0u) : t;
        p.x += (bg ? ch5_x_bg : ch5_x_fg)[(dt % 6000u) / 250u];
        p.y += (bg ? ch5_y_bg : ch5_y_fg)[(dt % 4000u) / 250u];
    }
    if (mode == 5 || mode == 6) {
        unsigned at = ms % 2250u, seg = at / 250u, phase = at % 250u;
        const uint8_t *a = (mode == 5) ? bg_alpha : fg_alpha;
        uint32_t numerator = (uint32_t)a[seg] * (250u - phase) + (uint32_t)a[seg + 1] * phase;
        p.alpha_q10 = (uint16_t)((numerator * 1024UL + 12500UL) / 25000UL);
    } else if (mode == 7) {
        p.alpha_q10 = 512;
    }
    return p;
}

/* Source viewport Q8 -> quarter-resolution texture using exact 32-bit math
 * (gcd(1024, 1000000) = 64 removed: num = -origin * 15625, den = scale * 16). */
static void ch5_axis_map(int16_t *map, unsigned count, int32_t origin,
                         uint32_t scale, unsigned texture_extent) {
    uint32_t den = scale * 16UL;
    uint32_t mag = (origin < 0) ? (uint32_t)(-origin) : (uint32_t)origin;
    uint32_t t1 = mag * 125UL;
    uint32_t q1 = t1 / den, r1 = t1 % den;
    uint32_t t2 = r1 * 125UL;
    uint32_t q_abs = q1 * 125UL + t2 / den;
    uint32_t r_abs = t2 % den;
    int32_t source, remainder;
    if (origin <= 0) {
        source = (int32_t)q_abs;
        remainder = (int32_t)r_abs;
    } else if (r_abs == 0) {
        source = -(int32_t)q_abs;
        remainder = 0;
    } else {
        source = -(int32_t)q_abs - 1;
        remainder = (int32_t)(den - r_abs);
    }
    int32_t step = 16000000L;
    int32_t delta = step / (int32_t)den;
    int32_t delta_remainder = step % (int32_t)den;
    for (unsigned i = 0; i < count; i++) {
        if (source >= (int32_t)texture_extent) {
            memset(map + i, 255, (count - i) * sizeof *map);
            break;
        }
        map[i] = (source < 0) ? -1 : (int16_t)source;
        source += delta;
        remainder += delta_remainder;
        if (remainder >= (int32_t)den) {
            remainder -= (int32_t)den;
            source++;
        }
    }
}
/* Independent per-pixel source sampler for host axis-map verification. */
static inline int ch5_source_pixel(int x, int y, ch5_motion_t p,
                                   unsigned tw, unsigned th,
                                   unsigned *sx, unsigned *sy) {
    uint32_t den = p.zoom_q10 * 16UL;
    int64_t nx = (int64_t)(-p.x) * 15625LL + (int64_t)x * 16000000LL;
    int64_t ny = (int64_t)(-p.y) * 15625LL + (int64_t)y * 16000000LL;
    int64_t qx = (nx >= 0) ? (nx / den) : -((-nx + (int64_t)den - 1) / den);
    int64_t qy = (ny >= 0) ? (ny / den) : -((-ny + (int64_t)den - 1) / den);
    if (qx < 0 || (uint64_t)qx >= tw || qy < 0 || (uint64_t)qy >= th) {
        return 0;
    }
    *sx = (unsigned)qx;
    *sy = (unsigned)qy;
    return 1;
}
#endif
