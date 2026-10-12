#ifndef NOISE_H
#define NOISE_H
#include <stdbool.h>
#include <stdint.h>
bool noise_begin(uint8_t scene);
void noise_frame(unsigned elapsed_ms);
void noise_end(void);
void noise_restore(void);
void noise_release(void);
#endif
