#ifndef NOISE_PRNG_H
#define NOISE_PRNG_H
#include <stdint.h>
/* Primitive 16-bit Fibonacci Galois LFSR x^16+x^14+x^13+x^11+1.
 * Zero is excluded; caller remaps zero seed to one. */
static inline uint16_t noise_lfsr16(uint16_t s) {
 return (s>>1)^((uint16_t)-(int16_t)(s&1u)&0xB400u);
}
static inline uint32_t noise_xorshift32(uint32_t s) {
 s^=s<<13;s^=s>>17;s^=s<<5;return s;
}
#endif
