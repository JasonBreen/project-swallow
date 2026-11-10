"""Run code translated by swallow-recompile from a built shared library, through ctypes.

Offline tooling. The library is a generated module linked with the PS1 core
in runtime/. It exports ``swallow_run`` and ``swallow_module_sha256`` from the
module, and the core's CPU and guest-memory functions. Every call declares
its argument and result types, so 32-bit guest addresses never pass through
a signed C ``int``. Region buffers are shared with the library, not copied,
so writes are visible in the caller's bytearray.

``python -m swallow_tools.native LIBRARY`` runs the recompiler self-test
cases (swallow_tools.selftest) through a library built from them, and
compares each with the interpreter. Contract: docs/recompiler.md.
"""
from __future__ import annotations

import ctypes
from dataclasses import dataclass
from pathlib import Path
import sys

from .gte import Gte, GteError, GteState, decode_command
from .mips import RETURN_ADDRESS, register_index

SWALLOW_OK = 0
REG_HI, REG_LO = 32, 33
MAP_ERRORS = {1: 'misaligned or past 4 GiB', 2: 'overlaps another region',
              3: 'too many regions', 4: 'bad name', 5: 'covers the return address'}


class NativeError(RuntimeError):
    """The library cannot be loaded or used as a translated module."""


@dataclass(frozen=True)
class NativeResult:
    ok: bool
    message: str
    registers: tuple[int, ...]
    hi: int
    lo: int
    write_ends: dict[str, int]
    gte: GteState = GteState()

    def __getitem__(self, name: str) -> int:
        return self.registers[register_index(name)]


class NativeModule:
    def __init__(self, path: Path):
        try:
            library = ctypes.CDLL(str(Path(path).resolve()))
        except OSError as exc:
            raise NativeError(f'Cannot load {path}: {exc}') from exc
        cpu_p = ctypes.c_void_p
        signatures = {
            'swallow_cpu_new': (cpu_p, []), 'swallow_cpu_free': (None, [cpu_p]),
            'swallow_cpu_reset': (None, [cpu_p]),
            'swallow_get_register': (ctypes.c_uint32, [cpu_p, ctypes.c_int]),
            'swallow_set_register': (None, [cpu_p, ctypes.c_int, ctypes.c_uint32]),
            'swallow_set_budget': (None, [cpu_p, ctypes.c_uint64]),
            'swallow_last_trap': (ctypes.c_char_p, [cpu_p]),
            'swallow_guest_map': (ctypes.c_int, [cpu_p, ctypes.c_char_p, ctypes.c_uint32,
                                                 ctypes.POINTER(ctypes.c_uint8),
                                                 ctypes.c_uint32, ctypes.c_int]),
            'swallow_guest_write_end': (ctypes.c_int64, [cpu_p, ctypes.c_char_p]),
            'swallow_run': (ctypes.c_int, [cpu_p, ctypes.c_uint32]),
            'swallow_module_sha256': (ctypes.c_char_p, []),
            'swallow_gte_get': (ctypes.c_uint32, [cpu_p, ctypes.c_int]),
            'swallow_gte_set': (None, [cpu_p, ctypes.c_int, ctypes.c_uint32]),
            'swallow_gte_execute': (ctypes.c_int, [cpu_p, ctypes.c_uint32]),
        }
        for name, (restype, argtypes) in signatures.items():
            try:
                function = getattr(library, name)
            except AttributeError:
                raise NativeError(f'{path} does not export {name}; is it a module built by '
                                  'swallow-recompile?') from None
            function.restype = restype
            function.argtypes = argtypes
        self._library = library
        self._cpu = library.swallow_cpu_new()
        if not self._cpu:
            raise NativeError('swallow_cpu_new failed')

    @property
    def sha256(self) -> str:
        return self._library.swallow_module_sha256().decode('ascii')

    def set_gte(self, state: GteState) -> None:
        """Load the GTE registers as stored, data then control."""
        for index, value in enumerate(state.data + state.control):
            self._library.swallow_gte_set(self._cpu, index, value)

    def get_gte(self) -> GteState:
        values = [self._library.swallow_gte_get(self._cpu, index) for index in range(64)]
        return GteState(tuple(values[:32]), tuple(values[32:]))

    def gte_command(self, state: GteState, word: int) -> GteState | None:
        """Run one command word from ``state`` on the C GTE; None if it refuses the word."""
        self._library.swallow_cpu_reset(self._cpu)
        self.set_gte(state)
        if self._library.swallow_gte_execute(self._cpu, word & 0xFFFFFFFF):
            return None
        return self.get_gte()

    def run(self, entry: int, regions: list[tuple[str, int, bytearray, bool]],
            arguments: dict[str, int] | None = None, *, budget: int, hi: int = 0, lo: int = 0,
            gte: GteState | None = None) -> NativeResult:
        """Map the regions (shared, not copied), set registers, and call ``entry``.

        ``$ra`` is set to the interpreter's return address unless given, so a
        routine's ``jr $ra`` returns as it does under swallow_tools.mips.
        ``hi``, ``lo``, and ``gte`` set the starting HI, LO, and GTE registers.
        """
        library, cpu = self._library, self._cpu
        library.swallow_cpu_reset(cpu)
        buffers = []
        for name, base, data, writable in regions:
            if not data:
                raise NativeError(f'Region {name} is empty')
            buffer = (ctypes.c_uint8 * len(data)).from_buffer(data)
            buffers.append(buffer)
            status = library.swallow_guest_map(cpu, name.encode('ascii'), base, buffer,
                                               len(data), int(writable))
            if status:
                raise NativeError(f'Region {name}: {MAP_ERRORS.get(status, status)}')
        values = {'ra': RETURN_ADDRESS, **(arguments or {})}
        for name, value in values.items():
            index = register_index(name)
            if index == 0:
                raise NativeError('$zero cannot be set')
            library.swallow_set_register(cpu, index, value & 0xFFFFFFFF)
        library.swallow_set_register(cpu, REG_HI, hi & 0xFFFFFFFF)
        library.swallow_set_register(cpu, REG_LO, lo & 0xFFFFFFFF)
        if gte is not None:
            self.set_gte(gte)
        library.swallow_set_budget(cpu, budget)
        status = library.swallow_run(cpu, entry)
        registers = tuple(library.swallow_get_register(cpu, index) for index in range(32))
        result = NativeResult(status == SWALLOW_OK,
                              (library.swallow_last_trap(cpu) or b'').decode('utf-8', 'replace'),
                              registers, library.swallow_get_register(cpu, REG_HI),
                              library.swallow_get_register(cpu, REG_LO),
                              {name: library.swallow_guest_write_end(cpu, name.encode('ascii'))
                               for name, *_ in regions},
                              self.get_gte())
        del buffers
        return result

    def close(self) -> None:
        if self._cpu:
            self._library.swallow_cpu_free(self._cpu)
            self._cpu = None

    def __enter__(self) -> 'NativeModule':
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def compare_selftest(module: NativeModule) -> list[str]:
    """Run every self-test case natively and through the interpreter; list differences."""
    from .selftest import MAX_STEPS, cases, interpret, regions
    problems = []
    for case in cases():
        expected = interpret(case)
        mapped = regions(case)
        result = module.run(case.entry, mapped, case.arguments, budget=MAX_STEPS,
                            gte=case.gte)
        if result.ok != expected.ok:
            problems.append(f'{case.name}: native {"returned" if result.ok else "trapped"} '
                            f'({result.message}), interpreter '
                            f'{"returned" if expected.ok else "stopped"} ({expected.message})')
            continue
        if not expected.ok:
            continue
        if (result.registers, result.hi, result.lo) != (expected.registers, expected.hi,
                                                         expected.lo):
            problems.append(f'{case.name}: registers differ')
        if result.gte != expected.gte:
            problems.append(f'{case.name}: GTE registers differ')
        data = next((region for region in mapped if region[0] == 'data'), None)
        if data is not None and (bytes(data[2]) != expected.data
                                 or result.write_ends['data'] != expected.write_end):
            problems.append(f'{case.name}: data or write end differs')
    return problems


# Values that reach the GTE's edges: saturation, 44-bit and 32-bit overflow,
# the division limit, and sign boundaries, mixed with random words.
_EDGE_WORDS = (0, 1, 0x7FFF, 0x8000, 0xFFFF, 0x7FFF7FFF, 0x80008000, 0xFFFFFFFF,
               0x7FFFFFFF, 0x80000000, 0x1000, 0x10001000, 0xF000F000, 0x00FF00FF)


def gte_command_words() -> list[int]:
    """Every modelled command with each sf and lm, and every modelled MVMVA selector."""
    from .gte import COMMANDS
    words = []
    for op, name in sorted(COMMANDS.items()):
        for sf in (0, 1):
            for lm in (0, 1):
                base = (0x12 << 26) | (1 << 25) | (sf << 19) | (lm << 10) | op
                if name != 'mvmva':
                    words.append(base)
                    continue
                for mx in (0, 1, 2):
                    for v in range(4):
                        for cv in (0, 1, 3):
                            words.append(base | (mx << 17) | (v << 15) | (cv << 13))
    return words


def _scene_gte_state(rng) -> GteState:
    """A state shaped like a 3D scene, so RTPS/RTPT divide without overflow."""
    from . import gte
    def pair(low, high):
        return (rng.randint(low, high) & 0xFFFF) | ((rng.randint(low, high) & 0xFFFF) << 16)
    control = {index: pair(-0x1000, 0x1000) for index in range(32)}
    control[gte.TRX] = rng.randint(-0x4000, 0x4000) & 0xFFFFFFFF
    control[gte.TRY] = rng.randint(-0x4000, 0x4000) & 0xFFFFFFFF
    control[gte.TRZ] = rng.randint(0x100, 0x20000)
    control[gte.OFX] = (rng.randint(0, 640) << 16) & 0xFFFFFFFF
    control[gte.OFY] = (rng.randint(0, 480) << 16) & 0xFFFFFFFF
    control[gte.H] = rng.randint(0x80, 0x400)
    control[gte.DQA] = rng.randint(-0x200, 0x200) & 0xFFFF
    control[gte.DQB] = rng.randint(0, 0x1800000)
    control[gte.ZSF3] = rng.randint(0, 0x600)
    control[gte.ZSF4] = rng.randint(0, 0x600)
    data = {index: pair(-0x800, 0x800) for index in range(32)}
    for index in (gte.SZ0, gte.SZ1, gte.SZ2, gte.SZ3):
        data[index] = rng.randint(0, 0xFFFF)
    data[gte.IR0] = rng.randint(0, 0x1000)
    return GteState.from_registers(data, control)


def random_gte_state(rng) -> GteState:
    if rng.random() < 0.5:
        return _scene_gte_state(rng)

    def word():
        choice = rng.random()
        if choice < 0.3:
            return rng.choice(_EDGE_WORDS)
        if choice < 0.6:
            value = rng.randint(-0x1000, 0x1000) & 0xFFFF
            return value | ((rng.randint(-0x1000, 0x1000) & 0xFFFF) << 16)
        return rng.getrandbits(32)
    return GteState.from_registers({index: word() for index in range(32)},
                                   {index: word() for index in range(32)})


def divider_state(h: int, sz3: int) -> GteState:
    """RTPS with RT = 0 and TR = (0, 0, sz3), DQA = 1, DQB = 0: MAC0 is the quotient h / sz3."""
    from . import gte
    return GteState.from_registers({}, {gte.TRZ: sz3, gte.H: h, gte.DQA: 1})


def compare_gte_vectors(module: NativeModule, states: int = 200, seed: int = 0x5A11,
                        divisions: int = 20000) -> list[str]:
    """Run every command word on random states in C and in swallow_tools.gte; list differences.

    Random states rarely reach every rounding case of the perspective
    divider, so it is also swept directly through RTPS on ``divisions``
    (H, SZ3) pairs. Also checks that the C GTE refuses exactly the words the
    Python decoder rejects, on a sample of command and non-command words.
    """
    import random
    rng = random.Random(seed)
    problems = []
    rtps = (0x12 << 26) | (1 << 25) | (1 << 19) | 0x01
    pairs = [(0, 1), (1, 1), (0xFFFF, 0xFFFF), (0xFFFF, 0x8000), (0xFFFF, 0x7FFF),
             (0x7FFF, 0x4000), (0x7FFE, 0x3FFF), (1, 0xFFFF)]
    while len(pairs) < divisions:
        sz3 = rng.randint(1, 0xFFFF)
        pairs.append((rng.randint(0, min(0xFFFF, 2 * sz3 + 1)), sz3))
    for h, sz3 in pairs:
        state = divider_state(h, sz3)
        python = Gte(state)
        python.execute(decode_command(rtps))
        if module.gte_command(state, rtps) != python.snapshot():
            problems.append(f'RTPS divider, H=0x{h:04x}, SZ3=0x{sz3:04x}: C and Python differ')
            if len(problems) >= 20:
                return problems
    words = gte_command_words()
    for state_index in range(states):
        state = random_gte_state(rng)
        for word in words:
            # Bits 20-24, 11-12, and 6-9 are ignored; set some of them.
            noisy = word | (rng.getrandbits(5) << 20) | (rng.getrandbits(2) << 11) | \
                (rng.getrandbits(4) << 6)
            python = Gte(state)
            python.execute(decode_command(noisy))
            native = module.gte_command(state, noisy)
            if native != python.snapshot():
                problems.append(f'state {state_index}, word 0x{noisy:08x}: C and Python differ')
                if len(problems) >= 20:
                    return problems
    for _ in range(2000):
        word = (0x12 << 26) | (1 << 25) | rng.getrandbits(25) if rng.random() < 0.8 \
            else rng.getrandbits(32)
        try:
            decode_command(word)
            accepted = True
        except GteError:
            accepted = False
        native = module.gte_command(GteState(), word)
        if accepted != (native is not None):
            problems.append(f'word 0x{word:08x}: Python {"accepts" if accepted else "rejects"}, '
                            f'C {"accepts" if native is not None else "rejects"}')
    return problems


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print('usage: python -m swallow_tools.native LIBRARY', file=sys.stderr)
        return 2
    try:
        with NativeModule(Path(args[0])) as module:
            problems = compare_selftest(module)
            gte_problems = compare_gte_vectors(module)
    except NativeError as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 2
    for problem in problems + gte_problems:
        print(f'FAIL {problem}')
    from .selftest import cases
    print(f'native selftest: {len(cases())} cases, {len(problems)} failure(s); '
          f'GTE vectors: {len(gte_command_words())} command words on 200 states, '
          f'{len(gte_problems)} failure(s)')
    return 1 if problems or gte_problems else 0


if __name__ == '__main__':
    raise SystemExit(main())
