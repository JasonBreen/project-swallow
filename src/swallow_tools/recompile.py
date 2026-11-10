"""Translate original MIPS functions to C that runs against the PS1 core (swallow-recompile).

Offline tooling. Each function becomes one C function over the register
file and guest memory in runtime/ps1/core. Code is found by following
control flow from the entry points, so data inside the text, such as a jump
table, is never translated. Branch delay slots run before the transfer, as
on the R3000. Loads take effect at once, which is faithful only because a
static check first proves that no instruction uses a register in the delay
slot of the load that sets it. Anything outside the translated subset
becomes a runtime trap, never a silent miscompile.

The generated C is derived from the game and is written only to a directory
outside the repository. Contract: docs/recompiler.md.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import sys

from .gte import COMMANDS as GTE_COMMANDS
from .mips import REGISTER_NAMES, MipsError, _decode
from .oracle import OracleError, find_executable
from .split import DEFAULT_CONFIG_DIR, REPO_ROOT, _inside

SCHEMA_VERSION = 1
WORD_MASK = 0xFFFFFFFF
DEFAULT_FUNCTIONS = ('ulz_decompress',)
# Jump tables inside the text whose words are computed-jump targets; each runs
# to the next symbol in the symbol file. Seeded with the one game code needs.
DEFAULT_JUMP_TABLES = ('ulz_version_table',)
RUNTIME_DIR = REPO_ROOT / 'runtime'
MODULE_FILE = 'module.c'
LIBRARY_TARGET = 'swallow_recompiled'

_SYMBOL = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(0x[0-9A-Fa-f]+)\s*;(.*)$')
_TYPE = re.compile(r'\btype:(\w+)')
_SIZE = re.compile(r'\bsize:(0x[0-9A-Fa-f]+|\d+)')

CONDITIONAL = frozenset({'beq', 'bne', 'blez', 'bgtz', 'bltz', 'bgez'})
LOADS = {'lb': (1, 'swallow_sext8'), 'lh': (2, 'swallow_sext16'), 'lw': (4, None),
         'lbu': (1, None), 'lhu': (2, None)}
STORES = {'sb': 1, 'sh': 2, 'sw': 4}
# Instructions whose result lands after the next instruction on the R3000.
DELAYED = frozenset({'lb', 'lh', 'lw', 'lbu', 'lhu', 'lwl', 'lwr', 'mfc2', 'cfc2'})
HILO = frozenset({'mult', 'multu', 'div', 'divu', 'mfhi', 'mflo', 'mthi', 'mtlo'})
GTE_COMMAND_NAMES = frozenset(GTE_COMMANDS.values())
# For the timing checks: the same classes swallow_tools.mips.call refuses on.
HILO_WRITES = frozenset({'mult', 'multu', 'div', 'divu', 'mthi', 'mtlo'})
COP2_WRITES = frozenset({'mtc2', 'ctc2', 'lwc2'})
COP2 = frozenset({'mfc2', 'cfc2', 'mtc2', 'ctc2', 'lwc2', 'swc2'}) | GTE_COMMAND_NAMES


class RecompileError(ValueError):
    """The input cannot be translated faithfully; nothing is written."""


@dataclass(frozen=True)
class Symbol:
    name: str
    address: int
    kind: str | None
    size: int | None


@dataclass(frozen=True)
class Function:
    name: str
    start: int
    end: int
    entries: dict[int, str]


@dataclass
class Module:
    """Code to translate: a text image, its functions, and its jump tables."""
    prefix: str
    text: bytes
    text_address: int
    functions: list[Function]
    jump_tables: dict[int, tuple[int, ...]]
    source: str
    sha256: str


@dataclass
class Analysis:
    function: Function
    decoded: dict[int, object] = field(default_factory=dict)   # pc -> _Decoded or None
    normal: set[int] = field(default_factory=set)               # run as ordinary instructions
    labels: set[int] = field(default_factory=set)
    returns: bool = False
    hilo: bool = False
    registers: set[int] = field(default_factory=set)


def read_symbol_file(path: Path) -> list[Symbol]:
    """Symbols with the type and size from their ``// type:... size:...`` comments."""
    symbols = []
    for line in path.read_text(encoding='utf-8').splitlines():
        match = _SYMBOL.match(line)
        if not match:
            continue
        kind, size = _TYPE.search(match.group(3)), _SIZE.search(match.group(3))
        symbols.append(Symbol(match.group(1), int(match.group(2), 16),
                              kind.group(1) if kind else None,
                              int(size.group(1), 0) if size else None))
    return symbols


def functions_from_symbols(symbols: list[Symbol], names: list[str]) -> list[Function]:
    """One Function per named sized ``type:func`` symbol; funcs inside it become extra entries."""
    by_name = {symbol.name: symbol for symbol in symbols}
    functions = []
    for name in names:
        symbol = by_name.get(name)
        if symbol is None or symbol.kind != 'func' or not symbol.size:
            raise RecompileError(f'{name} is not a sized type:func symbol')
        start, end = symbol.address, symbol.address + symbol.size
        entries = {other.address: other.name for other in symbols
                   if other.kind == 'func' and start <= other.address < end}
        entries.setdefault(start, name)
        functions.append(Function(name, start, end, entries))
    return functions


def jump_tables_from_symbols(symbols: list[Symbol], names: list[str], text: bytes,
                             text_address: int) -> dict[int, tuple[int, ...]]:
    """Each named table's words, from its address to the next symbol's."""
    addresses = sorted({symbol.address for symbol in symbols})
    by_name = {symbol.name: symbol for symbol in symbols}
    tables = {}
    for name in names:
        symbol = by_name.get(name)
        if symbol is None:
            raise RecompileError(f'Jump table {name} is not in the symbol file')
        following = [address for address in addresses if address > symbol.address]
        if not following:
            raise RecompileError(f'Jump table {name} has no following symbol to end it')
        count = (following[0] - symbol.address) // 4
        tables[symbol.address] = tuple(_word(text, text_address, symbol.address + 4 * index)
                                       for index in range(count))
    return tables


def _word(text: bytes, text_address: int, address: int) -> int:
    offset = address - text_address
    if offset < 0 or offset + 4 > len(text) or offset % 4:
        raise RecompileError(f'Address 0x{address:08x} is outside the text image')
    return int.from_bytes(text[offset:offset + 4], 'little')


def _fields(word: int) -> tuple[int, int, int, int, int, int]:
    imm = word & 0xFFFF
    simm = imm - 0x10000 if imm & 0x8000 else imm
    return (word >> 21) & 31, (word >> 16) & 31, (word >> 11) & 31, (word >> 6) & 31, imm, simm


def _branch_target(word: int, pc: int, mnemonic: str) -> int:
    if mnemonic in ('j', 'jal'):
        return ((pc + 4) & 0xF0000000) | ((word & 0x03FFFFFF) << 2)
    return (pc + 4 + (_fields(word)[5] << 2)) & WORD_MASK


def _always(mnemonic: str, word: int) -> bool:
    """A conditional branch that is always taken: ``b`` (beq x, x) or bgez $zero."""
    rs, rt = _fields(word)[:2]
    return (mnemonic == 'beq' and rs == rt) or (mnemonic == 'bgez' and rs == 0)


def _table_targets(module: Module, function: Function) -> list[int]:
    """Words of any jump table that land inside the function.

    A table may sit inside the function (``ulz_version_table``) or in rodata,
    as the compiler's ``jtbl_`` tables do; either way a computed ``jr`` may
    reach exactly these addresses, and any other value traps.
    """
    return sorted({target for words in module.jump_tables.values()
                   for target in words if function.start <= target < function.end})


def _decode_at(module: Module, analysis: Analysis, pc: int):
    if pc not in analysis.decoded:
        try:
            analysis.decoded[pc] = _decode(_word(module.text, module.text_address, pc), pc)
        except MipsError:
            analysis.decoded[pc] = None            # not decodable: becomes a trap
    return analysis.decoded[pc]


def analyse(module: Module, function: Function) -> Analysis:
    """Follow control flow from the entries; check the shape the translation relies on."""
    analysis = Analysis(function)
    table_targets = _table_targets(module, function)
    pending = sorted(function.entries)
    analysis.labels.update(function.entries)
    while pending:
        pc = pending.pop()
        if pc in analysis.normal:
            continue
        if not function.start <= pc < function.end:
            raise RecompileError(f'Control reaches 0x{pc:08x}, outside {function.name}')
        analysis.normal.add(pc)
        decoded = _decode_at(module, analysis, pc)
        if decoded is None or decoded.mnemonic in ('break', 'syscall'):
            continue
        if not decoded.is_branch:
            pending.append(pc + 4)
            continue
        slot = pc + 4
        if not function.start <= slot < function.end:
            raise RecompileError(f'Delay slot 0x{slot:08x} is outside {function.name}')
        slot_decoded = _decode_at(module, analysis, slot)
        if slot_decoded is not None and slot_decoded.is_branch:
            raise RecompileError(f'Branch in the delay slot at 0x{slot:08x}')
        for successor in _successors(module, function, analysis, pc, decoded, table_targets):
            pending.append(successor)
    for pc in list(analysis.normal):
        decoded = analysis.decoded[pc]
        if decoded is not None and decoded.is_branch and pc + 4 in analysis.normal:
            # The slot is also reached on its own: it gets a label, and the
            # branch's not-taken path jumps over it to pc + 8.
            analysis.labels.add(pc + 4)
            word = _word(module.text, module.text_address, pc)
            if decoded.mnemonic in CONDITIONAL and not _always(decoded.mnemonic, word):
                analysis.labels.add(pc + 8)
    for pc in _executed(analysis):
        decoded = analysis.decoded.get(pc)
        if decoded is not None:
            analysis.registers.update(decoded.reads | decoded.writes)
            analysis.hilo |= decoded.mnemonic in HILO
    _check_hazards(module, function, analysis, table_targets)
    _check_timing(module, function, analysis, table_targets)
    return analysis


def _successors(module: Module, function: Function, analysis: Analysis, pc: int, decoded,
                table_targets: list[int]) -> list[int]:
    """Where control goes after a branch and its slot, for following control flow."""
    word = _word(module.text, module.text_address, pc)
    mnemonic = decoded.mnemonic
    if mnemonic in CONDITIONAL or mnemonic == 'j':
        target = _branch_target(word, pc, mnemonic)
        inside = function.start <= target < function.end
        if inside:
            analysis.labels.add(target)
        following = [pc + 8] if mnemonic in CONDITIONAL and not _always(mnemonic, word) else []
        return ([target] if inside else []) + following
    if mnemonic in ('jal', 'jalr'):
        return [pc + 8]
    rs = _fields(word)[0]
    if rs == 31:
        analysis.returns = True
        return []
    analysis.labels.update(table_targets)
    return list(table_targets)


def _check_hazards(module: Module, function: Function, analysis: Analysis,
                   table_targets: list[int]) -> None:
    """Refuse any path where an instruction uses a register in the delay slot of its load."""
    problems = []
    slots = {pc + 4: pc for pc in analysis.normal
             if analysis.decoded[pc] is not None and analysis.decoded[pc].is_branch}
    for pc in sorted(analysis.normal | set(slots)):
        decoded = analysis.decoded.get(pc)
        if decoded is None or decoded.mnemonic not in DELAYED:
            continue
        loaded = _fields(_word(module.text, module.text_address, pc))[1]
        if loaded == 0:
            continue
        followers = []
        if pc in analysis.normal:
            followers.append(pc + 4)
        if pc in slots:
            branch_pc = slots[pc]
            branch = analysis.decoded[branch_pc]
            word = _word(module.text, module.text_address, branch_pc)
            if branch.mnemonic in CONDITIONAL:
                followers.append(_branch_target(word, branch_pc, branch.mnemonic))
                if not _always(branch.mnemonic, word):
                    followers.append(branch_pc + 8)
            elif branch.mnemonic == 'j':
                followers.append(_branch_target(word, branch_pc, 'j'))
            elif branch.mnemonic == 'jr' and _fields(word)[0] != 31:
                followers += table_targets
            else:
                problems.append(f'0x{pc:08x}: {decoded.mnemonic} in the delay slot of '
                                f'{branch.mnemonic}, whose next instruction is elsewhere')
                continue
        for follower in followers:
            if not function.start <= follower < function.end:
                continue
            after = _decode_at(module, analysis, follower)
            if after is None or loaded not in (after.reads | after.writes):
                continue
            if (decoded.mnemonic == 'lwl' and after.pair is not None and after.pair[0] == 'lwr'
                    and after.pair[1:] == decoded.pair[1:] and after.pair[1] != after.pair[2]
                    and follower == pc + 4):
                continue
            problems.append(f'0x{follower:08x}: {after.mnemonic} uses '
                            f'${REGISTER_NAMES[loaded]} in the delay slot of the '
                            f'{decoded.mnemonic} at 0x{pc:08x}')
    if problems:
        raise RecompileError('Load-delay hazards in ' + function.name + ': '
                             + '; '.join(problems))


def _next_nodes(module: Module, function: Function, analysis: Analysis, node,
                table_targets: list[int]) -> list:
    """Instructions that run straight after ``node`` inside the function.

    A node is ``(pc, branch)``: ``branch`` is the address of the branch whose
    delay slot ``pc`` is, or None for an ordinary instruction. Control that
    leaves the function (a call, a return, a jump outside) ends the path.
    """
    pc, branch = node
    if branch is None:
        decoded = analysis.decoded.get(pc)
        if decoded is None or decoded.mnemonic in ('break', 'syscall'):
            return []
        if decoded.is_branch:
            return [(pc + 4, pc)]
        return [(pc + 4, None)] if pc + 4 in analysis.normal else []
    decoded = analysis.decoded[branch]
    word = _word(module.text, module.text_address, branch)
    mnemonic = decoded.mnemonic
    targets = []
    if mnemonic in CONDITIONAL or mnemonic == 'j':
        targets.append(_branch_target(word, branch, mnemonic))
        if mnemonic in CONDITIONAL and not _always(mnemonic, word):
            targets.append(branch + 8)
    elif mnemonic == 'jr' and _fields(word)[0] != 31:
        targets += table_targets
    return [(target, None) for target in targets
            if function.start <= target < function.end and target in analysis.normal]


def _check_timing(module: Module, function: Function, analysis: Analysis,
                  table_targets: list[int]) -> None:
    """Refuse the timing cases swallow_tools.mips refuses, on every path inside the function.

    MIPS I leaves a HI/LO write within two instructions after mfhi/mflo
    undefined; the GTE needs two instructions between a register write and a
    command, and one between a command and a register write; and no COP2
    instruction may sit in a branch delay slot. The translation runs each
    instruction to completion in order, so it is faithful only where none of
    these occurs.
    """
    problems = []
    nodes = [(pc, None) for pc in analysis.normal]
    nodes += [(pc + 4, pc) for pc in analysis.normal
              if analysis.decoded[pc] is not None and analysis.decoded[pc].is_branch]
    for node in sorted(nodes, key=lambda item: (item[0], item[1] or 0)):
        decoded = analysis.decoded.get(node[0])
        if decoded is None:
            continue
        mnemonic = decoded.mnemonic
        if node[1] is not None and mnemonic in COP2:
            problems.append(f'0x{node[0]:08x}: {mnemonic} in a branch delay slot')
        if mnemonic in ('mfhi', 'mflo'):
            forbidden, reach, what = HILO_WRITES, 2, f'{mnemonic} at 0x{node[0]:08x}'
        elif mnemonic in COP2_WRITES:
            forbidden, reach, what = GTE_COMMAND_NAMES, 2, f'GTE write at 0x{node[0]:08x}'
        elif mnemonic in GTE_COMMAND_NAMES:
            forbidden, reach, what = COP2_WRITES, 1, f'GTE command at 0x{node[0]:08x}'
        else:
            continue
        frontier = [node]
        for _ in range(reach):
            frontier = [following for current in frontier
                        for following in _next_nodes(module, function, analysis, current,
                                                     table_targets)]
            for following in frontier:
                after = analysis.decoded.get(following[0])
                if after is not None and after.mnemonic in forbidden:
                    problems.append(f'0x{following[0]:08x}: {after.mnemonic} too soon after the '
                                    f'{what}')
    if problems:
        raise RecompileError('Timing cases in ' + function.name + ': '
                             + '; '.join(sorted(set(problems))))


class _Emitter:
    def __init__(self, module: Module, analyses: dict[str, Analysis]):
        self.module = module
        self.analyses = analyses
        self.entry_owner = {address: self.c_name(function)
                            for function in module.functions for address in function.entries}

    def c_name(self, function: Function) -> str:
        return f'{self.module.prefix}_fn_{function.start:08X}'

    @staticmethod
    def reg(index: int) -> str:
        return '0u' if index == 0 else f'r_{REGISTER_NAMES[index]}'

    def assign(self, index: int, expression: str) -> str:
        if index != 0:
            return f'{self.reg(index)} = {expression};'
        if 'swallow_' in expression:
            return f'(void){expression};'          # keep its traps
        return '/* result discarded: $zero */;'

    def statement(self, pc: int, decoded) -> str:
        """One non-branch instruction as a C statement."""
        word = _word(self.module.text, self.module.text_address, pc)
        if decoded is None:
            return (f'swallow_trap(cpu, "unsupported: instruction 0x{word:08x} at '
                    f'0x{pc:08x}");')
        mnemonic = decoded.mnemonic
        rs, rt, rd, sa, imm, simm = _fields(word)
        a, b = self.reg(rs), self.reg(rt)
        at = f'0x{pc:08X}u'
        offset = f'{a} + 0x{simm & WORD_MASK:X}u' if simm else a
        if word == 0:
            return '/* nop */;'
        shifts = {'sll': f'{b} << {sa}u', 'srl': f'{b} >> {sa}u',
                  'sra': f'swallow_sra({b}, {sa}u)', 'sllv': f'{b} << ({a} & 31u)',
                  'srlv': f'{b} >> ({a} & 31u)', 'srav': f'swallow_sra({b}, {a})'}
        if mnemonic in shifts:
            return self.assign(rd, shifts[mnemonic])
        register_ops = {
            'add': f'swallow_add(cpu, {a}, {b}, {at}, "add")', 'addu': f'{a} + {b}',
            'sub': f'swallow_sub(cpu, {a}, {b}, {at})', 'subu': f'{a} - {b}',
            'and': f'{a} & {b}', 'or': f'{a} | {b}', 'xor': f'{a} ^ {b}',
            'nor': f'~({a} | {b})',
            'slt': f'(uint32_t)(swallow_s32({a}) < swallow_s32({b}))',
            'sltu': f'(uint32_t)({a} < {b})'}
        if mnemonic in register_ops:
            return self.assign(rd, register_ops[mnemonic])
        immediate = f'0x{simm & WORD_MASK:X}u'
        immediate_ops = {
            'addi': f'swallow_add(cpu, {a}, {immediate}, {at}, "addi")',
            'addiu': f'{a} + {immediate}',
            'slti': f'(uint32_t)(swallow_s32({a}) < {simm})',
            'sltiu': f'(uint32_t)({a} < {immediate})',
            'andi': f'{a} & 0x{imm:X}u', 'ori': f'{a} | 0x{imm:X}u',
            'xori': f'{a} ^ 0x{imm:X}u', 'lui': f'0x{imm << 16:X}u'}
        if mnemonic in immediate_ops:
            return self.assign(rt, immediate_ops[mnemonic])
        if mnemonic in LOADS:
            size, extend = LOADS[mnemonic]
            load = f'swallow_load(cpu, {offset}, {size}u, {at})'
            return self.assign(rt, f'{extend}({load})' if extend else load)
        if mnemonic in STORES:
            return f'swallow_store(cpu, {offset}, {STORES[mnemonic]}u, {b}, {at});'
        if mnemonic in ('mult', 'multu'):
            return f'swallow_mult(&r_hi, &r_lo, {a}, {b}, {int(mnemonic == "mult")});'
        if mnemonic in ('div', 'divu'):
            return f'swallow_divide(&r_hi, &r_lo, {a}, {b}, {int(mnemonic == "div")});'
        if mnemonic in ('mfhi', 'mflo'):
            return self.assign(rd, 'r_hi' if mnemonic == 'mfhi' else 'r_lo')
        if mnemonic in ('mthi', 'mtlo'):
            return f'{"r_hi" if mnemonic == "mthi" else "r_lo"} = {a};'
        if mnemonic in ('lwl', 'lwr'):
            return self.assign(rt, f'swallow_{mnemonic}(cpu, {offset}, {b}, {at})')
        if mnemonic in ('swl', 'swr'):
            return f'swallow_{mnemonic}(cpu, {offset}, {b}, {at});'
        if mnemonic == 'mfc2':
            return self.assign(rt, f'swallow_gte_read_data(&cpu->gte, {rd}u)')
        if mnemonic == 'cfc2':
            return self.assign(rt, f'swallow_gte_read_control(&cpu->gte, {rd}u)')
        if mnemonic == 'mtc2':
            return f'swallow_gte_write_data(&cpu->gte, {rd}u, {b});'
        if mnemonic == 'ctc2':
            return f'swallow_gte_write_control(&cpu->gte, {rd}u, {b});'
        if mnemonic == 'lwc2':
            return (f'swallow_gte_write_data(&cpu->gte, {rt}u, '
                    f'swallow_load(cpu, {offset}, 4u, {at}));')
        if mnemonic == 'swc2':
            return (f'swallow_store(cpu, {offset}, 4u, swallow_gte_read_data(&cpu->gte, {rt}u), '
                    f'{at});')
        if mnemonic in GTE_COMMAND_NAMES:
            # The decoder accepted the word, so the C GTE models it too.
            return f'(void)swallow_gte_command(&cpu->gte, 0x{word:08X}u); /* {mnemonic} */'
        if mnemonic in ('break', 'syscall'):
            code = (word >> 16) & 0x3FF if mnemonic == 'break' else (word >> 6) & 0xFFFFF
            return (f'swallow_trap(cpu, "{mnemonic} {code} at 0x{pc:08x}; the R3000 would '
                    'take an exception");')
        return f'swallow_trap(cpu, "unsupported: {mnemonic} at 0x{pc:08x}");'

    def spill(self, analysis: Analysis) -> list[str]:
        lines = [f'cpu->r[{index}] = {self.reg(index)};' for index in sorted(analysis.registers)
                 if index]
        if analysis.hilo:
            lines += ['cpu->hi = r_hi;', 'cpu->lo = r_lo;']
        return lines

    def reload(self, analysis: Analysis) -> list[str]:
        lines = [f'{self.reg(index)} = cpu->r[{index}];' for index in sorted(analysis.registers)
                 if index]
        if analysis.hilo:
            lines += ['r_hi = cpu->hi;', 'r_lo = cpu->lo;']
        return lines

    def tail_call(self, analysis: Analysis, target: int) -> list[str]:
        """A branch or jump into another function's entry: run it in place of the rest.

        ``$ra`` still holds this function's return address, so the callee
        returns to this function's caller; its registers are the result.
        """
        return [*self.spill(analysis), f'{self.entry_owner[target]}(cpu, 0x{target:08X}u);',
                'swallow_leave(cpu);', 'return;']

    def budget(self, target: int, pc: int) -> str:
        if target > pc:
            return ''
        return (f'if (cpu->budget-- == 0u) {{ swallow_trap(cpu, "branch budget exhausted at '
                f'0x{pc:08x}"); }} ')

    def branch(self, pc: int, analysis: Analysis) -> list[str]:
        decoded = analysis.decoded[pc]
        mnemonic = decoded.mnemonic
        word = _word(self.module.text, self.module.text_address, pc)
        rs, rt, rd, *_ = _fields(word)
        slot = self.statement(pc + 4, analysis.decoded.get(pc + 4))
        function = analysis.function
        lines = [f'{{ /* 0x{pc:08X}: {mnemonic} */']
        if mnemonic in CONDITIONAL and _always(mnemonic, word):
            mnemonic = 'j'                         # always taken: no fall-through path
        if mnemonic in CONDITIONAL:
            a, b = self.reg(rs), self.reg(rt)
            condition = {'beq': f'{a} == {b}', 'bne': f'{a} != {b}',
                         'blez': f'swallow_s32({a}) <= 0', 'bgtz': f'swallow_s32({a}) > 0',
                         'bltz': f'({a} & 0x80000000u) != 0u',
                         'bgez': f'({a} & 0x80000000u) == 0u'}[mnemonic]
            target = _branch_target(word, pc, mnemonic)
            lines += [f'    const int taken = {condition};', f'    {slot}']
            if function.start <= target < function.end:
                lines.append(f'    if (taken) {{ {self.budget(target, pc)}goto L_{target:08X}; }}')
            elif target in self.entry_owner:
                lines.append('    if (taken) {')
                lines += ['        ' + line for line in self.tail_call(analysis, target)]
                lines.append('    }')
            else:
                lines.append(f'    if (taken) swallow_trap(cpu, "branch to 0x{target:08x}, '
                             f'outside {function.name}");')
            lines.append('}')
            if pc + 4 in analysis.normal:
                lines.append(f'goto L_{pc + 8:08X};')
            return lines
        if mnemonic == 'j':
            target = _branch_target(word, pc, decoded.mnemonic)
            lines.append(f'    {slot}')
            if function.start <= target < function.end:
                lines.append(f'    {self.budget(target, pc)}goto L_{target:08X};')
            elif target in self.entry_owner:
                lines += ['    ' + line for line in self.tail_call(analysis, target)]
            else:
                lines.append(f'    swallow_trap(cpu, "jump to 0x{target:08x}, outside '
                             f'{function.name}");')
            return lines + ['}']
        if mnemonic == 'jal':
            target = _branch_target(word, pc, 'jal')
            lines += [f'    r_ra = 0x{pc + 8:08X}u;', f'    {slot}']
            callee = self.entry_owner.get(target)
            if callee is None:
                lines.append(f'    swallow_trap(cpu, "call to untranslated 0x{target:08x}");')
            else:
                lines += ['    ' + line for line in self.spill(analysis)]
                lines.append(f'    {callee}(cpu, 0x{target:08X}u);')
                lines += ['    ' + line for line in self.reload(analysis)]
            return lines + ['}']
        if mnemonic == 'jalr':
            lines.append(f'    const uint32_t target = {self.reg(rs)};')
            lines += [f'    {self.assign(rd, f"0x{pc + 8:08X}u")}', f'    {slot}']
            lines += ['    ' + line for line in self.spill(analysis)]
            lines.append(f'    {self.module.prefix}_dispatch(cpu, target);')
            lines += ['    ' + line for line in self.reload(analysis)]
            return lines + ['}']
        lines.append(f'    const uint32_t target = {self.reg(rs)};')
        lines.append(f'    {slot}')
        if rs == 31:
            lines += ['    if (target != ra_entry) swallow_trap(cpu, "jr $ra at '
                      f'0x{pc:08x} returns to 0x%08x, not to its caller at 0x%08x", '
                      '(unsigned)target, (unsigned)ra_entry);',
                      '    goto swallow_exit;', '}']
            return lines
        lines.append('    switch (target) {')
        for target in _table_targets(self.module, function):
            lines.append(f'    case 0x{target:08X}u: {self.budget(target, pc)}goto L_{target:08X};')
        lines += [f'    default: swallow_trap(cpu, "jr at 0x{pc:08x} to 0x%08x, not a known '
                  'target", (unsigned)target);', '    }', '}']
        return lines

    def function(self, analysis: Analysis) -> list[str]:
        function = analysis.function
        name = self.c_name(function)
        out = [f'/* {function.name}: 0x{function.start:08X}-0x{function.end:08X} */',
               f'static void {name}(swallow_cpu *cpu, uint32_t entry)', '{']
        for index in sorted(analysis.registers):
            if index:
                out.append(f'    uint32_t {self.reg(index)} = cpu->r[{index}];')
        if analysis.hilo:
            out += ['    uint32_t r_hi = cpu->hi;', '    uint32_t r_lo = cpu->lo;']
        if analysis.returns:
            out.append('    const uint32_t ra_entry = cpu->r[31];')
        out.append('    swallow_enter(cpu, entry);')
        out.append('    switch (entry) {')
        for address, symbol in sorted(function.entries.items()):
            out.append(f'    case 0x{address:08X}u: goto L_{address:08X}; /* {symbol} */')
        out += [f'    default: swallow_trap(cpu, "0x%08x is not an entry of {function.name}", '
                '(unsigned)entry);', '    }']
        slots = {pc + 4 for pc in analysis.normal
                 if analysis.decoded[pc] is not None and analysis.decoded[pc].is_branch}
        for pc in sorted(analysis.normal):
            if pc in analysis.labels:
                out.append(f'L_{pc:08X}:')
            decoded = analysis.decoded[pc]
            if decoded is not None and decoded.is_branch:
                out += ['    ' + line for line in self.branch(pc, analysis)]
            else:
                out.append(f'    {self.statement(pc, decoded)}')
                if decoded is not None and decoded.mnemonic not in ('break', 'syscall') \
                        and pc + 4 not in analysis.normal and pc + 4 not in slots:
                    out.append(f'    swallow_trap(cpu, "control leaves {function.name} at '
                               f'0x{pc + 4:08x}");')
        if analysis.returns:
            out.append('swallow_exit:')
            out += ['    ' + line for line in self.spill(analysis)]
            out.append('    swallow_leave(cpu);')
            out.append('    return;')
        out.append('}')
        return out


def translate(module: Module, *, export: bool = True,
              analyses: dict[str, Analysis] | None = None) -> tuple[str, dict]:
    """C source for every function in the module, and a summary without code."""
    if analyses is None:
        analyses = {function.name: analyse(module, function) for function in module.functions}
    emitter = _Emitter(module, analyses)
    prefix = module.prefix
    out = [f'/* Generated by swallow-recompile from {module.source} (SHA256 {module.sha256}).',
           ' * Derived from the original program: keep it outside the repository. */',
           '#include "ps1/core/cpu.h"', '#include "ps1/core/guest.h"', '',
           f'static void {prefix}_dispatch(swallow_cpu *cpu, uint32_t entry);']
    for function in module.functions:
        out.append(f'static void {emitter.c_name(function)}(swallow_cpu *cpu, uint32_t entry);')
    out.append('')
    for function in module.functions:
        out += emitter.function(analyses[function.name]) + ['']
    out += [f'static void {prefix}_dispatch(swallow_cpu *cpu, uint32_t entry)', '{',
            '    switch (entry) {']
    for function in module.functions:
        for address in sorted(function.entries):
            out.append(f'    case 0x{address:08X}u: {emitter.c_name(function)}(cpu, entry); return;')
    out += ['    default: swallow_trap(cpu, "no translated function starts at 0x%08x", '
            '(unsigned)entry);', '    }', '}', '']
    if export:
        out += ['SWALLOW_API int swallow_run(swallow_cpu *cpu, uint32_t entry)', '{',
                f'    return swallow_call(cpu, {prefix}_dispatch, entry);', '}', '',
                'SWALLOW_API const char *swallow_module_sha256(void)', '{',
                f'    return "{module.sha256}";', '}', '']
    summary = {'functions': [{
        'name': function.name, 'start': f'0x{function.start:08X}',
        'end': f'0x{function.end:08X}',
        'entries': {name: f'0x{address:08X}' for address, name in sorted(function.entries.items())},
        'instructions': len(_executed(analyses[function.name])),
        'labels': len(analyses[function.name].labels),
        'untranslated': _untranslated(emitter, analyses[function.name]),
    } for function in module.functions]}
    return '\n'.join(out), summary


def _executed(analysis: Analysis) -> set[int]:
    """Every translated address: ordinary instructions and delay slots."""
    return analysis.normal | {pc + 4 for pc in analysis.normal
                              if analysis.decoded[pc] is not None
                              and analysis.decoded[pc].is_branch}


def _untranslated(emitter: _Emitter, analysis: Analysis) -> list[str]:
    """Addresses whose translation is a trap because the instruction is not translated."""
    found = []
    for pc in sorted(_executed(analysis)):
        decoded = analysis.decoded.get(pc)
        if decoded is not None and decoded.is_branch:
            continue
        if emitter.statement(pc, decoded).startswith('swallow_trap(cpu, "unsupported'):
            found.append(f'0x{pc:08X}')
    return found


def cmake_project(runtime_dir: Path = RUNTIME_DIR) -> str:
    return '\n'.join([
        '# Generated by swallow-recompile. Derived from the original program:',
        '# keep this directory outside the repository.',
        'cmake_minimum_required(VERSION 3.21)',
        'project(swallow_recompiled LANGUAGES C)',
        'set(CMAKE_C_STANDARD 11)',
        'set(CMAKE_C_STANDARD_REQUIRED ON)',
        f'add_subdirectory("{runtime_dir.resolve().as_posix()}" swallow_runtime)',
        f'add_library({LIBRARY_TARGET} SHARED {MODULE_FILE})',
        f'target_link_libraries({LIBRARY_TARGET} PRIVATE swallow_ps1_core)',
        f'set_target_properties({LIBRARY_TARGET} PROPERTIES C_VISIBILITY_PRESET hidden)', ''])


def recompile_image(image: Path, out_dir: Path, *, functions: list[str] | None = None,
                    jump_tables: list[str] | None = None,
                    config_dir: Path = DEFAULT_CONFIG_DIR, repo_root: Path = REPO_ROOT) -> dict:
    """Translate the named functions of the executable that defines them; write C and CMake."""
    if _inside(out_dir, repo_root):
        raise RecompileError(f'Output directory {out_dir} is inside the repository; '
                             'generated C is derived from the game and stays outside commits')
    if out_dir.exists() and any(out_dir.iterdir()):
        raise RecompileError(f'Output directory {out_dir} is not empty')
    names = list(functions or DEFAULT_FUNCTIONS)
    try:
        staged = find_executable(image, config_dir, names[0])
    except OracleError as exc:
        raise RecompileError(str(exc)) from exc
    symbols = read_symbol_file(config_dir / staged.symbols_file)
    module = Module(Path(staged.config).stem, staged.image, staged.text_address,
                    functions_from_symbols(symbols, names),
                    jump_tables_from_symbols(symbols, list(jump_tables or DEFAULT_JUMP_TABLES),
                                             staged.image, staged.text_address),
                    staged.path, staged.sha256)
    source, summary = translate(module)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / MODULE_FILE).write_text(source + '\n', encoding='utf-8')
    (out_dir / 'CMakeLists.txt').write_text(cmake_project(), encoding='utf-8')
    report = {'schema_version': SCHEMA_VERSION, 'source_image': image.name,
              'executable': {'path': staged.path, 'sha256': staged.sha256,
                             'config': staged.config, 'symbols': staged.symbols_file},
              'jump_tables': {f'0x{address:08X}': len(words)
                              for address, words in module.jump_tables.items()},
              **summary, 'library_target': LIBRARY_TARGET}
    (out_dir / 'recompile-report.json').write_text(json.dumps(report, indent=2) + '\n',
                                                   encoding='utf-8')
    return report


def split_functions(split_dir: Path, sha256: str, psyq_report: dict | None,
                    symbols: list[Symbol]) -> tuple[list[Function], list[tuple[int, int]], int]:
    """Game functions of one executable from a swallow-split work directory.

    Function bounds and alternate entries come from splat's assembly;
    ``type:func`` symbols inside a function are entries too. Library code,
    by the PsyQ report's object ranges, is left out: the runtime replaces it
    at its API. Returns the functions, the ``jtbl_`` tables as (address,
    word count), and how many library functions were skipped.
    """
    from .inventory import library_ranges, parse_data, parse_text
    try:
        split = json.loads((split_dir / 'split-report.json').read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise RecompileError(f'Cannot read split-report.json in {split_dir}: {exc}') from exc
    units = [item for item in split.get('executables', []) if item.get('sha256') == sha256]
    if len(units) != 1:
        raise RecompileError(f'{split_dir} has no split of the executable {sha256[:12]}')
    ranges = library_ranges(psyq_report, sha256) if psyq_report is not None else None
    if ranges is None:
        raise RecompileError('The PsyQ report does not list this executable; library code '
                             'cannot be told from game code')
    parsed, tables = [], []
    for text_file in units[0]['text_files']:
        path = split_dir / 'asm' / text_file
        try:
            parsed += parse_text(path.read_text(encoding='utf-8', errors='replace'))
        except OSError as exc:
            raise RecompileError(f'Missing assembly {path}: {exc}') from exc
        stem = text_file.removesuffix('_text.s')
        for data_file in sorted((split_dir / 'asm' / 'data').glob(f'{stem}_*.s')):
            for label, address, words in parse_data(
                    data_file.read_text(encoding='utf-8', errors='replace')):
                if label.startswith('jtbl_'):
                    tables.append((address, len(words)))
    functions, skipped = [], 0
    for item in parsed:
        if any(start <= item.address < end for start, end in ranges):
            skipped += 1
            continue
        start = item.address
        end = item.instructions[-1].address + 4
        entries = {start: item.name}
        for name, address in item.entry_addresses.items():
            entries.setdefault(address, name)
        for symbol in symbols:
            if symbol.kind == 'func' and start <= symbol.address < end:
                entries.setdefault(symbol.address, symbol.name)
        functions.append(Function(item.name, start, end, entries))
    return functions, tables, skipped


def recompile_split(image: Path, split_dir: Path, out_dir: Path, *,
                    config_dir: Path = DEFAULT_CONFIG_DIR, repo_root: Path = REPO_ROOT,
                    psyq_report: Path | None = None, symbol: str = 'ace3_entry') -> dict:
    """Translate every game function of one executable; refuse functions one at a time.

    The executable is the one whose symbol file names ``symbol``. A function
    the translator refuses is left out of the module, with its reason in the
    report; calls to it, and to library code, trap at run time.
    """
    if _inside(out_dir, repo_root):
        raise RecompileError(f'Output directory {out_dir} is inside the repository; '
                             'generated C is derived from the game and stays outside commits')
    if out_dir.exists() and any(out_dir.iterdir()):
        raise RecompileError(f'Output directory {out_dir} is not empty')
    try:
        staged = find_executable(image, config_dir, symbol)
    except OracleError as exc:
        raise RecompileError(str(exc)) from exc
    symbols = read_symbol_file(config_dir / staged.symbols_file)
    report_path = psyq_report or REPO_ROOT / 'docs' / 'reports' / 'psyq-jp1.json'
    try:
        psyq = json.loads(Path(report_path).read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise RecompileError(f'Cannot read the PsyQ report {report_path}: {exc}') from exc
    functions, jtbls, skipped = split_functions(split_dir, staged.sha256, psyq, symbols)
    tables = {address: tuple(_word(staged.image, staged.text_address, address + 4 * index)
                             for index in range(count)) for address, count in jtbls}
    names = [name for name in DEFAULT_JUMP_TABLES if any(s.name == name for s in symbols)]
    tables.update(jump_tables_from_symbols(symbols, names, staged.image, staged.text_address))
    prefix = Path(staged.config).stem
    candidate = Module(prefix, staged.image, staged.text_address, functions, tables,
                       staged.path, staged.sha256)
    analyses, refused = {}, []
    for function in functions:
        try:
            analyses[function.name] = analyse(candidate, function)
        except RecompileError as exc:
            refused.append({'name': function.name, 'start': f'0x{function.start:08X}',
                            'reason': str(exc)})
    module = Module(prefix, staged.image, staged.text_address,
                    [function for function in functions if function.name in analyses], tables,
                    staged.path, staged.sha256)
    source, summary = translate(module, analyses=analyses)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / MODULE_FILE).write_text(source + '\n', encoding='utf-8')
    (out_dir / 'CMakeLists.txt').write_text(cmake_project(), encoding='utf-8')
    translated = summary['functions']
    reasons: dict[str, int] = {}
    for item in refused:
        key = _reason_class(item['reason'])
        reasons[key] = reasons.get(key, 0) + 1
    report = {
        'schema_version': SCHEMA_VERSION, 'source_image': image.name,
        'executable': {'path': staged.path, 'sha256': staged.sha256,
                       'config': staged.config, 'symbols': staged.symbols_file},
        'split_dir_report': 'split-report.json',
        'library_functions_skipped': skipped,
        'game_functions': len(functions),
        'translated_functions': len(translated),
        'refused_functions': len(refused),
        'refused_by_reason': dict(sorted(reasons.items())),
        'translated_instructions': sum(item['instructions'] for item in translated),
        'untranslated_instructions': sum(len(item['untranslated']) for item in translated),
        'jump_tables': len(tables),
        'calls': _call_summary(module, analyses),
        'refused': refused,
        'functions': translated,
        'library_target': LIBRARY_TARGET,
    }
    (out_dir / 'recompile-report.json').write_text(json.dumps(report, indent=2) + '\n',
                                                   encoding='utf-8')
    return report


def _reason_class(reason: str) -> str:
    for marker, key in (('Load-delay hazards', 'load_delay_hazard'),
                        ('Timing cases', 'timing_case'),
                        ('Branch in the delay slot', 'branch_in_delay_slot'),
                        ('Delay slot', 'delay_slot_outside'),
                        ('Control reaches', 'control_leaves_function')):
        if reason.startswith(marker):
            return key
    return 'other'


def _call_summary(module: Module, analyses: dict[str, Analysis]) -> dict:
    """Direct calls and tail jumps by kind of target, counted at their sites."""
    entries = {address for function in module.functions for address in function.entries}
    counts = {'jal_translated': 0, 'jal_untranslated': 0, 'jalr': 0,
              'tail_translated': 0, 'outside_untranslated': 0}
    for name, analysis in analyses.items():
        function = analysis.function
        for pc in analysis.normal:
            decoded = analysis.decoded.get(pc)
            if decoded is None or not decoded.is_branch:
                continue
            word = _word(module.text, module.text_address, pc)
            if decoded.mnemonic == 'jalr':
                counts['jalr'] += 1
            elif decoded.mnemonic == 'jal':
                target = _branch_target(word, pc, 'jal')
                counts['jal_translated' if target in entries else 'jal_untranslated'] += 1
            elif decoded.mnemonic in CONDITIONAL or decoded.mnemonic == 'j':
                target = _branch_target(word, pc, decoded.mnemonic)
                if not function.start <= target < function.end:
                    counts['tail_translated' if target in entries
                           else 'outside_untranslated'] += 1
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description='Translate original MIPS functions to C for the Project Swallow PS1 core')
    parser.add_argument('bin', type=Path, nargs='?', help='Local raw MODE2/2352 disc image')
    parser.add_argument('out_dir', type=Path, nargs='?',
                        help='New output directory outside the repository')
    parser.add_argument('--function', action='append',
                        help='Function symbol to translate (repeatable; default ulz_decompress)')
    parser.add_argument('--jump-table', action='append',
                        help='Jump-table symbol inside the text (repeatable)')
    parser.add_argument('--config-dir', type=Path, default=DEFAULT_CONFIG_DIR,
                        help='splat config directory with manifest.json and symbol files')
    parser.add_argument('--split-dir', type=Path,
                        help='swallow-split work directory: translate every game function of '
                             'ACE3.BIN, refusing functions one at a time')
    parser.add_argument('--psyq-report', type=Path,
                        help='PsyQ report that marks library code (default: '
                             'docs/reports/psyq-jp1.json)')
    parser.add_argument('--selftest-out', type=Path,
                        help='Write the synthetic self-test C to this directory and exit')
    args = parser.parse_args(argv)
    try:
        if args.selftest_out is not None:
            from .selftest import write_selftest
            written = write_selftest(args.selftest_out)
            print(f'Wrote {len(written)} self-test files to {args.selftest_out}')
            return 0
        if args.bin is None or args.out_dir is None:
            parser.error('a disc image and an output directory are required')
        if args.split_dir is not None:
            report = recompile_split(args.bin, args.split_dir, args.out_dir,
                                     config_dir=args.config_dir, psyq_report=args.psyq_report)
        else:
            report = recompile_image(args.bin, args.out_dir, functions=args.function,
                                     jump_tables=args.jump_table, config_dir=args.config_dir)
    except (OSError, RecompileError, ValueError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 2
    if args.split_dir is not None:
        print(f"{report['executable']['path']}: {report['translated_functions']} of "
              f"{report['game_functions']} game functions translated "
              f"({report['translated_instructions']} instructions, "
              f"{report['untranslated_instructions']} untranslated); "
              f"{report['refused_functions']} refused: {report['refused_by_reason']}")
        print(f"Build with: cmake -S {args.out_dir} -B {args.out_dir / 'build'} && "
              f"cmake --build {args.out_dir / 'build'}")
        return 0
    for function in report['functions']:
        print(f"{function['name']}: {function['instructions']} instructions, "
              f"{len(function['entries'])} entries, {len(function['untranslated'])} untranslated")
    print(f"Build with: cmake -S {args.out_dir} -B {args.out_dir / 'build'} && "
          f"cmake --build {args.out_dir / 'build'}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
