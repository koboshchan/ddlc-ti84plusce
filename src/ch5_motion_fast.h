#ifndef CH5_MOTION_FAST_H
#define CH5_MOTION_FAST_H
#include <stdint.h>
#include <stddef.h>
/* count is <=320; all source offsets have been clipped before this call. */
void ch5_motion_opaque_fast(uint8_t *dst,const uint8_t *src,
                            const int16_t *map,size_t count);
#endif
