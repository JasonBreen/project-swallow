"""Synthetic tests for swallow_tools.oracle; the executables are assembled here."""
from pathlib import Path
import tempfile
import unittest

from swallow_tools.mips import MipsError
from swallow_tools.mipsasm import ADDIU, JR, LUI, LW, NOP, ORI, SW, program
from swallow_tools.oracle import (
    RAM_END, SCRATCHPAD_BASE, Layout, OracleError, StagedExecutable, crt0_gp, game_call,
    game_memory, read_layout)

ROOT = Path(__file__).resolve().parents[1]
TEXT = 0x80010000


def staged(image, text_address=TEXT, entry=TEXT):
    return StagedExecutable('GAME.EXE', '00' * 32, 'game.yaml', 'symbols.txt', {}, image,
                            text_address, entry, 0x801FFFF0)


class LayoutTests(unittest.TestCase):
    def test_checked_in_configs(self):
        ace3 = read_layout(ROOT / 'splat' / 'ace3.yaml')
        self.assertEqual(ace3.code, ((0x80025724, 0x800B0130),))
        self.assertEqual(ace3.gp_value, 0x800BCB74)
        boot = read_layout(ROOT / 'splat' / 'boot.yaml')
        self.assertEqual(boot.gp_value, 0x80194F40)
        ovl1 = read_layout(ROOT / 'splat' / 'ovl1.yaml')
        self.assertEqual(ovl1.code, ((0x800CBFE8 + 0x5F0, 0x800CBFE8 + 0x10B18),))
        self.assertIsNone(ovl1.gp_value)

    def test_malformed_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'bad.yaml'
            path.write_text('segments:\n  - name: main\n    type: code\n')
            with self.assertRaisesRegex(OracleError, 'expected one segment'):
                read_layout(path)


class Crt0Tests(unittest.TestCase):
    def test_finds_the_gp_pair_after_the_entry_point(self):
        image = program(NOP(), LUI('gp', 0x800C), ADDIU('gp', 'gp', -0x348C), JR('ra'))
        self.assertEqual(crt0_gp(image, TEXT, TEXT), 0x800BCB74)
        image = program(LUI('gp', 0x8019), ORI('gp', 'gp', 0x4F40))
        self.assertEqual(crt0_gp(image, TEXT, TEXT), 0x80194F40)

    def test_absent_or_out_of_range(self):
        self.assertIsNone(crt0_gp(program(NOP(), JR('ra')), TEXT, TEXT))
        self.assertIsNone(crt0_gp(program(NOP()), TEXT, TEXT + 0x100))


class MemoryTests(unittest.TestCase):
    # rodata (8 bytes), code (16 bytes), data (8 bytes).
    IMAGE = (bytes(8) + program(LW('v0', -0x7FF0, 'gp'), NOP(), JR('ra'), SW('v0', -8, 'sp'))
             + (42).to_bytes(4, 'little') + bytes(4))
    LAYOUT = Layout(((TEXT + 8, TEXT + 24),), None)

    def test_regions_follow_the_layout(self):
        memory = game_memory(staged(self.IMAGE), self.LAYOUT)
        regions = {region.name: region for region in memory.regions}
        self.assertEqual(set(regions), {'data0', 'code0', 'ram', 'scratchpad'})
        self.assertTrue(regions['code0'].executable and not regions['code0'].writable)
        self.assertTrue(regions['data0'].writable and regions['ram'].writable)
        self.assertEqual(regions['ram'].end, RAM_END)
        self.assertEqual(regions['scratchpad'].base, SCRATCHPAD_BASE)

    def test_a_call_reads_through_gp_and_writes_its_frame(self):
        memory = game_memory(staged(self.IMAGE), self.LAYOUT)
        gp = TEXT + 24 + 0x7FF0                      # -0x7FF0($gp) is the data word 42
        result = game_call(memory, TEXT + 8, gp=gp, sp=0x801FFFF0, max_steps=10)
        self.assertEqual(result['v0'], 42)
        self.assertEqual(memory.load(0x801FFFE8, 4), 42)

    def test_without_gp_the_same_call_fails(self):
        memory = game_memory(staged(self.IMAGE), self.LAYOUT)
        with self.assertRaisesRegex(MipsError, 'Unmapped'):
            game_call(memory, TEXT + 8, gp=0, sp=0x801FFFF0, max_steps=10)

    def test_bad_layouts_and_stacks(self):
        with self.assertRaisesRegex(OracleError, 'outside the image'):
            game_memory(staged(self.IMAGE), Layout(((TEXT, TEXT + 0x100),), None))
        memory = game_memory(staged(self.IMAGE), self.LAYOUT)
        with self.assertRaisesRegex(MipsError, 'Stack pointer'):
            game_call(memory, TEXT + 8, gp=0, sp=0x801FFFF4, max_steps=10)


if __name__ == '__main__':
    unittest.main()
