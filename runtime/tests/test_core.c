/* Unit tests for the Project Swallow PS1 core; synthetic data only. */
#include "ps1/core/cpu.h"
#include "ps1/core/guest.h"

#include <stdio.h>
#include <string.h>

static int failures;

static void check(int passed, const char *text, int line)
{
    if (!passed) {
        printf("FAIL test_core.c:%d: %s\n", line, text);
        ++failures;
    }
}

#define CHECK(condition) check((condition) != 0, #condition, __LINE__)

enum {
    ACTION_STORE_AND_LOAD = 1,
    ACTION_MISALIGNED,
    ACTION_UNMAPPED,
    ACTION_READ_ONLY,
    ACTION_OVERFLOW,
    ACTION_EXPLICIT_TRAP
};

static void actions(swallow_cpu *cpu, uint32_t entry)
{
    switch (entry) {
    case ACTION_STORE_AND_LOAD:
        swallow_store(cpu, 0x80100004u, 2, 0xBEEFu, 0x1000u);
        swallow_store(cpu, 0x80100001u, 1, 0x1FFu, 0x1004u);
        cpu->r[2] = swallow_load(cpu, 0x80100004u, 4, 0x1008u);
        cpu->r[3] = swallow_sext8(swallow_load(cpu, 0x80100001u, 1, 0x100Cu));
        break;
    case ACTION_MISALIGNED:
        (void)swallow_load(cpu, 0x80100002u, 4, 0x2000u);
        break;
    case ACTION_UNMAPPED:
        (void)swallow_load(cpu, 0x90000000u, 4, 0x2004u);
        break;
    case ACTION_READ_ONLY:
        swallow_store(cpu, 0x80010000u, 4, 0u, 0x2008u);
        break;
    case ACTION_OVERFLOW:
        cpu->r[2] = swallow_add(cpu, 0x7FFFFFFFu, 1u, 0x200Cu, "addi");
        break;
    default:
        swallow_trap(cpu, "unknown action %u", (unsigned)entry);
    }
}

static void test_mapping_rules(void)
{
    swallow_cpu *cpu = swallow_cpu_new();
    uint8_t a[16], b[16];
    CHECK(cpu != NULL);
    CHECK(swallow_guest_map(cpu, "a", 0x1000u, a, sizeof(a), 0) == SWALLOW_MAP_OK);
    CHECK(swallow_guest_map(cpu, "b", 0x1008u, b, sizeof(b), 0) == SWALLOW_MAP_OVERLAP);
    CHECK(swallow_guest_map(cpu, "b", 0x2002u, b, 4, 0) == SWALLOW_MAP_MISALIGNED);
    CHECK(swallow_guest_map(cpu, "b", 0xFFFFFF00u, b, 0x100u, 0) == SWALLOW_MAP_RESERVED);
    CHECK(swallow_guest_map(cpu, "", 0x3000u, b, 4, 0) == SWALLOW_MAP_BAD_NAME);
    CHECK(swallow_guest_write_end(cpu, "a") == 0);
    CHECK(swallow_guest_write_end(cpu, "missing") == -1);
    swallow_cpu_free(cpu);
}

static void test_accesses_and_trap_recovery(void)
{
    swallow_cpu *cpu = swallow_cpu_new();
    uint8_t text[8] = {0};
    uint8_t data[8] = {0};
    CHECK(swallow_guest_map(cpu, "text", 0x80010000u, text, sizeof(text), 0) == SWALLOW_MAP_OK);
    CHECK(swallow_guest_map(cpu, "data", 0x80100000u, data, sizeof(data), 1) == SWALLOW_MAP_OK);

    CHECK(swallow_call(cpu, actions, ACTION_STORE_AND_LOAD) == SWALLOW_OK);
    CHECK(swallow_get_register(cpu, 2) == 0x0000BEEFu);
    CHECK(swallow_get_register(cpu, 3) == 0xFFFFFFFFu);
    CHECK(data[1] == 0xFFu && data[4] == 0xEFu && data[5] == 0xBEu);
    CHECK(swallow_guest_write_end(cpu, "data") == 6);

    CHECK(swallow_call(cpu, actions, ACTION_MISALIGNED) == SWALLOW_TRAPPED);
    CHECK(strstr(swallow_last_trap(cpu), "Misaligned 4-byte read at 0x80100002") != NULL);
    CHECK(swallow_call(cpu, actions, ACTION_UNMAPPED) == SWALLOW_TRAPPED);
    CHECK(strstr(swallow_last_trap(cpu), "Unmapped 4-byte read at 0x90000000") != NULL);
    CHECK(swallow_call(cpu, actions, ACTION_READ_ONLY) == SWALLOW_TRAPPED);
    CHECK(strstr(swallow_last_trap(cpu), "read-only region text") != NULL);
    CHECK(swallow_call(cpu, actions, ACTION_OVERFLOW) == SWALLOW_TRAPPED);
    CHECK(strstr(swallow_last_trap(cpu), "addi at 0x0000200c overflows") != NULL);
    CHECK(swallow_call(cpu, actions, ACTION_EXPLICIT_TRAP) == SWALLOW_TRAPPED);

    /* The CPU is usable after a trap, and a clean call clears the message. */
    CHECK(swallow_call(cpu, actions, ACTION_STORE_AND_LOAD) == SWALLOW_OK);
    CHECK(swallow_last_trap(cpu)[0] == '\0');
    CHECK(text[0] == 0u);
    swallow_cpu_free(cpu);
}

static void test_registers(void)
{
    swallow_cpu *cpu = swallow_cpu_new();
    swallow_set_register(cpu, 0, 5u);
    swallow_set_register(cpu, 31, 7u);
    swallow_set_register(cpu, SWALLOW_REG_HI, 8u);
    swallow_set_register(cpu, SWALLOW_REG_LO, 9u);
    CHECK(swallow_get_register(cpu, 0) == 0u);
    CHECK(swallow_get_register(cpu, 31) == 7u);
    CHECK(swallow_get_register(cpu, SWALLOW_REG_HI) == 8u);
    CHECK(swallow_get_register(cpu, SWALLOW_REG_LO) == 9u);
    CHECK(cpu->budget == UINT64_MAX);
    swallow_cpu_free(cpu);
}

static void test_arithmetic(void)
{
    uint32_t hi, lo;
    CHECK(swallow_s32(0x80000000u) == INT32_MIN);
    CHECK(swallow_s32(0xFFFFFFFFu) == -1);
    CHECK(swallow_sext16(0x8000u) == 0xFFFF8000u);
    CHECK(swallow_sra(0x80000000u, 4u) == 0xF8000000u);
    CHECK(swallow_sra(0x40000000u, 4u) == 0x04000000u);
    CHECK(swallow_sra(0x80000000u, 0u) == 0x80000000u);

    swallow_mult(&hi, &lo, 0xFFFFFFFFu, 2u, 1);
    CHECK(hi == 0xFFFFFFFFu && lo == 0xFFFFFFFEu);
    swallow_mult(&hi, &lo, 0xFFFFFFFFu, 2u, 0);
    CHECK(hi == 1u && lo == 0xFFFFFFFEu);
    swallow_divide(&hi, &lo, 0xFFFFFFF9u, 2u, 1);          /* -7 / 2 */
    CHECK(hi == 0xFFFFFFFFu && lo == 0xFFFFFFFDu);
    /* Documentation-derived R3000 results (docs/mips-interpreter.md). */
    swallow_divide(&hi, &lo, 5u, 0u, 1);
    CHECK(hi == 5u && lo == 0xFFFFFFFFu);
    swallow_divide(&hi, &lo, 0xFFFFFFFBu, 0u, 1);
    CHECK(hi == 0xFFFFFFFBu && lo == 1u);
    swallow_divide(&hi, &lo, 0xFFFFFFFBu, 0u, 0);
    CHECK(hi == 0xFFFFFFFBu && lo == 0xFFFFFFFFu);
    swallow_divide(&hi, &lo, 0x80000000u, 0xFFFFFFFFu, 1);
    CHECK(hi == 0u && lo == 0x80000000u);
}

int main(void)
{
    test_mapping_rules();
    test_accesses_and_trap_recovery();
    test_registers();
    test_arithmetic();
    if (failures != 0) {
        printf("%d check(s) failed\n", failures);
        return 1;
    }
    printf("core: all checks passed\n");
    return 0;
}
