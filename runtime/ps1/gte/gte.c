/*
 * Project Swallow PS1 core: GTE registers and the fifteen commands game code
 * issues. A line-for-line port of swallow_tools.gte; see gte.h.
 *
 * Arithmetic is done in int64_t. Python's >> floors and its << never
 * overflows, while C leaves >> of a negative value implementation-defined
 * and << of one undefined, so shifts of signed values go through
 * asr64() and multiplications by powers of two.
 */
#include "gte.h"

/* Data registers. */
enum {
    VXY0 = 0, VZ0 = 1, RGBC = 6, OTZ = 7,
    IR0 = 8, IR1 = 9, IR2 = 10, IR3 = 11, SXY0 = 12, SXY1 = 13, SXY2 = 14, SXYP = 15,
    SZ0 = 16, SZ1 = 17, SZ2 = 18, SZ3 = 19, RGB0 = 20, RGB1 = 21, RGB2 = 22,
    MAC0 = 24, MAC1 = 25, IRGB = 28, ORGB = 29, LZCS = 30, LZCR = 31
};
/* Control registers. */
enum {
    RT_FIRST = 0, TRX = 5, LLM_FIRST = 8, RBK = 13, LCM_FIRST = 16, RFC = 21,
    OFX = 24, OFY = 25, H = 26, DQA = 27, DQB = 28, ZSF3 = 29, ZSF4 = 30, FLAG = 31
};

#define FLAG_SZ_OTZ 18
#define FLAG_DIVIDE 17
#define FLAG_MAC0_POSITIVE 16
#define FLAG_MAC0_NEGATIVE 15
#define FLAG_SX 14
#define FLAG_SY 13
#define FLAG_IR0 12
#define FLAG_ERROR_MASK 0x7F87E000u
#define FLAG_WRITABLE 0x7FFFF000u

static const int FLAG_MAC_POSITIVE[3] = {30, 29, 28};
static const int FLAG_MAC_NEGATIVE[3] = {27, 26, 25};
static const int FLAG_IR[3] = {24, 23, 22};
static const int FLAG_COLOR[3] = {21, 20, 19};

#define MAC_LIMIT ((int64_t)1 << 43)

typedef struct command {
    int op;
    int shift; /* 12 * sf */
    int lm;
    int mx;
    int v;
    int cv;
} command;

static int is_signed_16(unsigned index)
{
    return index == 1u || index == 3u || index == 5u || (index >= IR0 && index <= IR3);
}

static int is_unsigned_16(unsigned index)
{
    return index == OTZ || (index >= SZ0 && index <= SZ3);
}

static int is_control_16(unsigned index)
{
    return index == 4u || index == 12u || index == 20u || (index >= H && index <= ZSF4 &&
                                                          index != DQB);
}

static int32_t s16(uint32_t value)
{
    value &= 0xFFFFu;
    return (int32_t)value - (int32_t)((value & 0x8000u) << 1);
}

static int32_t s32(uint32_t value)
{
    return value <= 0x7FFFFFFFu ? (int32_t)value : -(int32_t)(~value) - 1;
}

/* Floor division by 2^n, as Python's >> on a negative int. */
static int64_t asr64(int64_t value, int shift)
{
    if (shift == 0 || value >= 0) {
        return value >> shift;
    }
    return -((-(value + 1)) >> shift) - 1;
}

static int64_t wrap44(int64_t value)
{
    uint64_t bits = (uint64_t)value & (((uint64_t)1 << 44) - 1u);
    if (bits & ((uint64_t)1 << 43)) {
        return (int64_t)bits - ((int64_t)1 << 44);
    }
    return (int64_t)bits;
}

static void set_flag(swallow_gte *gte, int bit)
{
    gte->control[FLAG] |= (uint32_t)1 << bit;
}

static uint32_t leading_sign_bits(uint32_t value)
{
    uint32_t count = 0u;
    if (value & 0x80000000u) {
        value = ~value;
    }
    while (count < 32u && !(value & 0x80000000u)) {
        value <<= 1;
        ++count;
    }
    return count;
}

static uint32_t orgb(const swallow_gte *gte)
{
    uint32_t result = 0u;
    int which;
    for (which = 0; which < 3; ++which) {
        int32_t value = s16(gte->data[IR1 + which]);
        value = value < 0 ? -((-value + 127) / 128) : value / 128; /* floor(value / 0x80) */
        if (value < 0) {
            value = 0;
        } else if (value > 0x1F) {
            value = 0x1F;
        }
        result |= (uint32_t)value << (5 * which);
    }
    return result;
}

uint32_t swallow_gte_read_data(const swallow_gte *gte, unsigned index)
{
    index &= 31u;
    if (is_signed_16(index)) {
        return (uint32_t)(int64_t)s16(gte->data[index]) & 0xFFFFFFFFu;
    }
    if (is_unsigned_16(index)) {
        return gte->data[index] & 0xFFFFu;
    }
    if (index == SXYP) {
        return gte->data[SXY2];
    }
    if (index == IRGB || index == ORGB) {
        return orgb(gte);
    }
    if (index == LZCR) {
        return leading_sign_bits(gte->data[LZCS]);
    }
    return gte->data[index];
}

void swallow_gte_write_data(swallow_gte *gte, unsigned index, uint32_t value)
{
    index &= 31u;
    if (is_signed_16(index) || is_unsigned_16(index)) {
        gte->data[index] = value & 0xFFFFu;
    } else if (index == SXYP) {
        gte->data[SXY0] = gte->data[SXY1];
        gte->data[SXY1] = gte->data[SXY2];
        gte->data[SXY2] = value;
    } else if (index == IRGB) {
        int which;
        gte->data[IRGB] = value;
        for (which = 0; which < 3; ++which) {
            gte->data[IR1 + which] = ((value >> (5 * which)) & 0x1Fu) << 7;
        }
    } else if (index == ORGB || index == LZCR) {
        /* Read-only; LZCR follows LZCS. */
    } else {
        gte->data[index] = value;
    }
}

uint32_t swallow_gte_read_control(const swallow_gte *gte, unsigned index)
{
    index &= 31u;
    if (is_control_16(index)) {
        return (uint32_t)(int64_t)s16(gte->control[index]) & 0xFFFFFFFFu;
    }
    if (index == FLAG) {
        uint32_t flag = gte->control[FLAG] & FLAG_WRITABLE;
        return flag | ((flag & FLAG_ERROR_MASK) ? 0x80000000u : 0u);
    }
    return gte->control[index];
}

void swallow_gte_write_control(swallow_gte *gte, unsigned index, uint32_t value)
{
    index &= 31u;
    if (is_control_16(index)) {
        gte->control[index] = value & 0xFFFFu;
    } else if (index == FLAG) {
        gte->control[FLAG] = value & FLAG_WRITABLE;
    } else {
        gte->control[index] = value;
    }
}

/* Typed views of the stored registers. */

static int64_t ir(const swallow_gte *gte, int which)
{
    return s16(gte->data[IR0 + which]);
}

static void vector(const swallow_gte *gte, int which, int64_t out[3])
{
    if (which == 3) {
        out[0] = ir(gte, 1);
        out[1] = ir(gte, 2);
        out[2] = ir(gte, 3);
        return;
    }
    out[0] = s16(gte->data[VXY0 + 2 * which]);
    out[1] = s16(gte->data[VXY0 + 2 * which] >> 16);
    out[2] = s16(gte->data[VZ0 + 2 * which]);
}

static void matrix(const swallow_gte *gte, int first, int64_t out[3][3])
{
    int64_t halves[10];
    int index;
    for (index = 0; index < 5; ++index) {
        uint32_t word = gte->control[first + index];
        halves[2 * index] = s16(word);
        halves[2 * index + 1] = s16(word >> 16);
    }
    for (index = 0; index < 9; ++index) {
        out[index / 3][index % 3] = halves[index];
    }
}

static void translation(const swallow_gte *gte, int first, int64_t out[3])
{
    int offset;
    for (offset = 0; offset < 3; ++offset) {
        out[offset] = s32(gte->control[first + offset]);
    }
}

/* Flag helpers. */

static int64_t check_mac(swallow_gte *gte, int which, int64_t value)
{
    if (value >= MAC_LIMIT) {
        set_flag(gte, FLAG_MAC_POSITIVE[which]);
    } else if (value < -MAC_LIMIT) {
        set_flag(gte, FLAG_MAC_NEGATIVE[which]);
    }
    return wrap44(value);
}

static int64_t check_mac0(swallow_gte *gte, int64_t value)
{
    if (value > 0x7FFFFFFF) {
        set_flag(gte, FLAG_MAC0_POSITIVE);
    } else if (value < -(int64_t)0x80000000) {
        set_flag(gte, FLAG_MAC0_NEGATIVE);
    }
    return value;
}

static int64_t saturate_ir(swallow_gte *gte, int which, int64_t value, int lm)
{
    int64_t low = lm ? 0 : -0x8000;
    if (value < low || value > 0x7FFF) {
        set_flag(gte, FLAG_IR[which]);
        value = value < low ? low : 0x7FFF;
    }
    return value;
}

static void set_mac_ir(swallow_gte *gte, int which, int64_t value, int shift, int lm)
{
    value = asr64(check_mac(gte, which, value), shift);
    gte->data[MAC1 + which] = (uint32_t)(uint64_t)value;
    gte->data[IR1 + which] =
        (uint32_t)(uint64_t)saturate_ir(gte, which, s32((uint32_t)(uint64_t)value), lm) &
        0xFFFFu;
}

static void push_sz(swallow_gte *gte, int64_t value)
{
    if (value < 0 || value > 0xFFFF) {
        set_flag(gte, FLAG_SZ_OTZ);
        value = value < 0 ? 0 : 0xFFFF;
    }
    gte->data[SZ0] = gte->data[SZ1];
    gte->data[SZ1] = gte->data[SZ2];
    gte->data[SZ2] = gte->data[SZ3];
    gte->data[SZ3] = (uint32_t)value;
}

static uint32_t clamp_screen(swallow_gte *gte, int64_t value, int bit)
{
    if (value < -0x400 || value > 0x3FF) {
        set_flag(gte, bit);
        value = value < -0x400 ? -0x400 : 0x3FF;
    }
    return (uint32_t)(uint64_t)value & 0xFFFFu;
}

static void push_sxy(swallow_gte *gte, int64_t x, int64_t y)
{
    uint32_t sx = clamp_screen(gte, x, FLAG_SX);
    uint32_t sy = clamp_screen(gte, y, FLAG_SY);
    gte->data[SXY0] = gte->data[SXY1];
    gte->data[SXY1] = gte->data[SXY2];
    gte->data[SXY2] = sx | (sy << 16);
}

static void push_rgb_from_mac(swallow_gte *gte)
{
    uint32_t components[3];
    int which;
    for (which = 0; which < 3; ++which) {
        int64_t value = asr64(s32(gte->data[MAC1 + which]), 4);
        if (value < 0 || value > 0xFF) {
            set_flag(gte, FLAG_COLOR[which]);
            value = value < 0 ? 0 : 0xFF;
        }
        components[which] = (uint32_t)value;
    }
    gte->data[RGB0] = gte->data[RGB1];
    gte->data[RGB1] = gte->data[RGB2];
    gte->data[RGB2] = components[0] | (components[1] << 8) | (components[2] << 16) |
                      (gte->data[RGBC] & 0xFF000000u);
}

static void set_mac0(swallow_gte *gte, int64_t value)
{
    gte->data[MAC0] = (uint32_t)(uint64_t)check_mac0(gte, value);
}

static void set_otz(swallow_gte *gte, int64_t value)
{
    if (value < 0 || value > 0xFFFF) {
        set_flag(gte, FLAG_SZ_OTZ);
        value = value < 0 ? 0 : 0xFFFF;
    }
    gte->data[OTZ] = (uint32_t)value;
}

/* Command building blocks. */

/* MAC = (T * 0x1000 + M * V) >> shift, with a 44-bit check after every addition. */
static void matrix_vector(swallow_gte *gte, int64_t m[3][3], const int64_t t[3],
                          const int64_t v[3], int shift, int lm)
{
    int row;
    for (row = 0; row < 3; ++row) {
        int64_t total = t[row] * 0x1000;
        int column;
        for (column = 0; column < 3; ++column) {
            total = check_mac(gte, row, total + m[row][column] * v[column]);
        }
        set_mac_ir(gte, row, total, shift, lm);
    }
}

/* MAC = MAC + (FC - MAC) * IR0, then MAC >>= shift. */
static void interpolate_color(swallow_gte *gte, const int64_t macs[3], int shift, int lm)
{
    int64_t far[3];
    int64_t ir0;
    int which;
    translation(gte, RFC, far);
    for (which = 0; which < 3; ++which) {
        set_mac_ir(gte, which, far[which] * 0x1000 - macs[which], shift, 0);
    }
    ir0 = ir(gte, 0);
    for (which = 0; which < 3; ++which) {
        set_mac_ir(gte, which, ir(gte, 1 + which) * ir0 + macs[which], shift, lm);
    }
}

static void depth_cue(swallow_gte *gte, uint32_t color, const command *c)
{
    int64_t macs[3];
    int which;
    for (which = 0; which < 3; ++which) {
        macs[which] = (int64_t)((color >> (8 * which)) & 0xFFu) * 0x10000;
    }
    interpolate_color(gte, macs, c->shift, c->lm);
    push_rgb_from_mac(gte);
}

static uint32_t unr_entry(uint32_t index)
{
    int64_t value = ((int64_t)0x40000 / (int64_t)(index + 0x100u) + 1) / 2 - 0x101;
    return value < 0 ? 0u : (uint32_t)value;
}

/* h / sz3 as RTPS computes it (unsigned 1.16), or -1 when it overflows. */
static int64_t unr_divide(uint32_t h, uint32_t sz3)
{
    int shift = 16;
    uint32_t bits = sz3;
    int64_t n, d, u;
    if (!((uint64_t)h < (uint64_t)sz3 * 2u)) {
        return -1;
    }
    while (bits != 0u) {
        --shift;
        bits >>= 1;
    }
    n = (int64_t)h << shift;
    d = (int64_t)sz3 << shift;
    u = (int64_t)unr_entry((uint32_t)((d - 0x7FC0) >> 7)) + 0x101;
    d = asr64(0x2000080 - d * u, 8);
    d = asr64(0x80 + d * u, 8);
    d = asr64(n * d + 0x8000, 16);
    return d < 0x1FFFF ? d : 0x1FFFF;
}

static void perspective(swallow_gte *gte, const int64_t v[3], const command *c, int last)
{
    int64_t rotation[3][3], offset[3], sums[3];
    int64_t unshifted, mac3, low, quotient, screen_x, screen_y;
    int row;
    matrix(gte, RT_FIRST, rotation);
    translation(gte, TRX, offset);
    for (row = 0; row < 3; ++row) {
        int64_t total = offset[row] * 0x1000;
        int column;
        for (column = 0; column < 3; ++column) {
            total = check_mac(gte, row, total + rotation[row][column] * v[column]);
        }
        sums[row] = total;
        gte->data[MAC1 + row] = (uint32_t)(uint64_t)asr64(total, c->shift);
    }
    for (row = 0; row < 2; ++row) {
        gte->data[IR1 + row] =
            (uint32_t)(uint64_t)saturate_ir(gte, row, s32(gte->data[MAC1 + row]), c->lm) &
            0xFFFFu;
    }
    /* IR3's flag follows MAC3 >> 12 whatever sf is; the value follows MAC3. */
    unshifted = asr64(sums[2], 12);
    mac3 = s32(gte->data[MAC1 + 2]);
    low = c->lm ? 0 : -0x8000;
    if (unshifted < -0x8000 || unshifted > 0x7FFF) {
        set_flag(gte, FLAG_IR[2]);
    }
    mac3 = mac3 < low ? low : mac3;
    mac3 = mac3 > 0x7FFF ? 0x7FFF : mac3;
    gte->data[IR3] = (uint32_t)(uint64_t)mac3 & 0xFFFFu;
    push_sz(gte, unshifted);
    quotient = unr_divide(gte->control[H] & 0xFFFFu, gte->data[SZ3]);
    if (quotient < 0) {
        set_flag(gte, FLAG_DIVIDE);
        quotient = 0x1FFFF;
    }
    screen_x = check_mac0(gte, quotient * ir(gte, 1) + s32(gte->control[OFX]));
    screen_y = check_mac0(gte, quotient * ir(gte, 2) + s32(gte->control[OFY]));
    push_sxy(gte, s32((uint32_t)(uint64_t)asr64(screen_x, 16)),
             s32((uint32_t)(uint64_t)asr64(screen_y, 16)));
    if (last) {
        int64_t depth = quotient * s16(gte->control[DQA]) + s32(gte->control[DQB]);
        int64_t ir0;
        set_mac0(gte, depth);
        ir0 = asr64(depth, 12);
        if (ir0 < 0 || ir0 > 0x1000) {
            set_flag(gte, FLAG_IR0);
            ir0 = ir0 < 0 ? 0 : 0x1000;
        }
        gte->data[IR0] = (uint32_t)ir0;
    }
}

static void normal_color(swallow_gte *gte, const int64_t v[3], const command *c,
                         int multiply_color)
{
    static const int64_t zero[3] = {0, 0, 0};
    int64_t m[3][3], back[3], irs[3];
    matrix(gte, LLM_FIRST, m);
    matrix_vector(gte, m, zero, v, c->shift, c->lm);
    matrix(gte, LCM_FIRST, m);
    translation(gte, RBK, back);
    irs[0] = ir(gte, 1);
    irs[1] = ir(gte, 2);
    irs[2] = ir(gte, 3);
    matrix_vector(gte, m, back, irs, c->shift, c->lm);
    if (multiply_color) {
        uint32_t color = gte->data[RGBC];
        int which;
        for (which = 0; which < 3; ++which) {
            int64_t component = (int64_t)((color >> (8 * which)) & 0xFFu);
            set_mac_ir(gte, which, component * ir(gte, 1 + which) * 16, c->shift, c->lm);
        }
    }
    push_rgb_from_mac(gte);
}

/* Commands. */

static void avsz(swallow_gte *gte, int64_t factor, int64_t sum)
{
    int64_t total = factor * sum;
    set_mac0(gte, total);
    set_otz(gte, asr64(total, 12));
}

static void execute(swallow_gte *gte, const command *c)
{
    int64_t v[3];
    int which;
    gte->control[FLAG] = 0u;
    switch (c->op) {
    case 0x01: /* RTPS */
        vector(gte, 0, v);
        perspective(gte, v, c, 1);
        break;
    case 0x30: /* RTPT */
        for (which = 0; which < 3; ++which) {
            vector(gte, which, v);
            perspective(gte, v, c, which == 2);
        }
        break;
    case 0x06: { /* NCLIP */
        int64_t x0 = s16(gte->data[SXY0]), y0 = s16(gte->data[SXY0] >> 16);
        int64_t x1 = s16(gte->data[SXY1]), y1 = s16(gte->data[SXY1] >> 16);
        int64_t x2 = s16(gte->data[SXY2]), y2 = s16(gte->data[SXY2] >> 16);
        set_mac0(gte, x0 * y1 + x1 * y2 + x2 * y0 - x0 * y2 - x1 * y0 - x2 * y1);
        break;
    }
    case 0x12: { /* MVMVA */
        static const int matrices[3] = {RT_FIRST, LLM_FIRST, LCM_FIRST};
        int64_t m[3][3], t[3] = {0, 0, 0};
        matrix(gte, matrices[c->mx], m);
        if (c->cv != 3) {
            translation(gte, c->cv == 0 ? TRX : RBK, t);
        }
        vector(gte, c->v, v);
        matrix_vector(gte, m, t, v, c->shift, c->lm);
        break;
    }
    case 0x10: /* DPCS */
        depth_cue(gte, gte->data[RGBC], c);
        break;
    case 0x2A: /* DPCT: each push moves the next entry up to RGB0 */
        for (which = 0; which < 3; ++which) {
            depth_cue(gte, gte->data[RGB0], c);
        }
        break;
    case 0x11: { /* INTPL */
        int64_t macs[3];
        for (which = 0; which < 3; ++which) {
            macs[which] = ir(gte, 1 + which) * 0x1000;
        }
        interpolate_color(gte, macs, c->shift, c->lm);
        push_rgb_from_mac(gte);
        break;
    }
    case 0x28: /* SQR */
        for (which = 0; which < 3; ++which) {
            int64_t value = ir(gte, 1 + which);
            set_mac_ir(gte, which, value * value, c->shift, c->lm);
        }
        break;
    case 0x3D: { /* GPF */
        int64_t ir0 = ir(gte, 0);
        for (which = 0; which < 3; ++which) {
            set_mac_ir(gte, which, ir(gte, 1 + which) * ir0, c->shift, c->lm);
        }
        push_rgb_from_mac(gte);
        break;
    }
    case 0x3E: { /* GPL */
        int64_t ir0 = ir(gte, 0);
        for (which = 0; which < 3; ++which) {
            int64_t base = (int64_t)s32(gte->data[MAC1 + which]) * (c->shift ? 0x1000 : 1);
            set_mac_ir(gte, which, ir(gte, 1 + which) * ir0 + base, c->shift, c->lm);
        }
        push_rgb_from_mac(gte);
        break;
    }
    case 0x2D: /* AVSZ3 */
        avsz(gte, s16(gte->control[ZSF3]),
             (int64_t)gte->data[SZ1] + gte->data[SZ2] + gte->data[SZ3]);
        break;
    case 0x2E: /* AVSZ4 */
        avsz(gte, s16(gte->control[ZSF4]),
             (int64_t)gte->data[SZ0] + gte->data[SZ1] + gte->data[SZ2] + gte->data[SZ3]);
        break;
    case 0x1E: /* NCS */
        vector(gte, 0, v);
        normal_color(gte, v, c, 0);
        break;
    case 0x20: /* NCT */
        for (which = 0; which < 3; ++which) {
            vector(gte, which, v);
            normal_color(gte, v, c, 0);
        }
        break;
    case 0x1B: /* NCCS */
        vector(gte, 0, v);
        normal_color(gte, v, c, 1);
        break;
    default:
        break;
    }
}

int swallow_gte_command(swallow_gte *gte, uint32_t word)
{
    command c;
    if (!(word & (1u << 25)) || (word >> 26) != 0x12u) {
        return SWALLOW_GTE_NOT_A_COMMAND;
    }
    c.op = (int)(word & 0x3Fu);
    switch (c.op) {
    case 0x01: case 0x06: case 0x10: case 0x11: case 0x12: case 0x1B: case 0x1E: case 0x20:
    case 0x28: case 0x2A: case 0x2D: case 0x2E: case 0x30: case 0x3D: case 0x3E:
        break;
    default:
        return SWALLOW_GTE_UNMODELLED;
    }
    c.shift = (word & (1u << 19)) ? 12 : 0;
    c.lm = (int)((word >> 10) & 1u);
    c.mx = (int)((word >> 17) & 3u);
    c.v = (int)((word >> 15) & 3u);
    c.cv = (int)((word >> 13) & 3u);
    if (c.op == 0x12 && (c.mx == 3 || c.cv == 2)) {
        return SWALLOW_GTE_UNMODELLED;
    }
    execute(gte, &c);
    return SWALLOW_GTE_OK;
}
