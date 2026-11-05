"""Synthetic tests for the R3000A interpreter; every program is hand-written here."""
import struct
import unittest

from swallow_tools.gte import GteState, MAC0, IR1, IR2, IR3, FLAG
from swallow_tools.mips import (
    RETURN_ADDRESS, SUPPORTED_MNEMONICS, TRAP_MNEMONICS, Memory, MipsError, MipsTrap,
    RunResult, _decode, call, divide, register_index)
from swallow_tools.mipsasm import (
    NOP, SLL, SRL, SRA, SLLV, SRLV, SRAV, JR, JALR, SYSCALL,
    BREAK, MFHI, MTHI, MFLO, MTLO, MULT, MULTU, DIV, DIVU, ADD, ADDU, SUB, SUBU, AND, OR,
    XOR, NOR, SLT, SLTU, BLTZ, BGEZ, J, JAL, BEQ, BNE, BLEZ, BGTZ, ADDI, ADDIU, SLTI, SLTIU,
    ANDI, ORI, XORI, LUI, LB, LH, LWL, LW, LBU, LHU, LWR, SB, SH, SWL, SW, SWR, MFC2, CFC2,
    MTC2, CTC2, LWC2, SWC2, GTE, program)

BASE = 0x80010000
DATA = 0x80100000


def run(*words, arguments=None, data=None, writable=True, max_steps=1000, **state):
    memory = Memory()
    memory.map('text', BASE, program(*words), executable=True)
    if data is not None:
        memory.map('data', DATA, data, writable=writable)
    return call(memory, BASE, arguments, max_steps=max_steps, **state), memory


class ControlFlowTests(unittest.TestCase):
    def test_return_executes_the_delay_slot(self):
        result, _ = run(JR('ra'), ADDIU('v0', 'zero', 7))
        self.assertEqual(result['v0'], 7)
        self.assertEqual(result['ra'], RETURN_ADDRESS)
        self.assertEqual(result.steps, 2)

    def test_taken_and_untaken_branches_both_run_their_delay_slots(self):
        result, _ = run(
            BEQ('zero', 'zero', 2),      # taken, to the JR below
            ADDIU('v0', 'zero', 1),      # delay slot runs
            ADDIU('v0', 'zero', 99),     # skipped
            BNE('zero', 'zero', 5),      # not taken
            ADDIU('v1', 'zero', 2),      # delay slot still runs
            JR('ra'), NOP())
        self.assertEqual((result['v0'], result['v1']), (1, 2))

    def test_signed_branches(self):
        cases = ((BLTZ, -1, True), (BLTZ, 0, False), (BGEZ, 0, True), (BGEZ, -5, False),
                 (BLEZ, 0, True), (BLEZ, 1, False), (BGTZ, 1, True), (BGTZ, -1, False))
        for branch, value, taken in cases:
            with self.subTest(branch=branch.__name__, value=value):
                result, _ = run(branch('a0', 2), NOP(), ADDIU('v0', 'zero', 1), JR('ra'), NOP(),
                                arguments={'a0': value})
                self.assertEqual(result['v0'], 0 if taken else 1)

    def test_j_jumps_within_its_256_mib_segment(self):
        result, _ = run(
            J(BASE + 0x10),               # 0x00
            ADDIU('v0', 'zero', 1),       # 0x04 delay slot runs
            ADDIU('v0', 'zero', 99),      # 0x08 skipped
            NOP(),                        # 0x0c
            JR('ra'),                     # 0x10
            ADDIU('v1', 'zero', 2))       # 0x14
        self.assertEqual((result['v0'], result['v1']), (1, 2))

    def test_jal_and_jalr(self):
        words = [
            ADDU('t1', 'ra', 'zero'),     # 0x00 keep the return address
            JAL(BASE + 0x14), NOP(),      # 0x04
            JR('t1'), NOP(),              # 0x0c
            ADDU('v1', 'ra', 'zero'),     # 0x14 link value
            LUI('t2', BASE >> 16),        # 0x18
            ORI('t2', 't2', 0x2C),        # 0x1c
            JALR('t3', 't2'), NOP(),      # 0x20
            JR('t3'), NOP(),              # 0x28 never reached directly
            ADDU('v0', 't3', 'zero'),     # 0x2c link from jalr
            JR('v1'), NOP(),              # 0x30 back to 0x0c
        ]
        result, _ = run(*words)
        self.assertEqual(result['v1'], BASE + 0x0C)
        self.assertEqual(result['v0'], BASE + 0x28)

    def test_branch_in_a_delay_slot_is_an_error(self):
        with self.assertRaisesRegex(MipsError, 'in a delay slot'):
            run(BEQ('zero', 'zero', 2), BEQ('zero', 'zero', 2), NOP(), JR('ra'), NOP())

    def test_step_limit(self):
        with self.assertRaisesRegex(MipsError, 'did not return within 10'):
            run(BEQ('zero', 'zero', -1), NOP(), max_steps=10)


class ArithmeticTests(unittest.TestCase):
    def check(self, words, arguments, register, expected):
        result, _ = run(*words, JR('ra'), NOP(), arguments=arguments)
        self.assertEqual(result[register], expected)

    def test_register_operations(self):
        cases = [
            (SLL('v0', 'a0', 4), {'a0': 0x0F00000F}, 0xF00000F0),
            (SRL('v0', 'a0', 4), {'a0': 0x80000000}, 0x08000000),
            (SRA('v0', 'a0', 4), {'a0': 0x80000000}, 0xF8000000),
            (SLLV('v0', 'a0', 'a1'), {'a0': 1, 'a1': 33}, 2),
            (SRLV('v0', 'a0', 'a1'), {'a0': 0x100, 'a1': 36}, 0x10),
            (SRAV('v0', 'a0', 'a1'), {'a0': 0xFFFFFF00, 'a1': 4}, 0xFFFFFFF0),
            (ADDU('v0', 'a0', 'a1'), {'a0': 0xFFFFFFFF, 'a1': 2}, 1),
            (SUBU('v0', 'a0', 'a1'), {'a0': 0, 'a1': 1}, 0xFFFFFFFF),
            (ADD('v0', 'a0', 'a1'), {'a0': 0x80000000, 'a1': 5}, 0x80000005),
            (SUB('v0', 'a0', 'a1'), {'a0': 3, 'a1': 5}, 0xFFFFFFFE),
            (AND('v0', 'a0', 'a1'), {'a0': 0xF0F0, 'a1': 0xFF00}, 0xF000),
            (OR('v0', 'a0', 'a1'), {'a0': 0xF0F0, 'a1': 0xFF00}, 0xFFF0),
            (XOR('v0', 'a0', 'a1'), {'a0': 0xF0F0, 'a1': 0xFF00}, 0x0FF0),
            (NOR('v0', 'a0', 'a1'), {'a0': 0xF0F0, 'a1': 0xFF00}, 0xFFFF000F),
            (SLT('v0', 'a0', 'a1'), {'a0': 0xFFFFFFFF, 'a1': 0}, 1),
            (SLTU('v0', 'a0', 'a1'), {'a0': 0xFFFFFFFF, 'a1': 0}, 0),
        ]
        for word, arguments, expected in cases:
            with self.subTest(word=f'0x{word:08x}'):
                self.check([word], arguments, 'v0', expected)

    def test_immediate_operations(self):
        cases = [
            (ADDIU('v0', 'a0', -1), {'a0': 0}, 0xFFFFFFFF),
            (ADDI('v0', 'a0', -1), {'a0': 0x80000001}, 0x80000000),
            (SLTI('v0', 'a0', -1), {'a0': 0xFFFFFFFE}, 1),
            (SLTIU('v0', 'a0', -1), {'a0': 0xFFFFFFFE}, 1),   # immediate sign-extends
            (SLTIU('v0', 'a0', 1), {'a0': 0xFFFFFFFE}, 0),
            (ANDI('v0', 'a0', 0xFFFF), {'a0': 0xFFFFFFFF}, 0xFFFF),   # zero-extends
            (ORI('v0', 'a0', 0x8000), {'a0': 0x10000}, 0x18000),
            (XORI('v0', 'a0', 0xFFFF), {'a0': 0x0000FF00}, 0x000000FF),
            (LUI('v0', 0x8001), {}, 0x80010000),
        ]
        for word, arguments, expected in cases:
            with self.subTest(word=f'0x{word:08x}'):
                self.check([word], arguments, 'v0', expected)

    def test_trapping_arithmetic_overflow_is_a_trap(self):
        cases = [(ADD('v0', 'a0', 'a1'), {'a0': 0x7FFFFFFF, 'a1': 1}, 'add'),
                 (ADDI('v0', 'a0', 1), {'a0': 0x7FFFFFFF}, 'addi'),
                 (SUB('v0', 'a0', 'a1'), {'a0': 0x80000000, 'a1': 1}, 'sub')]
        for word, arguments, mnemonic in cases:
            with self.subTest(word=f'0x{word:08x}'):
                with self.assertRaisesRegex(MipsTrap, 'overflows') as caught:
                    run(word, JR('ra'), NOP(), arguments=arguments)
                self.assertEqual((caught.exception.reason, caught.exception.mnemonic,
                                  caught.exception.pc), ('overflow', mnemonic, BASE))

    def test_zero_register_stays_zero(self):
        result, _ = run(ADDIU('zero', 'zero', 5), ADDU('v0', 'zero', 'zero'), JR('ra'), NOP())
        self.assertEqual((result['zero'], result['v0']), (0, 0))
        with self.assertRaisesRegex(MipsError, 'cannot be set'):
            run(JR('ra'), NOP(), arguments={'zero': 1})


class MemoryTests(unittest.TestCase):
    def test_loads_extend_by_width_and_sign(self):
        data = bytes([0x80, 0xFF, 0x01, 0x80])
        words = [LB('t0', 0, 'a0'), LBU('t1', 0, 'a0'), LH('t2', 0, 'a0'),
                 LHU('t3', 2, 'a0'), LW('t4', 0, 'a0'), NOP(), JR('ra'), NOP()]
        result, _ = run(*words, arguments={'a0': DATA}, data=data)
        self.assertEqual(result['t0'], 0xFFFFFF80)
        self.assertEqual(result['t1'], 0x80)
        self.assertEqual(result['t2'], 0xFFFFFF80)
        self.assertEqual(result['t3'], 0x8001)
        self.assertEqual(result['t4'], 0x8001FF80)

    def test_stores_and_write_extent(self):
        words = [ADDIU('t0', 'zero', -1), SB('t0', 1, 'a0'), SH('t0', 2, 'a0'),
                 SW('t0', 4, 'a0'), JR('ra'), NOP()]
        _, memory = run(*words, arguments={'a0': DATA}, data=bytes(12))
        region = memory.region('data')
        self.assertEqual(bytes(region.data), b'\0\xff\xff\xff\xff\xff\xff\xff\0\0\0\0')
        self.assertEqual(region.write_end, 8)

    def test_load_result_appears_after_the_next_instruction(self):
        words = [LW('t0', 0, 'a0'), ADDU('v1', 'zero', 'zero'), ADDU('v0', 't0', 'zero'),
                 JR('ra'), NOP()]
        result, _ = run(*words, arguments={'a0': DATA}, data=struct.pack('<I', 42))
        self.assertEqual(result['v0'], 42)

    def test_load_delay_hazards_are_errors(self):
        hazards = {'reads': ADDU('v0', 't0', 'zero'), 'writes': ADDIU('t0', 'zero', 1),
                   'loads again': LW('t0', 0, 'a0')}
        for name, slot in hazards.items():
            with self.subTest(hazard=name):
                with self.assertRaisesRegex(MipsError, 'delay slot of its load'):
                    run(LW('t0', 0, 'a0'), slot, JR('ra'), NOP(),
                        arguments={'a0': DATA, 't0': 9}, data=bytes(4))

    def test_load_in_the_return_delay_slot_still_lands(self):
        result, _ = run(JR('ra'), LW('v0', 0, 'a0'), arguments={'a0': DATA},
                        data=struct.pack('<I', 5))
        self.assertEqual(result['v0'], 5)

    def test_access_errors(self):
        cases = {
            'Unmapped 4-byte read': (LW('t0', 0x100, 'a0'), True),
            'Misaligned 4-byte read': (LW('t0', 2, 'a0'), True),
            'Misaligned 2-byte write': (SH('t0', 1, 'a0'), True),
            'read-only region data': (SB('t0', 0, 'a0'), False),
        }
        for message, (word, writable) in cases.items():
            with self.subTest(message=message):
                with self.assertRaisesRegex(MipsError, message):
                    run(word, NOP(), JR('ra'), NOP(), arguments={'a0': DATA}, data=bytes(8),
                        writable=writable)

    def test_code_cannot_run_from_data_or_unmapped_memory(self):
        with self.assertRaisesRegex(MipsError, 'non-executable region data'):
            run(JR('a0'), NOP(), arguments={'a0': DATA}, data=bytes(8))
        with self.assertRaisesRegex(MipsError, 'Unmapped 4-byte instruction fetch'):
            run(JR('a0'), NOP(), arguments={'a0': 0x80200000})

    def test_text_is_readable_but_not_writable(self):
        result, _ = run(LW('v0', 8, 'a0'), NOP(), JR('ra'), NOP(), arguments={'a0': BASE})
        self.assertEqual(result['v0'], JR('ra'))
        with self.assertRaisesRegex(MipsError, 'read-only region text'):
            run(SW('zero', 0, 'a0'), JR('ra'), NOP(), arguments={'a0': BASE})

    def test_region_mapping_rules(self):
        memory = Memory()
        memory.map('a', 0x1000, bytes(16))
        for kwargs, message in (({'base': 0x1008, 'data': bytes(16)}, 'overlaps a'),
                                ({'base': 0x2002, 'data': bytes(4)}, 'not word aligned'),
                                ({'base': 0xFFFFFF00, 'data': bytes(0x100)}, 'reserved return'),
                                ({'base': 0x3000, 'data': bytes(4), 'writable': True,
                                  'executable': True}, 'both writable and executable')):
            with self.subTest(message=message):
                with self.assertRaisesRegex(MipsError, message):
                    memory.map('b', **kwargs)


class MultiplyDivideTests(unittest.TestCase):
    def hilo(self, *words, arguments):
        result, _ = run(*words, NOP(), NOP(), MFHI('v0'), MFLO('v1'), JR('ra'), NOP(),
                        arguments=arguments)
        return result['v0'], result['v1']

    def test_multiply(self):
        cases = [(MULT, 0xFFFFFFFF, 2, (0xFFFFFFFF, 0xFFFFFFFE)),     # -1 * 2
                 (MULTU, 0xFFFFFFFF, 2, (1, 0xFFFFFFFE)),
                 (MULT, 0x80000000, 0x80000000, (0x40000000, 0)),
                 (MULTU, 0x12345678, 0x9ABCDEF0, divmod(0x12345678 * 0x9ABCDEF0, 1 << 32))]
        for instruction, a, b, expected in cases:
            with self.subTest(instruction=instruction.__name__, a=a, b=b):
                self.assertEqual(self.hilo(instruction('a0', 'a1'),
                                           arguments={'a0': a, 'a1': b}), expected)

    def test_divide_rounds_toward_zero(self):
        cases = [(DIV, 7, 2, (1, 3)), (DIV, -7 & 0xFFFFFFFF, 2, (0xFFFFFFFF, 0xFFFFFFFD)),
                 (DIV, 7, -2 & 0xFFFFFFFF, (1, 0xFFFFFFFD)), (DIVU, 0xFFFFFFFF, 0x10, (15, 0x0FFFFFFF))]
        for instruction, a, b, expected in cases:
            with self.subTest(instruction=instruction.__name__, a=a, b=b):
                self.assertEqual(self.hilo(instruction('a0', 'a1'),
                                           arguments={'a0': a, 'a1': b}), expected)

    def test_documented_division_edge_cases(self):
        # Documentation-derived R3000 results, not yet measured by this project.
        cases = [('positive by zero', DIV, 5, 0, (5, 0xFFFFFFFF)),
                 ('negative by zero', DIV, 0xFFFFFFFB, 0, (0xFFFFFFFB, 1)),
                 ('divu by zero', DIVU, 0xFFFFFFFB, 0, (0xFFFFFFFB, 0xFFFFFFFF)),
                 ('INT_MIN / -1', DIV, 0x80000000, 0xFFFFFFFF, (0, 0x80000000))]
        for name, instruction, a, b, expected in cases:
            with self.subTest(case=name):
                self.assertEqual(self.hilo(instruction('a0', 'a1'),
                                           arguments={'a0': a, 'a1': b}), expected)
                self.assertEqual(divide(a, b, instruction is DIV), expected)

    def test_move_to_hi_and_lo_and_initial_state(self):
        self.assertEqual(self.hilo(MTHI('a0'), MTLO('a1'), arguments={'a0': 3, 'a1': 4}),
                         (3, 4))
        result, _ = run(JR('ra'), NOP(), hi=7, lo=8)
        self.assertEqual((result.hi, result.lo), (7, 8))

    def test_writing_hi_lo_soon_after_reading_them_is_refused(self):
        for gap in (0, 1):
            with self.subTest(gap=gap):
                with self.assertRaisesRegex(MipsError, 'within two instructions'):
                    run(MFLO('v0'), *[NOP()] * gap, MULT('a0', 'a1'), JR('ra'), NOP())
        result, _ = run(MFLO('v0'), NOP(), NOP(), MULT('a0', 'a1'), JR('ra'), NOP())
        self.assertEqual(result.steps, 6)


class UnalignedAccessTests(unittest.TestCase):
    DATA_BYTES = bytes(range(0x10, 0x20))

    def test_lwl_lwr_pair_merges_from_the_in_flight_value(self):
        # The register's stale bytes differ from memory, so a version that merged
        # lwr into the old register value instead of lwl's result would fail.
        for offset in range(4):
            with self.subTest(offset=offset):
                result, _ = run(LWL('t0', offset + 3, 'a0'), LWR('t0', offset, 'a0'), NOP(),
                                ADDU('v0', 't0', 'zero'), JR('ra'), NOP(),
                                arguments={'a0': DATA, 't0': 0xAAAAAAAA}, data=self.DATA_BYTES)
                expected = int.from_bytes(self.DATA_BYTES[offset:offset + 4], 'little')
                self.assertEqual(result['v0'], expected)

    def test_lone_lwl_and_lwr_keep_the_other_bytes(self):
        cases = [(LWL('t0', 1, 'a0'), 0x1110AAAA), (LWL('t0', 3, 'a0'), 0x13121110),
                 (LWR('t0', 1, 'a0'), 0xAA131211), (LWR('t0', 3, 'a0'), 0xAAAAAA13)]
        for word, expected in cases:
            with self.subTest(word=f'0x{word:08x}'):
                result, _ = run(word, NOP(), JR('ra'), NOP(),
                                arguments={'a0': DATA, 't0': 0xAAAAAAAA}, data=self.DATA_BYTES)
                self.assertEqual(result['t0'], expected)

    def test_unaligned_access_touches_only_its_bytes(self):
        # lwl at the first byte of a region reads one byte; lwr at the last reads one.
        result, _ = run(LWL('t0', 0, 'a0'), NOP(), LWR('t1', 15, 'a0'), NOP(), JR('ra'), NOP(),
                        arguments={'a0': DATA}, data=self.DATA_BYTES)
        self.assertEqual((result['t0'], result['t1']), (0x10000000, 0x1F))
        with self.assertRaisesRegex(MipsError, 'Unmapped 4-byte unaligned read'):
            run(LWL('t0', 19, 'a0'), NOP(), JR('ra'), NOP(), arguments={'a0': DATA},
                data=self.DATA_BYTES)

    def test_swl_swr_write_exactly_their_bytes(self):
        cases = [(SWL('a1', 1, 'a0'), b'\x22\x11\0\0', 2),
                 (SWR('a1', 1, 'a0'), b'\0\x44\x33\x22', 4),
                 (SWL('a1', 3, 'a0'), b'\x44\x33\x22\x11', 4),
                 (SWR('a1', 3, 'a0'), b'\0\0\0\x44', 4)]
        for word, expected, write_end in cases:
            with self.subTest(word=f'0x{word:08x}'):
                _, memory = run(word, JR('ra'), NOP(), arguments={'a0': DATA, 'a1': 0x11223344},
                                data=bytes(4))
                region = memory.region('data')
                self.assertEqual(bytes(region.data), expected)
                self.assertEqual(region.write_end, write_end)

    def test_pairs_the_hardware_does_not_merge_are_refused(self):
        cases = {'lwr first': (LWR('t0', 0, 'a0'), LWL('t0', 3, 'a0')),
                 'different base': (LWL('t0', 3, 'a0'), LWR('t0', 0, 'a1')),
                 'base is rt': (LWL('a0', 3, 'a0'), LWR('a0', 0, 'a0')),
                 'use after lwr': (LWR('t0', 0, 'a0'), ADDU('v0', 't0', 'zero'))}
        for name, (first, second) in cases.items():
            with self.subTest(case=name):
                with self.assertRaisesRegex(MipsError, 'delay slot of its load'):
                    run(first, second, NOP(), JR('ra'), NOP(),
                        arguments={'a0': DATA, 'a1': DATA}, data=self.DATA_BYTES)


class TrapTests(unittest.TestCase):
    def test_break_and_syscall_raise_traps_with_their_codes(self):
        cases = [(BREAK(7), 'break', 7, 0x1C00), (BREAK(6), 'break', 6, 0x1800),
                 (BREAK(0, 1), 'break', 0, 1), (SYSCALL(0x12), 'syscall', 0x12, 0x12)]
        for word, mnemonic, code, raw_code in cases:
            with self.subTest(word=f'0x{word:08x}'):
                with self.assertRaises(MipsTrap) as caught:
                    run(NOP(), word, JR('ra'), NOP())
                trap = caught.exception
                self.assertEqual((trap.mnemonic, trap.reason, trap.code, trap.raw_code, trap.pc),
                                 (mnemonic, mnemonic, code, raw_code, BASE + 4))
        self.assertEqual(BREAK(7), 0x0007000D)

    def test_a_guarded_division_skips_its_break(self):
        # GCC's divide-by-zero guard: div; bnez divisor, +2; nop; break 7.
        words = [DIV('a0', 'a1'), BNE('a1', 'zero', 2), NOP(), BREAK(7), MFLO('v0'), JR('ra'),
                 NOP()]
        result, _ = run(*words, arguments={'a0': 9, 'a1': 3})
        self.assertEqual(result['v0'], 3)
        with self.assertRaises(MipsTrap):
            run(*words, arguments={'a0': 9, 'a1': 0})


class Cop2Tests(unittest.TestCase):
    def test_moves_and_the_mfc2_load_delay(self):
        words = [MTC2('a0', IR1), CTC2('a1', 26), MFC2('t0', IR1), CFC2('t1', 26), NOP(),
                 ADDU('v0', 't0', 'zero'), ADDU('v1', 't1', 'zero'), JR('ra'), NOP()]
        result, _ = run(*words, arguments={'a0': 0xFFFF8000, 'a1': 0x8000})
        self.assertEqual(result['v0'], 0xFFFF8000)     # IR1 sign-extends
        self.assertEqual(result['v1'], 0xFFFF8000)     # H reads back sign-extended
        with self.assertRaisesRegex(MipsError, 'delay slot of its load'):
            run(MFC2('t0', IR1), ADDU('v0', 't0', 'zero'), JR('ra'), NOP())

    def test_lwc2_and_swc2_go_through_memory(self):
        words = [LWC2(IR2, 0, 'a0'), NOP(), NOP(), SWC2(IR2, 4, 'a0'), JR('ra'), NOP()]
        _, memory = run(*words, arguments={'a0': DATA}, data=struct.pack('<II', 0x0000FFFE, 0))
        self.assertEqual(struct.unpack_from('<I', memory.region('data').data, 4)[0], 0xFFFFFFFE)

    def test_gte_state_goes_in_and_comes_out(self):
        state = GteState.from_registers(data={IR1: 3, IR2: 4, IR3: 0xFFFB})
        result, _ = run(GTE('sqr', sf=0), JR('ra'), NOP(), gte=state)
        self.assertEqual([result.gte.read_data(i) for i in (IR1, IR2, IR3)], [9, 16, 25])
        result, _ = run(GTE('nclip'), JR('ra'), NOP(),
                        gte=GteState.from_registers(data={12: 0, 13: 4, 14: 4 << 16}))
        self.assertEqual(result.gte.read_data(MAC0), 16)
        self.assertEqual(result.gte.read_control(FLAG), 0)

    def test_psyq_macro_numbers_in_command_words_are_ignored(self):
        state = GteState.from_registers(data={IR1: 2})
        plain, _ = run(GTE('sqr', sf=0), JR('ra'), NOP(), gte=state)
        marked, _ = run(GTE('sqr', sf=0, macro=0x1F), JR('ra'), NOP(), gte=state)
        self.assertEqual(plain.gte, marked.gte)

    def test_cop2_timing_cases_are_refused(self):
        cases = {'command after write': (MTC2('a0', IR1), NOP(), GTE('sqr')),
                 'write after command': (GTE('sqr'), MTC2('a0', IR1), NOP()),
                 'cop2 in a delay slot': (BEQ('zero', 'zero', 1), MFC2('t0', IR1), NOP())}
        for name, words in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(MipsError):
                    run(*words, NOP(), JR('ra'), NOP())
        result, _ = run(MTC2('a0', IR1), NOP(), NOP(), GTE('sqr'), MFC2('v0', IR1), JR('ra'),
                        NOP(), arguments={'a0': 3})
        self.assertEqual(result['v0'], 0)              # sf=1: 9 >> 12


class ResultTests(unittest.TestCase):
    def test_two_argument_construction_and_name_lookup_still_work(self):
        result = RunResult(tuple(range(32)), 5)
        self.assertEqual((result['v0'], result.steps, result.hi, result.lo, result.gte),
                         (2, 5, 0, 0, None))
        with self.assertRaisesRegex(MipsError, 'Unknown register'):
            result['hi']


class DecodeTests(unittest.TestCase):
    def test_supported_mnemonics_are_exactly_what_the_decoder_accepts(self):
        words = [SLL('v0', 'a0', 1), SRL('v0', 'a0', 1), SRA('v0', 'a0', 1),
                 SLLV('v0', 'a0', 'a1'), SRLV('v0', 'a0', 'a1'), SRAV('v0', 'a0', 'a1'),
                 JR('ra'), JALR('t3', 't2'), ADD('v0', 'a0', 'a1'), ADDU('v0', 'a0', 'a1'),
                 SUB('v0', 'a0', 'a1'), SUBU('v0', 'a0', 'a1'), AND('v0', 'a0', 'a1'),
                 OR('v0', 'a0', 'a1'), XOR('v0', 'a0', 'a1'), NOR('v0', 'a0', 'a1'),
                 SLT('v0', 'a0', 'a1'), SLTU('v0', 'a0', 'a1'), BLTZ('a0', 1), BGEZ('a0', 1),
                 J(BASE), JAL(BASE), BEQ('a0', 'a1', 1), BNE('a0', 'a1', 1), BLEZ('a0', 1),
                 BGTZ('a0', 1), ADDI('v0', 'a0', 1), ADDIU('v0', 'a0', 1), SLTI('v0', 'a0', 1),
                 SLTIU('v0', 'a0', 1), ANDI('v0', 'a0', 1), ORI('v0', 'a0', 1),
                 XORI('v0', 'a0', 1), LUI('v0', 1), LB('v0', 0, 'a0'), LH('v0', 0, 'a0'),
                 LW('v0', 0, 'a0'), LBU('v0', 0, 'a0'), LHU('v0', 0, 'a0'), SB('v0', 0, 'a0'),
                 SH('v0', 0, 'a0'), SW('v0', 0, 'a0'),
                 MULT('a0', 'a1'), MULTU('a0', 'a1'), DIV('a0', 'a1'), DIVU('a0', 'a1'),
                 MFHI('v0'), MFLO('v0'), MTHI('a0'), MTLO('a0'), LWL('v0', 3, 'a0'),
                 LWR('v0', 0, 'a0'), SWL('v0', 3, 'a0'), SWR('v0', 0, 'a0'),
                 MFC2('v0', 1), CFC2('v0', 1), MTC2('v0', 1), CTC2('v0', 1),
                 LWC2(1, 0, 'a0'), SWC2(1, 0, 'a0'),
                 BREAK(7), SYSCALL(0)]
        words += [GTE(name) for name in ('rtps', 'rtpt', 'nclip', 'mvmva', 'dpcs', 'dpct',
                                         'intpl', 'sqr', 'gpf', 'gpl', 'avsz3', 'avsz4', 'ncs',
                                         'nct', 'nccs')]
        self.assertEqual({_decode(word, BASE).mnemonic for word in words},
                         SUPPORTED_MNEMONICS | TRAP_MNEMONICS)
        self.assertFalse(SUPPORTED_MNEMONICS & TRAP_MNEMONICS)

    def test_instructions_outside_the_subset_are_errors(self):
        cases = {'mfc0': (0x10 << 26, 'Unsupported instruction'),
                 'bc2f': ((0x12 << 26) | (8 << 21), 'Unsupported instruction'),
                 'mfc2 with low bits set': (MFC2('v0', 1) | 1, 'Unsupported instruction'),
                 'GTE op': ((0x12 << 26) | (1 << 25) | 0x0C, 'not modelled'),
                 'MVMVA cv=2': (GTE('mvmva', cv=2), 'not modelled'),
                 'MVMVA mx=3': (GTE('mvmva', mx=3), 'not modelled'),
                 'shift with rs set': (SLL('v0', 'a0', 1) | (1 << 21), 'Unsupported instruction'),
                 'lui with rs set': (LUI('v0', 1) | (1 << 21), 'Unsupported instruction')}
        for name, (word, message) in cases.items():
            with self.subTest(case=name):
                with self.assertRaisesRegex(MipsError, message):
                    run(word, JR('ra'), NOP())

    def test_unknown_register_name(self):
        with self.assertRaisesRegex(MipsError, 'Unknown register'):
            register_index('$x9')
        self.assertEqual(register_index('$sp'), 29)


if __name__ == '__main__':
    unittest.main()
