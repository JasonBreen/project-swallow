"""A tiny MIPS I assembler for synthetic programs; one function per instruction.

Offline tooling for tests and probe programs. Each helper returns one
32-bit instruction word, and ``program`` packs words little-endian. It is not
a general assembler: there are no labels, relocations, or pseudo-instructions
beyond ``NOP``. Register names are those of swallow_tools.mips.
"""
from __future__ import annotations

import struct

from .gte import COMMANDS
from .mips import register_index

_COMMAND_NUMBERS = {name: number for number, name in COMMANDS.items()}


def reg(name: str) -> int:
    return register_index(name)


def r_type(funct: int, rs: str = 'zero', rt: str = 'zero', rd: str = 'zero', sa: int = 0) -> int:
    return (reg(rs) << 21) | (reg(rt) << 16) | (reg(rd) << 11) | (sa << 6) | funct


def i_type(op: int, rs: str, rt: str, imm: int) -> int:
    return (op << 26) | (reg(rs) << 21) | (reg(rt) << 16) | (imm & 0xFFFF)


def program(*words: int) -> bytes:
    return b''.join(struct.pack('<I', word) for word in words)


def NOP(): return 0
def SLL(rd, rt, sa): return r_type(0x00, rt=rt, rd=rd, sa=sa)
def SRL(rd, rt, sa): return r_type(0x02, rt=rt, rd=rd, sa=sa)
def SRA(rd, rt, sa): return r_type(0x03, rt=rt, rd=rd, sa=sa)
def SLLV(rd, rt, rs): return r_type(0x04, rs, rt, rd)
def SRLV(rd, rt, rs): return r_type(0x06, rs, rt, rd)
def SRAV(rd, rt, rs): return r_type(0x07, rs, rt, rd)
def JR(rs): return r_type(0x08, rs)
def JALR(rd, rs): return r_type(0x09, rs, rd=rd)
def SYSCALL(code=0): return ((code & 0xFFFFF) << 6) | 0x0C
def BREAK(code=0, low=0): return ((code & 0x3FF) << 16) | ((low & 0x3FF) << 6) | 0x0D
def MFHI(rd): return r_type(0x10, rd=rd)
def MTHI(rs): return r_type(0x11, rs)
def MFLO(rd): return r_type(0x12, rd=rd)
def MTLO(rs): return r_type(0x13, rs)
def MULT(rs, rt): return r_type(0x18, rs, rt)
def MULTU(rs, rt): return r_type(0x19, rs, rt)
def DIV(rs, rt): return r_type(0x1A, rs, rt)
def DIVU(rs, rt): return r_type(0x1B, rs, rt)
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
def LWL(rt, off, rs): return i_type(0x22, rs, rt, off)
def LW(rt, off, rs): return i_type(0x23, rs, rt, off)
def LBU(rt, off, rs): return i_type(0x24, rs, rt, off)
def LHU(rt, off, rs): return i_type(0x25, rs, rt, off)
def LWR(rt, off, rs): return i_type(0x26, rs, rt, off)
def SB(rt, off, rs): return i_type(0x28, rs, rt, off)
def SH(rt, off, rs): return i_type(0x29, rs, rt, off)
def SWL(rt, off, rs): return i_type(0x2A, rs, rt, off)
def SW(rt, off, rs): return i_type(0x2B, rs, rt, off)
def SWR(rt, off, rs): return i_type(0x2E, rs, rt, off)


def _cop2_move(rs_field: int, rt: str, gte_register: int) -> int:
    return (0x12 << 26) | (rs_field << 21) | (reg(rt) << 16) | ((gte_register & 31) << 11)


def MFC2(rt, gte_register): return _cop2_move(0, rt, gte_register)
def CFC2(rt, gte_register): return _cop2_move(2, rt, gte_register)
def MTC2(rt, gte_register): return _cop2_move(4, rt, gte_register)
def CTC2(rt, gte_register): return _cop2_move(6, rt, gte_register)
def LWC2(gte_register, off, rs): return (0x32 << 26) | (reg(rs) << 21) | (gte_register << 16) | (off & 0xFFFF)
def SWC2(gte_register, off, rs): return (0x3A << 26) | (reg(rs) << 21) | (gte_register << 16) | (off & 0xFFFF)


def GTE(name: str, sf: int = 1, lm: int = 0, mx: int = 0, v: int = 0, cv: int = 0,
        macro: int = 0) -> int:
    """A GTE command word. ``macro`` fills bits 20-24, where PsyQ puts its macro number."""
    return ((0x12 << 26) | (1 << 25) | ((macro & 31) << 20) | (sf << 19) | (mx << 17)
            | (v << 15) | (cv << 13) | (lm << 10) | _COMMAND_NUMBERS[name])
