"""The oracle's calling contract: run a compiled game function with the state it needs.

Offline validation tooling. Compiled PsyQ code reaches small data through
``$gp`` and keeps its frames on ``$sp``, so a function run with zeroed
registers and only its text mapped stops at its first ``%gp_rel`` access or
frame push. This module stages an executable from a disc image by content
hash, maps it the way the console would after crt0 ran (read-only code and
rodata, writable data and the rest of RAM, scratchpad), and starts a call
with ``$gp`` and ``$sp`` set. GTE control registers the libraries would have
set are the caller's to supply. Contract: docs/mips-interpreter.md.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re

from .disc import FileEntry, PlayStationDisc
from .exe import HEADER_BYTES, _read_whole, inspect_executables, parse_psx_exe
from .gte import GteState
from .mips import Memory, MipsError, RunResult, call
from .psyq import _SYMBOL_LINE
from .split import load_manifest

RAM_BASE = 0x80000000
RAM_END = 0x80200000
SCRATCHPAD_BASE = 0x1F800000
SCRATCHPAD_BYTES = 0x400
# How far from the entry point crt0's $gp setup is looked for.
CRT0_WINDOW = 64

_SEGMENT = re.compile(r'^\s*start:\s*(0x[0-9A-Fa-f]+)\s*\n\s*vram:\s*(0x[0-9A-Fa-f]+)\s*$',
                      re.M)
_GP_VALUE = re.compile(r'^\s*gp_value:\s*(0x[0-9A-Fa-f]+)\s*$', re.M)
_SUBSEGMENT = re.compile(r'^\s*-\s*\[\s*(0x[0-9A-Fa-f]+)\s*,\s*(\w+)\s*,\s*\w+\s*\]', re.M)
_END = re.compile(r'^\s*-\s*\[\s*(0x[0-9A-Fa-f]+)\s*\]\s*$', re.M)


class OracleError(ValueError):
    """The image, configuration, or code does not support an oracle run."""


@dataclass(frozen=True)
class StagedExecutable:
    path: str
    sha256: str
    config: str
    symbols_file: str
    symbols: dict[str, int]
    image: bytes            # the loaded image: everything after the 2 KiB header
    text_address: int
    initial_pc: int
    stack_base: int


@dataclass(frozen=True)
class Layout:
    """Where a splat config puts code, and the $gp value it was split with."""
    code: tuple[tuple[int, int], ...]
    gp_value: int | None


def read_symbols(path: Path) -> dict[str, int]:
    symbols: dict[str, int] = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        match = _SYMBOL_LINE.match(line)
        if match:
            symbols.setdefault(match.group(1), int(match.group(2), 16))
    return symbols


def find_executable(image: Path, config_dir: Path, symbol: str) -> StagedExecutable:
    """The one executable on the image whose manifest hash matches and whose symbols name ``symbol``."""
    report = inspect_executables(image)
    configs = [entry for entry in load_manifest(config_dir) if entry.kind == 'executable']
    found = []
    for exe in report['executables']:
        for config in configs:
            if exe['sha256'] in config.sha256:
                symbols = read_symbols(config_dir / config.symbols)
                if symbol in symbols:
                    found.append((exe, config, symbols))
    if len(found) != 1:
        raise OracleError(f'Expected one executable whose symbol file names {symbol}; '
                          f'found {len(found)}')
    exe, config, symbols = found[0]
    disc = PlayStationDisc(image)
    files = {(item['lba'], item['size']): FileEntry(**item)
             for item in disc.inspect()['files'] if not item['is_directory']}
    data = _read_whole(disc, files[(exe['lba'], exe['size_bytes'])])
    if hashlib.sha256(data).hexdigest() != exe['sha256']:
        raise OracleError(f"{exe['path']}: bytes changed between report and extraction")
    header = parse_psx_exe(data[:HEADER_BYTES], len(data)).header
    staged = StagedExecutable(exe['path'], exe['sha256'], config.config, config.symbols,
                              symbols, data[HEADER_BYTES:], header.text_address,
                              header.initial_pc, header.stack_base)
    address = symbols[symbol]
    if not staged.text_address <= address < staged.text_address + len(staged.image):
        raise OracleError(f'{symbol} 0x{address:08x} is outside the loaded image')
    return staged


def read_layout(config_path: Path) -> Layout:
    """Code ranges and gp_value from one of the project's splat configs.

    Reads the subset of splat's YAML the configs in splat/ use: one code
    segment with ``start`` and ``vram``, subsegments written as
    ``- [offset, type, name]``, and a closing ``- [offset]``. Each ``asm`` or
    ``c`` subsegment runs to the next subsegment or the end.
    """
    text = config_path.read_text(encoding='utf-8')
    segments = _SEGMENT.findall(text)
    if len(segments) != 1:
        raise OracleError(f'{config_path.name}: expected one segment with start and vram, '
                          f'found {len(segments)}')
    offset, base = (int(value, 16) for value in segments[0])
    subsegments = [(int(value, 16), kind) for value, kind in _SUBSEGMENT.findall(text)]
    ends = [int(value, 16) for value in _END.findall(text)]
    if not subsegments or not ends:
        raise OracleError(f'{config_path.name}: no subsegments or no end offset')
    bounds = [value for value, _ in subsegments] + [ends[-1]]
    code = tuple((base + bounds[index] - offset, base + bounds[index + 1] - offset)
                 for index, (_, kind) in enumerate(subsegments) if kind in ('asm', 'c'))
    gp = _GP_VALUE.search(text)
    return Layout(code, int(gp.group(1), 16) if gp else None)


def crt0_gp(image: bytes, text_address: int, entry: int, window: int = CRT0_WINDOW) -> int | None:
    """$gp as crt0 sets it: the first ``lui $gp`` and ``addiu``/``ori $gp, $gp`` after ``entry``."""
    high = None
    for index in range(window):
        offset = entry - text_address + 4 * index
        if not 0 <= offset <= len(image) - 4:
            return None
        word = int.from_bytes(image[offset:offset + 4], 'little')
        op, rs, rt, imm = word >> 26, (word >> 21) & 31, (word >> 16) & 31, word & 0xFFFF
        if op == 0x0F and rt == 28:
            high = imm << 16
        elif high is not None and rs == 28 and rt == 28 and op in (0x09, 0x0D):
            low = imm - 0x10000 if op == 0x09 and imm & 0x8000 else imm
            return (high + low) & 0xFFFFFFFF
    return None


def game_memory(staged: StagedExecutable, layout: Layout) -> Memory:
    """Map the executable as the console holds it after crt0, plus the rest of RAM.

    Code ranges are executable and read-only. Everything else the image
    loads, and all RAM after it up to 2 MiB (bss, heap, overlays, stack), is
    writable data; RAM below the image (the BIOS area) stays unmapped. The
    1 KiB scratchpad is mapped writable and zeroed.
    """
    start, end = staged.text_address, staged.text_address + len(staged.image)
    memory = Memory()
    cursor = start
    for index, (code_start, code_end) in enumerate(sorted(layout.code)):
        if not start <= code_start < code_end <= end or code_start < cursor:
            raise OracleError(f'Code range 0x{code_start:08x}-0x{code_end:08x} is outside '
                              'the image or overlaps another')
        if code_start > cursor:
            memory.map(f'data{index}', cursor, staged.image[cursor - start:code_start - start],
                       writable=True)
        memory.map(f'code{index}', code_start,
                   staged.image[code_start - start:code_end - start], executable=True)
        cursor = code_end
    tail = staged.image[cursor - start:] + bytes(max(0, RAM_END - end))
    memory.map('ram', cursor, tail, writable=True)
    memory.map('scratchpad', SCRATCHPAD_BASE, bytes(SCRATCHPAD_BYTES), writable=True)
    return memory


def game_call(memory: Memory, entry: int, arguments: dict[str, int] | None = None, *,
              gp: int, sp: int, max_steps: int, gte: GteState | None = None) -> RunResult:
    """Call ``entry`` with ``$gp`` and ``$sp`` set; other registers follow ``mips.call``."""
    if sp % 8 or not RAM_BASE < sp <= RAM_END:
        raise MipsError(f'Stack pointer 0x{sp:08x} is not an aligned RAM address')
    return call(memory, entry, {'gp': gp, 'sp': sp, **(arguments or {})},
                max_steps=max_steps, gte=gte)
