"""Synthetic tests for swallow_tools.ulzcheck; no game code or disc data is used.

The routine under test is a small project-written MIPS program that copies a
literals-only version 2 blob and leaves its stream pointers where the
harness reads them. It stands in for the game routine, which never appears
in tests.
"""
from contextlib import redirect_stderr, redirect_stdout
import dataclasses
import hashlib
from io import StringIO
import json
from pathlib import Path
import struct
import tempfile
import unittest

from swallow_tools.mips import Memory, MipsError, call
from swallow_tools.native import NativeError, NativeModule, NativeResult
from swallow_tools.ulz import decompress_ulz
from swallow_tools.ulzcheck import (
    ROUTINE_SYMBOL, RoutineOutput, check_image, compare_with_decoder, iter_ulz_blobs, main,
    run_routine)
from swallow_tools.oracle import read_symbols
from test_bph import make_index
from test_bpb import container, padded
from test_exe import make_exe, make_image
from test_mips import (
    ADDIU, ADDU, AND, BEQ, BNE, JR, LBU, LUI, LW, NOP, ORI, SB, SLL, SRL, program)
from test_ulz import build_ulz, encode_ulz

TEXT = 0x80010000
ENTRY = TEXT + 0x40

# literal_copier(a0 = blob, a1 = destination) -> v0 = end of output.
# Copies the declared size from the literal stream, then leaves t6 after the
# flag words that size needs, t5 at the literal stream rounded up to a word,
# and a3 at the pair stream, as the harness expects of a version 2 decode.
COPIER = [
    LW('t0', 4, 'a0'), LW('t1', 8, 'a0'), LW('t2', 12, 'a0'),         # 0-2
    LUI('t4', 0x00FF), ORI('t4', 't4', 0xFFFF),                        # 3-4
    AND('t0', 't0', 't4'), AND('t1', 't1', 't4'), AND('t2', 't2', 't4'),  # 5-7
    ADDU('t5', 'a0', 't1'), ADDU('a3', 'a0', 't2'),                    # 8-9
    ADDIU('t3', 't0', 31), SRL('t3', 't3', 5), SLL('t3', 't3', 2),     # 10-12
    ADDIU('t6', 'a0', 16), ADDU('t6', 't6', 't3'),                     # 13-14
    ADDU('v0', 'a1', 'zero'),                                          # 15
    BEQ('t0', 'zero', 7), NOP(),                                       # 16-17, to 24
    LBU('t8', 0, 't5'), ADDIU('t0', 't0', -1), SB('t8', 0, 'v0'),      # 18-20 loop
    ADDIU('t5', 't5', 1), BNE('t0', 'zero', -5), ADDIU('v0', 'v0', 1),  # 21-23
    ADDIU('t5', 't5', 3), ADDIU('t9', 'zero', -4), AND('t5', 't5', 't9'),  # 24-26
    JR('ra'), NOP(),
]
LITERAL_COPIER = program(*COPIER)
# The same copier with one extra byte stored after the output it reports.
OVERRUNNING_COPIER = program(*COPIER[:24], SB('t8', 0, 'v0'), *COPIER[24:])


def copier_text(routine=LITERAL_COPIER):
    text = bytearray(2048)
    text[ENTRY - TEXT:ENTRY - TEXT + len(routine)] = routine
    return bytes(text)


def literal_blob(payload):
    return build_ulz(len(payload), 2, 10, [1] * len(payload), payload, [])


def output_for(blob, **changes):
    """A RoutineOutput that agrees with ulz.py, optionally altered field by field."""
    result = decompress_ulz(blob)
    header = result.header
    flags = 4 * (result.flag_words_used + (header.version == 0))
    literals = result.literal_bytes_used
    if header.version == 2:
        literals = (literals + 3) // 4 * 4
    output = RoutineOutput(result.payload, header.decompressed_size, header.decompressed_size,
                           flags, literals, result.pair_bytes_used, 1)
    return dataclasses.replace(output, **changes)


class CompareTests(unittest.TestCase):
    def test_agreeing_runs_have_no_problems_for_both_versions(self):
        for version in (0, 2):
            blob = encode_ulz(b'abcabcabcabcxyz' * 9, version=version)
            with self.subTest(version=version):
                self.assertEqual(compare_with_decoder(blob, output_for(blob)), [])

    def test_version_rules_for_stream_consumption(self):
        v0 = encode_ulz(b'hello hello hello', version=0)
        # Version 0 reads the terminator word; version 2 reads literal words.
        self.assertEqual(output_for(v0).flag_bytes_read,
                         4 * (decompress_ulz(v0).flag_words_used + 1))
        self.assertEqual(output_for(literal_blob(b'abcde')).literal_bytes_read, 8)

    def test_each_difference_is_reported(self):
        blob = encode_ulz(b'abcdabcdabcd', version=2)
        size = decompress_ulz(blob).header.decompressed_size
        cases = {
            'returned end': {'end_offset': size - 1},
            'past the declared size': {'write_end': size + 2},
            'payload bytes differ': {'payload': b'x' * size},
            'flags stream': {'flag_bytes_read': 0},
            'literals stream': {'literal_bytes_read': 1},
            'pairs stream': {'pair_bytes_read': 99},
        }
        for message, change in cases.items():
            with self.subTest(message=message):
                problems = compare_with_decoder(blob, output_for(blob, **change))
                self.assertEqual(len(problems), 1, problems)
                self.assertIn(message, problems[0])


class RoutineTests(unittest.TestCase):
    def test_runs_the_routine_and_measures_streams(self):
        blob = literal_blob(b'swallow!')
        output = run_routine(copier_text(), TEXT, ENTRY, blob)
        self.assertEqual(output.payload, b'swallow!')
        self.assertEqual((output.end_offset, output.write_end), (8, 8))
        self.assertEqual((output.flag_bytes_read, output.literal_bytes_read,
                          output.pair_bytes_read), (4, 8, 0))
        self.assertGreater(output.steps, 8)
        self.assertEqual(compare_with_decoder(blob, output), [])

    def test_version_2_literal_reads_count_whole_words(self):
        blob = literal_blob(b'abcde')
        output = run_routine(copier_text(), TEXT, ENTRY, blob)
        self.assertEqual((output.payload, output.literal_bytes_read), (b'abcde', 8))
        self.assertEqual(compare_with_decoder(blob, output), [])

    def test_empty_output(self):
        output = run_routine(copier_text(), TEXT, ENTRY, literal_blob(b''))
        self.assertEqual((output.payload, output.end_offset, output.write_end), (b'', 0, 0))

    def test_reading_past_the_blob_stops_the_run(self):
        blob = bytearray(literal_blob(b'abcd'))
        struct.pack_into('<I', blob, 4, 64 | (2 << 24))   # declares more than it holds
        with self.assertRaisesRegex(MipsError, 'Unmapped 1-byte read'):
            run_routine(copier_text(), TEXT, ENTRY, bytes(blob))

    def test_write_past_the_declared_size_is_measured_inside_the_guard(self):
        blob = literal_blob(b'abcd')
        output = run_routine(copier_text(OVERRUNNING_COPIER), TEXT, ENTRY, blob)
        self.assertEqual((output.end_offset, output.write_end), (4, 5))
        self.assertEqual(compare_with_decoder(blob, output),
                         ['wrote 1 bytes past the declared size'])
        with self.assertRaisesRegex(MipsError, 'Unmapped 1-byte write'):
            run_routine(copier_text(OVERRUNNING_COPIER), TEXT, ENTRY, blob, guard_bytes=0)


class WalkTests(unittest.TestCase):
    def test_yields_stored_and_decoded_blobs_with_paths(self):
        inner = literal_blob(b'inner')
        outer = literal_blob(container(padded(b'skip'), inner))
        data = container(padded(b'opaque'), outer, container(inner))
        found = list(iter_ulz_blobs(data, (7,)))
        self.assertEqual([(path, origin) for path, origin, _ in found],
                         [((7, 1), 'stored'), ((7, 1, 1), 'decoded'), ((7, 2, 0), 'stored')])
        self.assertEqual(found[1][2], inner)

    def test_rejected_blob_is_yielded_but_not_descended(self):
        broken = literal_blob(container(literal_blob(b'x')))[:-8]
        self.assertEqual([origin for _, origin, _ in iter_ulz_blobs(broken, (0,))], ['stored'])


class SymbolTests(unittest.TestCase):
    def test_first_definition_wins_and_comments_are_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'symbols.txt'
            path.write_text('// ulz_decompress = 0x1;\nulz_decompress = 0x800AFD08; // type:func\n'
                            'ulz_decompress = 0x80000000;\nother=0x10;\n')
            self.assertEqual(read_symbols(path), {'ulz_decompress': 0x800AFD08, 'other': 0x10})

    def test_checked_in_symbol_lies_inside_the_ace3_text_image(self):
        root = Path(__file__).resolve().parents[1]
        symbols = read_symbols(root / 'splat' / 'symbol_addrs.ace3.txt')
        report = json.loads((root / 'docs' / 'reports' / 'exe-jp1.json').read_text())
        ace3 = next(e for e in report['executables'] if e['path'] == 'ACE3.BIN')
        start, end = (int(value, 16) for value in ace3['text_range'])
        self.assertTrue(start <= symbols[ROUTINE_SYMBOL] < end)
        self.assertNotIn(ROUTINE_SYMBOL, read_symbols(root / 'splat' / 'symbol_addrs.boot.txt'))


class FakeNative:
    """Stands in for a library built by swallow-recompile: runs the interpreter
    behind NativeModule's interface, writing into the caller's buffers."""

    def __init__(self, sha256, trap=None):
        self.sha256 = sha256
        self.trap = trap
        self.calls = 0

    def run(self, entry, regions, arguments=None, *, budget):
        self.calls += 1
        if self.trap:
            return NativeResult(False, self.trap, (0,) * 32, 0, 0, {})
        memory = Memory()
        for name, base, data, writable in regions:
            memory.map(name, base, data, writable=writable, executable=name == 'text')
        result = call(memory, entry, arguments, max_steps=budget)
        write_ends = {}
        for name, _, data, _ in regions:
            region = memory.region(name)
            data[:] = region.data
            write_ends[name] = region.write_end
        return NativeResult(True, '', result.registers, result.hi, result.lo, write_ends)


def archive_files(payloads):
    """ACE.BPH and ACE.BPB bytes with each payload on its own sector."""
    records, bpb, sector = [], bytearray(), 0
    for payload in payloads:
        records.append((0xE0000000 | ((len(payload) + 3) // 4), sector))
        bpb += payload + bytes(-len(payload) % 2048)
        sector += (len(payload) + 2047) // 2048
    return make_index(records), bytes(bpb)


class ImageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.configs = self.root / 'configs'
        self.configs.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, payloads, *, symbols='ulz_decompress = 0x80010040; // type:func\n'):
        boot = make_exe(t_addr=0x80120000, pc=0x80120000)
        game = make_exe(text=copier_text(), pc=ENTRY)
        bph, bpb = archive_files(payloads)
        image = make_image(self.root, [('BOOT.EXE', boot), ('GAME.BIN', game),
                                       ('ACE.BPH', bph), ('ACE.BPB', bpb)])
        entries = [('boot.yaml', boot, '// none\n'), ('game.yaml', game, symbols)]
        for config, _, text in entries:
            (self.configs / config).write_text('target\n')
            (self.configs / f'symbols.{config}.txt').write_text(text)
        (self.configs / 'manifest.json').write_text(json.dumps({
            'schema_version': 1,
            'configs': [{'config': c, 'target': f'{c}.psexe', 'symbols': f'symbols.{c}.txt',
                         'sha256': [hashlib.sha256(data).hexdigest()]}
                        for c, data, _ in entries]}))
        return image

    def test_every_blob_is_run_and_matched(self):
        image = self.build([container(literal_blob(b'mission'), padded(b'raw')),
                            literal_blob(container(literal_blob(b'nested')))])
        report = check_image(image, config_dir=self.configs)
        self.assertEqual(report['executable']['path'], 'GAME.BIN')
        self.assertEqual(report['routine'], {'symbol': ROUTINE_SYMBOL, 'address': '0x80010040'})
        self.assertEqual((report['blobs'], report['matched'], report['mismatch_count']),
                         (3, 3, 0))
        self.assertEqual(report['versions'], {'2': 3})
        self.assertGreater(report['instructions_executed'], 0)
        self.assertEqual(json.loads(json.dumps(report)), report)

    def test_disagreement_and_cli_exit_codes(self):
        bad = bytearray(literal_blob(b'abcd'))
        bad[16:20] = struct.pack('<I', 0x70000000)    # first flag asks for a missing pair
        image = self.build([literal_blob(b'good'), bytes(bad)])
        report = check_image(image, config_dir=self.configs)
        self.assertEqual((report['matched'], report['decoder_error_count']), (1, 1))
        self.assertEqual(report['decoder_errors'][0]['path'], '1')
        out = StringIO()
        with redirect_stdout(out):
            self.assertEqual(main([str(image), '--config-dir', str(self.configs)]), 1)
        self.assertIn('1 of 2 blobs match', out.getvalue())
        with redirect_stdout(StringIO()):
            self.assertEqual(main([str(image), '--config-dir', str(self.configs),
                                   '--entry', '0', '--json']), 0)

    def test_native_run_applies_the_same_checks(self):
        image = self.build([literal_blob(b'mission'), literal_blob(b'second')])
        game = make_exe(text=copier_text(), pc=ENTRY)
        native = FakeNative(hashlib.sha256(game).hexdigest())
        report = check_image(image, config_dir=self.configs, native=native)
        self.assertEqual((report['oracle'], report['matched'], report['blobs']),
                         ('native', 2, 2))
        self.assertIsNone(report['instructions_executed'])
        self.assertEqual(native.calls, 2)
        self.assertEqual(report['schema_version'], 2)
        interpreted = check_image(image, config_dir=self.configs)
        self.assertEqual(interpreted['oracle'], 'interpreter')

    def test_native_trap_is_a_routine_error(self):
        image = self.build([literal_blob(b'x')])
        game = make_exe(text=copier_text(), pc=ENTRY)
        native = FakeNative(hashlib.sha256(game).hexdigest(), trap='Unmapped 4-byte read')
        report = check_image(image, config_dir=self.configs, native=native)
        self.assertEqual(report['routine_error_count'], 1)
        self.assertIn('Native trap: Unmapped', report['routine_errors'][0]['error'])

    def test_library_from_another_executable_is_refused(self):
        image = self.build([literal_blob(b'x')])
        with self.assertRaisesRegex(Exception, 'translated from SHA256 ' + '0' * 64):
            check_image(image, config_dir=self.configs, native=FakeNative('0' * 64))

    def test_missing_library_exits_2(self):
        image = self.build([literal_blob(b'x')])
        err = StringIO()
        with redirect_stderr(err):
            self.assertEqual(main([str(image), '--config-dir', str(self.configs),
                                   '--native', str(self.root / 'missing.so')]), 2)
        self.assertIn('Cannot load', err.getvalue())
        with self.assertRaises(NativeError):
            NativeModule(self.root / 'missing.so')

    def test_missing_routine_symbol_exits_2(self):
        image = self.build([literal_blob(b'x')], symbols='// none\n')
        err = StringIO()
        with redirect_stderr(err):
            self.assertEqual(main([str(image), '--config-dir', str(self.configs)]), 2)
        self.assertIn('found 0', err.getvalue())


if __name__ == '__main__':
    unittest.main()
