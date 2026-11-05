"""Synthetic tests for the GTE model. Expected values are worked from the documented formulas."""
import unittest

from swallow_tools import gte
from swallow_tools.gte import (
    DQA, DQB, FLAG, H, IR0, IR1, IR2, IR3, IRGB, LZCR, LZCS, MAC0, MAC1, MAC2, MAC3, OFX, OFY,
    ORGB, OTZ, RGB0, RGB1, RGB2, RGBC, SXY0, SXY1, SXY2, SXYP, SZ0, SZ1, SZ2, SZ3, TRX, VXY0,
    VXY1, VXY2, VZ0, VZ1, VZ2, ZSF3, ZSF4, Gte, GteError, GteState, decode_command, unr_divide)
from swallow_tools.mips import Memory, call
from swallow_tools.mipsasm import GTE, JR, MFC2, NOP, program

ONE = 0x1000          # 1.0 in the GTE's 1.3.12 fixed point
IDENTITY = {0: ONE, 2: ONE, 4: ONE}          # RT11, RT22, RT33 packed as the hardware does


def matrix_words(first, rows):
    """Pack a 3x3 matrix into the five control words starting at ``first``."""
    halves = [value & 0xFFFF for row in rows for value in row] + [0]
    return {first + index: halves[2 * index] | (halves[2 * index + 1] << 16)
            for index in range(5)}


def xy(x, y):
    return (x & 0xFFFF) | ((y & 0xFFFF) << 16)


def bits(*numbers):
    value = 0
    for number in numbers:
        value |= 1 << number
    return value


def run(name, data=None, control=None, **fields):
    machine = Gte(GteState.from_registers(data, control))
    machine.execute(gte.GteCommand(name, fields.pop('sf', 1), fields.pop('lm', 0), **fields))
    return machine


def flag(machine):
    return machine.read_control(FLAG)


def signed(value):
    return value - 0x100000000 if value & 0x80000000 else value


class TableTests(unittest.TestCase):
    def test_reciprocal_table_follows_its_formula(self):
        table = gte.UNR_TABLE
        self.assertEqual(len(table), 0x101)
        self.assertEqual((table[0], table[1], table[0x100]), (0xFF, 0xFD, 0))
        self.assertTrue(all(a >= b for a, b in zip(table, table[1:])))

    def test_unr_divide(self):
        self.assertEqual(unr_divide(0x100, 0x100), 0x10000)       # 1.0 in 1.16
        self.assertEqual(unr_divide(0x100, 0x200), 0x8000)
        self.assertEqual(unr_divide(0x200, 0x1000), 0x2000)
        self.assertIsNone(unr_divide(0x200, 0x100))              # h >= 2 * sz3
        self.assertIsNone(unr_divide(0, 0))


class DecodeTests(unittest.TestCase):
    def test_fields_and_ignored_bits(self):
        command = decode_command(GTE('mvmva', sf=1, lm=1, mx=1, v=3, cv=3, macro=0x1F))
        self.assertEqual(command, gte.GteCommand('mvmva', 1, 1, 1, 3, 3))
        # Bits 6-9 and 11-12 are ignored too.
        self.assertEqual(decode_command(GTE('rtps') | 0x3C0 | 0x1800), gte.GteCommand('rtps', 1, 0))

    def test_unmodelled_commands_are_rejected(self):
        for word in ((0x12 << 26) | (1 << 25) | 0x0C, GTE('mvmva', cv=2), GTE('mvmva', mx=3),
                     0x48000000):
            with self.subTest(word=f'0x{word:08x}'):
                with self.assertRaises(GteError):
                    decode_command(word)


class RegisterTests(unittest.TestCase):
    def test_data_reads_extend_by_register(self):
        machine = Gte()
        for index in (VZ0, VZ1, VZ2, IR0, IR1, IR2, IR3, OTZ, SZ0, SZ1, SZ2, SZ3):
            machine.write_data(index, 0x12348000)
        for index in (VZ0, VZ1, VZ2, IR0, IR1, IR2, IR3):
            self.assertEqual(machine.read_data(index), 0xFFFF8000, index)
        for index in (OTZ, SZ0, SZ1, SZ2, SZ3):
            self.assertEqual(machine.read_data(index), 0x8000, index)
        machine.write_data(MAC1, 0x87654321)
        self.assertEqual(machine.read_data(MAC1), 0x87654321)

    def test_sxyp_pushes_on_write_and_reads_sxy2(self):
        machine = Gte()
        for value in (1, 2, 3, 4):
            machine.write_data(SXYP, value)
        self.assertEqual([machine.read_data(i) for i in (SXY0, SXY1, SXY2, SXYP)], [2, 3, 4, 4])

    def test_irgb_and_orgb(self):
        machine = Gte()
        machine.write_data(IRGB, 0x7FFF & ((3 << 10) | (31 << 5) | 1))
        self.assertEqual([machine.read_data(i) for i in (IR1, IR2, IR3)], [0x80, 0xF80, 0x180])
        machine.write_data(IR1, 0xFFFF8000)        # negative saturates to 0
        machine.write_data(IR2, 0x7FFF)            # above 0x1F << 7 saturates to 0x1F
        self.assertEqual(machine.read_data(ORGB), (3 << 10) | (31 << 5))
        self.assertEqual(machine.read_data(IRGB), machine.read_data(ORGB))
        machine.write_data(ORGB, 0)                # read-only
        self.assertEqual(machine.read_data(ORGB), (3 << 10) | (31 << 5))

    def test_leading_sign_bit_count(self):
        machine = Gte()
        self.assertEqual(machine.read_data(LZCR), 32)
        for value, expected in ((0xFFFFFFFF, 32), (1, 31), (0x80000000, 1), (0x7FFFFFFF, 1),
                                (0xF0000000, 4), (0x00010000, 15)):
            machine.write_data(LZCS, value)
            self.assertEqual(machine.read_data(LZCR), expected, hex(value))
        machine.write_data(LZCR, 5)                # read-only
        self.assertEqual(machine.read_data(LZCR), 15)

    def test_control_registers_that_read_back_sign_extended(self):
        machine = Gte()
        for index in range(31):
            machine.write_control(index, 0x00018000)
        for index in range(31):
            expected = 0xFFFF8000 if index in (4, 12, 20, H, DQA, ZSF3, ZSF4) else 0x00018000
            self.assertEqual(machine.read_control(index), expected, index)

    def test_flag_bit_31_and_unused_bits(self):
        machine = Gte()
        for written, read in ((bits(17), bits(31, 17)), (bits(12), bits(12)),
                              (bits(22), bits(22)), (bits(23), bits(31, 23)), (0xFFFFFFFF, 0xFFFFF000), (0xFFF, 0)):
            machine.write_control(FLAG, written)
            self.assertEqual(machine.read_control(FLAG), read, hex(written))

    def test_every_command_clears_flag_first(self):
        machine = Gte(GteState.from_registers(control={FLAG: bits(30, 17)}))
        machine.execute(gte.GteCommand('sqr', 1, 0))
        self.assertEqual(flag(machine), 0)


class PerspectiveTests(unittest.TestCase):
    CONTROL = {**IDENTITY, OFX: 160 << 16, OFY: 120 << 16, H: 0x100, DQA: 0x10, DQB: 0x100000}

    def test_rtps(self):
        machine = run('rtps', {VXY0: xy(100, 50), VZ0: 0x200}, self.CONTROL)
        # MAC = V; SZ3 = 0x200; h / sz3 = 0.5 (0x8000 in 1.16).
        self.assertEqual([machine.read_data(i) for i in (MAC1, MAC2, MAC3)], [100, 50, 0x200])
        self.assertEqual([machine.read_data(i) for i in (IR1, IR2, IR3)], [100, 50, 0x200])
        self.assertEqual(machine.read_data(SZ3), 0x200)
        self.assertEqual(machine.read_data(SXY2), xy(160 + 50, 120 + 25))
        self.assertEqual(machine.read_data(MAC0), 0x8000 * 0x10 + 0x100000)
        self.assertEqual(machine.read_data(IR0), (0x8000 * 0x10 + 0x100000) >> 12)
        self.assertEqual(flag(machine), 0)

    def test_rtps_translation_and_matrix(self):
        control = {**matrix_words(0, ((0, -ONE, 0), (ONE, 0, 0), (0, 0, 2 * ONE))),
                   TRX: 7, TRX + 1: -3, TRX + 2: 0x100, H: 0x100}
        machine = run('rtps', {VXY0: xy(10, 20), VZ0: 0x80}, control)
        self.assertEqual([signed(machine.read_data(i)) for i in (MAC1, MAC2, MAC3)],
                         [7 - 20, -3 + 10, 0x100 + 0x100])

    def test_division_overflow(self):
        machine = run('rtps', {VZ0: 0x80}, {**IDENTITY, H: 0x100})
        self.assertEqual(machine.read_data(SZ3), 0x80)       # h >= 2 * sz3
        self.assertEqual(flag(machine), bits(31, 17))
        self.assertEqual(signed(machine.read_data(MAC0)), 0)
        self.assertEqual(machine.read_data(IR0), 0)

    def test_screen_saturation(self):
        machine = run('rtps', {VXY0: xy(0x7000, -0x7000), VZ0: 0x100}, {**IDENTITY, H: 0x100})
        self.assertEqual(machine.read_data(SXY2), xy(0x3FF, -0x400))
        self.assertEqual(flag(machine), bits(31, 14, 13))

    def test_negative_depth_saturates_sz3_and_ir0(self):
        control = {**IDENTITY, H: 0x100, DQA: -0x100, DQB: 0}
        machine = run('rtps', {VZ0: 0xFFF0}, control)        # VZ0 = -16
        self.assertEqual(machine.read_data(SZ3), 0)
        self.assertEqual(flag(machine) & bits(18, 17), bits(18, 17))
        self.assertEqual(machine.read_data(IR0), 0)
        self.assertTrue(flag(machine) & bits(12))

    def test_ir3_flag_follows_mac3_shifted_by_12_when_sf_is_0(self):
        machine = run('rtps', {VZ0: 0x10}, {**IDENTITY, H: 0}, sf=0)
        # MAC3 = 0x10000 saturates IR3, but MAC3 >> 12 = 0x10 is in range: no flag 22.
        self.assertEqual(machine.read_data(MAC3), 0x10000)
        self.assertEqual(machine.read_data(IR3), 0x7FFF)
        self.assertFalse(flag(machine) & bits(22))
        machine = run('rtps', {VXY0: xy(0x10, 0)}, {**IDENTITY, H: 0}, sf=0)
        self.assertEqual(machine.read_data(IR1), 0x7FFF)      # IR1 saturates with its flag
        self.assertTrue(flag(machine) & bits(24))

    def test_rtpt_fills_the_fifos_in_order_and_depth_cues_the_last(self):
        data = {VXY0: xy(0, 0), VZ0: 0x100, VXY1: xy(0, 0), VZ1: 0x200,
                VXY2: xy(0, 0), VZ2: 0x400, SXYP: 0}
        control = {**IDENTITY, OFX: 5 << 16, H: 0x100, DQA: 1, DQB: 0}
        machine = run('rtpt', data, control)
        self.assertEqual([machine.read_data(i) for i in (SZ0, SZ1, SZ2, SZ3)],
                         [0, 0x100, 0x200, 0x400])
        self.assertEqual([machine.read_data(i) & 0xFFFF for i in (SXY0, SXY1, SXY2)], [5, 5, 5])
        self.assertEqual(machine.read_data(MAC0), unr_divide(0x100, 0x400))   # last vector only


class SimpleCommandTests(unittest.TestCase):
    def test_nclip(self):
        for points in (((0, 0), (4, 0), (0, 4)), ((0, 0), (0, 4), (4, 0)),
                       ((-0x8000, -0x8000), (0x7FFF, -0x8000), (-0x8000, 0x7FFF))):
            with self.subTest(points=points):
                (x0, y0), (x1, y1), (x2, y2) = points
                expected = x0 * y1 + x1 * y2 + x2 * y0 - x0 * y2 - x1 * y0 - x2 * y1
                machine = run('nclip', {SXY0: xy(x0, y0), SXY1: xy(x1, y1), SXY2: xy(x2, y2)})
                self.assertEqual(machine.read_data(MAC0), expected & 0xFFFFFFFF)
                self.assertEqual(flag(machine),
                                 bits(31, 16) if expected > 0x7FFFFFFF else 0)

    def test_average_z(self):
        machine = run('avsz3', {SZ1: 300, SZ2: 600, SZ3: 900}, {ZSF3: 0x555})
        self.assertEqual((machine.read_data(MAC0), machine.read_data(OTZ)),
                         (0x555 * 1800, (0x555 * 1800) >> 12))
        machine = run('avsz4', {SZ0: 100, SZ1: 200, SZ2: 300, SZ3: 400}, {ZSF4: 0x400})
        self.assertEqual((machine.read_data(MAC0), machine.read_data(OTZ)), (1024000, 250))
        machine = run('avsz3', {SZ1: 300}, {ZSF3: -0x555})
        self.assertEqual(machine.read_data(OTZ), 0)
        self.assertEqual(flag(machine), bits(31, 18))

    def test_sqr(self):
        machine = run('sqr', {IR1: 3, IR2: 4, IR3: -5}, sf=0)
        self.assertEqual([machine.read_data(i) for i in (MAC1, MAC2, MAC3)], [9, 16, 25])
        machine = run('sqr', {IR1: 0x7FFF, IR2: 0x40}, sf=1, lm=1)
        self.assertEqual(machine.read_data(MAC1), 0x3FFF0001 >> 12)
        self.assertEqual(machine.read_data(IR1), 0x7FFF)
        self.assertEqual(machine.read_data(IR2), 1)
        self.assertEqual(flag(machine), bits(31, 24))

    def test_mvmva_selectors(self):
        llm = matrix_words(8, ((ONE, 0, 0), (0, ONE // 2, 0), (0, 0, -ONE)))
        machine = run('mvmva', {IR1: 100, IR2: 200, IR3: 300}, llm, mx=1, v=3, cv=3)
        self.assertEqual([signed(machine.read_data(i)) for i in (MAC1, MAC2, MAC3)],
                         [100, 100, -300])
        machine = run('mvmva', {IR1: 100, IR2: 200, IR3: 300}, llm, mx=1, v=3, cv=3, lm=1)
        self.assertEqual(machine.read_data(IR3), 0)
        self.assertEqual(flag(machine), bits(22))           # IR3 is outside the bit-31 mask
        # RT with V1 and TR.
        control = {**IDENTITY, TRX: 1, TRX + 1: 2, TRX + 2: 3}
        machine = run('mvmva', {VXY1: xy(10, 20), VZ1: 30}, control, mx=0, v=1, cv=0)
        self.assertEqual([machine.read_data(i) for i in (MAC1, MAC2, MAC3)], [11, 22, 33])
        # LCM with V2 and BK, sf = 0.
        control = {**matrix_words(16, ((2, 0, 0), (0, 2, 0), (0, 0, 2))), 13: 1, 14: 1, 15: 1}
        machine = run('mvmva', {VXY2: xy(1, 2), VZ2: 3}, control, sf=0, mx=2, v=2, cv=1)
        self.assertEqual([machine.read_data(i) for i in (MAC1, MAC2, MAC3)],
                         [0x1000 + 2, 0x1000 + 4, 0x1000 + 6])

    def test_overflow_in_an_intermediate_sum_only(self):
        # TRX * 0x1000 + 0x7FFF * 0x7FFF passes 2^43; the next term brings the
        # exact total back, but the hardware checks and wraps after every addition.
        control = {**matrix_words(0, ((0x7FFF, -0x7FFF, 0), (0, 0, 0), (0, 0, 0))),
                   TRX: 0x7FFFFFFF}
        machine = run('mvmva', {IR1: 0x7FFF, IR2: 0x7FFF}, control, mx=0, v=3, cv=0)
        first = (0x7FFFFFFF << 12) + 0x7FFF * 0x7FFF
        self.assertGreaterEqual(first, 1 << 43)
        wrapped = first - (1 << 44)
        second = wrapped - 0x7FFF * 0x7FFF
        self.assertLess(second, -(1 << 43))
        self.assertEqual(machine.read_data(MAC1), ((second + (1 << 44)) >> 12) & 0xFFFFFFFF)
        self.assertEqual(flag(machine), bits(31, 30, 27, 24))


class ColorCommandTests(unittest.TestCase):
    def test_dpcs(self):
        data = {RGBC: 0x30204080, IR0: 0x800}
        control = {21: 0xFF0, 22: 0, 23: 0x200}             # far colour, 12.4 per channel
        machine = run('dpcs', data, control)
        # R: 0x80 halfway to 0xFF; G: 0x40 halfway to 0; B: 0x20 stays at 0x20.
        self.assertEqual(machine.read_data(RGB2), 0x302020BF)
        self.assertEqual([machine.read_data(i) for i in (MAC1, MAC2, MAC3)], [0xBF8, 0x200, 0x200])
        self.assertEqual(flag(machine), 0)

    def test_dpct_consumes_rgb0_on_each_pass(self):
        data = {RGB0: 0x00112233, RGB1: 0x00445566, RGB2: 0x00778899, RGBC: 0x7F000000, IR0: 0}
        machine = run('dpct', data)
        self.assertEqual([machine.read_data(i) for i in (RGB0, RGB1, RGB2)],
                         [0x7F112233, 0x7F445566, 0x7F778899])

    def test_intpl(self):
        data = {IR1: 0x100, IR2: 0x200, IR3: 0x300, IR0: 0x800, RGBC: 0x01000000}
        machine = run('intpl', data, {21: 0x1000})
        self.assertEqual([machine.read_data(i) for i in (IR1, IR2, IR3)], [0x880, 0x100, 0x180])
        self.assertEqual(machine.read_data(RGB2), 0x01181088)

    def test_gpf_and_gpl(self):
        machine = run('gpf', {IR1: 0x10, IR2: 0x20, IR3: 0x30, IR0: 2}, sf=0)
        self.assertEqual([machine.read_data(i) for i in (MAC1, MAC2, MAC3)], [0x20, 0x40, 0x60])
        self.assertEqual(machine.read_data(RGB2), 0x060402)
        machine = run('gpf', {IR1: 0x10, IR2: 0x20, IR3: 0x30, IR0: 0x800})
        self.assertEqual([machine.read_data(i) for i in (MAC1, MAC2, MAC3)], [8, 0x10, 0x18])
        data = {MAC1: 0x100, MAC2: 0x200, MAC3: 0x300, IR1: 0x10, IR2: 0x20, IR3: 0x30,
                IR0: 0x1000}
        machine = run('gpl', data)
        self.assertEqual([machine.read_data(i) for i in (MAC1, MAC2, MAC3)],
                         [0x110, 0x220, 0x330])
        self.assertEqual(machine.read_data(RGB2), 0x332211)

    def test_color_saturation(self):
        machine = run('gpf', {IR1: 0x7FFF, IR2: -0x10, IR3: 0x10, IR0: 0x1000})
        self.assertEqual(machine.read_data(RGB2), 0x0100FF)
        self.assertEqual(flag(machine), bits(21, 20))       # colour flags do not set bit 31

    def test_ncs_nct_and_nccs(self):
        control = {**matrix_words(8, ((ONE, 0, 0), (0, ONE, 0), (0, 0, ONE))),
                   **matrix_words(16, ((ONE, 0, 0), (0, ONE, 0), (0, 0, ONE))),
                   13: 0x10, 14: 0x20, 15: 0x30}
        data = {VXY0: xy(0x100, 0x200), VZ0: -0x100, RGBC: 0x05000000}
        machine = run('ncs', data, control, lm=1)
        # Step 1 clamps IR3 at 0 (lm = 1); step 2 adds BK.
        self.assertEqual([machine.read_data(i) for i in (MAC1, MAC2, MAC3)], [0x110, 0x220, 0x30])
        self.assertEqual(machine.read_data(RGB2), 0x05032211)
        self.assertEqual(flag(machine), bits(22))
        machine = run('nct', {**data, VXY1: xy(0x10, 0), VXY2: xy(0x20, 0)}, control, lm=1)
        self.assertEqual([machine.read_data(i) & 0xFF for i in (RGB0, RGB1, RGB2)],
                         [0x11, 0x02, 0x03])
        machine = run('nccs', {**data, RGBC: 0x05808080}, control, lm=1)
        self.assertEqual(machine.read_data(MAC1), (0x80 * 0x110 << 4) >> 12)
        self.assertEqual(machine.read_data(RGB2) & 0xFF, ((0x80 * 0x110 << 4) >> 12) >> 4)


# The GTE command encodings Japanese Disc 1's game code uses, as decoded fields
# (command, sf, lm, mx, v, cv), from swallow-inventory's gte_commands_game table.
MEASURED_ROWS = [
    ('avsz3', 1, 0, 0, 0, 0), ('avsz4', 1, 0, 0, 0, 0), ('dpcs', 1, 0, 0, 0, 0),
    ('dpct', 1, 0, 0, 0, 0), ('gpf', 0, 0, 0, 0, 0), ('gpf', 1, 0, 0, 0, 0),
    ('gpl', 1, 0, 0, 0, 0), ('intpl', 1, 0, 0, 0, 0), ('mvmva', 1, 0, 0, 0, 0),
    ('mvmva', 1, 0, 0, 0, 3), ('mvmva', 1, 0, 0, 1, 3), ('mvmva', 1, 0, 0, 2, 3),
    ('mvmva', 1, 0, 0, 3, 3), ('mvmva', 1, 1, 1, 0, 3), ('nccs', 1, 1, 0, 0, 0),
    ('nclip', 0, 0, 0, 0, 0), ('ncs', 1, 1, 0, 0, 0), ('nct', 1, 1, 0, 0, 0),
    ('rtps', 1, 0, 0, 0, 0), ('rtpt', 1, 0, 0, 0, 0), ('sqr', 0, 1, 0, 0, 0),
]


class MeasuredEncodingTests(unittest.TestCase):
    DATA = {VXY0: xy(10, -20), VZ0: 30, VXY1: xy(40, 50), VZ1: 60, VXY2: xy(70, 80), VZ2: 90,
            IR1: 100, IR2: 200, IR3: 300}
    CONTROL = {**matrix_words(0, ((ONE, 0, 0), (0, 2 * ONE, 0), (0, 0, -ONE))),
               **matrix_words(8, ((0, ONE, 0), (ONE, 0, 0), (0, 0, ONE))),
               TRX: 1, TRX + 1: 2, TRX + 2: 3}
    # (T * 0x1000 + M * V) >> 12 worked by hand: RT * V = (x, 2y, -z), LLM * V = (y, x, z).
    MVMVA = {(0, 0, 0): (11, -38, -27), (0, 0, 3): (10, -40, -30), (0, 1, 3): (40, 100, -60),
             (0, 2, 3): (70, 160, -90), (0, 3, 3): (100, 400, -300), (1, 0, 3): (-20, 10, 30)}

    def test_every_measured_encoding_decodes_and_runs(self):
        for command, sf, lm, mx, v, cv in MEASURED_ROWS:
            with self.subTest(command=command, sf=sf, lm=lm, mx=mx, v=v, cv=cv):
                decoded = decode_command(GTE(command, sf=sf, lm=lm, mx=mx, v=v, cv=cv))
                self.assertEqual((decoded.name, decoded.sf, decoded.lm), (command, sf, lm))
                machine = Gte(GteState.from_registers(self.DATA, {**self.CONTROL, H: 0x100}))
                machine.execute(decoded)

    def test_measured_mvmva_selectors(self):
        for (mx, v, cv), expected in self.MVMVA.items():
            lm = 1 if mx == 1 else 0
            with self.subTest(mx=mx, v=v, cv=cv):
                machine = run('mvmva', self.DATA, self.CONTROL, lm=lm, mx=mx, v=v, cv=cv)
                self.assertEqual([signed(machine.read_data(i)) for i in (MAC1, MAC2, MAC3)],
                                 list(expected))
                clamped = [max(0, value) if lm else value for value in expected]
                self.assertEqual([signed(machine.read_data(i)) for i in (IR1, IR2, IR3)],
                                 clamped)
                self.assertEqual(flag(machine), bits(31, 24) if lm else 0)

    def test_sqr_without_shift_and_with_lm(self):
        machine = run('sqr', {IR1: 3, IR2: -4, IR3: 5}, sf=0, lm=1)
        self.assertEqual([machine.read_data(i) for i in (IR1, IR2, IR3)], [9, 16, 25])
        self.assertEqual(flag(machine), 0)


class InterpreterTests(unittest.TestCase):
    def test_rtps_through_the_cpu_with_supplied_control_registers(self):
        memory = Memory()
        memory.map('text', 0x80010000, program(GTE('rtps'), MFC2('v0', SXY2), JR('ra'), NOP()),
                   executable=True)
        state = GteState.from_registers({VXY0: xy(100, 50), VZ0: 0x200},
                                        PerspectiveTests.CONTROL)
        result = call(memory, 0x80010000, max_steps=10, gte=state)
        self.assertEqual(result['v0'], xy(210, 145))
        self.assertEqual(result.gte.read_control(FLAG), 0)
        # Without the libraries' setup (H, offsets) the same code gives a different answer.
        result = call(memory, 0x80010000, max_steps=10,
                      gte=GteState.from_registers({VXY0: xy(100, 50), VZ0: 0x200}))
        self.assertNotEqual(result['v0'], xy(210, 145))


if __name__ == '__main__':
    unittest.main()
