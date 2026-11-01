"""Minimal R3000A integer interpreter for running one original routine in isolation.

Offline validation tooling, not an emulator. It executes a bounded subset of
the MIPS I integer instruction set over explicitly mapped memory regions, so a
game routine can be run on the same input as a project decoder and the results
compared. There are no coprocessors, exceptions, interrupts, caches, timing,
or hardware registers. Every case the interpreter does not model faithfully
stops execution with ``MipsError`` instead of guessing: an instruction outside
the subset, an unmapped or misaligned access, a write to a read-only region,
an ``add``/``addi``/``sub`` overflow (a trap on the R3000), a branch in a
delay slot, and any instruction that reads or writes a register in the delay
slot of the load that targets it. Contract: docs/ulz-routine.md.
"""
from __future__ import annotations

from dataclasses import dataclass
import struct

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


class MipsError(ValueError):
    """The routine left the modelled subset; the run stops instead of guessing."""


def register_index(name: str) -> int:
    try:
        return _REGISTER_INDEX[name.removeprefix('$')]
    except KeyError:
        raise MipsError(f'Unknown register name {name!r}') from None


def _signed(value: int) -> int:
    return value - 0x100000000 if value & 0x80000000 else value


def _checked(value: int, mnemonic: str, pc: int) -> int:
    if not -0x80000000 <= value <= 0x7FFFFFFF:
        raise MipsError(f'{mnemonic} at 0x{pc:08x} overflows; the R3000 would trap')
    return value & WORD_MASK


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

    def __getitem__(self, name: str) -> int:
        return self.registers[register_index(name)]


@dataclass(frozen=True)
class _Decoded:
    run: object        # callable(cpu) -> int | None (branch target) or None
    reads: frozenset[int]
    writes: frozenset[int]
    is_branch: bool
    mnemonic: str


class _Cpu:
    def __init__(self, memory: Memory) -> None:
        self.memory = memory
        self.r = [0] * 32
        self.pc = 0
        self.pending: tuple[int, int] | None = None   # load issued by this instruction


def _decode(word: int, pc: int) -> _Decoded:
    op = word >> 26
    rs = (word >> 21) & 31
    rt = (word >> 16) & 31
    rd = (word >> 11) & 31
    sa = (word >> 6) & 31
    funct = word & 63
    imm = word & 0xFFFF
    simm = imm - 0x10000 if imm & 0x8000 else imm

    def decoded(run, reads, writes, mnemonic, is_branch=False):
        return _Decoded(run, frozenset(r for r in reads if r), frozenset(w for w in writes if w),
                        is_branch, mnemonic)

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
        if sa:
            unsupported()
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
    unsupported()


def call(memory: Memory, entry: int, arguments: dict[str, int] | None = None, *,
         max_steps: int) -> RunResult:
    """Run from ``entry`` until it returns to ``RETURN_ADDRESS``; return all registers.

    ``arguments`` sets registers by name before the first instruction; every
    other register starts at zero and ``$ra`` holds ``RETURN_ADDRESS``. Branch
    delay slots execute as on the R3000. A load result becomes visible only
    after the following instruction, and that instruction may not touch the
    loaded register, so no behaviour depends on interlock details.
    """
    cpu = _Cpu(memory)
    cpu.r[31] = RETURN_ADDRESS
    for name, value in (arguments or {}).items():
        index = register_index(name)
        if index == 0:
            raise MipsError('$zero cannot be set')
        cpu.r[index] = value & WORD_MASK
    cache: dict[int, _Decoded] = {}
    pc, next_pc = entry & WORD_MASK, (entry + 4) & WORD_MASK
    in_delay_slot = False
    load: tuple[int, int] | None = None
    steps = 0
    while not (pc == RETURN_ADDRESS and not in_delay_slot):
        if steps >= max_steps:
            raise MipsError(f'Routine did not return within {max_steps} instructions')
        instruction = cache.get(pc)
        if instruction is None:
            instruction = cache[pc] = _decode(memory.fetch(pc), pc)
        if load is not None and (load[0] in instruction.reads or load[0] in instruction.writes):
            raise MipsError(f'{instruction.mnemonic} at 0x{pc:08x} uses ${REGISTER_NAMES[load[0]]} '
                            'in the delay slot of its load')
        if instruction.is_branch and in_delay_slot:
            raise MipsError(f'Branch {instruction.mnemonic} at 0x{pc:08x} is in a delay slot')
        cpu.pc = pc
        cpu.pending = None
        target = instruction.run(cpu)
        if load is not None:
            cpu.r[load[0]] = load[1]
        load = cpu.pending if cpu.pending and cpu.pending[0] else None
        cpu.r[0] = 0
        steps += 1
        following = (next_pc + 4) & WORD_MASK
        if target is not None:
            following = target
        in_delay_slot = instruction.is_branch
        pc, next_pc = next_pc, following
    if load is not None:
        cpu.r[load[0]] = load[1]
    return RunResult(tuple(cpu.r), steps)
