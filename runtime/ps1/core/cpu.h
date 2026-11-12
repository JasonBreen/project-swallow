/*
 * Project Swallow PS1 core: guest CPU state, traps, and arithmetic helpers.
 *
 * Translated MIPS code (swallow-recompile) runs against this state. Every
 * case the original R3000 would raise an exception for, and every case the
 * translation does not model, calls swallow_trap(), which returns control to
 * the innermost swallow_call() with a message. Contract: docs/recompiler.md.
 */
#ifndef SWALLOW_PS1_CORE_CPU_H
#define SWALLOW_PS1_CORE_CPU_H

#include <setjmp.h>
#include <stdint.h>

#include "ps1/gte/gte.h"

#if defined(_WIN32)
#define SWALLOW_API __declspec(dllexport)
#else
#define SWALLOW_API __attribute__((visibility("default")))
#endif

#if defined(_MSC_VER)
#define SWALLOW_NORETURN __declspec(noreturn)
#else
#define SWALLOW_NORETURN __attribute__((noreturn))
#endif

#define SWALLOW_MAX_REGIONS 16
#define SWALLOW_REGION_NAME_BYTES 32
#define SWALLOW_TRAP_MESSAGE_BYTES 256
/* Translated functions active at once, calls and tail calls together. Each
 * is a host C frame, so a guest recursion or tail-call cycle traps here
 * rather than overflowing the host stack. */
#define SWALLOW_MAX_DEPTH 4096u
/* Register indexes beyond the 32 general registers, for get/set_register. */
#define SWALLOW_REG_HI 32
#define SWALLOW_REG_LO 33
/* GTE register indexes for swallow_gte_get/set: data 0-31, control 32-63. */
#define SWALLOW_GTE_REGISTERS 64
/* swallow_call() results. */
#define SWALLOW_OK 0
#define SWALLOW_TRAPPED 1

typedef struct swallow_region {
    char name[SWALLOW_REGION_NAME_BYTES];
    uint32_t base;
    uint32_t size;
    uint8_t *data;
    int writable;
    /* One past the highest byte written, relative to base; 0 if never written. */
    uint32_t write_end;
} swallow_region;

typedef struct swallow_cpu swallow_cpu;
typedef void (*swallow_dispatch_fn)(swallow_cpu *cpu, uint32_t entry);

struct swallow_cpu {
    uint32_t r[32];
    uint32_t hi;
    uint32_t lo;
    swallow_gte gte;
    /* Taken backward branches left before the run traps; the native
     * counterpart of the interpreter's max_steps. */
    uint64_t budget;
    /* Translated functions entered and not yet left (SWALLOW_MAX_DEPTH). */
    uint32_t depth;
    swallow_region regions[SWALLOW_MAX_REGIONS];
    int region_count;
    jmp_buf *trap_target;
    char trap_message[SWALLOW_TRAP_MESSAGE_BYTES];
};

SWALLOW_API swallow_cpu *swallow_cpu_new(void);
SWALLOW_API void swallow_cpu_free(swallow_cpu *cpu);
/* Zero the registers, unmap every region, and make the budget unlimited. */
SWALLOW_API void swallow_cpu_reset(swallow_cpu *cpu);
SWALLOW_API uint32_t swallow_get_register(const swallow_cpu *cpu, int index);
SWALLOW_API void swallow_set_register(swallow_cpu *cpu, int index, uint32_t value);
SWALLOW_API void swallow_set_budget(swallow_cpu *cpu, uint64_t budget);
SWALLOW_API const char *swallow_last_trap(const swallow_cpu *cpu);
/* GTE registers as stored (no read conversion), for tests and the oracle
 * comparison; index 0-31 is data, 32-63 control. */
SWALLOW_API uint32_t swallow_gte_get(const swallow_cpu *cpu, int index);
SWALLOW_API void swallow_gte_set(swallow_cpu *cpu, int index, uint32_t value);
/* Run one GTE command word on the CPU's GTE; a swallow_gte_command() result. */
SWALLOW_API int swallow_gte_execute(swallow_cpu *cpu, uint32_t word);

/* Run dispatch(cpu, entry); SWALLOW_TRAPPED if it trapped, SWALLOW_OK otherwise.
 * After a trap the CPU stays usable: registers hold their values from the
 * last call boundary, and the message is in swallow_last_trap(). */
SWALLOW_API int swallow_call(swallow_cpu *cpu, swallow_dispatch_fn dispatch, uint32_t entry);

SWALLOW_NORETURN void swallow_trap(swallow_cpu *cpu, const char *format, ...);

/* Entering and leaving a translated function; see SWALLOW_MAX_DEPTH. */
static inline void swallow_enter(swallow_cpu *cpu, uint32_t entry)
{
    if (++cpu->depth > SWALLOW_MAX_DEPTH) {
        swallow_trap(cpu, "call depth exceeds %u at 0x%08x", (unsigned)SWALLOW_MAX_DEPTH,
                     (unsigned)entry);
    }
}

static inline void swallow_leave(swallow_cpu *cpu)
{
    --cpu->depth;
}

/* Two's-complement conversions without implementation-defined casts. */
static inline int32_t swallow_s32(uint32_t value)
{
    return value <= 0x7FFFFFFFu ? (int32_t)value : -(int32_t)(~value) - 1;
}

static inline uint32_t swallow_sext8(uint32_t value)
{
    value &= 0xFFu;
    return (value ^ 0x80u) - 0x80u;
}

static inline uint32_t swallow_sext16(uint32_t value)
{
    value &= 0xFFFFu;
    return (value ^ 0x8000u) - 0x8000u;
}

/* Arithmetic shift right; C leaves >> on negative values implementation-defined. */
static inline uint32_t swallow_sra(uint32_t value, uint32_t shift)
{
    shift &= 31u;
    if (shift == 0u) {
        return value;
    }
    if (value & 0x80000000u) {
        return (value >> shift) | ~(0xFFFFFFFFu >> shift);
    }
    return value >> shift;
}

/* add, addi, and sub raise an overflow exception on the R3000. */
static inline uint32_t swallow_add(swallow_cpu *cpu, uint32_t a, uint32_t b, uint32_t pc,
                                   const char *mnemonic)
{
    int64_t sum = (int64_t)swallow_s32(a) + (int64_t)swallow_s32(b);
    if (sum > INT32_MAX || sum < INT32_MIN) {
        swallow_trap(cpu, "%s at 0x%08x overflows; the R3000 would trap", mnemonic,
                     (unsigned)pc);
    }
    return a + b;
}

static inline uint32_t swallow_sub(swallow_cpu *cpu, uint32_t a, uint32_t b, uint32_t pc)
{
    int64_t difference = (int64_t)swallow_s32(a) - (int64_t)swallow_s32(b);
    if (difference > INT32_MAX || difference < INT32_MIN) {
        swallow_trap(cpu, "sub at 0x%08x overflows; the R3000 would trap", (unsigned)pc);
    }
    return a - b;
}

static inline void swallow_mult(uint32_t *hi, uint32_t *lo, uint32_t a, uint32_t b, int is_signed)
{
    uint64_t product;
    if (is_signed) {
        product = (uint64_t)((int64_t)swallow_s32(a) * (int64_t)swallow_s32(b));
    } else {
        product = (uint64_t)a * (uint64_t)b;
    }
    *hi = (uint32_t)(product >> 32);
    *lo = (uint32_t)product;
}

/* div/divu with the R3000's documented results for a zero divisor and for
 * 0x80000000 / -1, where C's / and % are undefined. */
static inline void swallow_divide(uint32_t *hi, uint32_t *lo, uint32_t a, uint32_t b,
                                  int is_signed)
{
    if (b == 0u) {
        *hi = a;
        *lo = (is_signed && (a & 0x80000000u)) ? 1u : 0xFFFFFFFFu;
        return;
    }
    if (!is_signed) {
        *hi = a % b;
        *lo = a / b;
        return;
    }
    if (a == 0x80000000u && b == 0xFFFFFFFFu) {
        *hi = 0u;
        *lo = 0x80000000u;
        return;
    }
    {
        int32_t quotient = swallow_s32(a) / swallow_s32(b);
        int32_t remainder = swallow_s32(a) % swallow_s32(b);
        *hi = (uint32_t)remainder;
        *lo = (uint32_t)quotient;
    }
}

#endif
