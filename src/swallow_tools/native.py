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

    def run(self, entry: int, regions: list[tuple[str, int, bytearray, bool]],
            arguments: dict[str, int] | None = None, *, budget: int) -> NativeResult:
        """Map the regions (shared, not copied), set registers, and call ``entry``.

        ``$ra`` is set to the interpreter's return address unless given, so a
        routine's ``jr $ra`` returns as it does under swallow_tools.mips.
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
        library.swallow_set_budget(cpu, budget)
        status = library.swallow_run(cpu, entry)
        registers = tuple(library.swallow_get_register(cpu, index) for index in range(32))
        result = NativeResult(status == SWALLOW_OK,
                              (library.swallow_last_trap(cpu) or b'').decode('utf-8', 'replace'),
                              registers, library.swallow_get_register(cpu, REG_HI),
                              library.swallow_get_register(cpu, REG_LO),
                              {name: library.swallow_guest_write_end(cpu, name.encode('ascii'))
                               for name, *_ in regions})
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
        result = module.run(case.entry, mapped, case.arguments, budget=MAX_STEPS)
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
        data = next((region for region in mapped if region[0] == 'data'), None)
        if data is not None and (bytes(data[2]) != expected.data
                                 or result.write_ends['data'] != expected.write_end):
            problems.append(f'{case.name}: data or write end differs')
    return problems


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print('usage: python -m swallow_tools.native LIBRARY', file=sys.stderr)
        return 2
    try:
        with NativeModule(Path(args[0])) as module:
            problems = compare_selftest(module)
    except NativeError as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 2
    for problem in problems:
        print(f'FAIL {problem}')
    from .selftest import cases
    print(f'native selftest: {len(cases())} cases, {len(problems)} failure(s)')
    return 1 if problems else 0


if __name__ == '__main__':
    raise SystemExit(main())
