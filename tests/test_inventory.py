"""Synthetic tests for swallow_tools.inventory; the assembly is written here, in spimdisasm's format."""
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from swallow_tools.inventory import (
    InventoryError, inventory_work_dir, library_ranges, main, parse_data, parse_text)
from swallow_tools.mipsasm import BREAK, DIV, GTE


def line(address, mnemonic, operands='', slot=False, word=0):
    pad = '   ' if slot else '  '
    raw = word.to_bytes(4, 'little').hex().upper()     # spimdisasm prints file byte order
    return f'    /* {address - 0x80010000:X} {address:08X} {raw} */{pad}{mnemonic:<10} {operands}'


GAME_TEXT = '\n'.join([
    '.section .text, "ax"',
    'glabel main_func',
    line(0x80010000, 'lui', '$a0, %hi(cb_func)'),
    line(0x80010004, 'addiu', '$a0, $a0, %lo(cb_func)'),
    line(0x80010008, 'jal', 'CdReadyCallback'),
    line(0x8001000C, 'nop', slot=True),
    line(0x80010010, 'jal', 'ResetCallback'),
    line(0x80010014, 'nop', slot=True),
    line(0x80010018, 'jalr', '$v0'),
    line(0x8001001C, 'nop', slot=True),
    line(0x80010020, 'lui', '$s0, (0x1F800004 >> 16)'),
    line(0x80010024, 'sh', '$v0, (0x1F800004 & 0xFFFF)($s0)'),
    line(0x80010028, 'jr', '$v1'),
    line(0x8001002C, 'nop', slot=True),
    '  .L80010030:',
    line(0x80010030, 'mult', '$a0, $a1'),
    line(0x80010034, 'lwl', '$v0, 0x3($a0)'),
    line(0x80010038, 'mtc2', '$a0, $0'),
    line(0x8001003C, 'jal', 'bios_stub'),
    line(0x80010040, 'addu', '$a0, $zero, $zero', slot=True),
    line(0x80010044, 'jal', 'VSyncCallback'),
    line(0x80010048, 'nop', slot=True),
    line(0x8001004C, 'jr', '$ra'),
    line(0x80010050, 'nop', slot=True),
    'endlabel main_func',
    '',
    'glabel cb_func',
    line(0x80010054, 'jr', '$ra'),
    line(0x80010058, 'nop', slot=True),
    'endlabel cb_func',
    '',
    'glabel odd_jump',
    line(0x8001005C, 'jr', '$t9  /* handwritten instruction */'),
    line(0x80010060, 'nop', slot=True),
    'endlabel odd_jump',
    '',
    'glabel math_func',
    line(0x80010070, 'div', '$zero, $a0, $a1', word=DIV('a0', 'a1')),
    line(0x80010074, 'bnez', '$a1, .L80010080'),
    line(0x80010078, 'nop', slot=True),
    line(0x8001007C, 'break', '7', word=BREAK(7)),
    '  .L80010080:',
    line(0x80010080, 'addiu', '$at, $zero, -0x1'),
    line(0x80010084, 'bne', '$a1, $at, .L80010098'),
    line(0x80010088, 'lui', '$at, (0x80000000 >> 16)', slot=True),
    line(0x8001008C, 'bne', '$a0, $at, .L80010098'),
    line(0x80010090, 'nop', slot=True),
    line(0x80010094, 'break', '6', word=BREAK(6)),
    '  .L80010098:',
    line(0x80010098, 'mflo', '$v0'),
    line(0x8001009C, 'div', '$zero, $v0, $a2  /* handwritten instruction */',
         word=DIV('v0', 'a2')),
    line(0x800100A0, 'mtc2', '$a0, $0'),
    line(0x800100A4, 'rtps', '', word=GTE('rtps', macro=1)),
    line(0x800100A8, 'mvmva', '1, 1, 0, 3, 1', word=GTE('mvmva', lm=1, mx=1, v=0, cv=3)),
    line(0x800100AC, 'mvmva', '1, 0, 0, 2, 0', word=GTE('mvmva', cv=2)),
    line(0x800100B0, 'jr', '$ra'),
    line(0x800100B4, 'nop', slot=True),
    'endlabel math_func',
    '',
    'glabel lib_func',
    line(0x80020000, 'lui', '$t0, (0x1F801120 >> 16)'),
    line(0x80020004, 'lw', '$t1, (0x1F801120 & 0xFFFF)($t0)'),
    line(0x80020008, 'lui', '$a1, %hi(cb_func)'),
    line(0x8002000C, 'jal', 'DMACallback'),
    line(0x80020010, 'addiu', '$a1, $a1, %lo(cb_func)', slot=True),
    line(0x80020014, 'jr', '$ra'),
    line(0x80020018, 'nop', slot=True),
    'endlabel lib_func',
    '',
    'glabel bios_stub',
    line(0x80020100, 'addiu', '$t2, $zero, 0xA0'),
    line(0x80020104, 'jr', '$t2'),
    line(0x80020108, 'addiu', '$t1, $zero, 0x39', slot=True),
    'endlabel bios_stub',
])

GAME_DATA = '\n'.join([
    'dlabel jtbl_80000100',
    '    /* 100 80000100 30000180 */ .word .L80010030',
    'enddlabel jtbl_80000100',
    'dlabel handler_table',
    '    /* 104 80000104 00000000 */ .word cb_func',
    '    /* 108 80000108 00000000 */ .word 0x00000000',
    '    /* 10C 8000010C 00000000 */ .word D_80000000 + 0x4',
    'enddlabel handler_table',
])

OVERLAY_TEXT = '\n'.join([
    'glabel ovl_func',
    line(0x800C0000, 'jal', 'main_func'),
    line(0x800C0004, 'nop', slot=True),
    line(0x800C0008, 'jr', '$ra'),
    line(0x800C000C, 'nop', slot=True),
    'endlabel ovl_func',
])

GAME_SHA = 'aa' * 32
PSYQ = {'executables': [{'sha256': GAME_SHA, 'objects': [
    {'address': '0x80020000', 'size': 0x20}, {'address': '0x80020100', 'size': 0x10}]}]}


def make_work_dir(root):
    (root / 'asm' / 'data').mkdir(parents=True)
    (root / 'asm' / 'game_text.s').write_text(GAME_TEXT)
    (root / 'asm' / 'data' / 'game_rodata.rodata.s').write_text(GAME_DATA)
    (root / 'asm' / 'ovl_text.s').write_text(OVERLAY_TEXT)
    (root / 'asm' / 'data' / 'ovl_data.data.s').write_text(
        'dlabel ovl_table\n    /* 0 800C1000 00000000 */ .word ovl_func\nenddlabel ovl_table\n')
    (root / 'split-report.json').write_text(json.dumps({
        'schema_version': 2, 'source_image': 'synthetic.bin',
        'executables': [{'path': 'GAME.BIN', 'sha256': GAME_SHA, 'config': 'game.yaml',
                         'text_files': ['game_text.s']}],
        'overlays': [{'archive': 'ACE.BPB', 'bph_entry': 1, 'sha256': 'bb' * 32,
                      'config': 'ovl.yaml', 'text_files': ['ovl_text.s']}]}))


class ParseTests(unittest.TestCase):
    def test_functions_labels_and_operands(self):
        functions = {f.name: f for f in parse_text(GAME_TEXT)}
        self.assertEqual(list(functions), ['main_func', 'cb_func', 'odd_jump', 'math_func',
                                           'lib_func', 'bios_stub'])
        self.assertEqual(functions['math_func'].instructions[3].word, 0x0007000D)
        self.assertEqual(functions['main_func'].address, 0x80010000)
        self.assertIn('.L80010030', functions['main_func'].labels)
        self.assertEqual(functions['odd_jump'].instructions[0].operands, '$t9')

    def test_data_words_keep_symbols_and_drop_numbers(self):
        blocks = parse_data(GAME_DATA)
        self.assertEqual(blocks[1], ('handler_table', 0x80000104, ['cb_func', '', 'D_80000000']))

    def test_library_blocks_merge_close_objects_only(self):
        report = {'executables': [{'sha256': 'x', 'objects': [
            {'address': '0x80000000', 'size': 0x10}, {'address': '0x80000100', 'size': 0x10},
            {'address': '0x80100000', 'size': 0x10}]}]}
        self.assertEqual(library_ranges(report, 'x'),
                         [(0x80000000, 0x80000110), (0x80100000, 0x80100010)])
        self.assertIsNone(library_ranges(report, 'other'))
        self.assertIsNone(library_ranges(None, 'x'))


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        make_work_dir(self.root)
        report = inventory_work_dir(self.root, PSYQ)
        self.game, self.overlay = report['units']

    def tearDown(self):
        self.tmp.cleanup()

    def test_library_and_game_code_are_split_by_the_psyq_objects(self):
        self.assertTrue(self.game['library_split'])
        self.assertEqual(self.game['counts']['game_functions'], 4)
        self.assertEqual(self.game['counts']['library_functions'], 2)
        self.assertFalse(self.overlay['library_split'])
        self.assertEqual(self.overlay['source'], 'ACE.BPB entry 1')

    def test_indirect_control_flow(self):
        self.assertEqual(self.game['jalr'], [{'function': 'main_func', 'address': '0x80010018',
                                              'library': False, 'register': '$v0'}])
        self.assertEqual(self.game['computed_jumps'], 3)
        self.assertEqual([site['function'] for site in self.game['computed_jumps_unresolved']],
                         ['odd_jump'])
        self.assertEqual((self.game['jump_tables'], self.game['functions_with_jump_tables']),
                         (1, 1))
        self.assertEqual(self.game['bios_stubs'], [{
            'function': 'bios_stub', 'address': '0x80020100', 'vector': '0xA0',
            'number': '0x39', 'calls_from_game': 1}])

    def test_callbacks_and_address_taken_functions(self):
        found = {(c['setter'], c['callback'], c['library'])
                 for c in self.game['callback_registrations']}
        self.assertEqual(found, {('CdReadyCallback', 'cb_func', False),
                                 ('VSyncCallback', 'none', False),
                                 ('DMACallback', 'cb_func', True)})
        self.assertIn('cb_func', self.game['address_taken_functions'])
        self.assertEqual(self.game['function_pointer_tables'], [{
            'label': 'handler_table', 'address': '0x80000104', 'words': 3,
            'function_pointers': 1}])
        self.assertEqual(self.overlay['address_taken_functions'], ['ovl_func'])

    def test_hardware_addresses_and_interpreter_gaps(self):
        self.assertEqual(self.game['hardware_addresses'],
                         {'game_scratchpad': 2, 'library_io': 2})
        self.assertEqual(self.game['io_registers'], {'0x1F801120': 2})
        # mult, lwl, mtc2 and two GTE commands now run; MVMVA with cv = 2 does not.
        self.assertEqual(self.game['not_in_interpreter'], {'game_gte': 1, 'game_trap': 2})
        self.assertEqual(self.game['not_in_interpreter_game_mnemonics'],
                         {'break': 2, 'mvmva': 1})

    def test_gte_encodings_division_guards_and_timing(self):
        self.assertEqual(self.game['gte_commands_game'], [
            {'command': 'mvmva', 'sf': 1, 'lm': 0, 'mx': 0, 'v': 0, 'cv': 2, 'count': 1},
            {'command': 'mvmva', 'sf': 1, 'lm': 1, 'mx': 1, 'v': 0, 'cv': 3, 'count': 1},
            {'command': 'rtps', 'sf': 1, 'lm': 0, 'mx': None, 'v': None, 'cv': None,
             'count': 1}])
        self.assertEqual(self.game['traps_game'], {'overflow': 1, 'zero_divisor': 1})
        self.assertEqual(self.game['unguarded_divisions_game'], [{
            'function': 'math_func', 'address': '0x8001009C', 'library': False,
            'mnemonic': 'div'}])
        # rtps and the first mvmva follow mtc2 within two instructions; mflo is
        # right before the unguarded div.
        self.assertEqual(self.game['refused_timing_cases_game'],
                         {'gte_command_after_write': 2, 'hilo_write_after_read': 1})

    def test_unknown_executable_has_no_library_split(self):
        report = inventory_work_dir(self.root, {'executables': []})
        self.assertFalse(report['units'][0]['library_split'])
        self.assertEqual(report['units'][0]['counts']['game_functions'], 6)


class CliTests(unittest.TestCase):
    def test_summary_json_and_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_work_dir(root)
            psyq = root / 'psyq.json'
            psyq.write_text(json.dumps(PSYQ))
            out = StringIO()
            with redirect_stdout(out):
                self.assertEqual(main([str(root), '--psyq', str(psyq)]), 0)
            self.assertIn('game: 6 functions, 1 jalr (1 in game code)', out.getvalue())
            out = StringIO()
            with redirect_stdout(out):
                self.assertEqual(main([str(root), '--psyq', str(psyq), '--json']), 0)
            self.assertEqual(json.loads(out.getvalue())['schema_version'], 2)
            err = StringIO()
            with redirect_stderr(err):
                self.assertEqual(main([str(root / 'missing')]), 2)
            self.assertIn('split-report.json', err.getvalue())
            with self.assertRaises(InventoryError):
                inventory_work_dir(root / 'missing')


if __name__ == '__main__':
    unittest.main()
