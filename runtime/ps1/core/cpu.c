/* Project Swallow PS1 core: CPU state, traps, and the call boundary. */
#include "cpu.h"

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

SWALLOW_API swallow_cpu *swallow_cpu_new(void)
{
    swallow_cpu *cpu = (swallow_cpu *)calloc(1, sizeof(swallow_cpu));
    if (cpu != NULL) {
        swallow_cpu_reset(cpu);
    }
    return cpu;
}

SWALLOW_API void swallow_cpu_free(swallow_cpu *cpu)
{
    free(cpu);
}

SWALLOW_API void swallow_cpu_reset(swallow_cpu *cpu)
{
    memset(cpu, 0, sizeof(*cpu));
    cpu->budget = UINT64_MAX;
}

SWALLOW_API uint32_t swallow_get_register(const swallow_cpu *cpu, int index)
{
    if (index >= 0 && index < 32) {
        return index == 0 ? 0u : cpu->r[index];
    }
    if (index == SWALLOW_REG_HI) {
        return cpu->hi;
    }
    if (index == SWALLOW_REG_LO) {
        return cpu->lo;
    }
    return 0u;
}

SWALLOW_API void swallow_set_register(swallow_cpu *cpu, int index, uint32_t value)
{
    if (index > 0 && index < 32) {
        cpu->r[index] = value;
    } else if (index == SWALLOW_REG_HI) {
        cpu->hi = value;
    } else if (index == SWALLOW_REG_LO) {
        cpu->lo = value;
    }
}

SWALLOW_API void swallow_set_budget(swallow_cpu *cpu, uint64_t budget)
{
    cpu->budget = budget;
}

SWALLOW_API const char *swallow_last_trap(const swallow_cpu *cpu)
{
    return cpu->trap_message;
}

SWALLOW_API uint32_t swallow_gte_get(const swallow_cpu *cpu, int index)
{
    if (index >= 0 && index < 32) {
        return cpu->gte.data[index];
    }
    if (index >= 32 && index < SWALLOW_GTE_REGISTERS) {
        return cpu->gte.control[index - 32];
    }
    return 0u;
}

SWALLOW_API void swallow_gte_set(swallow_cpu *cpu, int index, uint32_t value)
{
    if (index >= 0 && index < 32) {
        cpu->gte.data[index] = value;
    } else if (index >= 32 && index < SWALLOW_GTE_REGISTERS) {
        cpu->gte.control[index - 32] = value;
    }
}

SWALLOW_API int swallow_gte_execute(swallow_cpu *cpu, uint32_t word)
{
    return swallow_gte_command(&cpu->gte, word);
}

SWALLOW_API int swallow_call(swallow_cpu *cpu, swallow_dispatch_fn dispatch, uint32_t entry)
{
    /* Volatile, so their values survive longjmp back into this frame. */
    swallow_cpu *volatile state = cpu;
    jmp_buf *volatile previous = cpu->trap_target;
    jmp_buf target;
    state->trap_message[0] = '\0';
    if (setjmp(target) != 0) {
        state->trap_target = previous;
        return SWALLOW_TRAPPED;
    }
    state->trap_target = &target;
    dispatch(state, entry);
    state->trap_target = previous;
    return SWALLOW_OK;
}

void swallow_trap(swallow_cpu *cpu, const char *format, ...)
{
    va_list arguments;
    va_start(arguments, format);
    vsnprintf(cpu->trap_message, sizeof(cpu->trap_message), format, arguments);
    va_end(arguments);
    if (cpu->trap_target != NULL) {
        longjmp(*cpu->trap_target, 1);
    }
    fprintf(stderr, "swallow_trap outside swallow_call: %s\n", cpu->trap_message);
    abort();
}
