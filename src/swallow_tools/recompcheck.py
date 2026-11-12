"""Compare every translated game function with the interpreter on random calls (swallow-recompcheck).

Offline validation tooling. A module built by ``swallow-recompile --split-dir``
holds C translations of every game function in an executable. This tool calls
each translated function natively and through swallow_tools.mips, from the
same starting state, and compares the outcome: whether it returned, every
general register, HI, LO, the GTE registers, and every byte of writable
memory. Memory is laid out as swallow_tools.oracle maps it after crt0: code
read-only, data, bss, heap and stack writable, and the scratchpad.

Arguments are random but shaped: each of ``$a0``-``$a3`` is a small integer,
a negative one, or a pointer into the heap, the image's data, or the
scratchpad. The heap starts filled with seeded random bytes, so loads see
varied data. A trial is not comparable, and is counted as such, when the
interpreter spends its step limit or the native run reaches code the module
does not translate (PsyQ library calls). The report holds counts, names and
addresses, never game bytes. Contract: docs/recompiler.md.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import random
import re
import sys

from .gte import GteState
from .mips import Memory, MipsError
from .native import NativeError, NativeModule, _scene_gte_state
from .oracle import (
    RAM_END, SCRATCHPAD_BASE, SCRATCHPAD_BYTES, OracleError, crt0_gp, find_executable,
    game_call, game_memory, read_layout)
from .split import DEFAULT_CONFIG_DIR

SCHEMA_VERSION = 1
STACK_POINTER = RAM_END - 0x100
HEAP_BASE = 0x80100000
HEAP_END = 0x801F0000
MAX_STEPS = 200_000
NATIVE_BUDGET = 1_000_000
MAX_REPORTED = 50
# Native traps that mean the run left the translated code, not a disagreement.
_BOUNDARY = ('call to untranslated', 'no translated function', 'branch to 0x', 'jump to 0x')


class RecompCheckError(ValueError):
    """The image, library, or recompile report cannot be used together."""


def _argument(rng: random.Random, data_ranges: list[tuple[int, int]]) -> int:
    choice = rng.random()
    if choice < 0.25:
        return rng.randint(0, 64)
    if choice < 0.35:
        return rng.randint(-64, -1) & 0xFFFFFFFF
    if choice < 0.75:
        return rng.randrange(HEAP_BASE, HEAP_END, 4)
    if choice < 0.9 and data_ranges:
        start, end = rng.choice(data_ranges)
        return rng.randrange(start, max(start + 4, end - 0x100), 4)
    return rng.randrange(SCRATCHPAD_BASE, SCRATCHPAD_BASE + SCRATCHPAD_BYTES - 0x40, 4)


def _seed_heap(memory: Memory, seed: int) -> None:
    """Fill the heap with words that are heap pointers, small integers, or random bits.

    Game data is full of pointers; random bytes alone would make nearly every
    call stop at its first dereference.
    """
    rng = random.Random(seed)
    words = bytearray()
    for _ in range((HEAP_END - HEAP_BASE) // 4):
        choice = rng.random()
        if choice < 0.4:
            value = rng.randrange(HEAP_BASE, HEAP_END, 4)
        elif choice < 0.7:
            value = rng.randint(0, 255)
        else:
            value = rng.getrandbits(32)
        words += value.to_bytes(4, 'little')
    ram = memory.region('ram')
    start = HEAP_BASE - ram.base
    ram.data[start:start + len(words)] = words


def _regions(memory: Memory) -> list[tuple[str, int, bytearray, bool]]:
    return [(region.name, region.base, bytearray(region.data), region.writable)
            for region in memory.regions]


_ADDRESS = re.compile(r'0x([0-9A-Fa-f]{8})')


_FETCH = re.compile(r'instruction fetch at 0x([0-9A-Fa-f]{8})')
_UNKNOWN_TARGET = re.compile(r'^jr at 0x[0-9A-Fa-f]{8} to 0x([0-9A-Fa-f]{8}), not a known target')
_WRONG_RETURN = re.compile(r'^jr \$ra at 0x[0-9A-Fa-f]{8} returns to 0x([0-9A-Fa-f]{8}),')


def _stop_key(message: str) -> tuple[str, int | None]:
    """The kind of stop (its first word) and the first address a stop message names.

    A computed jump, or a return through a clobbered ``$ra``, to an address
    that is not code is an instruction-fetch stop in the interpreter and a
    trap natively; both name the target, so they key alike.
    """
    for pattern in (_FETCH, _UNKNOWN_TARGET, _WRONG_RETURN):
        jump = pattern.search(message)
        if jump:
            return 'jump', int(jump.group(1), 16)
    match = _ADDRESS.search(message)
    words = message.split()
    return (words[0].lower() if words else '', int(match.group(1), 16) if match else None)


def compare_function(module: NativeModule, base: Memory, entry: int, gp: int,
                     arguments: dict[str, int], gte: GteState) -> tuple[str, str]:
    """('match' | 'mismatch' | 'both_stopped' | 'boundary' | 'step_limit', detail)."""
    memory = Memory()
    for region in base.regions:
        memory.map(region.name, region.base, region.data, writable=region.writable,
                   executable=region.executable)
    native_regions = _regions(base)
    try:
        expected = game_call(memory, entry, arguments, gp=gp, sp=STACK_POINTER,
                             max_steps=MAX_STEPS, gte=gte)
        interpreter_error = None
    except MipsError as exc:
        expected, interpreter_error = None, str(exc)
    result = module.run(entry, native_regions, {'gp': gp, 'sp': STACK_POINTER, **arguments},
                        budget=NATIVE_BUDGET, gte=gte)
    if not result.ok and any(marker in result.message for marker in _BOUNDARY):
        return 'boundary', result.message
    if interpreter_error is not None and 'did not return within' in interpreter_error:
        return 'step_limit', interpreter_error
    if interpreter_error is not None or not result.ok:
        if interpreter_error is not None and not result.ok:
            # Both stopped: they must stop for the same kind of reason at the
            # same address (the first address each message names).
            if _stop_key(interpreter_error) != _stop_key(result.message):
                return 'mismatch', (f'interpreter: {interpreter_error}; '
                                    f'native: {result.message}')
            return 'both_stopped', ''
        return 'mismatch', (f'interpreter: {interpreter_error or "returned"}; '
                            f'native: {result.message or "returned"}')
    problems = []
    if result.registers != expected.registers:
        differing = [index for index in range(32)
                     if result.registers[index] != expected.registers[index]]
        problems.append(f'registers {differing}')
    if (result.hi, result.lo) != (expected.hi, expected.lo):
        problems.append('HI/LO')
    if result.gte != expected.gte:
        problems.append('GTE')
    for (name, _, data, writable), region in zip(native_regions, memory.regions):
        if writable and bytes(data) != bytes(region.data):
            problems.append(f'memory {name}')
        if writable and result.write_ends.get(name) != region.write_end:
            problems.append(f'write end {name}')
    return ('mismatch', ', '.join(problems)) if problems else ('match', '')


def check(image: Path, library: Path, recompile_report: Path, *, trials: int = 4,
          seed: int = 0x5A11, config_dir: Path = DEFAULT_CONFIG_DIR,
          symbol: str = 'ace3_entry') -> dict:
    try:
        report = json.loads(recompile_report.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise RecompCheckError(f'Cannot read {recompile_report}: {exc}') from exc
    try:
        staged = find_executable(image, config_dir, symbol)
    except OracleError as exc:
        raise RecompCheckError(str(exc)) from exc
    if report.get('executable', {}).get('sha256') != staged.sha256:
        raise RecompCheckError('The recompile report was made from a different executable')
    layout = read_layout(config_dir / staged.config)
    gp = crt0_gp(staged.image, staged.text_address, staged.initial_pc)
    if gp is None:
        raise RecompCheckError('crt0 does not set $gp near the entry point')
    base = game_memory(staged, layout)
    _seed_heap(base, seed)
    data_ranges = [(region.base, region.end) for region in base.regions
                   if region.writable and region.name.startswith('data')]
    rng = random.Random(seed)
    outcomes: Counter = Counter()
    functions_matched = functions_compared = 0
    mismatches = []
    with NativeModule(library) as module:
        if module.sha256 != staged.sha256:
            raise RecompCheckError('The library was translated from a different executable')
        for function in report['functions']:
            entry = int(function['start'], 16)
            compared = matched = 0
            for trial in range(trials):
                arguments = {name: _argument(rng, data_ranges) for name in ('a0', 'a1', 'a2', 'a3')}
                gte = _scene_gte_state(rng)
                outcome, detail = compare_function(module, base, entry, gp, arguments, gte)
                outcomes[outcome] += 1
                if outcome in ('match', 'both_stopped', 'mismatch'):
                    compared += 1
                if outcome == 'match':
                    matched += 1
                if outcome == 'mismatch':
                    mismatches.append({'function': function['name'], 'entry': function['start'],
                                       'trial': trial, 'detail': detail})
            functions_compared += compared > 0
            functions_matched += matched > 0
    return {
        'schema_version': SCHEMA_VERSION, 'source_image': image.name,
        'executable': {'path': staged.path, 'sha256': staged.sha256},
        'seed': seed, 'trials_per_function': trials,
        'layout': {'gp': f'0x{gp:08X}', 'sp': f'0x{STACK_POINTER:08X}',
                   'heap': f'0x{HEAP_BASE:08X}-0x{HEAP_END:08X}',
                   'max_steps': MAX_STEPS, 'native_budget': NATIVE_BUDGET},
        'functions': len(report['functions']),
        'functions_compared': functions_compared,
        'functions_with_a_returning_match': functions_matched,
        'outcomes': dict(sorted(outcomes.items())),
        'mismatch_count': len(mismatches),
        'mismatches': mismatches[:MAX_REPORTED],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description='Compare every translated game function with the interpreter')
    parser.add_argument('bin', type=Path, help='Local raw MODE2/2352 disc image')
    parser.add_argument('library', type=Path, help='Module built from swallow-recompile '
                                                   '--split-dir output')
    parser.add_argument('recompile_report', type=Path,
                        help='recompile-report.json written with that module')
    parser.add_argument('--trials', type=int, default=4, help='Random calls per function')
    parser.add_argument('--seed', type=lambda value: int(value, 0), default=0x5A11)
    parser.add_argument('--config-dir', type=Path, default=DEFAULT_CONFIG_DIR)
    parser.add_argument('--json', action='store_true', help='Print the full JSON report')
    args = parser.parse_args(argv)
    try:
        result = check(args.bin, args.library, args.recompile_report, trials=args.trials,
                       seed=args.seed, config_dir=args.config_dir)
    except (OSError, OracleError, RecompCheckError, NativeError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"{result['executable']['path']}: {result['functions']} functions, "
              f"{result['trials_per_function']} trials each; outcomes {result['outcomes']}; "
              f"{result['functions_with_a_returning_match']} functions matched on a returning "
              f"call; {result['mismatch_count']} mismatches")
        for item in result['mismatches'][:10]:
            print(f"  MISMATCH {item['function']} trial {item['trial']}: {item['detail']}")
    return 1 if result['mismatch_count'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
