#ifndef CH5_BANDS_H
#define CH5_BANDS_H
#include <stdint.h>
#include <stdbool.h>
/* Four dedicated originals only. Pointer valid until next row call.
 * One 2560B shared band cache, no full CG allocation. */
const uint8_t *ch5_band_row(uint8_t layer,uint16_t row);
void ch5_band_invalidate(void);
bool ch5_motion_draw(uint8_t scene);
bool ch5_motion_active(uint8_t scene);
bool ch5_motion_sync_clock(uint8_t scene);
uint32_t ch5_motion_elapsed_ms(uint8_t scene);
#endif
