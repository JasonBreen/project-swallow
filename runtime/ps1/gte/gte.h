/*
 * Project Swallow PS1 core: the Geometry Transformation Engine (COP2).
 *
 * A C port of swallow_tools.gte, the oracle's model. Registers are stored as
 * the hardware keeps them (16-bit registers hold their low 16 bits) and
 * converted on read, as mfc2/cfc2 see them. Every rule is
 * documentation-derived, exactly as in the Python model; the two must agree
 * bit for bit, which the native self-test checks on random vectors.
 * Contract: docs/mips-interpreter.md (rules) and docs/recompiler.md (port).
 */
#ifndef SWALLOW_PS1_GTE_GTE_H
#define SWALLOW_PS1_GTE_GTE_H

#include <stdint.h>

typedef struct swallow_gte {
    uint32_t data[32];
    uint32_t control[32];
} swallow_gte;

/* swallow_gte_command() results. */
#define SWALLOW_GTE_OK 0
#define SWALLOW_GTE_NOT_A_COMMAND 1
#define SWALLOW_GTE_UNMODELLED 2

uint32_t swallow_gte_read_data(const swallow_gte *gte, unsigned index);
void swallow_gte_write_data(swallow_gte *gte, unsigned index, uint32_t value);
uint32_t swallow_gte_read_control(const swallow_gte *gte, unsigned index);
void swallow_gte_write_control(swallow_gte *gte, unsigned index, uint32_t value);

/* Decode and run one COP2 command word. FLAG is cleared first, as on the
 * hardware. Returns SWALLOW_GTE_OK, or an error code with the state unchanged
 * when the word is not a command or is outside the modelled set (the same
 * words swallow_tools.gte.decode_command rejects). */
int swallow_gte_command(swallow_gte *gte, uint32_t word);

#endif
