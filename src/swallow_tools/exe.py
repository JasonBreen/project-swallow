"""Read-only PS-X EXE header and SYSTEM.CNF reports; no disassembly or loading.

Format evidence and limits: docs/exe-format.md. The header layout follows the
public psx-spx description; every rule below was checked against all four
supplied references. Reports carry metadata, hashes, and string offsets only.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re
import struct
import sys

from .disc import DiscFormatError, FileEntry, Form2SectorError, PlayStationDisc

MAGIC = b'PS-X EXE'
HEADER_BYTES = 0x800
HEADER = struct.Struct('<8s8sIIIIIIIIII20s')
MARKER_OFFSET = 0x4C
RAM_BYTES = 2 * 1024 * 1024
# Main RAM is visible through three CPU segments (KUSEG, KSEG0, KSEG1), and
# within each the 2 MiB repeats four times across the first 8 MiB.
RAM_WINDOW_BYTES = 8 * 1024 * 1024
RAM_MIRRORS = {0x00000000: 'kuseg', 0x80000000: 'kseg0', 0xA0000000: 'kseg1'}
KERNEL_RAM_BYTES = 0x10000
MAX_SYSTEM_CNF_BYTES = 2048
READ_CHUNK_BYTES = 1024 * 1024


class ExeFormatError(ValueError):
    """Malformed PS-X EXE or SYSTEM.CNF data this reader cannot validate."""


@dataclass(frozen=True)
class PsxExeHeader:
    initial_pc: int
    initial_gp: int
    text_address: int
    text_size: int
    data_address: int
    data_size: int
    bss_address: int
    bss_size: int
    stack_base: int
    stack_offset: int
    marker: str
    reserved_08_hex: str
    reserved_38_hex: str


@dataclass(frozen=True)
class PsxExe:
    header: PsxExeHeader
    ram_mirror: str
    text_end: int
    entry_in_text: bool
    warnings: tuple[str, ...]

    def to_report(self) -> dict:
        header = asdict(self.header)
        for name in ('initial_pc', 'initial_gp', 'text_address', 'data_address',
                     'bss_address', 'stack_base'):
            header[name] = f'0x{header[name]:08x}'
        return {
            'header': header,
            'ram_mirror': self.ram_mirror,
            'text_range': [header['text_address'], f'0x{self.text_end:08x}'],
            'entry_in_text': self.entry_in_text,
            'warnings': list(self.warnings),
        }


def _ram_offset(address: int) -> tuple[str, int] | None:
    """Return the CPU segment and the physical offset into the 2 MiB of RAM."""
    for base, name in RAM_MIRRORS.items():
        if base <= address < base + RAM_WINDOW_BYTES:
            return name, (address - base) % RAM_BYTES
    return None


def parse_psx_exe(header: bytes, file_size: int) -> PsxExe:
    """Validate a PS-X EXE header against its file size and the 2 MiB RAM map.

    `header` is at least the first 0x800 bytes. The text image must exactly
    fill the rest of the file, sit word-aligned inside one RAM mirror, and
    stay clear of the 64 KiB kernel area. Fields the reader does not interpret
    (data, BSS, reserved bytes) are preserved as raw values.
    """
    if len(header) < HEADER_BYTES or file_size < HEADER_BYTES:
        raise ExeFormatError('Truncated PS-X EXE: header needs 2048 bytes')
    (magic, reserved_08, pc, gp, t_addr, t_size, d_addr, d_size, b_addr,
     b_size, s_addr, s_size, reserved_38) = HEADER.unpack_from(header)
    if magic != MAGIC:
        raise ExeFormatError('Invalid PS-X EXE magic')
    if t_size % HEADER_BYTES:
        raise ExeFormatError(f'Text size 0x{t_size:x} is not a multiple of 2048')
    if file_size != HEADER_BYTES + t_size:
        raise ExeFormatError(
            f'File size {file_size} does not equal 2048 + text size {t_size}')
    if t_addr % 4 or pc % 4:
        raise ExeFormatError('Text address and entry point must be word aligned')
    located = _ram_offset(t_addr)
    if located is None:
        raise ExeFormatError(f'Text address 0x{t_addr:08x} is outside main RAM')
    mirror, offset = located
    if offset < KERNEL_RAM_BYTES:
        raise ExeFormatError(f'Text address 0x{t_addr:08x} overlaps kernel RAM')
    if offset + t_size > RAM_BYTES:
        raise ExeFormatError(
            f'Text range 0x{t_addr:08x}+0x{t_size:x} runs past the end of a 2 MiB RAM mirror')
    marker_bytes = header[MARKER_OFFSET:HEADER_BYTES].split(b'\x00', 1)[0]
    if any(byte < 0x20 or byte > 0x7E for byte in marker_bytes):
        raise ExeFormatError('Region marker is not printable ASCII')

    warnings: list[str] = []
    text_end = t_addr + t_size
    # Containment is judged on physical RAM, so any mirror of the text counts.
    entry = _ram_offset(pc)
    entry_in_text = entry is not None and offset <= entry[1] < offset + t_size
    if not entry_in_text:
        warnings.append('Entry point lies outside the text image.')
    if any(reserved_08) or any(reserved_38):
        warnings.append('Reserved header bytes are nonzero; preserved uninterpreted.')
    marker_end = MARKER_OFFSET + len(marker_bytes)
    if any(header[marker_end:HEADER_BYTES]):
        warnings.append('Bytes after the region marker are nonzero; not interpreted.')
    if d_addr or d_size or b_addr or b_size:
        warnings.append('Data or BSS fields are nonzero; no memory effect is assumed.')
    parsed = PsxExeHeader(pc, gp, t_addr, t_size, d_addr, d_size, b_addr,
                          b_size, s_addr, s_size, marker_bytes.decode('ascii'),
                          reserved_08.hex(), reserved_38.hex())
    return PsxExe(parsed, mirror, text_end, entry_in_text, tuple(warnings))


@dataclass(frozen=True)
class SystemCnf:
    boot: str
    boot_path: str
    boot_arguments: str
    settings: dict[str, str]
    ignored_duplicates: tuple[str, ...] = ()
    boot_version: int | None = None


def parse_system_cnf(data: bytes) -> SystemCnf:
    """Read KEY = VALUE lines and resolve BOOT to a root-relative ISO path.

    Only `cdrom:` boot paths are accepted. Leading backslashes are removed and
    subdirectories are returned with `/` separators. The `;n` version is kept
    apart in `boot_version`, to choose between versions of one name.
    Text after whitespace is an argument string, kept apart from the path.
    A repeated key keeps its first value, as the PlayStation OS documentation
    specifies; later lines are listed in `ignored_duplicates`.
    """
    if len(data) > MAX_SYSTEM_CNF_BYTES:
        raise ExeFormatError('SYSTEM.CNF exceeds the 2048-byte reader limit')
    try:
        text = data.decode('ascii')
    except UnicodeDecodeError as exc:
        raise ExeFormatError('SYSTEM.CNF is not ASCII') from exc
    settings: dict[str, str] = {}
    ignored: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        key, sep, value = line.partition('=')
        key = key.strip().upper()
        if not sep or not key:
            raise ExeFormatError(f'Malformed SYSTEM.CNF line: {line!r}')
        if key in settings:
            ignored.append(line.strip())
            continue
        settings[key] = value.strip()
    boot = settings.get('BOOT')
    if boot is None:
        raise ExeFormatError('SYSTEM.CNF has no BOOT line')
    found = re.fullmatch(r'(?i)cdrom:\\*([^;\s]+)(?:;(\d+))?(?:\s+(.*))?', boot)
    if not found:
        raise ExeFormatError(f'Unsupported BOOT path {boot!r}')
    boot_path = found.group(1).replace('\\', '/').upper()
    version = None if found.group(2) is None else int(found.group(2))
    return SystemCnf(boot, boot_path, found.group(3) or '', settings, tuple(ignored),
                     version)


def find_path_references(data: bytes, paths: list[str], text_address: int,
                         versions: dict[str, set[int]] | None = None) -> list[dict]:
    """Locate `\\NAME;n` ISO path strings for known files inside an executable.

    A match must start at a path boundary, so `\\DIR\\GAME.BIN;1` names only
    `DIR/GAME.BIN`, never a root `GAME.BIN`. A string reference is evidence
    that code may open the file; it does not prove when or how it is loaded.
    When `versions` lists a path's ISO versions, only those `;n` values count.
    """
    found: list[dict] = []
    for path in sorted(paths):
        pattern = re.compile(rb'(?<![\\\w])\\' + re.escape(path.replace('/', '\\').encode('latin-1'))
                             + rb';(\d+)(?!\w)', re.I)
        for match in pattern.finditer(data):
            if versions and path in versions and int(match.group(1)) not in versions[path]:
                continue
            offset = match.start()
            address = (text_address + offset - HEADER_BYTES
                       if offset >= HEADER_BYTES else None)
            found.append({
                'path': path,
                'string': match.group().decode('latin-1'),
                'file_offset': offset,
                'address': None if address is None else f'0x{address:08x}',
            })
    return found


def _physical_start(exe: dict) -> int:
    located = _ram_offset(int(exe['text_range'][0], 16))
    assert located is not None  # parse_psx_exe already rejected other addresses
    return located[1]


def _iso_version(raw_name: str) -> int | None:
    _, sep, version = raw_name.rpartition(';')
    return int(version) if sep and version.isdigit() else None


def _read_whole(disc: PlayStationDisc, entry: FileEntry) -> bytes:
    return b''.join(
        disc.read_file(entry, offset=offset,
                       length=min(READ_CHUNK_BYTES, entry.size - offset))
        for offset in range(0, entry.size, READ_CHUNK_BYTES))


def inspect_executables(path: Path) -> dict:
    """Find PS-X EXE files by content and report headers, hashes, and links.

    Files are recognized by magic, not by name. A file whose first sector is
    MODE2 Form 2 is skipped and listed; it is never read as Form 1 data.
    """
    disc = PlayStationDisc(path)
    inventory = disc.inspect()
    files = [FileEntry(**item) for item in inventory['files'] if not item['is_directory']]
    cnf_entries = [entry for entry in files if entry.path.upper() == 'SYSTEM.CNF']
    if len(cnf_entries) != 1:
        raise ExeFormatError(f'Expected exactly one root SYSTEM.CNF; found {len(cnf_entries)}')
    cnf_entry = cnf_entries[0]
    if cnf_entry.size > MAX_SYSTEM_CNF_BYTES:
        raise ExeFormatError('SYSTEM.CNF exceeds the 2048-byte reader limit')
    cnf = parse_system_cnf(disc.read_file(cnf_entry))
    boot_matches = [entry for entry in files if entry.path.upper() == cnf.boot_path]
    if cnf.boot_version is not None:
        boot_matches = [entry for entry in boot_matches
                        if _iso_version(entry.raw_name) in (None, cnf.boot_version)]
    if len(boot_matches) != 1:
        raise ExeFormatError(f'BOOT file {cnf.boot_path} not found exactly once in the ISO')

    paths = sorted({entry.path for entry in files})
    versions: dict[str, set[int]] = {}
    for entry in files:
        version = _iso_version(entry.raw_name)
        if version is not None:
            versions.setdefault(entry.path, set()).add(version)
    executables: list[dict] = []
    form2_skipped: list[str] = []
    for entry in files:
        if entry.size == 0:
            continue
        try:
            magic = disc.read_file(entry, length=min(len(MAGIC), entry.size))
        except Form2SectorError:
            form2_skipped.append(entry.path)
            continue
        if magic != MAGIC:
            continue
        header = disc.read_file(entry, length=min(HEADER_BYTES, entry.size))
        try:
            exe = parse_psx_exe(header, entry.size)
        except ExeFormatError as exc:
            raise ExeFormatError(f'{entry.path}: {exc}') from exc
        data = _read_whole(disc, entry)
        executables.append({
            'path': entry.path,
            'lba': entry.lba,
            'size_bytes': entry.size,
            'sha256': hashlib.sha256(data).hexdigest(),
            'is_boot': entry is boot_matches[0],
            **exe.to_report(),
            'path_references': find_path_references(
                data, paths, exe.header.text_address, versions),
        })
    if not any(item['is_boot'] for item in executables):
        raise ExeFormatError(f'BOOT file {cnf.boot_path} is not a PS-X EXE')

    overlaps = []
    for index, first in enumerate(executables):
        for second in executables[index + 1:]:
            a0, b0 = (_physical_start(exe) for exe in (first, second))
            a1, b1 = a0 + first['header']['text_size'], b0 + second['header']['text_size']
            if max(a0, b0) < min(a1, b1):
                overlaps.append([first['path'], second['path']])
    return {
        'schema_version': 1,
        'source_image': disc.path.name,
        'image_size_bytes': inventory['size_bytes'],
        'volume_id': inventory['volume_id'],
        'system_cnf': {'path': cnf_entry.path, 'boot': cnf.boot,
                       'boot_path': cnf.boot_path,
                       'boot_arguments': cnf.boot_arguments, 'settings': cnf.settings,
                       'ignored_duplicates': list(cnf.ignored_duplicates)},
        'executables': executables,
        'text_range_overlaps': overlaps,
        'form2_skipped': form2_skipped,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Read-only PS-X EXE header report')
    parser.add_argument('bin', type=Path, help='Local raw MODE2/2352 disc image')
    parser.add_argument('--json', action='store_true', help='Full metadata-only report')
    args = parser.parse_args(argv)
    try:
        report = inspect_executables(args.bin)
    except (OSError, DiscFormatError, ExeFormatError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    print(f"{report['source_image']}: BOOT = {report['system_cnf']['boot']}")
    for exe in report['executables']:
        header = exe['header']
        role = 'boot' if exe['is_boot'] else 'exe'
        print(f"{exe['path']} [{role}] {exe['size_bytes']} bytes, sha256 {exe['sha256']}")
        print(f"  text {exe['text_range'][0]}-{exe['text_range'][1]} "
              f"({exe['ram_mirror']}), entry {header['initial_pc']}, "
              f"stack {header['stack_base']}+0x{header['stack_offset']:x}")
        for ref in exe['path_references']:
            where = ref['address'] or f"file offset {ref['file_offset']}"
            print(f"  references {ref['string']} at {where}")
        for warning in exe['warnings']:
            print(f'  WARNING: {warning}')
    for first, second in report['text_range_overlaps']:
        print(f'WARNING: text ranges of {first} and {second} overlap')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
