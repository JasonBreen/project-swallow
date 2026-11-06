"""Run AC3's own Ulz routine on every Ulz blob in ACE.BPB and compare it with ulz.py.

The routine runs either in the interpreter (``mips``) or, with ``--native``,
as C translated by swallow-recompile and built into a shared library
(``native``). Both runs map the same regions at the same addresses and pass
the same checks.

Contract and results: docs/ulz-routine.md. The routine is found by name in the
splat symbol file of the executable whose SHA256 the splat manifest lists
(``oracle.find_executable``), and it runs in the interpreter in ``mips`` with
only three regions mapped: the executable's text, the blob, and a destination buffer. Any access
outside them stops that run. The report holds counts, addresses, and paths,
never game bytes.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import json
from pathlib import Path
import struct
import sys

from .bph import BphFormatError, locate_archive_files, parse_bph
from .bpb import MAX_DEPTH, MAX_ENTRY_BYTES, ContainerFormatError, parse_offset_table
from .disc import DiscFormatError, PlayStationDisc
from .exe import ExeFormatError
from .mips import Memory, MipsError, call
from .native import NativeError, NativeModule
from .oracle import OracleError, find_executable
from .split import DEFAULT_CONFIG_DIR, SplitError
from .ulz import UlzFormatError, decompress_ulz, is_ulz

SCHEMA_VERSION = 2
ROUTINE_SYMBOL = 'ulz_decompress'
# Interpreter addresses, chosen clear of each other and of any PS-X text image.
# The routine only follows the pointers it is given, so these need not be RAM.
SOURCE_BASE = 0x81000000
DESTINATION_BASE = 0x82000000
# Writable bytes past the declared size, so an overrun is measured, not fatal.
GUARD_BYTES = 0x10000
MAX_REPORTED = 50
FIELD_MASK = 0xFFFFFF
# At return, the annotated routine keeps its stream pointers in these
# registers on every version path (docs/ulz-routine.md).
STREAM_REGISTERS = {'flags': 't6', 'literals': 't5', 'pairs': 'a3'}


class UlzCheckError(ValueError):
    """The image, executable, or symbol files do not support a routine check."""


@dataclass(frozen=True)
class RoutineOutput:
    payload: bytes
    end_offset: int
    write_end: int
    flag_bytes_read: int
    literal_bytes_read: int
    pair_bytes_read: int
    # Instructions executed; None for a native run, which does not count them.
    steps: int | None


def run_routine(text: bytes, text_address: int, entry: int, blob: bytes, *,
                guard_bytes: int = GUARD_BYTES,
                native: NativeModule | None = None) -> RoutineOutput:
    """Call the routine as ``entry(blob, destination)`` and measure what it did.

    Header fields are read the way the routine reads them: three 24-bit
    fields at ``+4``, ``+8`` and ``+0xC``. The destination holds the declared
    size plus ``guard_bytes``. With ``native``, the translated routine runs
    instead of the interpreter, on the same regions, and a trap in it is
    reported as a ``MipsError``.
    """
    if len(blob) < 16:
        raise UlzFormatError('Truncated Ulz header: expected at least 16 bytes')
    size_word, literal_word, pair_word = struct.unpack_from('<III', blob, 4)
    declared = size_word & FIELD_MASK
    max_steps = 32 * (declared + len(blob)) + 4096
    arguments = {'a0': SOURCE_BASE, 'a1': DESTINATION_BASE}
    if native is None:
        memory = Memory()
        memory.map('text', text_address, text, executable=True)
        memory.map('blob', SOURCE_BASE, blob)
        region = memory.map('destination', DESTINATION_BASE, bytes(declared + guard_bytes),
                            writable=True)
        result = call(memory, entry, arguments, max_steps=max_steps)
        destination, write_end, steps = region.data, region.write_end, result.steps
    else:
        destination = bytearray(declared + guard_bytes)
        regions = [('text', text_address, bytearray(text), False),
                   ('blob', SOURCE_BASE, bytearray(blob), False),
                   ('destination', DESTINATION_BASE, destination, True)]
        result = native.run(entry, regions, arguments, budget=max_steps)
        if not result.ok:
            raise MipsError(f'Native trap: {result.message}')
        write_end, steps = result.write_ends['destination'], None
    end = (result['v0'] - DESTINATION_BASE) & 0xFFFFFFFF
    if end > len(destination):
        raise MipsError(f'Returned end pointer 0x{result["v0"]:08x} is outside the destination')
    starts = {'flags': 16, 'literals': literal_word & FIELD_MASK,
              'pairs': pair_word & FIELD_MASK}
    read = {stream: (result[register] - SOURCE_BASE - starts[stream]) & 0xFFFFFFFF
            for stream, register in STREAM_REGISTERS.items()}
    return RoutineOutput(bytes(destination[:end]), end, write_end,
                         read['flags'], read['literals'], read['pairs'], steps)


def compare_with_decoder(blob: bytes, output: RoutineOutput) -> list[str]:
    """List every way the routine's run differs from ``decompress_ulz``; empty if none.

    Stream rules per version: version 0 reads the zero terminator word after
    the flags it uses and reads literals bytewise; version 2 reads only the
    flag words it uses and reads literals as aligned 32-bit words.
    """
    decoded = decompress_ulz(blob)
    header = decoded.header
    problems = []
    if output.end_offset != header.decompressed_size:
        problems.append(f'returned end {output.end_offset} != declared size '
                        f'{header.decompressed_size}')
    if output.write_end > header.decompressed_size:
        problems.append(f'wrote {output.write_end - header.decompressed_size} bytes past '
                        'the declared size')
    if output.payload[:header.decompressed_size] != decoded.payload:
        problems.append('payload bytes differ')
    flags = 4 * (decoded.flag_words_used + (1 if header.version == 0 else 0))
    literals = decoded.literal_bytes_used
    if header.version == 2:
        literals = (literals + 3) // 4 * 4
    expected = {'flags': flags, 'literals': literals, 'pairs': decoded.pair_bytes_used}
    measured = {'flags': output.flag_bytes_read, 'literals': output.literal_bytes_read,
                'pairs': output.pair_bytes_read}
    for stream in STREAM_REGISTERS:
        if measured[stream] != expected[stream]:
            problems.append(f'{stream} stream: routine consumed {measured[stream]} bytes, '
                            f'decoder accounting gives {expected[stream]}')
    return problems


def iter_ulz_blobs(data: bytes, path: tuple[int, ...], origin: str = 'stored',
                   depth: int = 0):
    """Yield ``(path, origin, blob)`` for every Ulz leaf the BPB walker would reach.

    Mirrors ``bpb.walk_payload`` with decoding: a decoded payload is walked
    again with origin ``decoded``. A blob that ulz.py rejects is yielded but
    not descended into.
    """
    if depth > MAX_DEPTH:
        raise ContainerFormatError(f'Container nesting exceeds depth {MAX_DEPTH}')
    if is_ulz(data):
        yield path, origin, data
        try:
            payload = decompress_ulz(data).payload
        except UlzFormatError:
            return
        yield from iter_ulz_blobs(payload, path, 'decoded', depth + 1)
        return
    try:
        table = parse_offset_table(data)
    except ContainerFormatError:
        return
    for index, (start, end) in enumerate(table.items):
        if end > start:
            yield from iter_ulz_blobs(data[start:end], path + (index,), origin, depth + 1)


def _routine_executable(image: Path, config_dir: Path) -> tuple[dict, dict, bytes, int, int]:
    try:
        staged = find_executable(image, config_dir, ROUTINE_SYMBOL)
    except OracleError as exc:
        raise UlzCheckError(str(exc)) from exc
    address = staged.symbols[ROUTINE_SYMBOL]
    described = {'path': staged.path, 'sha256': staged.sha256, 'config': staged.config,
                 'symbols': staged.symbols_file}
    return described, {'symbol': ROUTINE_SYMBOL, 'address': f'0x{address:08x}'}, \
        staged.image, staged.text_address, address


def check_image(image: Path, *, entries: list[int] | None = None,
                config_dir: Path = DEFAULT_CONFIG_DIR,
                native: NativeModule | None = None) -> dict:
    executable, routine, text, text_address, entry = _routine_executable(image, config_dir)
    if native is not None and native.sha256 != executable['sha256']:
        raise UlzCheckError(f"The native library was translated from SHA256 {native.sha256}, "
                            f"not from {executable['path']} ({executable['sha256']})")
    disc = PlayStationDisc(image)
    bph, bpb = locate_archive_files(disc, disc.inspect())
    index = parse_bph(disc.read_file(bph), bpb.size)
    wanted = None if entries is None else set(entries)
    selected = [item for item in index.entries if item.kind == 'normal'
                and (wanted is None or item.index in wanted)]
    if wanted is not None and len(selected) != len(wanted):
        raise BphFormatError('Requested entry indices are not all normal BPH records')
    versions: Counter = Counter()
    mismatches, decoder_errors, routine_errors = [], [], []
    blobs = matched = high_byte = steps = max_steps = 0
    for item in selected:
        if item.size_bytes > MAX_ENTRY_BYTES:
            raise ContainerFormatError(
                f'Entry {item.index} exceeds the {MAX_ENTRY_BYTES} byte walker limit')
        data = disc.read_file(bpb, offset=item.offset_bytes, length=item.size_bytes)
        for path, origin, blob in iter_ulz_blobs(data, (item.index,)):
            blobs += 1
            where = {'path': '.'.join(map(str, path)), 'origin': origin,
                     'version': blob[7] if len(blob) > 7 else None}
            versions[str(where['version'])] += 1
            if len(blob) >= 16 and blob[15]:
                high_byte += 1
            try:
                output = run_routine(text, text_address, entry, blob, native=native)
            except (MipsError, UlzFormatError) as exc:
                routine_errors.append({**where, 'error': str(exc)})
                continue
            if output.steps is not None:
                steps += output.steps
                max_steps = max(max_steps, output.steps)
            try:
                problems = compare_with_decoder(blob, output)
            except UlzFormatError as exc:
                decoder_errors.append({**where, 'error': str(exc),
                                       'routine_end': output.end_offset})
                continue
            if problems:
                mismatches.append({**where, 'problems': problems})
            else:
                matched += 1
    return {
        'schema_version': SCHEMA_VERSION,
        'source_image': image.name,
        'oracle': 'interpreter' if native is None else 'native',
        'executable': executable,
        'routine': routine,
        'layout': {'text_address': f'0x{text_address:08x}',
                   'source_base': f'0x{SOURCE_BASE:08x}',
                   'destination_base': f'0x{DESTINATION_BASE:08x}',
                   'guard_bytes': GUARD_BYTES},
        'entries_checked': len(selected),
        'blobs': blobs,
        'versions': dict(sorted(versions.items())),
        'matched': matched,
        'mismatch_count': len(mismatches),
        'decoder_error_count': len(decoder_errors),
        'routine_error_count': len(routine_errors),
        'pair_word_high_byte_nonzero': high_byte,
        'instructions_executed': steps if native is None else None,
        'max_instructions_per_blob': max_steps if native is None else None,
        'mismatches': mismatches[:MAX_REPORTED],
        'decoder_errors': decoder_errors[:MAX_REPORTED],
        'routine_errors': routine_errors[:MAX_REPORTED],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare AC3's own Ulz routine with the project decoder on one image")
    parser.add_argument('bin', type=Path, help='Local raw MODE2/2352 disc image')
    parser.add_argument('--entry', type=int, action='append',
                        help='Check only this BPH entry index (repeatable)')
    parser.add_argument('--config-dir', type=Path, default=DEFAULT_CONFIG_DIR,
                        help='splat config directory with manifest.json and symbol files')
    parser.add_argument('--native', type=Path, metavar='LIBRARY',
                        help='Run the routine translated by swallow-recompile from this '
                             'built shared library instead of the interpreter')
    parser.add_argument('--json', action='store_true', help='Print the full JSON report')
    args = parser.parse_args(argv)
    native = None
    try:
        if args.native is not None:
            native = NativeModule(args.native)
        report = check_image(args.bin, entries=args.entry, config_dir=args.config_dir,
                             native=native)
    except (OSError, DiscFormatError, BphFormatError, ContainerFormatError, ExeFormatError,
            SplitError, UlzCheckError, NativeError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 2
    finally:
        if native is not None:
            native.close()
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"{report['executable']['path']} {report['routine']['symbol']} "
              f"{report['routine']['address']} ({report['oracle']}): "
              f"{report['matched']} of {report['blobs']} "
              f"blobs match; {report['mismatch_count']} mismatches, "
              f"{report['decoder_error_count']} decoder errors, "
              f"{report['routine_error_count']} routine errors")
    clean = not (report['mismatch_count'] or report['decoder_error_count']
                 or report['routine_error_count'])
    return 0 if clean else 1


if __name__ == '__main__':
    raise SystemExit(main())
