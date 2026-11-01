"""Synthetic tests for the R3000A integer interpreter; every program is hand-written here."""
import struct
import unittest

from swallow_tools.mips import RETURN_ADDRESS, Memory, MipsError, call, register_index

BASE = 0x80010000
DATA = 0x80100000


def reg(name):
    return register_index(name)


def r_type(funct, rs='zero', rt='zero', rd='zero', sa=0):
    return (reg(rs) << 21) | (reg(rt) << 16) | (reg(rd) << 11) | (sa << 6) | funct


def i_type(op, rs, rt, imm):
    return (op << 26) | (reg(rs) << 21) | (reg(rt) << 16) | (imm & 0xFFFF)


# A small assembler for the tests: each helper returns one instruction word.
def NOP(): return 0
def SLL(rd, rt, sa): return r_type(0x00, rt=rt, rd=rd, sa=sa)
def SRL(rd, rt, sa): return r_type(0x02, rt=rt, rd=rd, sa=sa)
def SRA(rd, rt, sa): return r_type(0x03, rt=rt, rd=rd, sa=sa)
def SLLV(rd, rt, rs): return r_type(0x04, rs, rt, rd)
def SRLV(rd, rt, rs): return r_type(0x06, rs, rt, rd)
def SRAV(rd, rt, rs): return r_type(0x07, rs, rt, rd)
def JR(rs): return r_type(0x08, rs)
def JALR(rd, rs): return r_type(0x09, rs, rd=rd)
def ADD(rd, rs, rt): return r_type(0x20, rs, rt, rd)
def ADDU(rd, rs, rt): return r_type(0x21, rs, rt, rd)
def SUB(rd, rs, rt): return r_type(0x22, rs, rt, rd)
def SUBU(rd, rs, rt): return r_type(0x23, rs, rt, rd)
def AND(rd, rs, rt): return r_type(0x24, rs, rt, rd)
def OR(rd, rs, rt): return r_type(0x25, rs, rt, rd)
def XOR(rd, rs, rt): return r_type(0x26, rs, rt, rd)
def NOR(rd, rs, rt): return r_type(0x27, rs, rt, rd)
def SLT(rd, rs, rt): return r_type(0x2A, rs, rt, rd)
def SLTU(rd, rs, rt): return r_type(0x2B, rs, rt, rd)
def BLTZ(rs, off): return i_type(1, rs, 'zero', off)
def BGEZ(rs, off): return i_type(1, rs, 'at', off)
def J(target): return (2 << 26) | ((target >> 2) & 0x03FFFFFF)
def JAL(target): return (3 << 26) | ((target >> 2) & 0x03FFFFFF)
def BEQ(rs, rt, off): return i_type(4, rs, rt, off)
def BNE(rs, rt, off): return i_type(5, rs, rt, off)
def BLEZ(rs, off): return i_type(6, rs, 'zero', off)
def BGTZ(rs, off): return i_type(7, rs, 'zero', off)
def ADDI(rt, rs, imm): return i_type(0x08, rs, rt, imm)
def ADDIU(rt, rs, imm): return i_type(0x09, rs, rt, imm)
def SLTI(rt, rs, imm): return i_type(0x0A, rs, rt, imm)
def SLTIU(rt, rs, imm): return i_type(0x0B, rs, rt, imm)
def ANDI(rt, rs, imm): return i_type(0x0C, rs, rt, imm)
def ORI(rt, rs, imm): return i_type(0x0D, rs, rt, imm)
def XORI(rt, rs, imm): return i_type(0x0E, rs, rt, imm)
def LUI(rt, imm): return i_type(0x0F, 'zero', rt, imm)
def LB(rt, off, rs): return i_type(0x20, rs, rt, off)
def LH(rt, off, rs): return i_type(0x21, rs, rt, off)
def LW(rt, off, rs): return i_type(0x23, rs, rt, off)
def LBU(rt, off, rs): return i_type(0x24, rs, rt, off)
def LHU(rt, off, rs): return i_type(0x25, rs, rt, off)
def SB(rt, off, rs): return i_type(0x28, rs, rt, off)
def SH(rt, off, rs): return i_type(0x29, rs, rt, off)
def SW(rt, off, rs): return i_type(0x2B, rs, rt, off)


def program(*words):
    return b''.join(struct.pack('<I', word) for word in words)


def run(*words, arguments=None, data=None, writable=True, max_steps=1000):
    memory = Memory()
    memory.map('text', BASE, program(*words), executable=True)
    if data is not None:
        memory.map('data', DATA, data, writable=writable)
    return call(memory, BASE, arguments, max_steps=max_steps), memory


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

    def test_trapping_arithmetic_overflow_is_an_error(self):
        cases = [(ADD('v0', 'a0', 'a1'), {'a0': 0x7FFFFFFF, 'a1': 1}),
                 (ADDI('v0', 'a0', 1), {'a0': 0x7FFFFFFF}),
                 (SUB('v0', 'a0', 'a1'), {'a0': 0x80000000, 'a1': 1})]
        for word, arguments in cases:
            with self.subTest(word=f'0x{word:08x}'):
                with self.assertRaisesRegex(MipsError, 'overflows'):
                    run(word, JR('ra'), NOP(), arguments=arguments)

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


class DecodeTests(unittest.TestCase):
    def test_instructions_outside_the_subset_are_errors(self):
        for word in (0x0000000C,          # syscall
                     0x00850018,          # mult
                     0x88850000,          # lwl
                     0x48020000,          # cop2 mfc2
                     SLL('v0', 'a0', 1) | (1 << 21),   # shift with rs set
                     LUI('v0', 1) | (1 << 21)):        # lui with rs set
            with self.subTest(word=f'0x{word:08x}'):
                with self.assertRaisesRegex(MipsError, 'Unsupported instruction'):
                    run(word, JR('ra'), NOP())

    def test_unknown_register_name(self):
        with self.assertRaisesRegex(MipsError, 'Unknown register'):
            register_index('$x9')
        self.assertEqual(register_index('$sp'), 29)


if __name__ == '__main__':
    unittest.main()
