"""Geometry Transformation Engine (COP2) model for the oracle interpreter.

Offline validation tooling. It models the registers and the fifteen GTE
commands that Ace Combat 3's game code issues, so swallow_tools.mips can run
game functions that use them. Every rule here comes from public hardware
documentation and the published behaviour of open-source emulators; none has
yet been checked against this project's own hardware or emulator capture, so
docs/mips-interpreter.md labels them documentation-derived. No table or code
is copied: the reciprocal table is generated from its published formula.

Registers are stored as the hardware keeps them (16-bit registers hold their
low 16 bits) and converted on read, as ``mfc2``/``cfc2`` see them.
"""
from __future__ import annotations

from dataclasses import dataclass

WORD_MASK = 0xFFFFFFFF

# Data registers (cop2r0-31).
VXY0, VZ0, VXY1, VZ1, VXY2, VZ2, RGBC, OTZ = range(8)
IR0, IR1, IR2, IR3, SXY0, SXY1, SXY2, SXYP = range(8, 16)
SZ0, SZ1, SZ2, SZ3, RGB0, RGB1, RGB2, RES1 = range(16, 24)
MAC0, MAC1, MAC2, MAC3, IRGB, ORGB, LZCS, LZCR = range(24, 32)
# Control registers (cop2r32-63, numbered 0-31 here as cfc2/ctc2 do).
RT_FIRST, TRX, TRY, TRZ = 0, 5, 6, 7
LLM_FIRST, RBK, GBK, BBK = 8, 13, 14, 15
LCM_FIRST, RFC, GFC, BFC = 16, 21, 22, 23
OFX, OFY, H, DQA, DQB, ZSF3, ZSF4, FLAG = range(24, 32)

# Data registers that hold 16 bits, and how a read extends them.
_DATA_SIGNED_16 = frozenset({VZ0, VZ1, VZ2, IR0, IR1, IR2, IR3})
_DATA_UNSIGNED_16 = frozenset({OTZ, SZ0, SZ1, SZ2, SZ3})
# Control registers that hold 16 bits; all of them read back sign-extended,
# H included, although commands use H as unsigned.
_CONTROL_16 = frozenset({4, 12, 20, H, DQA, ZSF3, ZSF4})

# FLAG bits.
FLAG_MAC_POSITIVE = (30, 29, 28)      # MAC1-3 result larger than 43 bits
FLAG_MAC_NEGATIVE = (27, 26, 25)
FLAG_IR = (24, 23, 22)                # IR1-3 saturated
FLAG_COLOR = (21, 20, 19)             # colour FIFO R, G, B saturated
FLAG_SZ_OTZ = 18                      # SZ3 or OTZ saturated
FLAG_DIVIDE = 17                      # perspective division overflow
FLAG_MAC0_POSITIVE, FLAG_MAC0_NEGATIVE = 16, 15
FLAG_SX, FLAG_SY = 14, 13             # SX2 / SY2 saturated
FLAG_IR0 = 12
FLAG_ERROR_MASK = 0x7F87E000          # bits 30-23 and 18-13 set bit 31
FLAG_WRITABLE = 0x7FFFF000            # bits 0-11 always read as zero


def unr_table() -> tuple[int, ...]:
    """The 257-entry reciprocal seed table used by RTPS/RTPT, from its published formula."""
    return tuple(max(0, (0x40000 // (index + 0x100) + 1) // 2 - 0x101) for index in range(0x101))


UNR_TABLE = unr_table()


def _s16(value: int) -> int:
    value &= 0xFFFF
    return value - 0x10000 if value & 0x8000 else value


def _s32(value: int) -> int:
    value &= WORD_MASK
    return value - 0x100000000 if value & 0x80000000 else value


def _sign_extend_44(value: int) -> int:
    value &= (1 << 44) - 1
    return value - (1 << 44) if value & (1 << 43) else value


def unr_divide(h: int, sz3: int) -> int | None:
    """h / sz3 as RTPS computes it (unsigned 1.16), or None when it overflows."""
    if not h < sz3 * 2:
        return None
    shift = 16 - sz3.bit_length()           # leading zeros of the 16-bit SZ3
    n = h << shift
    d = sz3 << shift
    u = UNR_TABLE[(d - 0x7FC0) >> 7] + 0x101
    d = (0x2000080 - d * u) >> 8
    d = (0x80 + d * u) >> 8
    return min(0x1FFFF, (n * d + 0x8000) >> 16)


@dataclass(frozen=True)
class GteCommand:
    """A decoded GTE command word; fields the hardware ignores are not kept."""
    name: str
    sf: int     # bit 19: shift results right by 12
    lm: int     # bit 10: clamp IR1-3 at 0 instead of -0x8000
    mx: int = 0  # MVMVA: 0 RT, 1 LLM, 2 LCM
    v: int = 0   # MVMVA: 0-2 V0-V2, 3 IR
    cv: int = 0  # MVMVA: 0 TR, 1 BK, 3 none

    @property
    def shift(self) -> int:
        return 12 * self.sf


COMMANDS = {
    0x01: 'rtps', 0x06: 'nclip', 0x10: 'dpcs', 0x11: 'intpl', 0x12: 'mvmva',
    0x1B: 'nccs', 0x1E: 'ncs', 0x20: 'nct', 0x28: 'sqr', 0x2A: 'dpct',
    0x2D: 'avsz3', 0x2E: 'avsz4', 0x30: 'rtpt', 0x3D: 'gpf', 0x3E: 'gpl',
}


class GteError(ValueError):
    """A command or register access outside the modelled set."""


def decode_command(word: int) -> GteCommand:
    """Decode a COP2 command word (bit 25 set). Bits 20-24, 11-12 and 6-9 are ignored.

    PsyQ stores its inline-macro number in bits 20-24; the hardware ignores
    it, so a decoder must too. MVMVA's unused matrix (mx = 3) and its
    far-colour translation (cv = 2), both documented as defective, are
    rejected because game code never uses them.
    """
    if not word & (1 << 25) or word >> 26 != 0x12:
        raise GteError(f'0x{word:08x} is not a GTE command word')
    name = COMMANDS.get(word & 0x3F)
    if name is None:
        raise GteError(f'GTE command 0x{word & 0x3F:02x} in 0x{word:08x} is not modelled')
    sf, lm = (word >> 19) & 1, (word >> 10) & 1
    if name != 'mvmva':
        return GteCommand(name, sf, lm)
    mx, v, cv = (word >> 17) & 3, (word >> 15) & 3, (word >> 13) & 3
    if mx == 3 or cv == 2:
        raise GteError(f'MVMVA with mx={mx}, cv={cv} in 0x{word:08x} is not modelled')
    return GteCommand(name, sf, lm, mx, v, cv)


@dataclass(frozen=True)
class GteState:
    """An immutable snapshot of the 32 data and 32 control registers, as stored."""
    data: tuple[int, ...] = (0,) * 32
    control: tuple[int, ...] = (0,) * 32

    def __post_init__(self) -> None:
        if len(self.data) != 32 or len(self.control) != 32:
            raise GteError('GteState needs 32 data and 32 control registers')

    @classmethod
    def from_registers(cls, data: dict[int, int] | None = None,
                       control: dict[int, int] | None = None) -> 'GteState':
        """Build a state by writing registers as mtc2/ctc2 would, in index order."""
        gte = Gte()
        for index, value in sorted((control or {}).items()):
            gte.write_control(index, value)
        for index, value in sorted((data or {}).items()):
            gte.write_data(index, value)
        return gte.snapshot()

    def read_data(self, index: int) -> int:
        return Gte(self).read_data(index)

    def read_control(self, index: int) -> int:
        return Gte(self).read_control(index)


class Gte:
    """Mutable GTE used while a routine runs; snapshot() returns a GteState."""

    def __init__(self, state: GteState | None = None) -> None:
        state = state or GteState()
        self.d = list(state.data)
        self.c = list(state.control)

    def snapshot(self) -> GteState:
        return GteState(tuple(self.d), tuple(self.c))

    # Register access, as mfc2/mtc2/cfc2/ctc2/lwc2/swc2 see it.

    def read_data(self, index: int) -> int:
        if index in _DATA_SIGNED_16:
            return _s16(self.d[index]) & WORD_MASK
        if index in _DATA_UNSIGNED_16:
            return self.d[index] & 0xFFFF
        if index == SXYP:
            return self.d[SXY2]
        if index in (IRGB, ORGB):
            return self._orgb()
        if index == LZCR:
            return _leading_sign_bits(self.d[LZCS])
        return self.d[index] & WORD_MASK

    def write_data(self, index: int, value: int) -> None:
        value &= WORD_MASK
        if index in _DATA_SIGNED_16 or index in _DATA_UNSIGNED_16:
            self.d[index] = value & 0xFFFF
        elif index == SXYP:
            self.d[SXY0], self.d[SXY1], self.d[SXY2] = self.d[SXY1], self.d[SXY2], value
        elif index == IRGB:
            self.d[IRGB] = value
            for offset, register in enumerate((IR1, IR2, IR3)):
                self.d[register] = ((value >> (5 * offset)) & 0x1F) << 7
        elif index in (ORGB, LZCR):
            pass                                  # read-only; LZCR follows LZCS
        else:
            self.d[index] = value

    def read_control(self, index: int) -> int:
        if index in _CONTROL_16:
            return _s16(self.c[index]) & WORD_MASK
        if index == FLAG:
            flag = self.c[FLAG] & FLAG_WRITABLE
            return flag | (0x80000000 if flag & FLAG_ERROR_MASK else 0)
        return self.c[index] & WORD_MASK

    def write_control(self, index: int, value: int) -> None:
        value &= WORD_MASK
        if index in _CONTROL_16:
            self.c[index] = value & 0xFFFF
        elif index == FLAG:
            self.c[FLAG] = value & FLAG_WRITABLE
        else:
            self.c[index] = value

    def _orgb(self) -> int:
        result = 0
        for offset, register in enumerate((IR1, IR2, IR3)):
            result |= min(0x1F, max(0, _s16(self.d[register]) >> 7)) << (5 * offset)
        return result

    # Typed views of the stored registers.

    def _ir(self, which: int) -> int:
        return _s16(self.d[IR0 + which])

    def _vector(self, which: int) -> tuple[int, int, int]:
        if which == 3:
            return self._ir(1), self._ir(2), self._ir(3)
        xy = self.d[VXY0 + 2 * which]
        return _s16(xy), _s16(xy >> 16), _s16(self.d[VZ0 + 2 * which])

    def _matrix(self, first: int) -> tuple[tuple[int, int, int], ...]:
        halves = []
        for index in range(first, first + 5):
            word = self.c[index]
            halves += [_s16(word), _s16(word >> 16)]
        return tuple(tuple(halves[row * 3:row * 3 + 3]) for row in range(3))

    def _translation(self, first: int) -> tuple[int, int, int]:
        return tuple(_s32(self.c[first + offset]) for offset in range(3))

    # Flag helpers.

    def _set_flag(self, bit: int) -> None:
        self.c[FLAG] |= 1 << bit

    def _check_mac(self, which: int, value: int) -> int:
        """Check a MAC1-3 sum against 44 bits, set its flag, and wrap it as the hardware does."""
        if value >= 1 << 43:
            self._set_flag(FLAG_MAC_POSITIVE[which])
        elif value < -(1 << 43):
            self._set_flag(FLAG_MAC_NEGATIVE[which])
        return _sign_extend_44(value)

    def _check_mac0(self, value: int) -> int:
        if value > 0x7FFFFFFF:
            self._set_flag(FLAG_MAC0_POSITIVE)
        elif value < -0x80000000:
            self._set_flag(FLAG_MAC0_NEGATIVE)
        return value

    def _saturate_ir(self, which: int, value: int, lm: int) -> int:
        low = 0 if lm else -0x8000
        if value < low or value > 0x7FFF:
            self._set_flag(FLAG_IR[which])
            value = low if value < low else 0x7FFF
        return value

    def _set_mac_ir(self, which: int, value: int, shift: int, lm: int) -> None:
        value = self._check_mac(which, value) >> shift
        self.d[MAC1 + which] = value & WORD_MASK
        self.d[IR1 + which] = self._saturate_ir(which, _s32(value), lm) & 0xFFFF

    def _push_sz(self, value: int) -> None:
        if value < 0 or value > 0xFFFF:
            self._set_flag(FLAG_SZ_OTZ)
            value = 0 if value < 0 else 0xFFFF
        self.d[SZ0], self.d[SZ1], self.d[SZ2], self.d[SZ3] = (
            self.d[SZ1], self.d[SZ2], self.d[SZ3], value)

    def _push_sxy(self, x: int, y: int) -> None:
        coordinates = []
        for value, bit in ((x, FLAG_SX), (y, FLAG_SY)):
            if value < -0x400 or value > 0x3FF:
                self._set_flag(bit)
                value = -0x400 if value < -0x400 else 0x3FF
            coordinates.append(value & 0xFFFF)
        self.d[SXY0], self.d[SXY1] = self.d[SXY1], self.d[SXY2]
        self.d[SXY2] = coordinates[0] | (coordinates[1] << 16)

    def _push_rgb_from_mac(self) -> None:
        components = []
        for which in range(3):
            value = _s32(self.d[MAC1 + which]) >> 4
            if value < 0 or value > 0xFF:
                self._set_flag(FLAG_COLOR[which])
                value = 0 if value < 0 else 0xFF
            components.append(value)
        code = self.d[RGBC] & 0xFF000000
        self.d[RGB0], self.d[RGB1] = self.d[RGB1], self.d[RGB2]
        self.d[RGB2] = components[0] | (components[1] << 8) | (components[2] << 16) | code

    def _set_mac0(self, value: int) -> None:
        self.d[MAC0] = self._check_mac0(value) & WORD_MASK

    def _set_otz(self, value: int) -> None:
        if value < 0 or value > 0xFFFF:
            self._set_flag(FLAG_SZ_OTZ)
            value = 0 if value < 0 else 0xFFFF
        self.d[OTZ] = value

    # Command building blocks.

    def _matrix_vector(self, matrix, translation, vector, shift: int, lm: int) -> list[int]:
        """MAC = (T * 0x1000 + M * V) >> shift with a 44-bit check after every addition."""
        sums = []
        for row in range(3):
            total = translation[row] << 12
            for column in range(3):
                total = self._check_mac(row, total + matrix[row][column] * vector[column])
            sums.append(total)
            self._set_mac_ir(row, total, shift, lm)
        return sums

    def _interpolate_color(self, macs: list[int], shift: int, lm: int) -> None:
        """MAC = MAC + (FC - MAC) * IR0, then MAC >>= shift."""
        far = self._translation(RFC)
        for which in range(3):
            self._set_mac_ir(which, (far[which] << 12) - macs[which], shift, 0)
        ir0 = self._ir(0)
        for which in range(3):
            self._set_mac_ir(which, self._ir(1 + which) * ir0 + macs[which], shift, lm)

    def _depth_cue(self, color: int, command: GteCommand) -> None:
        macs = [((color >> (8 * which)) & 0xFF) << 16 for which in range(3)]
        self._interpolate_color(macs, command.shift, command.lm)
        self._push_rgb_from_mac()

    def _perspective(self, vector, command: GteCommand, last: bool) -> None:
        sums = []
        rotation, translation = self._matrix(RT_FIRST), self._translation(TRX)
        for row in range(3):
            total = translation[row] << 12
            for column in range(3):
                total = self._check_mac(row, total + rotation[row][column] * vector[column])
            sums.append(total)
            self.d[MAC1 + row] = (total >> command.shift) & WORD_MASK
        for row in range(2):
            self.d[IR1 + row] = self._saturate_ir(
                row, _s32(self.d[MAC1 + row]), command.lm) & 0xFFFF
        # IR3's flag follows MAC3 >> 12 whatever sf is; the value follows MAC3.
        unshifted = sums[2] >> 12
        mac3 = _s32(self.d[MAC3])
        low = 0 if command.lm else -0x8000
        if unshifted < -0x8000 or unshifted > 0x7FFF:
            self._set_flag(FLAG_IR[2])
        self.d[IR3] = min(0x7FFF, max(low, mac3)) & 0xFFFF
        self._push_sz(unshifted)
        quotient = unr_divide(self.c[H] & 0xFFFF, self.d[SZ3])
        if quotient is None:
            self._set_flag(FLAG_DIVIDE)
            quotient = 0x1FFFF
        offset_x, offset_y = _s32(self.c[OFX]), _s32(self.c[OFY])
        screen_x = self._check_mac0(quotient * self._ir(1) + offset_x)
        screen_y = self._check_mac0(quotient * self._ir(2) + offset_y)
        self._push_sxy(_s32(screen_x >> 16), _s32(screen_y >> 16))
        if last:
            depth = quotient * _s16(self.c[DQA]) + _s32(self.c[DQB])
            self._set_mac0(depth)
            ir0 = depth >> 12
            if ir0 < 0 or ir0 > 0x1000:
                self._set_flag(FLAG_IR0)
                ir0 = 0 if ir0 < 0 else 0x1000
            self.d[IR0] = ir0

    def _normal_color(self, vector, command: GteCommand, multiply_color: bool) -> None:
        shift, lm = command.shift, command.lm
        self._matrix_vector(self._matrix(LLM_FIRST), (0, 0, 0), vector, shift, lm)
        self._matrix_vector(self._matrix(LCM_FIRST), self._translation(RBK),
                            (self._ir(1), self._ir(2), self._ir(3)), shift, lm)
        if multiply_color:
            color = self.d[RGBC]
            for which in range(3):
                component = (color >> (8 * which)) & 0xFF
                self._set_mac_ir(which, (component * self._ir(1 + which)) << 4, shift, lm)
        self._push_rgb_from_mac()

    # Commands.

    def execute(self, command: GteCommand) -> None:
        """Run one command; FLAG is cleared first, as on the hardware."""
        self.c[FLAG] = 0
        getattr(self, f'_cmd_{command.name}')(command)

    def _cmd_rtps(self, command: GteCommand) -> None:
        self._perspective(self._vector(0), command, last=True)

    def _cmd_rtpt(self, command: GteCommand) -> None:
        for which in range(3):
            self._perspective(self._vector(which), command, last=which == 2)

    def _cmd_nclip(self, command: GteCommand) -> None:
        points = [(_s16(self.d[register]), _s16(self.d[register] >> 16))
                  for register in (SXY0, SXY1, SXY2)]
        (x0, y0), (x1, y1), (x2, y2) = points
        self._set_mac0(x0 * y1 + x1 * y2 + x2 * y0 - x0 * y2 - x1 * y0 - x2 * y1)

    def _cmd_mvmva(self, command: GteCommand) -> None:
        matrix = self._matrix((RT_FIRST, LLM_FIRST, LCM_FIRST)[command.mx])
        translation = ((0, 0, 0) if command.cv == 3 else
                       self._translation((TRX, RBK)[command.cv]))
        self._matrix_vector(matrix, translation, self._vector(command.v),
                            command.shift, command.lm)

    def _cmd_dpcs(self, command: GteCommand) -> None:
        self._depth_cue(self.d[RGBC], command)

    def _cmd_dpct(self, command: GteCommand) -> None:
        for _ in range(3):
            self._depth_cue(self.d[RGB0], command)     # the push moves the next entry up

    def _cmd_intpl(self, command: GteCommand) -> None:
        macs = [self._ir(1 + which) << 12 for which in range(3)]
        self._interpolate_color(macs, command.shift, command.lm)
        self._push_rgb_from_mac()

    def _cmd_sqr(self, command: GteCommand) -> None:
        for which in range(3):
            value = self._ir(1 + which)
            self._set_mac_ir(which, value * value, command.shift, command.lm)

    def _cmd_gpf(self, command: GteCommand) -> None:
        ir0 = self._ir(0)
        for which in range(3):
            self._set_mac_ir(which, self._ir(1 + which) * ir0, command.shift, command.lm)
        self._push_rgb_from_mac()

    def _cmd_gpl(self, command: GteCommand) -> None:
        ir0 = self._ir(0)
        for which in range(3):
            base = _s32(self.d[MAC1 + which]) << command.shift
            self._set_mac_ir(which, self._ir(1 + which) * ir0 + base, command.shift,
                             command.lm)
        self._push_rgb_from_mac()

    def _cmd_avsz3(self, command: GteCommand) -> None:
        total = _s16(self.c[ZSF3]) * (self.d[SZ1] + self.d[SZ2] + self.d[SZ3])
        self._set_mac0(total)
        self._set_otz(total >> 12)

    def _cmd_avsz4(self, command: GteCommand) -> None:
        total = _s16(self.c[ZSF4]) * (self.d[SZ0] + self.d[SZ1] + self.d[SZ2] + self.d[SZ3])
        self._set_mac0(total)
        self._set_otz(total >> 12)

    def _cmd_ncs(self, command: GteCommand) -> None:
        self._normal_color(self._vector(0), command, multiply_color=False)

    def _cmd_nct(self, command: GteCommand) -> None:
        for which in range(3):
            self._normal_color(self._vector(which), command, multiply_color=False)

    def _cmd_nccs(self, command: GteCommand) -> None:
        self._normal_color(self._vector(0), command, multiply_color=True)


def _leading_sign_bits(value: int) -> int:
    """LZCR: how many leading bits of LZCS equal its sign bit (1-32)."""
    value &= WORD_MASK
    if value & 0x80000000:
        value = ~value & WORD_MASK
    return 32 - value.bit_length()
