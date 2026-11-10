"""Inventory what a recompiler would need from a swallow-split work directory.

Reads the assembly splat wrote (never the disc) and reports, for each
executable and overlay: indirect calls, computed jumps and the jump tables
that resolve them, functions whose address is taken, function-pointer
tables, callbacks registered with the PsyQ libraries, addresses in the
0x1F80xxxx page, instructions that swallow_tools.mips does not run, the GTE
command encodings game code uses, the compiler's division guards and the
divisions without one, and the timing cases the interpreter refuses.
Library and game code are told apart by the object ranges in a PsyQ report.
Contract and results: docs/code-inventory.md. The report holds names,
addresses, decoded fields, and counts only, never instruction words.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import sys

from .gte import GteError, decode_command
from .mips import SUPPORTED_MNEMONICS

SCHEMA_VERSION = 2
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PSYQ_REPORT = REPO_ROOT / 'docs' / 'reports' / 'psyq-jp1.json'

_INSTRUCTION = re.compile(
    r'^\s*/\* ([0-9A-F]+) ([0-9A-F]{8}) ([0-9A-F]{8}) \*/\s+(\S+)\s*(.*?)\s*(?:/\*.*\*/)?\s*$')
_DIRECTIVE = re.compile(r'^\s*(glabel|alabel|endlabel|dlabel|enddlabel|jlabel)\s+(\S+)')
_LOCAL_LABEL = re.compile(r'^\s*(\.L[0-9A-Za-z_]+):\s*$')
_RELOC = re.compile(r'%(?:hi|lo|gp_rel)\(([A-Za-z_.][A-Za-z0-9_.]*)\)')
_HW_LITERAL = re.compile(r'0x1F80([0-9A-Fa-f]{4})\b')
_HW_SYMBOL = re.compile(r'\bD_1F80([0-9A-Fa-f]{4})\b')
_WORD_SYMBOL = re.compile(r'^([A-Za-z_.][A-Za-z0-9_.]*)(?:\s*\+\s*0x[0-9A-Fa-f]+)?$')

# spimdisasm prints these pseudo-instructions; each is one real instruction.
PSEUDO = {'nop': 'sll', 'beqz': 'beq', 'bnez': 'bne', 'b': 'beq', 'negu': 'subu',
          'neg': 'sub', 'not': 'nor', 'move': 'addu'}
GTE = frozenset({'mtc2', 'mfc2', 'ctc2', 'cfc2', 'lwc2', 'swc2', 'rtps', 'rtpt', 'nclip',
                 'op', 'dpcs', 'intpl', 'mvmva', 'ncds', 'cdp', 'ncdt', 'nccs', 'cc', 'dpcl',
                 'ncs', 'nct', 'sqr', 'dcpl', 'dpct', 'avsz3', 'avsz4', 'gpf', 'gpl',
                 'ncct'})
GAP_CLASSES = (
    ('multiply_divide', frozenset({'mult', 'multu', 'div', 'divu', 'mfhi', 'mflo', 'mthi',
                                   'mtlo'})),
    ('unaligned_access', frozenset({'lwl', 'lwr', 'swl', 'swr'})),
    ('trap', frozenset({'break', 'syscall'})),
    ('cop0', frozenset({'mfc0', 'mtc0', 'rfe'})),
    ('gte', GTE),
)
# For the timing cases swallow_tools.mips refuses (see timing_cases).
GTE_WRITES = frozenset({'mtc2', 'ctc2', 'lwc2'})
GTE_COMMANDS = GTE - {'mtc2', 'mfc2', 'ctc2', 'cfc2', 'lwc2', 'swc2'}
BRANCHES = frozenset({'beq', 'bne', 'blez', 'bgtz', 'bltz', 'bgez', 'bltzal', 'bgezal', 'bal',
                      'j', 'jal', 'jr', 'jalr'})
HILO_WRITES = frozenset({'mult', 'multu', 'div', 'divu', 'mthi', 'mtlo'})
# How far back from a call the argument register is looked for.
ARGUMENT_WINDOW = 8
# PsyQ functions whose names end in Callback but take no function pointer,
# and those that take it in $a1 rather than $a0.
NOT_SETTERS = frozenset({'ResetCallback', 'StopCallback', 'RestartCallback', 'CheckCallback'})
SECOND_ARGUMENT_SETTERS = frozenset({'DMACallback', 'InterruptCallback', 'VSyncCallbacks'})
# Placed library objects further apart than this belong to separate library
# blocks, and the code between blocks is the game's.
LIBRARY_CLUSTER_GAP = 0x10000
BIOS_VECTORS = (0xA0, 0xB0, 0xC0)


class InventoryError(ValueError):
    """The work directory or PsyQ report cannot be read as expected."""


@dataclass
class Instruction:
    address: int
    mnemonic: str
    operands: str
    word: int = 0

    def args(self) -> list[str]:
        return [part.strip() for part in self.operands.split(',')]


@dataclass
class Function:
    name: str
    address: int
    entries: list[str] = field(default_factory=list)
    instructions: list[Instruction] = field(default_factory=list)
    labels: set[str] = field(default_factory=set)
    library: bool | None = None
    entry_addresses: dict[str, int] = field(default_factory=dict)


def parse_text(text: str) -> list[Function]:
    """Functions, their alternate entry names, local labels, and instructions."""
    functions: list[Function] = []
    current: Function | None = None
    pending_entries: list[str] = []
    for line in text.splitlines():
        directive = _DIRECTIVE.match(line)
        if directive:
            kind, name = directive.groups()
            if kind == 'glabel':
                current = Function(name, -1)
                functions.append(current)
            elif kind == 'alabel' and current is not None:
                pending_entries.append(name)
            elif kind == 'jlabel' and current is not None:
                current.labels.add(name)
            elif kind == 'endlabel':
                current = None
            continue
        label = _LOCAL_LABEL.match(line)
        if label and current is not None:
            current.labels.add(label.group(1))
            continue
        match = _INSTRUCTION.match(line)
        if not match or current is None:
            continue
        address = int(match.group(2), 16)
        if current.address < 0:
            current.address = address
        for name in pending_entries:
            current.entries.append(name)
            current.entry_addresses[name] = address
        pending_entries = []
        word = int.from_bytes(bytes.fromhex(match.group(3)), 'little')
        current.instructions.append(Instruction(address, match.group(4), match.group(5), word))
    return [function for function in functions if function.address >= 0]


def parse_data(text: str) -> list[tuple[str, int, list[str]]]:
    """Data labels with their address and the symbols their words name."""
    blocks: list[tuple[str, int, list[str]]] = []
    current: list | None = None
    for line in text.splitlines():
        directive = _DIRECTIVE.match(line)
        if directive and directive.group(1) == 'dlabel':
            current = [directive.group(2), -1, []]
            blocks.append(current)
            continue
        if directive and directive.group(1) == 'enddlabel':
            current = None
            continue
        match = _INSTRUCTION.match(line)
        if not match or current is None or match.group(4) != '.word':
            continue
        if current[1] < 0:
            current[1] = int(match.group(2), 16)
        symbol = _WORD_SYMBOL.match(match.group(5))
        current[2].append(symbol.group(1) if symbol and not match.group(5).startswith('0x')
                          else '')
    return [(name, address, words) for name, address, words in blocks if address >= 0]


def library_ranges(psyq_report: dict | None, sha256: str) -> list[tuple[int, int]] | None:
    """Library blocks for the executable with this SHA256, or None if it is not listed.

    Placed objects are grouped into blocks wherever consecutive objects are
    at most LIBRARY_CLUSTER_GAP apart, so unplaced library code between them,
    such as short BIOS stubs, counts as library code too.
    """
    if not psyq_report:
        return None
    for exe in psyq_report.get('executables', []):
        if exe.get('sha256') != sha256:
            continue
        objects = sorted((int(obj['address'], 16), int(obj['address'], 16) + obj['size'])
                         for obj in exe['objects'])
        blocks: list[list[int]] = []
        for start, end in objects:
            if blocks and start - blocks[-1][1] <= LIBRARY_CLUSTER_GAP:
                blocks[-1][1] = max(blocks[-1][1], end)
            else:
                blocks.append([start, end])
        return [(start, end) for start, end in blocks]
    return None


def bios_stub(function: Function) -> tuple[int, int] | None:
    """(vector, number) for a PsyQ BIOS stub: li $t2, vector; jr $t2; li $t1, number."""
    body = function.instructions
    if len(body) < 3:
        return None
    first, jump, slot = body[0], body[1], body[2]
    def immediate(instruction: Instruction, register: str) -> int | None:
        parts = [part.strip() for part in instruction.operands.split(',')]
        if (instruction.mnemonic in ('addiu', 'li') and parts[0] == register
                and parts[-1].startswith('0x') and (len(parts) == 2 or parts[1] == '$zero')):
            return int(parts[-1], 16)
        return None
    vector = immediate(first, '$t2')
    number = immediate(slot, '$t1')
    if (vector in BIOS_VECTORS and number is not None and jump.mnemonic == 'jr'
            and jump.operands.strip() == '$t2'):
        return vector, number
    return None


def _argument(function: Function, index: int, register: str) -> str | None:
    """The symbol last put in a register before the call at index, '' if cleared."""
    candidates = function.instructions[max(0, index - ARGUMENT_WINDOW):index]
    if index + 1 < len(function.instructions):
        candidates = candidates + [function.instructions[index + 1]]   # delay slot
    found = None
    for instruction in candidates:
        operands = [part.strip() for part in instruction.operands.split(',')]
        if not operands or operands[0] != register:
            continue
        reloc = _RELOC.search(instruction.operands)
        if instruction.mnemonic in ('addiu', 'ori') and reloc:
            found = reloc.group(1)
        elif instruction.mnemonic == 'lui' and reloc:
            found = found or reloc.group(1)
        elif (instruction.mnemonic in ('addu', 'or', 'move')
              and all(part in ('$zero', register) for part in operands[1:])
              and '$zero' in operands[1:]):
            found = ''
        else:
            found = None
    return found


def gte_fields(instruction: Instruction) -> tuple:
    """(command, sf, lm, mx, v, cv) from a GTE command word; mx/v/cv only for MVMVA."""
    word = instruction.word
    fields = (instruction.mnemonic, (word >> 19) & 1, (word >> 10) & 1)
    if instruction.mnemonic == 'mvmva':
        return fields + ((word >> 17) & 3, (word >> 15) & 3, (word >> 13) & 3)
    return fields + (None, None, None)


def division_guard(body: list[Instruction], index: int) -> str | None:
    """Which GCC division guard the ``break`` at ``index`` closes, if its shape matches.

    ``zero_divisor``: div|divu $zero, a, b; bnez b; nop; break 7.
    ``overflow``: that division, then addiu $at, $zero, -1; bne b, $at;
    lui $at, 0x8000; bne a, $at; nop; break 6.
    """
    code = (body[index].word >> 16) & 0x3FF
    if code == 7 and index >= 3:
        division, test, slot = body[index - 3:index]
        if (division.mnemonic in ('div', 'divu') and test.mnemonic == 'bnez'
                and slot.mnemonic == 'nop' and test.args()[0] == division.args()[-1]):
            return 'zero_divisor'
    if code == 6 and index >= 9:
        division = body[index - 9]
        load, first, high, second, slot = body[index - 5:index]
        if (division.mnemonic == 'div' and load.mnemonic == 'addiu'
                and load.args() == ['$at', '$zero', '-0x1'] and first.mnemonic == 'bne'
                and first.args()[:2] == [division.args()[-1], '$at']
                and high.mnemonic == 'lui' and high.args()[0] == '$at'
                and '0x80000000' in high.operands and second.mnemonic == 'bne'
                and second.args()[:2] == [division.args()[-2], '$at']
                and slot.mnemonic == 'nop'):
            return 'overflow'
    return None


def guarded(body: list[Instruction], index: int) -> bool:
    """A div/divu at ``index`` is guarded when a zero-divisor guard follows it."""
    return (index + 3 < len(body) and body[index + 3].mnemonic == 'break'
            and division_guard(body, index + 3) == 'zero_divisor')


def timing_cases(body: list[Instruction], index: int) -> list[str]:
    """Static checks for the cases swallow_tools.mips refuses; straight-line code only."""
    def at(offset: int) -> str | None:
        position = index - offset
        return PSEUDO.get(body[position].mnemonic, body[position].mnemonic) \
            if position >= 0 else None
    mnemonic, found = at(0), []
    if mnemonic in GTE_COMMANDS and (at(1) in GTE_WRITES or at(2) in GTE_WRITES):
        found.append('gte_command_after_write')
    if mnemonic in GTE_WRITES and at(1) in GTE_COMMANDS:
        found.append('gte_write_after_command')
    if mnemonic in GTE and at(1) in BRANCHES:
        found.append('cop2_in_delay_slot')
    if mnemonic in HILO_WRITES and (at(1) in ('mfhi', 'mflo') or at(2) in ('mfhi', 'mflo')):
        found.append('hilo_write_after_read')
    return found


def inventory_unit(name: str, functions: list[Function],
                   data_blocks: list[tuple[str, int, list[str]]],
                   entry_names: set[str], ranges: list[tuple[int, int]] | None) -> dict:
    label_owner = {label: function for function in functions for label in function.labels}
    for function in functions:
        function.library = (None if ranges is None else
                            any(start <= function.address < end for start, end in ranges))

    jump_tables, pointer_tables, taken = [], [], set()
    jumped_functions: Counter = Counter()
    for label, address, words in data_blocks:
        if label.startswith('jtbl_'):
            owners = {label_owner[word].name for word in words if word in label_owner}
            jump_tables.append({'label': label, 'address': f'0x{address:08X}',
                                'entries': len(words), 'functions': sorted(owners)})
            jumped_functions.update(owners)
            continue
        pointers = [word for word in words if word in entry_names]
        if pointers:
            taken.update(pointers)
            pointer_tables.append({'label': label, 'address': f'0x{address:08X}',
                                   'words': len(words), 'function_pointers': len(pointers)})

    def where(function: Function, instruction: Instruction) -> dict:
        return {'function': function.name, 'address': f'0x{instruction.address:08X}',
                'library': function.library}

    jalr, computed, unresolved, callbacks, stubs = [], [], [], [], []
    stub_of = {function.name: bios_stub(function) for function in functions}
    stub_calls: Counter = Counter()
    hardware: Counter = Counter()
    io_registers: Counter = Counter()
    gaps: Counter = Counter()
    gap_mnemonics: Counter = Counter()
    gte_encodings: Counter = Counter()
    traps: Counter = Counter()
    timing: Counter = Counter()
    unguarded = []
    counts = Counter()
    for function in functions:
        side = 'library' if function.library else 'game'
        counts[f'{side}_functions'] += 1
        if stub_of[function.name]:
            vector, number = stub_of[function.name]
            stubs.append({'function': function.name, 'address': f'0x{function.address:08X}',
                          'vector': f'0x{vector:02X}', 'number': f'0x{number:02X}'})
        for index, instruction in enumerate(function.instructions):
            mnemonic = PSEUDO.get(instruction.mnemonic, instruction.mnemonic)
            counts[f'{side}_instructions'] += 1
            for symbol in _RELOC.findall(instruction.operands):
                if symbol in entry_names:
                    taken.add(symbol)
            if mnemonic == 'jalr':
                jalr.append({**where(function, instruction),
                             'register': instruction.operands.split(',')[-1].strip()})
            elif mnemonic == 'jr' and instruction.operands.strip() != '$ra':
                computed.append(where(function, instruction))
                if function.name not in jumped_functions and not stub_of[function.name]:
                    unresolved.append(where(function, instruction))
            elif mnemonic == 'jal':
                callee = instruction.operands.strip()
                if callee in stub_of and stub_of[callee] and side == 'game':
                    stub_calls[callee] += 1
                if (callee.endswith('Callback') or callee in SECOND_ARGUMENT_SETTERS) \
                        and callee not in NOT_SETTERS:
                    register = '$a1' if callee in SECOND_ARGUMENT_SETTERS else '$a0'
                    callback = _argument(function, index, register)
                    callbacks.append({**where(function, instruction), 'setter': callee,
                                      'callback': ('none' if callback == '' else
                                                   callback or 'unresolved')})
            addresses = [0x1F800000 | int(value, 16)
                         for value in _HW_LITERAL.findall(instruction.operands)]
            addresses += [0x1F800000 | int(value, 16)
                          for value in _HW_SYMBOL.findall(instruction.operands)]
            for value in addresses:
                kind = ('scratchpad' if value < 0x1F800400 else
                        'io' if 0x1F801000 <= value < 0x1F803000 else 'other')
                hardware[f'{side}_{kind}'] += 1
                if kind == 'io':
                    io_registers[f'0x{value:08X}'] += 1
            if (mnemonic == 'lui' and not addresses
                    and instruction.operands.replace(' ', '').endswith(',0x1F80')):
                hardware[f'{side}_unresolved_lui'] += 1
            supported = mnemonic in SUPPORTED_MNEMONICS
            if supported and mnemonic in GTE_COMMANDS:
                try:
                    decode_command(instruction.word)
                except GteError:
                    supported = False      # e.g. MVMVA with an unmodelled selector
            if side == 'game':
                if mnemonic in GTE_COMMANDS:
                    gte_encodings[gte_fields(instruction)] += 1
                if mnemonic == 'break':
                    traps[division_guard(function.instructions, index) or 'other'] += 1
                elif mnemonic == 'syscall':
                    traps['syscall'] += 1
                if mnemonic in ('div', 'divu') and not guarded(function.instructions, index):
                    unguarded.append({**where(function, instruction), 'mnemonic': mnemonic})
                timing.update(timing_cases(function.instructions, index))
            if not supported:
                group = next((group for group, members in GAP_CLASSES if mnemonic in members),
                             'other')
                gaps[f'{side}_{group}'] += 1
                if side == 'game':
                    gap_mnemonics[mnemonic] += 1

    game_jalr = [site for site in jalr if not site['library']]
    return {
        'unit': name,
        'library_split': ranges is not None,
        'functions': len(functions),
        'counts': dict(sorted(counts.items())),
        'jalr_sites': len(jalr),
        'jalr_sites_game': len(game_jalr),
        'jalr': game_jalr,
        'computed_jumps': len(computed),
        'computed_jumps_unresolved': unresolved,
        'jump_tables': len(jump_tables),
        'jump_table_entries': sum(table['entries'] for table in jump_tables),
        'functions_with_jump_tables': len(jumped_functions),
        'function_pointer_tables': pointer_tables,
        'address_taken_functions': sorted(taken),
        'callback_registrations': callbacks,
        'bios_stubs': [{**stub, 'calls_from_game': stub_calls[stub['function']]}
                       for stub in stubs],
        'hardware_addresses': dict(sorted(hardware.items())),
        'io_registers': dict(sorted(io_registers.items())),
        'not_in_interpreter': dict(sorted(gaps.items())),
        'not_in_interpreter_game_mnemonics': dict(gap_mnemonics.most_common()),
        'gte_commands_game': [
            {'command': command, 'sf': sf, 'lm': lm, 'mx': mx, 'v': v, 'cv': cv, 'count': count}
            for (command, sf, lm, mx, v, cv), count in sorted(
                gte_encodings.items(), key=lambda item: (item[0][0], *[-1 if f is None else f
                                                                       for f in item[0][1:]]))],
        'traps_game': dict(sorted(traps.items())),
        'unguarded_divisions_game': unguarded,
        'refused_timing_cases_game': dict(sorted(timing.items())),
    }


def inventory_work_dir(work_dir: Path, psyq_report: dict | None = None) -> dict:
    try:
        split = json.loads((work_dir / 'split-report.json').read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise InventoryError(f'Cannot read split-report.json in {work_dir}: {exc}') from exc
    units = []
    for item in split.get('executables', []):
        units.append((item['config'], item['sha256'], item['text_files'], item['path']))
    for item in split.get('overlays', []):
        units.append((item['config'], item['sha256'], item['text_files'],
                      f"{item['archive']} entry {item['bph_entry']}"))
    parsed = []
    for config, sha256, text_files, source in units:
        functions, blocks = [], []
        for text_file in text_files:
            stem = text_file.removesuffix('_text.s')
            try:
                functions += parse_text((work_dir / 'asm' / text_file).read_text(
                    encoding='utf-8', errors='replace'))
            except OSError as exc:
                raise InventoryError(f'Missing assembly {text_file}: {exc}') from exc
            for data_file in sorted((work_dir / 'asm' / 'data').glob(f'{stem}_*.s')):
                blocks += parse_data(data_file.read_text(encoding='utf-8', errors='replace'))
        parsed.append((Path(config).stem, sha256, source, functions, blocks))
    entry_names = {name for _, _, _, functions, _ in parsed for function in functions
                   for name in [function.name, *function.entries]}
    reports = []
    for unit, sha256, source, functions, blocks in parsed:
        report = inventory_unit(unit, functions, blocks, entry_names,
                                library_ranges(psyq_report, sha256))
        reports.append({'source': source, 'sha256': sha256, **report})
    return {'schema_version': SCHEMA_VERSION, 'source_image': split.get('source_image'),
            'units': reports}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description='Inventory indirect control flow and gaps from swallow-split output')
    parser.add_argument('work_dir', type=Path, help='A swallow-split work directory')
    parser.add_argument('--psyq', type=Path, default=DEFAULT_PSYQ_REPORT,
                        help='PsyQ report used to tell library code from game code')
    parser.add_argument('--json', action='store_true', help='Print the full JSON report')
    args = parser.parse_args(argv)
    try:
        psyq = json.loads(args.psyq.read_text(encoding='utf-8')) if args.psyq.is_file() else None
        report = inventory_work_dir(args.work_dir, psyq)
    except (InventoryError, ValueError, KeyError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    for unit in report['units']:
        print(f"{unit['unit']}: {unit['functions']} functions, {unit['jalr_sites']} jalr "
              f"({unit['jalr_sites_game']} in game code), {unit['computed_jumps']} computed jumps "
              f"({len(unit['computed_jumps_unresolved'])} without a jump table), "
              f"{len(unit['address_taken_functions'])} address-taken functions, "
              f"{len(unit['callback_registrations'])} callback registrations")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
