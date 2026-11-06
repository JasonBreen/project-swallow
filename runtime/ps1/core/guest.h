/*
 * Project Swallow PS1 core: guest memory as a table of mapped regions.
 *
 * Same contract as swallow_tools.mips.Memory: regions sit at fixed 32-bit
 * addresses and must not overlap; nothing else is mapped. A misaligned or
 * unmapped access, or a write to a read-only region, traps. Each region
 * records one past the highest byte written. The caller owns region buffers.
 */
#ifndef SWALLOW_PS1_CORE_GUEST_H
#define SWALLOW_PS1_CORE_GUEST_H

#include "cpu.h"

/* swallow_guest_map() results. */
#define SWALLOW_MAP_OK 0
#define SWALLOW_MAP_MISALIGNED 1
#define SWALLOW_MAP_OVERLAP 2
#define SWALLOW_MAP_FULL 3
#define SWALLOW_MAP_BAD_NAME 4
#define SWALLOW_MAP_RESERVED 5
/* Calls return to this address; no region may cover it. */
#define SWALLOW_RETURN_ADDRESS 0xFFFFFFF0u

SWALLOW_API int swallow_guest_map(swallow_cpu *cpu, const char *name, uint32_t base,
                                  uint8_t *data, uint32_t size, int writable);
SWALLOW_API void swallow_guest_unmap_all(swallow_cpu *cpu);
/* One past the highest byte written in the named region, or -1 if none is mapped. */
SWALLOW_API int64_t swallow_guest_write_end(const swallow_cpu *cpu, const char *name);

/* Accesses from translated code; pc names the instruction in a trap message. */
uint32_t swallow_load(swallow_cpu *cpu, uint32_t address, uint32_t size, uint32_t pc);
void swallow_store(swallow_cpu *cpu, uint32_t address, uint32_t size, uint32_t value,
                   uint32_t pc);

#endif
