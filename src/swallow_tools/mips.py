"""R3000A interpreter for running original routines in isolation, as an oracle.

Offline validation tooling, not an emulator. It executes the MIPS I
instructions Ace Combat 3's game code uses, including multiply and divide,
unaligned loads and stores, and the GTE (coprocessor 2, swallow_tools.gte),
over explicitly mapped memory regions, so a game routine can be run on the
same input as a project decoder or generated C and the results compared.
There are no interrupts, caches, cycle timing, or hardware registers.

Every case the interpreter does not model faithfully stops execution with
``MipsError`` instead of guessing: an instruction outside the subset, an
unmapped or misaligned access, a write to a read-only region, a branch in a
delay slot, any instruction that reads or writes a register in the delay slot
of the load that targets it (``lwl`` followed by ``lwr`` is the one pair that
merges), and the timing cases MIPS I or the GTE leave undefined. A case where
the R3000 itself would raise an exception (``break``, ``syscall``, or an
``add``/``addi``/``sub`` overflow) stops with ``MipsTrap``, a result of the
original code rather than a limit of the interpreter.
Contract: docs/mips-interpreter.md.
"""
from __future__ import annotations

from dataclasses import dataclass
import struct

from .gte import COMMANDS as GTE_COMMANDS, Gte, GteError, GteState, decode_command

WORD_MASK = 0xFFFFFFFF
# Execution stops when control reaches this address; it must stay unmapped.
RETURN_ADDRESS = 0xFFFFFFF0
REGISTER_NAMES = (
    'zero', 'at', 'v0', 'v1', 'a0', 'a1', 'a2', 'a3',
    't0', 't1', 't2', 't3', 't4', 't5', 't6', 't7',
    's0', 's1', 's2', 's3', 's4', 's5', 's6', 's7',
    't8', 't9', 'k0', 'k1', 'gp', 'sp', 'fp', 'ra',
)
_REGISTER_INDEX = {name: index for index, name in enumerate(REGISTER_NAMES)}
# Every mnemonic the decoder below accepts; any other instruction stops a run.
SUPPORTED_MNEMONICS = frozenset({
    'sll', 'srl', 'sra', 'sllv', 'srlv', 'srav', 'jr', 'jalr',
    'add', 'addu', 'sub', 'subu', 'and', 'or', 'xor', 'nor', 'slt', 'sltu',
    'mult', 'multu', 'div', 'divu', 'mfhi', 'mflo', 'mthi', 'mtlo',
    'bltz', 'bgez', 'j', 'jal', 'beq', 'bne', 'blez', 'bgtz',
    'addi', 'addiu', 'slti', 'sltiu', 'andi', 'ori', 'xori', 'lui',
    'lb', 'lh', 'lw', 'lbu', 'lhu', 'sb', 'sh', 'sw',
    'lwl', 'lwr', 'swl', 'swr',
    'mfc2', 'cfc2', 'mtc2', 'ctc2', 'lwc2', 'swc2', *GTE_COMMANDS.values(),
})
# Decoded, but running one raises MipsTrap: the R3000 would take an exception.
TRAP_MNEMONICS = frozenset({'break', 'syscall'})
# For the timing checks in call(): GTE commands, GTE register writes, every
# COP2 instruction, and the instructions that write HI/LO.
_GTE_COMMANDS = frozenset(GTE_COMMANDS.values())
_COP2_WRITES = frozenset({'mtc2', 'ctc2', 'lwc2'})
_COP2 = frozenset({'mfc2', 'cfc2', 'mtc2', 'ctc2', 'lwc2', 'swc2'}) | _GTE_COMMANDS
_HILO_WRITES = frozenset({'mult', 'multu', 'div', 'divu', 'mthi', 'mtlo'})


class MipsError(ValueError):
    """The routine left the modelled subset; the run stops instead of guessing."""


class MipsTrap(MipsError):
    """The original code would raise an exception here: a result, not a limitation.

    ``reason`` is ``break``, ``syscall``, or ``overflow``. For ``break`` and
    ``syscall``, ``raw_code`` is the 20-bit field in bits 6-25 and ``code`` is
    the value the GNU assembler and spimdisasm print: bits 16-25 for
    ``break`` (``break 7`` is 0x0007000D) and the whole field for ``syscall``.
    """

    def __init__(self, message: str, *, pc: int, mnemonic: str, reason: str,
                 code: int | None = None, raw_code: int | None = None) -> None:
        super().__init__(message)
        self.pc = pc
        self.mnemonic = mnemonic
        self.reason = reason
        self.code = code
        self.raw_code = raw_code


def register_index(name: str) -> int:
    try:
        return _REGISTER_INDEX[name.removeprefix('$')]
    except KeyError:
        raise MipsError(f'Unknown register name {name!r}') from None


def _signed(value: int) -> int:
    return value - 0x100000000 if value & 0x80000000 else value


def _checked(value: int, mnemonic: str, pc: int) -> int:
    if not -0x80000000 <= value <= 0x7FFFFFFF:
        raise MipsTrap(f'{mnemonic} at 0x{pc:08x} overflows; the R3000 would trap',
                       pc=pc, mnemonic=mnemonic, reason='overflow')
    return value & WORD_MASK


def divide(dividend: int, divisor: int, signed: bool) -> tuple[int, int]:
    """(HI, LO) after div/divu, including the documented R3000 results.

    By zero: HI = dividend; LO = -1 for a non-negative signed dividend, +1
    for a negative one, and 0xFFFFFFFF for divu. 0x80000000 / -1 gives
    HI = 0, LO = 0x80000000. These come from public documentation and are
    not yet checked by this project (docs/mips-interpreter.md).
    """
    dividend &= WORD_MASK
    divisor &= WORD_MASK
    if divisor == 0:
        if not signed:
            return dividend, WORD_MASK
        return dividend, (1 if dividend & 0x80000000 else WORD_MASK)
    if not signed:
        return dividend % divisor, dividend // divisor
    a, b = _signed(dividend), _signed(divisor)
    if a == -0x80000000 and b == -1:
        return 0, 0x80000000
    quotient = abs(a) // abs(b)
    if (a < 0) != (b < 0):
        quotient = -quotient
    return (a - quotient * b) & WORD_MASK, quotient & WORD_MASK


@dataclass
class Region:
    name: str
    base: int
    data: bytearray
    writable: bool
    executable: bool
    # One past the highest byte written, relative to base; 0 if never written.
    write_end: int = 0

    @property
    def end(self) -> int:
        return self.base + len(self.data)


class Memory:
    """Non-overlapping regions at fixed 32-bit addresses; nothing else is mapped."""

    def __init__(self) -> None:
        self.regions: list[Region] = []

    def map(self, name: str, base: int, data: bytes | bytearray, *, writable: bool = False,
            executable: bool = False) -> Region:
        if base % 4 or base < 0 or base + len(data) > 0x100000000:
            raise MipsError(f'Region {name} at 0x{base:08x} is not word aligned in 32 bits')
        if writable and executable:
            # Instructions are decoded once per address, so code must not change.
            raise MipsError(f'Region {name} cannot be both writable and executable')
        region = Region(name, base, bytearray(data), writable, executable)
        for other in self.regions:
            if region.base < other.end and other.base < region.end:
                raise MipsError(f'Region {name} overlaps {other.name}')
        if region.base <= RETURN_ADDRESS < region.end:
            raise MipsError(f'Region {name} covers the reserved return address')
        self.regions.append(region)
        return region

    def region(self, name: str) -> Region:
        for region in self.regions:
            if region.name == name:
                return region
        raise MipsError(f'No region named {name}')

    def _locate(self, address: int, size: int, access: str) -> tuple[Region, int]:
        if address % size:
            raise MipsError(f'Misaligned {size}-byte {access} at 0x{address:08x}')
        for region in self.regions:
            if region.base <= address and address + size <= region.end:
                return region, address - region.base
        raise MipsError(f'Unmapped {size}-byte {access} at 0x{address:08x}')

    def load(self, address: int, size: int, signed: bool = False) -> int:
        region, offset = self._locate(address, size, 'read')
        return int.from_bytes(region.data[offset:offset + size], 'little', signed=signed)

    def store(self, address: int, size: int, value: int) -> None:
        region, offset = self._locate(address, size, 'write')
        if not region.writable:
            raise MipsError(f'Write to read-only region {region.name} at 0x{address:08x}')
        region.data[offset:offset + size] = (value & ((1 << (8 * size)) - 1)).to_bytes(
            size, 'little')
        region.write_end = max(region.write_end, offset + size)

    def _locate_bytes(self, address: int, count: int, access: str) -> tuple[Region, int]:
        for region in self.regions:
            if region.base <= address and address + count <= region.end:
                return region, address - region.base
        raise MipsError(f'Unmapped {count}-byte unaligned {access} at 0x{address:08x}')

    def load_bytes(self, address: int, count: int) -> bytes:
        """Read exactly these bytes, without an alignment rule (lwl/lwr)."""
        region, offset = self._locate_bytes(address, count, 'read')
        return bytes(region.data[offset:offset + count])

    def store_bytes(self, address: int, data: bytes) -> None:
        """Write exactly these bytes, without an alignment rule (swl/swr)."""
        region, offset = self._locate_bytes(address, len(data), 'write')
        if not region.writable:
            raise MipsError(f'Write to read-only region {region.name} at 0x{address:08x}')
        region.data[offset:offset + len(data)] = data
        region.write_end = max(region.write_end, offset + len(data))

    def fetch(self, address: int) -> int:
        region, offset = self._locate(address, 4, 'instruction fetch')
        if not region.executable:
            raise MipsError(f'Instruction fetch from non-executable region {region.name} '
                            f'at 0x{address:08x}')
        return struct.unpack_from('<I', region.data, offset)[0]


@dataclass(frozen=True)
class RunResult:
    registers: tuple[int, ...]
    steps: int
    hi: int = 0
    lo: int = 0
    gte: GteState | None = None

    def __getitem__(self, name: str) -> int:
        return self.registers[register_index(name)]


@dataclass(frozen=True)
class _Decoded:
    run: object        # callable(cpu) -> int | None (branch target) or None
    reads: frozenset[int]
    writes: frozenset[int]
    is_branch: bool
    mnemonic: str
    # (mnemonic, rt, base) for lwl/lwr, so an lwl/lwr pair can merge.
    pair: tuple[str, int, int] | None = None


class _Cpu:
    def __init__(self, memory: Memory, gte: Gte) -> None:
        self.memory = memory
        self.gte = gte
        self.r = [0] * 32
        self.hi = 0
        self.lo = 0
        self.pc = 0
        self.pending: tuple[int, int] | None = None   # load issued by this instruction
        # The in-flight lwl result an lwr in its delay slot merges into.
        self.forward: int | None = None


def _decode(word: int, pc: int) -> _Decoded:
    op = word >> 26
    rs = (word >> 21) & 31
    rt = (word >> 16) & 31
    rd = (word >> 11) & 31
    sa = (word >> 6) & 31
    funct = word & 63
    imm = word & 0xFFFF
    simm = imm - 0x10000 if imm & 0x8000 else imm

    def decoded(run, reads, writes, mnemonic, is_branch=False, pair=None):
        return _Decoded(run, frozenset(r for r in reads if r), frozenset(w for w in writes if w),
                        is_branch, mnemonic, pair)

    def unsupported():
        raise MipsError(f'Unsupported instruction 0x{word:08x} at 0x{pc:08x}')

    if op == 0:
        if funct in (0x00, 0x02, 0x03) and rs:
            unsupported()
        if funct == 0x00:
            def run(c): c.r[rd] = (c.r[rt] << sa) & WORD_MASK
            return decoded(run, (rt,), (rd,), 'sll')
        if funct == 0x02:
            def run(c): c.r[rd] = c.r[rt] >> sa
            return decoded(run, (rt,), (rd,), 'srl')
        if funct == 0x03:
            def run(c): c.r[rd] = (_signed(c.r[rt]) >> sa) & WORD_MASK
            return decoded(run, (rt,), (rd,), 'sra')
        if funct in (0x04, 0x06, 0x07) and sa:
            unsupported()
        if funct == 0x04:
            def run(c): c.r[rd] = (c.r[rt] << (c.r[rs] & 31)) & WORD_MASK
            return decoded(run, (rs, rt), (rd,), 'sllv')
        if funct == 0x06:
            def run(c): c.r[rd] = c.r[rt] >> (c.r[rs] & 31)
            return decoded(run, (rs, rt), (rd,), 'srlv')
        if funct == 0x07:
            def run(c): c.r[rd] = (_signed(c.r[rt]) >> (c.r[rs] & 31)) & WORD_MASK
            return decoded(run, (rs, rt), (rd,), 'srav')
        if funct == 0x08:
            if rt or rd or sa:
                unsupported()
            def run(c): return c.r[rs]
            return decoded(run, (rs,), (), 'jr', True)
        if funct == 0x09:
            if rt or sa or rd == rs:
                unsupported()
            def run(c):
                target = c.r[rs]
                c.r[rd] = (c.pc + 8) & WORD_MASK
                return target
            return decoded(run, (rs,), (rd,), 'jalr', True)
        if funct in (0x0C, 0x0D):
            mnemonic = 'syscall' if funct == 0x0C else 'break'
            raw_code = (word >> 6) & 0xFFFFF
            code = raw_code if mnemonic == 'syscall' else raw_code >> 10
            def run(c):
                raise MipsTrap(f'{mnemonic} {code} at 0x{c.pc:08x}; the R3000 would take an '
                               'exception', pc=c.pc, mnemonic=mnemonic, reason=mnemonic,
                               code=code, raw_code=raw_code)
            return decoded(run, (), (), mnemonic)
        if sa:
            unsupported()
        if funct in (0x10, 0x12):
            if rs or rt:
                unsupported()
            if funct == 0x10:
                def run(c): c.r[rd] = c.hi
                return decoded(run, (), (rd,), 'mfhi')
            def run(c): c.r[rd] = c.lo
            return decoded(run, (), (rd,), 'mflo')
        if funct in (0x11, 0x13):
            if rt or rd:
                unsupported()
            if funct == 0x11:
                def run(c): c.hi = c.r[rs]
                return decoded(run, (rs,), (), 'mthi')
            def run(c): c.lo = c.r[rs]
            return decoded(run, (rs,), (), 'mtlo')
        if funct in (0x18, 0x19, 0x1A, 0x1B):
            if rd:
                unsupported()
            if funct == 0x18:
                def run(c):
                    product = _signed(c.r[rs]) * _signed(c.r[rt])
                    c.hi, c.lo = (product >> 32) & WORD_MASK, product & WORD_MASK
                return decoded(run, (rs, rt), (), 'mult')
            if funct == 0x19:
                def run(c):
                    product = c.r[rs] * c.r[rt]
                    c.hi, c.lo = product >> 32, product & WORD_MASK
                return decoded(run, (rs, rt), (), 'multu')
            signed = funct == 0x1A
            def run(c): c.hi, c.lo = divide(c.r[rs], c.r[rt], signed)
            return decoded(run, (rs, rt), (), 'div' if signed else 'divu')
        if funct == 0x20:
            def run(c): c.r[rd] = _checked(_signed(c.r[rs]) + _signed(c.r[rt]), 'add', c.pc)
            return decoded(run, (rs, rt), (rd,), 'add')
        if funct == 0x21:
            def run(c): c.r[rd] = (c.r[rs] + c.r[rt]) & WORD_MASK
            return decoded(run, (rs, rt), (rd,), 'addu')
        if funct == 0x22:
            def run(c): c.r[rd] = _checked(_signed(c.r[rs]) - _signed(c.r[rt]), 'sub', c.pc)
            return decoded(run, (rs, rt), (rd,), 'sub')
        if funct == 0x23:
            def run(c): c.r[rd] = (c.r[rs] - c.r[rt]) & WORD_MASK
            return decoded(run, (rs, rt), (rd,), 'subu')
        if funct == 0x24:
            def run(c): c.r[rd] = c.r[rs] & c.r[rt]
            return decoded(run, (rs, rt), (rd,), 'and')
        if funct == 0x25:
            def run(c): c.r[rd] = c.r[rs] | c.r[rt]
            return decoded(run, (rs, rt), (rd,), 'or')
        if funct == 0x26:
            def run(c): c.r[rd] = c.r[rs] ^ c.r[rt]
            return decoded(run, (rs, rt), (rd,), 'xor')
        if funct == 0x27:
            def run(c): c.r[rd] = ~(c.r[rs] | c.r[rt]) & WORD_MASK
            return decoded(run, (rs, rt), (rd,), 'nor')
        if funct == 0x2A:
            def run(c): c.r[rd] = int(_signed(c.r[rs]) < _signed(c.r[rt]))
            return decoded(run, (rs, rt), (rd,), 'slt')
        if funct == 0x2B:
            def run(c): c.r[rd] = int(c.r[rs] < c.r[rt])
            return decoded(run, (rs, rt), (rd,), 'sltu')
        unsupported()
    if op == 1:
        if rt not in (0, 1):
            unsupported()
        target = (pc + 4 + (simm << 2)) & WORD_MASK
        if rt == 0:
            def run(c): return target if c.r[rs] & 0x80000000 else None
            return decoded(run, (rs,), (), 'bltz', True)
        def run(c): return None if c.r[rs] & 0x80000000 else target
        return decoded(run, (rs,), (), 'bgez', True)
    if op in (2, 3):
        target = ((pc + 4) & 0xF0000000) | ((word & 0x03FFFFFF) << 2)
        if op == 2:
            def run(c): return target
            return decoded(run, (), (), 'j', True)
        def run(c):
            c.r[31] = (c.pc + 8) & WORD_MASK
            return target
        return decoded(run, (), (31,), 'jal', True)
    if op in (4, 5, 6, 7):
        if op in (6, 7) and rt:
            unsupported()
        target = (pc + 4 + (simm << 2)) & WORD_MASK
        if op == 4:
            def run(c): return target if c.r[rs] == c.r[rt] else None
            return decoded(run, (rs, rt), (), 'beq', True)
        if op == 5:
            def run(c): return target if c.r[rs] != c.r[rt] else None
            return decoded(run, (rs, rt), (), 'bne', True)
        if op == 6:
            def run(c): return target if _signed(c.r[rs]) <= 0 else None
            return decoded(run, (rs,), (), 'blez', True)
        def run(c): return target if _signed(c.r[rs]) > 0 else None
        return decoded(run, (rs,), (), 'bgtz', True)
    if op == 0x08:
        def run(c): c.r[rt] = _checked(_signed(c.r[rs]) + simm, 'addi', c.pc)
        return decoded(run, (rs,), (rt,), 'addi')
    if op == 0x09:
        def run(c): c.r[rt] = (c.r[rs] + simm) & WORD_MASK
        return decoded(run, (rs,), (rt,), 'addiu')
    if op == 0x0A:
        def run(c): c.r[rt] = int(_signed(c.r[rs]) < simm)
        return decoded(run, (rs,), (rt,), 'slti')
    if op == 0x0B:
        def run(c): c.r[rt] = int(c.r[rs] < (simm & WORD_MASK))
        return decoded(run, (rs,), (rt,), 'sltiu')
    if op == 0x0C:
        def run(c): c.r[rt] = c.r[rs] & imm
        return decoded(run, (rs,), (rt,), 'andi')
    if op == 0x0D:
        def run(c): c.r[rt] = c.r[rs] | imm
        return decoded(run, (rs,), (rt,), 'ori')
    if op == 0x0E:
        def run(c): c.r[rt] = c.r[rs] ^ imm
        return decoded(run, (rs,), (rt,), 'xori')
    if op == 0x0F:
        if rs:
            unsupported()
        value = imm << 16
        def run(c): c.r[rt] = value
        return decoded(run, (), (rt,), 'lui')
    loads = {0x20: ('lb', 1, True), 0x21: ('lh', 2, True), 0x23: ('lw', 4, False),
             0x24: ('lbu', 1, False), 0x25: ('lhu', 2, False)}
    if op in loads:
        mnemonic, size, signed = loads[op]
        def run(c):
            value = c.memory.load((c.r[rs] + simm) & WORD_MASK, size, signed) & WORD_MASK
            c.pending = (rt, value)
        # The loaded register is written after the next instruction, not here;
        # it is listed so a second load into it in that slot is a hazard too.
        return decoded(run, (rs,), (rt,), mnemonic)
    stores = {0x28: ('sb', 1), 0x29: ('sh', 2), 0x2B: ('sw', 4)}
    if op in stores:
        mnemonic, size = stores[op]
        def run(c): c.memory.store((c.r[rs] + simm) & WORD_MASK, size, c.r[rt])
        return decoded(run, (rs, rt), (), mnemonic)
    if op in (0x22, 0x26):
        # Little-endian: lwl fills the high bytes of rt from [address & ~3, address],
        # lwr the low bytes from [address, address | 3]; the rest of rt is kept.
        left = op == 0x22
        mnemonic = 'lwl' if left else 'lwr'
        def run(c):
            address = (c.r[rs] + simm) & WORD_MASK
            shift = address & 3
            old = c.forward if c.forward is not None else c.r[rt]
            if left:
                data = c.memory.load_bytes(address - shift, shift + 1)
                value = old.to_bytes(4, 'little')[:3 - shift] + data
            else:
                data = c.memory.load_bytes(address, 4 - shift)
                value = data + old.to_bytes(4, 'little')[4 - shift:]
            c.pending = (rt, int.from_bytes(value, 'little'))
        return decoded(run, (rs, rt), (rt,), mnemonic, pair=(mnemonic, rt, rs))
    if op in (0x2A, 0x2E):
        left = op == 0x2A
        mnemonic = 'swl' if left else 'swr'
        def run(c):
            address = (c.r[rs] + simm) & WORD_MASK
            shift = address & 3
            value = c.r[rt].to_bytes(4, 'little')
            if left:
                c.memory.store_bytes(address - shift, value[3 - shift:])
            else:
                c.memory.store_bytes(address, value[:4 - shift])
        return decoded(run, (rs, rt), (), mnemonic)
    if op == 0x12:
        if word & (1 << 25):
            try:
                command = decode_command(word)
            except GteError as exc:
                raise MipsError(f'{exc} at 0x{pc:08x}') from None
            def run(c): c.gte.execute(command)
            return decoded(run, (), (), command.name)
        if word & 0x7FF or rs not in (0, 2, 4, 6):
            unsupported()
        if rs == 0:
            def run(c): c.pending = (rt, c.gte.read_data(rd))
            return decoded(run, (), (rt,), 'mfc2')
        if rs == 2:
            def run(c): c.pending = (rt, c.gte.read_control(rd))
            return decoded(run, (), (rt,), 'cfc2')
        if rs == 4:
            def run(c): c.gte.write_data(rd, c.r[rt])
            return decoded(run, (rt,), (), 'mtc2')
        def run(c): c.gte.write_control(rd, c.r[rt])
        return decoded(run, (rt,), (), 'ctc2')
    if op == 0x32:
        def run(c): c.gte.write_data(rt, c.memory.load((c.r[rs] + simm) & WORD_MASK, 4))
        return decoded(run, (rs,), (), 'lwc2')
    if op == 0x3A:
        def run(c): c.memory.store((c.r[rs] + simm) & WORD_MASK, 4, c.gte.read_data(rt))
        return decoded(run, (rs,), (), 'swc2')
    unsupported()


def call(memory: Memory, entry: int, arguments: dict[str, int] | None = None, *,
         max_steps: int, hi: int = 0, lo: int = 0, gte: GteState | None = None) -> RunResult:
    """Run from ``entry`` until it returns to ``RETURN_ADDRESS``; return the final state.

    ``arguments`` sets registers by name before the first instruction; every
    other register starts at zero and ``$ra`` holds ``RETURN_ADDRESS``. ``hi``,
    ``lo``, and ``gte`` give the starting HI, LO, and GTE registers (all zero
    when omitted). Branch delay slots execute as on the R3000. A load,
    ``mfc2``, or ``cfc2`` result becomes visible only after the following
    instruction, and that instruction may not touch the loaded register, so
    no behaviour depends on interlock details. The exception is an ``lwr``
    right after an ``lwl`` into the same register from the same base, which
    merges into the ``lwl`` result as the hardware does.

    These are refused because MIPS I or the GTE leaves them undefined, and
    game code never does them: ``mult``/``div``/``mthi``/``mtlo`` within two
    instructions after ``mfhi``/``mflo``; a GTE command within two
    instructions after a GTE register write; a GTE register write right after
    a command; and any COP2 instruction in a branch delay slot.
    """
    cpu = _Cpu(memory, Gte(gte))
    cpu.r[31] = RETURN_ADDRESS
    cpu.hi, cpu.lo = hi & WORD_MASK, lo & WORD_MASK
    for name, value in (arguments or {}).items():
        index = register_index(name)
        if index == 0:
            raise MipsError('$zero cannot be set')
        cpu.r[index] = value & WORD_MASK
    cache: dict[int, _Decoded] = {}
    pc, next_pc = entry & WORD_MASK, (entry + 4) & WORD_MASK
    in_delay_slot = False
    load: tuple[int, int] | None = None
    previous: _Decoded | None = None
    # Step numbers of the last HI/LO read, GTE register write, and GTE command.
    last_hilo_read = last_cop2_write = last_command = -10
    steps = 0
    while not (pc == RETURN_ADDRESS and not in_delay_slot):
        if steps >= max_steps:
            raise MipsError(f'Routine did not return within {max_steps} instructions')
        instruction = cache.get(pc)
        if instruction is None:
            instruction = cache[pc] = _decode(memory.fetch(pc), pc)
        mnemonic = instruction.mnemonic
        cpu.forward = None
        if load is not None and (load[0] in instruction.reads or load[0] in instruction.writes):
            pair = instruction.pair
            if (pair is not None and pair[0] == 'lwr' and previous is not None
                    and previous.pair == ('lwl', pair[1], pair[2]) and pair[1] != pair[2]):
                cpu.forward, load = load[1], None
            else:
                raise MipsError(f'{mnemonic} at 0x{pc:08x} uses ${REGISTER_NAMES[load[0]]} '
                                'in the delay slot of its load')
        if instruction.is_branch and in_delay_slot:
            raise MipsError(f'Branch {mnemonic} at 0x{pc:08x} is in a delay slot')
        if in_delay_slot and mnemonic in _COP2:
            raise MipsError(f'{mnemonic} at 0x{pc:08x} is in a branch delay slot')
        if mnemonic in _HILO_WRITES and steps - last_hilo_read <= 2:
            raise MipsError(f'{mnemonic} at 0x{pc:08x} follows mfhi/mflo within two '
                            'instructions, which MIPS I leaves undefined')
        if mnemonic in _GTE_COMMANDS and steps - last_cop2_write <= 2:
            raise MipsError(f'GTE command {mnemonic} at 0x{pc:08x} follows a GTE register '
                            'write within two instructions')
        if mnemonic in _COP2_WRITES and steps - last_command == 1:
            raise MipsError(f'{mnemonic} at 0x{pc:08x} writes a GTE register right after '
                            'a GTE command')
        cpu.pc = pc
        cpu.pending = None
        target = instruction.run(cpu)
        if load is not None:
            cpu.r[load[0]] = load[1]
        load = cpu.pending if cpu.pending and cpu.pending[0] else None
        cpu.r[0] = 0
        if mnemonic in ('mfhi', 'mflo'):
            last_hilo_read = steps
        elif mnemonic in _COP2_WRITES:
            last_cop2_write = steps
        elif mnemonic in _GTE_COMMANDS:
            last_command = steps
        steps += 1
        following = (next_pc + 4) & WORD_MASK
        if target is not None:
            following = target
        in_delay_slot = instruction.is_branch
        previous = instruction
        pc, next_pc = next_pc, following
    if load is not None:
        cpu.r[load[0]] = load[1]
    return RunResult(tuple(cpu.r), steps, cpu.hi, cpu.lo, cpu.gte.snapshot())
