"""Synthetic programs for the recompiler self-test, with expected results from the interpreter.

Offline tooling. Each program is assembled here with swallow_tools.mipsasm,
so the self-test holds no game code. The same programs are translated by
swallow-recompile into one module. The C harness (selftest_main.c) and the
ctypes runner (swallow_tools.native) each run every case natively and
compare it with swallow_tools.mips. Contract: docs/recompiler.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import random

from .gte import GteState
from .mips import RETURN_ADDRESS, Memory, MipsError, call, register_index
from .mipsasm import (
    ADD, ADDI, ADDIU, ADDU, AND, BEQ, BGTZ, BLEZ, BNE, BREAK, CFC2, CTC2, DIV, DIVU, GTE, JAL,
    JALR, JR, LB, LBU, LH, LHU, LUI, LW, LWC2, LWL, LWR, MFC2, MFHI, MFLO, MTC2, MULT, MULTU,
    NOP, NOR, OR, ORI, SB, SH, SLL, SLLV, SLT, SLTI, SLTIU, SLTU, SRA, SRAV, SRL, SRLV, SUB,
    SUBU, SW, SWC2, SWL, SWR, XOR, XORI, ANDI, program)
from .recompile import Function, Module, translate

TEXT_BASE = 0x80010000
PROGRAM_STRIDE = 0x1000
DATA_BASE = 0x80100000
MAX_STEPS = 10_000
PREFIX = 'selftest'


@dataclass(frozen=True)
class Program:
    name: str
    base: int
    words: tuple[int, ...]
    functions: tuple[Function, ...]
    jump_tables: dict[int, tuple[int, ...]]

    @property
    def text(self) -> bytes:
        return program(*self.words)


@dataclass(frozen=True)
class Case:
    name: str
    program: Program
    arguments: dict[str, int]
    data: bytes = b''
    gte: GteState | None = None

    @property
    def entry(self) -> int:
        return self.program.base


@dataclass(frozen=True)
class Outcome:
    ok: bool
    registers: tuple[int, ...]
    hi: int
    lo: int
    data: bytes
    write_end: int
    message: str = ''
    gte: GteState = GteState()


def _alu(base: int) -> list[int]:
    return [ADDIU('t0', 'zero', -5), ADDIU('t1', 'zero', 3), ADDU('v0', 't0', 't1'),
            SUBU('v1', 't1', 't0'), SLT('a2', 't0', 't1'), SLTU('a3', 't0', 't1'),
            SRA('t2', 't0', 1), SRL('t3', 't0', 28), SLL('t4', 't1', 30),
            SLLV('t5', 't1', 't1'), SRAV('t6', 't0', 't1'), SRLV('t7', 't0', 't1'),
            AND('s0', 't0', 't1'), OR('s1', 't0', 't1'), XOR('s2', 't0', 't1'),
            NOR('s3', 't0', 't1'), SLTI('s4', 't0', -4), SLTIU('s5', 't1', -1),
            ANDI('s6', 't0', 0xFF00), ORI('s7', 'zero', 0x8001), XORI('t8', 't0', 0xFFFF),
            LUI('t9', 0x8001), ADDI('a0', 't1', -10), SUB('a1', 't1', 't0'),
            ADD('k0', 't0', 't0'), ADDU('zero', 't0', 't1'), JR('ra'), NOP()]


def _slot_changes_condition(base: int) -> list[int]:
    # bgtz tests $t8 before its delay slot decrements it.
    return [ADDIU('t8', 'zero', 3), ADDIU('v0', 'zero', 0), ADDIU('v0', 'v0', 1),
            BGTZ('t8', -2), ADDI('t8', 't8', -1), JR('ra'), NOP()]


def _slot_is_target(base: int) -> list[int]:
    # The slot at +0x0C belongs to blez and is also bgtz's target, as at
    # 0x800AFEDC in ulz_decompress.
    return [ADDIU('a2', 'zero', 10), NOP(),
            BLEZ('a0', 6),                   # +0x08 -> +0x24
            ADDI('a2', 'a2', -1),            # +0x0C slot and target
            ADDIU('v0', 'v0', 1), ADDI('a0', 'a0', -1),
            BGTZ('a0', -4),                  # +0x18 -> +0x0C
            ADDIU('v1', 'v1', 1), NOP(),
            JR('ra'), NOP()]


def _jump_table(base: int) -> list[int]:
    table = base + 0x20
    return [SLL('t0', 'a0', 2), LUI('t1', table >> 16), ADDIU('t1', 't1', table & 0xFFFF),
            ADDU('t1', 't1', 't0'), LW('t2', 0, 't1'), NOP(), JR('t2'), ADDIU('v1', 'zero', 7),
            base + 0x2C, base + 0x38, 0,                       # the table, in the text
            ADDIU('v0', 'zero', 100), JR('ra'), NOP(),
            ADDIU('v0', 'zero', 200), JR('ra'), NOP()]


def _memory(base: int) -> list[int]:
    return [LUI('t0', DATA_BASE >> 16), LW('t1', 0, 't0'), LBU('t2', 1, 't0'), LB('t3', 2, 't0'),
            LH('t4', 2, 't0'), LHU('t5', 2, 't0'), SB('t2', 8, 't0'), SH('t4', 10, 't0'),
            SW('t1', 12, 't0'), JR('ra'), NOP()]


def _faults(base: int) -> list[int]:
    # a0 = 0: a write to the read-only text; otherwise a misaligned load.
    return [BEQ('a0', 'zero', 5), NOP(),                     # +0x00 -> +0x18
            LUI('t1', DATA_BASE >> 16), LW('v0', 2, 't1'), JR('ra'), NOP(),
            LUI('t2', base >> 16), ORI('t2', 't2', base & 0xFFFF),  # +0x18
            SW('zero', 0, 't2'), JR('ra'), NOP()]


def _multiply_divide(base: int) -> list[int]:
    return [MULT('a0', 'a1'), MFHI('t0'), MFLO('t1'), NOP(), NOP(),
            DIV('a0', 'a2'), MFHI('t2'), MFLO('t3'), NOP(), NOP(),
            DIVU('a0', 'a2'), MFHI('t4'), MFLO('t5'), NOP(), NOP(),
            MULTU('a0', 'a1'), MFHI('t6'), MFLO('t7'), JR('ra'), NOP()]


def _calls(base: int) -> list[int]:
    callee, callee2 = base + 0x30, base + 0x3C
    return [ADDU('s0', 'ra', 'zero'), JAL(callee), ADDIU('a0', 'zero', 5), ADDU('s1', 'v0', 'zero'),
            LUI('t9', callee2 >> 16), ADDIU('t9', 't9', callee2 & 0xFFFF), JALR('ra', 't9'),
            ADDIU('a0', 'zero', 7), ADDU('s2', 'v0', 'zero'), ADDU('ra', 's0', 'zero'),
            JR('ra'), NOP(),
            ADDU('v0', 'a0', 'a0'), JR('ra'), NOP(),                  # +0x30
            SLL('v0', 'a0', 3), JR('ra'), NOP()]                      # +0x3C


def _traps(base: int) -> list[int]:
    # a0 = 0: an overflowing addi. Otherwise GCC's divide-by-zero guard
    # (div; bnez divisor; nop; break 7) with $zero as the divisor reaches break 7.
    return [BNE('a0', 'zero', 6), NOP(),                     # +0x00 -> +0x1C
            LUI('t0', 0x7FFF), ORI('t0', 't0', 0xFFFF), ADDI('v0', 't0', 1), JR('ra'), NOP(),
            DIV('a1', 'zero'), BNE('zero', 'zero', 2), NOP(),  # +0x1C; never taken
            BREAK(7), MFLO('v0'), JR('ra'), NOP()]


def _unaligned(base: int) -> list[int]:
    # lwl/lwr pairs at every misalignment, a lone lwl and lwr that merge into
    # the register's old value, and swl/swr at every misalignment.
    return [LUI('t0', DATA_BASE >> 16),
            LWL('t1', 4, 't0'), LWR('t1', 1, 't0'), NOP(),
            LWL('t2', 5, 't0'), LWR('t2', 2, 't0'), NOP(),
            LWL('t3', 6, 't0'), LWR('t3', 3, 't0'), NOP(),
            LWL('t4', 7, 't0'), LWR('t4', 4, 't0'), NOP(),
            ADDU('t5', 'a0', 'zero'), LWL('t5', 9, 't0'), NOP(),
            ADDU('t6', 'a0', 'zero'), LWR('t6', 10, 't0'), NOP(),
            SWL('a1', 16, 't0'), SWL('a1', 21, 't0'), SWL('a1', 26, 't0'), SWL('a1', 31, 't0'),
            SWR('a2', 32, 't0'), SWR('a2', 37, 't0'), SWR('a2', 42, 't0'), SWR('a2', 47, 't0'),
            JR('ra'), NOP()]


def _gte(base: int) -> list[int]:
    # Every kind of COP2 access and a run of commands from a given GTE state.
    return [LUI('t0', DATA_BASE >> 16),
            LWC2(0, 0, 't0'), LWC2(1, 4, 't0'), NOP(), NOP(),
            GTE('rtps'), MFC2('t1', 14), CFC2('t2', 31), NOP(),
            SWC2(19, 16, 't0'), GTE('nclip'), MFC2('t3', 24), NOP(),
            MTC2('a0', 30), MFC2('t5', 31), NOP(),
            MTC2('a1', 28), MFC2('t6', 29), NOP(), MFC2('t7', 9), NOP(),
            CTC2('a2', 26), CFC2('s0', 26), NOP(),
            MTC2('a1', 15), MFC2('s1', 15), NOP(), MFC2('s2', 12), NOP(),
            CTC2('a3', 31), CFC2('s3', 31), NOP(),
            MTC2('a2', 1), MFC2('s4', 1), NOP(), MTC2('a2', 16), MFC2('s5', 16), NOP(),
            NOP(), NOP(),
            GTE('rtpt', sf=1, lm=0), GTE('avsz3'), MFC2('s6', 7), NOP(),
            GTE('avsz4'), GTE('mvmva', sf=1, lm=1, mx=1, v=3, cv=1),
            GTE('sqr', sf=0), GTE('dpct'), GTE('nccs', lm=1), GTE('intpl'),
            GTE('gpf', sf=0), GTE('gpl', sf=0), GTE('ncs'), GTE('nct'), GTE('dpcs'),
            GTE('mvmva', sf=0, mx=2, v=0, cv=3, macro=7), CFC2('s7', 31), NOP(),
            SWC2(22, 20, 't0'), SWC2(25, 24, 't0'),
            JR('ra'), NOP()]


def _endless(base: int) -> list[int]:
    return [BEQ('zero', 'zero', -1), NOP()]


_BUILDERS = [('alu', _alu, None), ('slot_changes_condition', _slot_changes_condition, None),
             ('slot_is_target', _slot_is_target, None), ('jump_table', _jump_table, (0x20, 3)),
             ('memory', _memory, None), ('faults', _faults, None),
             ('multiply_divide', _multiply_divide, None), ('calls', _calls, None),
             ('traps', _traps, None), ('endless', _endless, None),
             ('unaligned', _unaligned, None), ('gte', _gte, None)]


def programs() -> list[Program]:
    built = []
    for index, (name, builder, table) in enumerate(_BUILDERS):
        base = TEXT_BASE + index * PROGRAM_STRIDE
        words = tuple(builder(base))
        end = base + 4 * len(words)
        if name == 'calls':
            functions = (Function(name, base, base + 0x30, {base: name}),
                         Function(f'{name}_callee', base + 0x30, base + 0x3C,
                                  {base + 0x30: f'{name}_callee'}),
                         Function(f'{name}_callee2', base + 0x3C, end,
                                  {base + 0x3C: f'{name}_callee2'}))
        else:
            functions = (Function(name, base, end, {base: name}),)
        tables = {}
        if table is not None:
            offset, count = table
            tables[base + offset] = words[offset // 4:offset // 4 + count]
        built.append(Program(name, base, words, functions, tables))
    return built


def cases() -> list[Case]:
    by_name = {item.name: item for item in programs()}
    data = bytes([0x11, 0x88, 0x99, 0xAA]) + bytes(range(1, 13))
    return [
        Case('alu', by_name['alu'], {}),
        Case('slot_changes_condition', by_name['slot_changes_condition'], {}),
        Case('slot_is_target_loop', by_name['slot_is_target'], {'a0': 3}),
        Case('slot_is_target_skip', by_name['slot_is_target'], {'a0': 0}),
        Case('jump_table_0', by_name['jump_table'], {'a0': 0}),
        Case('jump_table_1', by_name['jump_table'], {'a0': 1}),
        Case('jump_table_zero_word', by_name['jump_table'], {'a0': 2}),
        Case('jump_table_past_end', by_name['jump_table'], {'a0': 3}),
        Case('memory', by_name['memory'], {}, data),
        Case('fault_write_read_only', by_name['faults'], {'a0': 0}, data),
        Case('fault_misaligned', by_name['faults'], {'a0': 1}, data),
        Case('multiply_divide', by_name['multiply_divide'],
             {'a0': 0xFFFFFFF9, 'a1': 3, 'a2': 2}),
        Case('divide_by_zero', by_name['multiply_divide'], {'a0': 5, 'a1': 0xFFFFFFFF, 'a2': 0}),
        Case('divide_overflow', by_name['multiply_divide'],
             {'a0': 0x80000000, 'a1': 0x80000000, 'a2': 0xFFFFFFFF}),
        Case('calls', by_name['calls'], {}),
        Case('trap_overflow', by_name['traps'], {'a0': 0}),
        Case('trap_break', by_name['traps'], {'a0': 1, 'a1': 9}),
        Case('endless_loop', by_name['endless'], {}),
        Case('unaligned', by_name['unaligned'],
             {'a0': 0xA1B2C3D4, 'a1': 0x11223344, 'a2': 0x55667788}, bytes(range(1, 49))),
        Case('fault_unaligned_unmapped', by_name['unaligned'], {}, bytes(range(1, 9))),
        Case('gte_scene', by_name['gte'],
             {'a0': 0xFFFF0000, 'a1': 0x7FFF, 'a2': 0x8123, 'a3': 0xFFFFFFFF},
             bytes(range(0x30, 0x60)), _gte_state('scene', 7)),
        Case('gte_random', by_name['gte'],
             {'a0': 0x00012345, 'a1': 0x1234, 'a2': 0x7FFF, 'a3': 0x7FFFF000},
             bytes(range(0xA0, 0xD0)), _gte_state('random', 11)),
    ]


def _gte_state(kind: str, seed: int) -> GteState:
    from .native import _scene_gte_state, random_gte_state
    rng = random.Random(seed)
    return _scene_gte_state(rng) if kind == 'scene' else random_gte_state(rng)


def regions(case: Case) -> list[tuple[str, int, bytearray, bool]]:
    """(name, base, bytes, writable) for every region a case maps."""
    mapped = [('text', case.program.base, bytearray(case.program.text), False)]
    if case.data:
        mapped.append(('data', DATA_BASE, bytearray(case.data), True))
    return mapped


def interpret(case: Case) -> Outcome:
    memory = Memory()
    for name, base, data, writable in regions(case):
        memory.map(name, base, data, writable=writable, executable=name == 'text')
    try:
        result = call(memory, case.entry, case.arguments, max_steps=MAX_STEPS, gte=case.gte)
    except MipsError as exc:
        return Outcome(False, (0,) * 32, 0, 0, b'', 0, str(exc))
    data = memory.region('data') if case.data else None
    return Outcome(True, result.registers, result.hi, result.lo,
                   bytes(data.data) if data else b'', data.write_end if data else 0,
                   gte=result.gte)


def selftest_module() -> Module:
    built = programs()
    end = max(item.base + 4 * len(item.words) for item in built)
    text = bytearray(end - TEXT_BASE)
    for item in built:
        offset = item.base - TEXT_BASE
        text[offset:offset + 4 * len(item.words)] = item.text
    tables = {address: words for item in built for address, words in item.jump_tables.items()}
    return Module(PREFIX, bytes(text), TEXT_BASE,
                  [function for item in built for function in item.functions], tables,
                  'the synthetic self-test programs', 'selftest')


def _c_bytes(data: bytes) -> str:
    return ', '.join(f'0x{byte:02X}' for byte in data) or '0'


def harness(case_list: list[Case]) -> str:
    """selftest_main.c: run every case natively and compare with the interpreter's outcome."""
    lines = ['/* Generated by swallow-recompile --selftest-out; synthetic programs only. */',
             '#include "ps1/core/cpu.h"', '#include "ps1/core/guest.h"', '',
             '#include <stdio.h>', '#include <string.h>', '',
             'SWALLOW_API int swallow_run(swallow_cpu *cpu, uint32_t entry);', '',
             'struct selftest_case {', '    const char *name;', '    uint32_t text_base;',
             '    const uint8_t *text;', '    uint32_t text_size;', '    const uint8_t *data;',
             '    uint32_t data_size;', '    int arguments;', '    int argument_index[4];',
             '    uint32_t argument_value[4];', '    int expect_ok;',
             '    uint32_t expect_registers[34];', '    const uint8_t *expect_data;',
             '    uint32_t expect_write_end;', '    uint32_t gte[64];', '    uint32_t expect_gte[64];',
             '};', '']
    for item in programs():
        lines.append(f'static const uint8_t text_{item.name}[] = {{{_c_bytes(item.text)}}};')
    entries = []
    for index, case in enumerate(case_list):
        outcome = interpret(case)
        lines.append(f'static const uint8_t data_{index}[] = {{{_c_bytes(case.data)}}};')
        lines.append(f'static const uint8_t expect_data_{index}[] = {{{_c_bytes(outcome.data)}}};')
        arguments = sorted((register_index(name), value) for name, value in case.arguments.items())
        registers = list(outcome.registers) + [outcome.hi, outcome.lo]
        start = case.gte or GteState()
        gte_in = ', '.join(f'0x{v:08X}u' for v in start.data + start.control)
        gte_out = ', '.join(f'0x{v:08X}u' for v in outcome.gte.data + outcome.gte.control)
        entries.append(
            f'    {{"{case.name}", 0x{case.program.base:08X}u, text_{case.program.name}, '
            f'{len(case.program.text)}u, data_{index}, {len(case.data)}u, {len(arguments)}, '
            f'{{{", ".join(str(i) for i, _ in arguments) or "0"}}}, '
            f'{{{", ".join(f"0x{v:08X}u" for _, v in arguments) or "0"}}}, '
            f'{int(outcome.ok)}, {{{", ".join(f"0x{v:08X}u" for v in registers)}}}, '
            f'expect_data_{index}, {outcome.write_end}u, {{{gte_in}}}, {{{gte_out}}}}},')
    lines += ['', 'static const struct selftest_case cases[] = {', *entries, '};', '',
              'static uint8_t text_buffer[0x1000];', 'static uint8_t data_buffer[256];', '',
              'static int run_case(swallow_cpu *cpu, const struct selftest_case *c)', '{',
              '    int failures = 0;', '    int index;', '    int status;',
              '    swallow_cpu_reset(cpu);',
              '    memcpy(text_buffer, c->text, c->text_size);',
              '    swallow_guest_map(cpu, "text", c->text_base, text_buffer, c->text_size, 0);',
              '    if (c->data_size != 0u) {',
              '        memcpy(data_buffer, c->data, c->data_size);',
              f'        swallow_guest_map(cpu, "data", 0x{DATA_BASE:08X}u, data_buffer, '
              'c->data_size, 1);', '    }',
              '    for (index = 0; index < c->arguments; ++index) {',
              '        swallow_set_register(cpu, c->argument_index[index], '
              'c->argument_value[index]);', '    }',
              '    for (index = 0; index < 64; ++index) {',
              '        swallow_gte_set(cpu, index, c->gte[index]);', '    }',
              f'    swallow_set_register(cpu, 31, 0x{RETURN_ADDRESS:08X}u);',
              f'    swallow_set_budget(cpu, {MAX_STEPS}u);',
              '    status = swallow_run(cpu, c->text_base);',
              '    if ((status == SWALLOW_OK) != (c->expect_ok != 0)) {',
              '        printf("FAIL %s: native %s (%s), interpreter %s\\n", c->name,',
              '               status == SWALLOW_OK ? "returned" : "trapped",',
              '               swallow_last_trap(cpu), c->expect_ok ? "returned" : "stopped");',
              '        return 1;', '    }',
              '    if (!c->expect_ok) {', '        return 0;', '    }',
              '    for (index = 0; index < 34; ++index) {',
              '        uint32_t value = swallow_get_register(cpu, index);',
              '        if (value != c->expect_registers[index]) {',
              '            printf("FAIL %s: register %d is 0x%08x, expected 0x%08x\\n", c->name,',
              '                   index, (unsigned)value, (unsigned)c->expect_registers[index]);',
              '            ++failures;', '        }', '    }',
              '    for (index = 0; index < 64; ++index) {',
              '        uint32_t value = swallow_gte_get(cpu, index);',
              '        if (value != c->expect_gte[index]) {',
              '            printf("FAIL %s: GTE register %d is 0x%08x, expected 0x%08x\\n", '
              'c->name,',
              '                   index, (unsigned)value, (unsigned)c->expect_gte[index]);',
              '            ++failures;', '        }', '    }',
              '    if (c->data_size != 0u) {',
              '        if (memcmp(data_buffer, c->expect_data, c->data_size) != 0) {',
              '            printf("FAIL %s: data differs\\n", c->name);', '            ++failures;',
              '        }',
              '        if (swallow_guest_write_end(cpu, "data") != (int64_t)c->expect_write_end) {',
              '            printf("FAIL %s: write end differs\\n", c->name);',
              '            ++failures;', '        }', '    }',
              '    return failures;', '}', '',
              'int main(void)', '{', '    swallow_cpu *cpu = swallow_cpu_new();',
              '    int failures = 0;', '    size_t index;',
              '    for (index = 0; index < sizeof(cases) / sizeof(cases[0]); ++index) {',
              '        failures += run_case(cpu, &cases[index]);', '    }',
              '    swallow_cpu_free(cpu);',
              '    printf("selftest: %u cases, %d failure(s)\\n", '
              '(unsigned)(sizeof(cases) / sizeof(cases[0])), failures);',
              '    return failures == 0 ? 0 : 1;', '}', '']
    return '\n'.join(lines)


def write_selftest(out_dir: Path) -> list[Path]:
    """Write selftest_module.c (the translated programs) and selftest_main.c (the harness)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    source, _ = translate(selftest_module())
    written = [out_dir / 'selftest_module.c', out_dir / 'selftest_main.c']
    written[0].write_text(source + '\n', encoding='utf-8')
    written[1].write_text(harness(cases()), encoding='utf-8')
    return written
