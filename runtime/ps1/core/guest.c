/* Project Swallow PS1 core: guest memory regions and checked accesses. */
#include "guest.h"

#include <stdio.h>
#include <string.h>

static uint64_t region_end(const swallow_region *region)
{
    return (uint64_t)region->base + region->size;
}

SWALLOW_API int swallow_guest_map(swallow_cpu *cpu, const char *name, uint32_t base,
                                  uint8_t *data, uint32_t size, int writable)
{
    uint64_t end = (uint64_t)base + size;
    int index;
    swallow_region *region;
    if (name == NULL || name[0] == '\0' || strlen(name) >= SWALLOW_REGION_NAME_BYTES) {
        return SWALLOW_MAP_BAD_NAME;
    }
    if (base % 4u != 0u || end > 0x100000000ull) {
        return SWALLOW_MAP_MISALIGNED;
    }
    if (base <= SWALLOW_RETURN_ADDRESS && SWALLOW_RETURN_ADDRESS < end) {
        return SWALLOW_MAP_RESERVED;
    }
    for (index = 0; index < cpu->region_count; ++index) {
        const swallow_region *other = &cpu->regions[index];
        if (base < region_end(other) && other->base < end) {
            return SWALLOW_MAP_OVERLAP;
        }
    }
    if (cpu->region_count >= SWALLOW_MAX_REGIONS) {
        return SWALLOW_MAP_FULL;
    }
    region = &cpu->regions[cpu->region_count++];
    memset(region, 0, sizeof(*region));
    snprintf(region->name, sizeof(region->name), "%s", name);
    region->base = base;
    region->size = size;
    region->data = data;
    region->writable = writable != 0;
    return SWALLOW_MAP_OK;
}

SWALLOW_API void swallow_guest_unmap_all(swallow_cpu *cpu)
{
    memset(cpu->regions, 0, sizeof(cpu->regions));
    cpu->region_count = 0;
}

SWALLOW_API int64_t swallow_guest_write_end(const swallow_cpu *cpu, const char *name)
{
    int index;
    for (index = 0; index < cpu->region_count; ++index) {
        if (strcmp(cpu->regions[index].name, name) == 0) {
            return (int64_t)cpu->regions[index].write_end;
        }
    }
    return -1;
}

static swallow_region *locate(swallow_cpu *cpu, uint32_t address, uint32_t size,
                              const char *access, uint32_t pc)
{
    int index;
    if (address % size != 0u) {
        swallow_trap(cpu, "Misaligned %u-byte %s at 0x%08x (instruction 0x%08x)",
                     (unsigned)size, access, (unsigned)address, (unsigned)pc);
    }
    for (index = 0; index < cpu->region_count; ++index) {
        swallow_region *region = &cpu->regions[index];
        if (region->base <= address && (uint64_t)address + size <= region_end(region)) {
            return region;
        }
    }
    swallow_trap(cpu, "Unmapped %u-byte %s at 0x%08x (instruction 0x%08x)", (unsigned)size,
                 access, (unsigned)address, (unsigned)pc);
}

uint32_t swallow_load(swallow_cpu *cpu, uint32_t address, uint32_t size, uint32_t pc)
{
    const swallow_region *region = locate(cpu, address, size, "read", pc);
    const uint8_t *bytes = region->data + (address - region->base);
    uint32_t value = 0u;
    uint32_t index;
    for (index = 0u; index < size; ++index) {
        value |= (uint32_t)bytes[index] << (8u * index);
    }
    return value;
}

void swallow_store(swallow_cpu *cpu, uint32_t address, uint32_t size, uint32_t value,
                   uint32_t pc)
{
    swallow_region *region = locate(cpu, address, size, "write", pc);
    uint32_t offset = address - region->base;
    uint32_t index;
    if (!region->writable) {
        swallow_trap(cpu, "Write to read-only region %s at 0x%08x (instruction 0x%08x)",
                     region->name, (unsigned)address, (unsigned)pc);
    }
    for (index = 0u; index < size; ++index) {
        region->data[offset + index] = (uint8_t)(value >> (8u * index));
    }
    if (offset + size > region->write_end) {
        region->write_end = offset + size;
    }
}
