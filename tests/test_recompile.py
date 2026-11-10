"""Compiler-free tests for swallow_tools.recompile and the self-test generator.

They check the emitted C text and the translator's refusals. Compiling and
running the C is covered by the runtime's CTest targets (runtime/CMakeLists.txt).
"""
from pathlib import Path
import re
import tempfile
import unittest

from swallow_tools import selftest
from swallow_tools.mipsasm import (
    ADDI, ADDIU, ADDU, BEQ, BGTZ, BLEZ, CFC2, CTC2, GTE, J, JAL, JR, LW, LWC2, LWL, LWR, MFC2,
    MFLO, MTC2, MULT, NOP, SWC2, SWL, SWR, program)
from swallow_tools.recompile import (
    Function, Module, RecompileError, Symbol, cmake_project, functions_from_symbols,
    jump_tables_from_symbols, read_symbol_file, recompile_image, recompile_split,
    split_functions, translate)

BASE = 0x80010000
ROOT = Path(__file__).resolve().parents[1]


def module(words, *, functions=None, tables=None):
    end = BASE + 4 * len(words)
    return Module('t', program(*words), BASE,
                  functions or [Function('f', BASE, end, {BASE: 'f'})], tables or {},
                  'a synthetic program', 'synthetic')


def c_source(words, **kwargs):
    return translate(module(words, **kwargs))[0]


class SymbolTests(unittest.TestCase):
    def test_reads_types_sizes_entries_and_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'symbols.txt'
            path.write_text('// comment\n'
                            'outer = 0x80010000; // type:func size:0x40\n'
                            'table = 0x80010010; // type:label\n'
                            'inner = 0x80010020; // type:func\n'
                            'after = 0x80010040; // type:func size:0x8\n')
            symbols = read_symbol_file(path)
        self.assertEqual([(s.name, s.kind, s.size) for s in symbols],
                         [('outer', 'func', 0x40), ('table', 'label', None),
                          ('inner', 'func', None), ('after', 'func', 8)])
        [outer] = functions_from_symbols(symbols, ['outer'])
        self.assertEqual(outer.entries, {0x80010000: 'outer', 0x80010020: 'inner'})
        with self.assertRaisesRegex(RecompileError, 'not a sized type:func'):
            functions_from_symbols(symbols, ['inner'])
        text = bytes(range(0x48))
        tables = jump_tables_from_symbols(symbols, ['table'], text, BASE)
        self.assertEqual(len(tables[0x80010010]), 4)     # up to inner at 0x80010020
        self.assertEqual(tables[0x80010010][0], int.from_bytes(text[0x10:0x14], 'little'))

    def test_the_checked_in_symbol_file_describes_the_ulz_routine(self):
        symbols = read_symbol_file(ROOT / 'splat' / 'symbol_addrs.ace3.txt')
        [function] = functions_from_symbols(symbols, ['ulz_decompress'])
        self.assertEqual(function.entries, {0x800AFD08: 'ulz_decompress',
                                            0x800AFF74: 'ulz_stream_init',
                                            0x800AFFF0: 'ulz_stream_step'})
        self.assertEqual((function.start, function.end), (0x800AFD08, 0x800B0130))


class TranslationTests(unittest.TestCase):
    def test_delay_slot_runs_after_the_condition_and_before_the_transfer(self):
        source = c_source([ADDIU('t8', 'zero', 3), BGTZ('t8', -1), ADDI('t8', 't8', -1),
                           JR('ra'), NOP()])
        block = source[source.index('bgtz */'):]
        taken = block.index('const int taken = swallow_s32(r_t8) > 0;')
        slot = block.index('r_t8 = swallow_add(cpu, r_t8, 0xFFFFFFFFu')
        transfer = block.index('if (taken)')
        self.assertLess(taken, slot)
        self.assertLess(slot, transfer)

    def test_backward_branches_spend_the_budget(self):
        source = c_source([BEQ('a0', 'zero', 1), NOP(), BGTZ('a0', -3), NOP(), JR('ra'), NOP()])
        self.assertIn('if (taken) { goto L_80010008; }', source)       # forward: no budget
        self.assertIn('if (taken) { if (cpu->budget-- == 0u) { swallow_trap(', source)

    def test_a_slot_that_is_also_a_target_is_emitted_twice(self):
        words = [BLEZ('a0', 4), ADDI('a2', 'a2', -1),     # +0x00, slot +0x04
                 ADDI('a0', 'a0', -1), BGTZ('a0', -3), NOP(),  # +0x0C -> +0x04
                 JR('ra'), NOP()]
        source = c_source(words)
        self.assertEqual(source.count('r_a2 = swallow_add(cpu, r_a2, 0xFFFFFFFFu, 0x80010004u'), 2)
        self.assertIn('goto L_80010008;\nL_80010004:', source)

    def test_jump_table_words_are_data_and_unknown_targets_trap(self):
        table = BASE + 0x10
        words = [LW('t0', 0, 'a0'), NOP(), JR('t0'), NOP(),
                 BASE + 0x18, 0,                             # the table
                 ADDIU('v0', 'zero', 1), JR('ra'), NOP()]
        source, summary = translate(module(words, tables={table: (BASE + 0x18, 0)}))
        self.assertIn('case 0x80010018u: goto L_80010018;', source)
        self.assertIn('not a known target', source)
        self.assertNotIn('0x80010010u', source)               # table words never translated
        self.assertEqual(summary['functions'][0]['instructions'], 7)

    def test_zero_register_and_unsupported_instructions(self):
        unmodelled = (0x12 << 26) | (1 << 25) | 0x0C          # GTE "op": not modelled
        source = c_source([ADDU('zero', 'a0', 'a1'), LW('zero', 0, 'a0'), unmodelled,
                           JR('ra'), NOP()])
        self.assertIn('/* result discarded: $zero */;', source)
        self.assertIn('(void)swallow_load(cpu, r_a0, 4u, 0x80010004u);', source)
        self.assertIn('swallow_trap(cpu, "unsupported: instruction 0x4a00000c at 0x80010008");',
                      source)
        self.assertEqual(translate(module([unmodelled, JR('ra'), NOP()]))[1]
                         ['functions'][0]['untranslated'], ['0x80010000'])

    def test_cop2_instructions_use_the_gte_core(self):
        source, summary = translate(module([
            LWC2(0, 4, 'a0'), MTC2('a1', 1), CTC2('a2', 26), NOP(), NOP(),
            GTE('rtps', macro=5), MFC2('v0', 14), CFC2('v1', 31), NOP(), SWC2(19, 8, 'a0'),
            JR('ra'), NOP()]))
        self.assertIn('swallow_gte_write_data(&cpu->gte, 0u, swallow_load(cpu, r_a0 + 0x4u, '
                      '4u, 0x80010000u));', source)
        self.assertIn('swallow_gte_write_data(&cpu->gte, 1u, r_a1);', source)
        self.assertIn('swallow_gte_write_control(&cpu->gte, 26u, r_a2);', source)
        self.assertIn('(void)swallow_gte_command(&cpu->gte, 0x4A580001u); /* rtps */', source)
        self.assertIn('r_v0 = swallow_gte_read_data(&cpu->gte, 14u);', source)
        self.assertIn('r_v1 = swallow_gte_read_control(&cpu->gte, 31u);', source)
        self.assertIn('swallow_store(cpu, r_a0 + 0x8u, 4u, swallow_gte_read_data(&cpu->gte, '
                      '19u), 0x80010024u);', source)
        self.assertEqual(summary['functions'][0]['untranslated'], [])

    def test_unaligned_accesses_merge_into_the_register(self):
        source = c_source([LWL('t0', 3, 'a0'), LWR('t0', 0, 'a0'), NOP(),
                           SWL('t0', 7, 'a1'), SWR('t0', 4, 'a1'), JR('ra'), NOP()])
        self.assertIn('r_t0 = swallow_lwl(cpu, r_a0 + 0x3u, r_t0, 0x80010000u);', source)
        self.assertIn('r_t0 = swallow_lwr(cpu, r_a0, r_t0, 0x80010004u);', source)
        self.assertIn('swallow_swl(cpu, r_a1 + 0x7u, r_t0, 0x8001000Cu);', source)
        self.assertIn('swallow_swr(cpu, r_a1 + 0x4u, r_t0, 0x80010010u);', source)

    def test_jumps_into_another_function_are_tail_calls(self):
        other = BASE + 0x10
        words = [BEQ('a0', 'zero', 3), NOP(), J(other), ADDIU('v0', 'zero', 1),
                 ADDIU('v0', 'zero', 2), JR('ra'), NOP()]
        functions = [Function('f', BASE, other, {BASE: 'f'}),
                     Function('g', other, BASE + 0x1C, {other: 'g'})]
        source = c_source(words, functions=functions)
        body = source[source.index('/* f:'):source.index('/* g:')]
        self.assertEqual(body.count('t_fn_80010010(cpu, 0x80010010u);'), 2)
        self.assertLess(body.index('cpu->r[2] = r_v0;'), body.index('t_fn_80010010(cpu'))
        self.assertIn('return;', body)

    def test_jump_tables_in_rodata_resolve_by_their_targets(self):
        words = [LW('t0', 0, 'a0'), NOP(), JR('t0'), NOP(),
                 ADDIU('v0', 'zero', 1), JR('ra'), NOP(),
                 ADDIU('v0', 'zero', 2), JR('ra'), NOP()]
        rodata = 0x80090000
        source = c_source(words, tables={rodata: (BASE + 0x10, BASE + 0x1C, 0x80050000)})
        self.assertIn('case 0x80010010u: goto L_80010010;', source)
        self.assertIn('case 0x8001001Cu: goto L_8001001C;', source)
        self.assertNotIn('case 0x80050000u', source)

    def test_calls_spill_and_reload_registers(self):
        callee = BASE + 0x10
        words = [JAL(callee), ADDIU('a0', 'zero', 5), JR('ra'), NOP(),
                 ADDU('v0', 'a0', 'a0'), JR('ra'), NOP()]
        functions = [Function('caller', BASE, callee, {BASE: 'caller'}),
                     Function('callee', callee, BASE + 0x1C, {callee: 'callee'})]
        source = c_source(words, functions=functions)
        call = source[source.index('jal */'):]
        self.assertLess(call.index('cpu->r[4] = r_a0;'),
                        call.index('t_fn_80010010(cpu, 0x80010010u);'))
        self.assertIn('r_a0 = cpu->r[4];', call)
        unknown = c_source([JAL(0x80020000), NOP(), JR('ra'), NOP()])
        self.assertIn('call to untranslated 0x80020000', unknown)

    def test_always_taken_branch_needs_no_fall_through(self):
        source = c_source([ADDIU('v0', 'zero', 1), BEQ('zero', 'zero', -2), NOP()])
        self.assertIn('if (cpu->budget-- == 0u)', source)
        self.assertNotIn('const int taken', source)

    def test_unaligned_pair_is_allowed(self):
        translate(module([LWL('t0', 3, 'a0'), LWR('t0', 0, 'a0'), NOP(), JR('ra'), NOP()]))


class RefusalTests(unittest.TestCase):
    def test_load_delay_hazards_are_refused(self):
        with self.assertRaisesRegex(RecompileError, r'uses \$t0 in the delay slot'):
            translate(module([LW('t0', 0, 'a0'), ADDU('v0', 't0', 'zero'), JR('ra'), NOP()]))
        # A load in a branch's delay slot is checked against the branch target too.
        with self.assertRaisesRegex(RecompileError, 'delay slot of the lw at 0x80010004'):
            translate(module([BEQ('a0', 'zero', 2), LW('t0', 0, 'a0'), NOP(),
                              ADDU('v0', 't0', 'zero'), JR('ra'), NOP()]))
        with self.assertRaisesRegex(RecompileError, 'delay slot of jr'):
            translate(module([JR('ra'), LW('v0', 0, 'a0')]))

    def test_control_leaving_the_function_is_refused(self):
        with self.assertRaisesRegex(RecompileError, 'outside f'):
            translate(module([ADDIU('v0', 'zero', 1)]))
        with self.assertRaisesRegex(RecompileError, 'Branch in the delay slot'):
            translate(module([BEQ('a0', 'zero', 1), BEQ('a0', 'zero', 1), NOP(), JR('ra'), NOP()]))

    def test_timing_cases_are_refused(self):
        with self.assertRaisesRegex(RecompileError, 'mult too soon after the mflo'):
            translate(module([MFLO('v0'), NOP(), MULT('a0', 'a1'), JR('ra'), NOP()]))
        with self.assertRaisesRegex(RecompileError, 'rtps too soon after the GTE write'):
            translate(module([MTC2('a0', 0), NOP(), GTE('rtps'), JR('ra'), NOP()]))
        with self.assertRaisesRegex(RecompileError, 'mtc2 too soon after the GTE command'):
            translate(module([GTE('rtps'), MTC2('a0', 0), JR('ra'), NOP()]))
        with self.assertRaisesRegex(RecompileError, 'mfc2 in a branch delay slot'):
            translate(module([BEQ('a0', 'zero', 1), MFC2('v0', 14), JR('ra'), NOP()]))
        # The distance is counted along every path, here from a branch's slot
        # to its target; the fall-through path returns.
        with self.assertRaisesRegex(RecompileError, 'mult too soon after the mflo'):
            translate(module([BEQ('a0', 'zero', 3), MFLO('v0'), JR('ra'), NOP(),
                              MULT('a0', 'a1'), JR('ra'), NOP()]))
        translate(module([MTC2('a0', 0), NOP(), NOP(), GTE('rtps'), MFC2('v0', 14), NOP(),
                          MFLO('v1'), NOP(), NOP(), MULT('a0', 'a1'), JR('ra'), NOP()]))

    def test_output_must_be_outside_the_repository_and_empty(self):
        with self.assertRaisesRegex(RecompileError, 'inside the repository'):
            recompile_image(ROOT / 'missing.bin', ROOT / 'build-generated', repo_root=ROOT)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'out'
            out.mkdir()
            (out / 'old.c').write_text('')
            with self.assertRaisesRegex(RecompileError, 'not empty'):
                recompile_image(ROOT / 'missing.bin', out, repo_root=ROOT)

    def test_cmake_project_links_the_runtime(self):
        text = cmake_project(ROOT / 'runtime')
        self.assertIn(f'add_subdirectory("{(ROOT / "runtime").resolve().as_posix()}"', text)
        self.assertIn('add_library(swallow_recompiled SHARED module.c)', text)


def _asm_line(address, word, text):
    return f'    /* 0 {address:08X} {word.to_bytes(4, "little").hex().upper()} */  {text}'


class SplitTests(unittest.TestCase):
    def work_dir(self, tmp: Path) -> Path:
        (tmp / 'asm' / 'data').mkdir(parents=True)
        lines = ['glabel game_a', _asm_line(BASE, JR('ra'), 'jr $ra'),
                 '  alabel game_a_alt', _asm_line(BASE + 4, NOP(), 'nop'), 'endlabel game_a',
                 'glabel lib_b', _asm_line(BASE + 8, JR('ra'), 'jr $ra'),
                 _asm_line(BASE + 12, NOP(), 'nop'), 'endlabel lib_b']
        (tmp / 'asm' / 'exe_text.s').write_text('\n'.join(lines) + '\n')
        data = ['dlabel jtbl_80020000', _asm_line(0x80020000, BASE, '.word .L80010000'),
                _asm_line(0x80020004, BASE + 4, '.word .L80010004'), 'enddlabel jtbl_80020000',
                'dlabel D_80020008', _asm_line(0x80020008, 0, '.word 0x00000000'),
                'enddlabel D_80020008']
        (tmp / 'asm' / 'data' / 'exe_rodata.rodata.s').write_text('\n'.join(data) + '\n')
        (tmp / 'split-report.json').write_text(
            '{"executables": [{"sha256": "ab", "config": "exe.yaml", '
            '"text_files": ["exe_text.s"]}]}')
        return tmp

    def test_game_functions_entries_and_jump_tables_come_from_the_split(self):
        psyq = {'executables': [{'sha256': 'ab', 'objects': [
            {'address': f'0x{BASE + 8:08x}', 'size': 8}]}]}
        with tempfile.TemporaryDirectory() as tmp:
            work = self.work_dir(Path(tmp))
            functions, tables, skipped = split_functions(
                work, 'ab', psyq, [Symbol('extra', BASE + 4, 'func', None)])
        self.assertEqual(skipped, 1)
        self.assertEqual([(f.name, f.start, f.end) for f in functions],
                         [('game_a', BASE, BASE + 8)])
        self.assertEqual(functions[0].entries, {BASE: 'game_a', BASE + 4: 'game_a_alt'})
        self.assertEqual(tables, [(0x80020000, 2)])

    def test_split_needs_the_executable_and_a_library_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = self.work_dir(Path(tmp))
            with self.assertRaisesRegex(RecompileError, 'no split of the executable'):
                split_functions(work, 'cd', {}, [])
            with self.assertRaisesRegex(RecompileError, 'does not list this executable'):
                split_functions(work, 'ab', {'executables': []}, [])
        with self.assertRaisesRegex(RecompileError, 'inside the repository'):
            recompile_split(ROOT / 'missing.bin', ROOT, ROOT / 'build-generated', repo_root=ROOT)


class SelftestTests(unittest.TestCase):
    def test_programs_translate_and_the_interpreter_sets_the_expectations(self):
        outcomes = {case.name: selftest.interpret(case) for case in selftest.cases()}
        self.assertEqual(outcomes['slot_changes_condition'].registers[2], 4)
        self.assertEqual(outcomes['slot_is_target_loop'].registers[6], 7)
        self.assertEqual(outcomes['calls'].registers[2], 56)
        self.assertEqual(outcomes['divide_by_zero'].lo, 0xFFFFFFFB)   # multu ran last
        for name in ('jump_table_zero_word', 'fault_write_read_only', 'trap_overflow',
                     'trap_break', 'endless_loop'):
            self.assertFalse(outcomes[name].ok, name)
        source, summary = translate(selftest.selftest_module())
        self.assertIn('SWALLOW_API int swallow_run', source)
        self.assertEqual(len(summary['functions']), 14)
        self.assertTrue(outcomes['unaligned'].ok)
        self.assertFalse(outcomes['fault_unaligned_unmapped'].ok)
        for name in ('gte_scene', 'gte_random'):
            self.assertTrue(outcomes[name].ok, name)
            self.assertNotEqual(outcomes[name].gte, selftest.cases()[-1].gte or None)

    def test_write_selftest(self):
        with tempfile.TemporaryDirectory() as tmp:
            written = selftest.write_selftest(Path(tmp))
            self.assertEqual([path.name for path in written],
                             ['selftest_module.c', 'selftest_main.c'])
            harness = written[1].read_text()
        self.assertEqual(len(re.findall(r'^    \{"', harness, re.M)), len(selftest.cases()))


if __name__ == '__main__':
    unittest.main()
