#ifndef CH5_BANDS_H
#define CH5_BANDS_H
#include <stdint.h>
#include <stdbool.h>
/* Four layer-local band caches, no full CG allocation. A row pointer is
 * valid until another band of that SAME layer is requested or invalidated.
 * begin/end scope data pointers to a read-only draw: no variable mutations,
 * archive GC or storage moves are allowed inside this interval. */
const uint8_t *ch5_band_row(uint8_t layer,uint16_t row);
void ch5_band_invalidate(void);
void ch5_band_begin(void);
void ch5_band_end(void);
bool ch5_motion_draw(uint8_t scene);
bool ch5_motion_active(uint8_t scene);
bool ch5_motion_sync_clock(uint8_t scene);
uint32_t ch5_motion_elapsed_ms(uint8_t scene);
#endif
