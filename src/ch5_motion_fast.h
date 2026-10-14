#ifndef CH5_MOTION_FAST_H
#define CH5_MOTION_FAST_H
#include <stdint.h>
#include <stddef.h>
/* count is <=320; all source offsets have been clipped before this call. */
void ch5_motion_opaque_fast(uint8_t *dst,const uint8_t *src,
                            const int16_t *map,size_t count);
/* Partial-alpha table is fg*32+groups[bg], source 255 is transparent. */
void ch5_motion_lut_fast(uint8_t *dst,const uint8_t *src,
                         const int16_t *map,size_t count,
                         const uint8_t *groups,const uint8_t *lut);
/* Returns consumed pixels up to the first uncached opaque pair. */
size_t ch5_motion_cached_fast(uint8_t*,const uint8_t*,const int16_t*,size_t,const uint16_t*,const uint8_t*);
#endif
