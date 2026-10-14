#ifndef DDLC_PERF_MEMORY_H
#define DDLC_PERF_MEMORY_H
#include <stddef.h>
#include <stdbool.h>
#ifdef DDLC_PERF_OVERLAY
typedef struct { size_t bytes, peak; unsigned count; bool complete; } perf_memory_t;
void *perf_memory_malloc(size_t bytes);
void perf_memory_free(void *ptr);
perf_memory_t perf_memory_snapshot(void);
#ifndef DDLC_PERF_MEMORY_IMPL
#define malloc(bytes) perf_memory_malloc(bytes)
#define free(ptr) perf_memory_free(ptr)
#endif
#endif
#endif
