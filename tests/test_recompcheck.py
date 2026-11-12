"""Synthetic tests for swallow_tools.recompcheck; no game code, disc, or compiler is used.

A stand-in for the native module runs the interpreter itself, optionally
corrupting its result, so the comparison logic is tested on its own.
"""
import random
import unittest

from swallow_tools.gte import GteState
from swallow_tools.mips import Memory, MipsError, call
from swallow_tools.mipsasm import ADDIU, JR, LW, NOP, SW, program
from swallow_tools.native import NativeResult
from swallow_tools.recompcheck import (
    HEAP_BASE, HEAP_END, _argument, _seed_heap, _stop_key, compare_function)

TEXT = 0x80010000
RAM = 0x80100000


class FakeModule:
    """Runs the interpreter in place of a native module; ``corrupt`` alters its result."""

    def __init__(self, corrupt=None, trap=None):
        self.corrupt = corrupt
        self.trap = trap

    def run(self, entry, regions, arguments, *, budget, gte=None):
        if self.trap:
            return NativeResult(False, self.trap, (0,) * 32, 0, 0, {}, GteState())
        memory = Memory()
        mapped = {}
        for name, base, data, writable in regions:
            mapped[name] = memory.map(name, base, data, writable=writable,
                                      executable=not writable)
        try:
            result = call(memory, entry, arguments, max_steps=1000, gte=gte)
        except MipsError as exc:
            return NativeResult(False, str(exc), (0,) * 32, 0, 0, {}, GteState())
        for name, base, data, writable in regions:
            data[:] = mapped[name].data
        registers = list(result.registers)
        if self.corrupt == 'register':
            registers[2] ^= 1
        if self.corrupt == 'memory':
            regions[1][2][0] ^= 0xFF
        return NativeResult(True, '', tuple(registers), result.hi, result.lo,
                            {name: mapped[name].write_end for name, *_ in regions}, result.gte)


def base_memory(words):
    memory = Memory()
    memory.map('code0', TEXT, program(*words), executable=True)
    memory.map('ram', RAM, bytes(0x100), writable=True)
    return memory


STORE = [LW('t0', 0, 'a0'), NOP(), ADDIU('v0', 't0', 1), SW('v0', 4, 'a0'), JR('ra'), NOP()]


class CompareTests(unittest.TestCase):
    def compare(self, module, words=STORE, arguments=None):
        return compare_function(module, base_memory(words), TEXT, 0, arguments or {'a0': RAM},
                                GteState())

    def test_identical_runs_match(self):
        self.assertEqual(self.compare(FakeModule()), ('match', ''))

    def test_register_and_memory_differences_are_mismatches(self):
        self.assertEqual(self.compare(FakeModule('register')), ('mismatch', 'registers [2]'))
        outcome, detail = self.compare(FakeModule('memory'))
        self.assertEqual(outcome, 'mismatch')
        self.assertIn('memory ram', detail)

    def test_stops_must_agree_in_kind_and_address(self):
        unmapped = {'a0': 0x80300000}
        self.assertEqual(self.compare(FakeModule(), arguments=unmapped)[0], 'both_stopped')
        other = FakeModule(trap='Unmapped 4-byte read at 0x80300004 (instruction 0x80010000)')
        self.assertEqual(self.compare(other, arguments=unmapped)[0], 'mismatch')
        returned = FakeModule(trap='Misaligned 4-byte read at 0x80100001')
        self.assertEqual(self.compare(returned)[0], 'mismatch')

    def test_untranslated_code_and_step_limits_are_not_comparable(self):
        boundary = FakeModule(trap='call to untranslated 0x800a0000')
        self.assertEqual(self.compare(boundary)[0], 'boundary')
        endless = [ADDIU('v0', 'v0', 1), 0x1000FFFE, NOP()]      # b -2: loops forever
        self.assertEqual(self.compare(FakeModule(trap='budget'), words=endless)[0],
                         'step_limit')


class HelperTests(unittest.TestCase):
    def test_stop_keys(self):
        self.assertEqual(_stop_key('Unmapped 4-byte read at 0x80300000'),
                         ('unmapped', 0x80300000))
        self.assertEqual(_stop_key('Unmapped 4-byte read at 0x80300000 (instruction 0x80010000)'),
                         ('unmapped', 0x80300000))
        jump = ('jump', 0x04C10004)
        self.assertEqual(_stop_key('Unmapped 4-byte instruction fetch at 0x04c10004'), jump)
        self.assertEqual(_stop_key('jr at 0x800afd28 to 0x04c10004, not a known target'), jump)
        self.assertEqual(_stop_key('jr $ra at 0x80027ad8 returns to 0x04c10004, not to its '
                                   'caller at 0xfffffff0'), jump)

    def test_arguments_and_heap_are_shaped(self):
        rng = random.Random(1)
        values = [_argument(rng, [(0x800B0000, 0x800B1000)]) for _ in range(400)]
        self.assertTrue(any(HEAP_BASE <= value < HEAP_END for value in values))
        self.assertTrue(any(0x800B0000 <= value < 0x800B1000 for value in values))
        self.assertTrue(any(value < 65 for value in values))
        self.assertTrue(all(value % 4 == 0 for value in values if 0x1F800000 <= value < 0xFFFFFF00))
        memory = Memory()
        memory.map('ram', 0x800C0000, bytes(0x80200000 - 0x800C0000), writable=True)
        _seed_heap(memory, 7)
        ram = memory.region('ram')
        words = [int.from_bytes(ram.data[HEAP_BASE - ram.base + 4 * i:][:4], 'little')
                 for i in range(1000)]
        self.assertGreater(sum(HEAP_BASE <= word < HEAP_END for word in words), 300)


if __name__ == '__main__':
    unittest.main()
