#ifndef NOISE_H
#define NOISE_H
#include <stdbool.h>
#include <stdint.h>
bool noise_begin(uint8_t scene);
void noise_frame(unsigned elapsed_ms);
/* Scene-only noise; caller must repaint dialogue rows before presentation. */
void noise_frame_scene(unsigned elapsed_ms);
/* Whether the active noise frame covers all 240 rows (rows 180..239 live). */
bool noise_is_fullscreen(void);
/* True once all 5 amortized slices of the current frame are complete in the
 * back buffer and ready for atomic presentation (gfx_SwapDraw). */
bool noise_frame_ready(void);
void noise_end(void);
void noise_restore(void);
void noise_release(void);
#endif
