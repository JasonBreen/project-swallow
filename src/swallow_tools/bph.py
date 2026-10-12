"""Read-only AC3 BPH index parsing; no asset extraction or decompression.

Format evidence and limits: docs/bph-format.md. This implementation is original
project code; it does not incorporate third-party unpacker implementations.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, replace
import hashlib
import json
from pathlib import Path
import struct
import sys

from .disc import DiscFormatError, FileEntry, PlayStationDisc

HEADER = struct.Struct('<4sIHHII')
RECORD = struct.Struct('<II')
MAX_INDEX_BYTES = 1024 * 1024
SECTOR_BYTES = 2048
SIZE_MASK = 0x7FFFF
NORMAL_BIT = 0x80000000
REQUIRED_BIT = 0x20000000
UNKNOWN_BIT = 0x40000000
UNSUPPORTED_BITS = 0x1FF80000


class BphFormatError(ValueError):
    """Malformed BPH data or a normal-record layout this reader cannot validate."""


@dataclass(frozen=True)
class BphHeader:
    magic: str
    archive_type: int
    unknown_08: int
    unknown_0a: int
    unknown_0c: int
    entry_count: int


@dataclass(frozen=True)
class BphEntry:
    index: int
    index_offset: int
    descriptor: int
    sector_word: int
    kind: str
    offset_bytes: int | None
    size_bytes: int | None
    unknown_bit30: bool | None
    gap_after_bytes: int | None = None


@dataclass(frozen=True)
class BphIndex:
    header: BphHeader
    bpb_size: int
    index_size: int
    entries: tuple[BphEntry, ...]
    warnings: tuple[str, ...]

    def to_report(self) -> dict:
        normal = [entry for entry in self.entries if entry.kind == 'normal']
        special_count = len(self.entries) - len(normal)
        return {
            'schema_version': 1,
            'header': asdict(self.header),
            'index_size_bytes': self.index_size,
            'accounted_index_bytes': HEADER.size + RECORD.size * len(self.entries),
            'bpb_size_bytes': self.bpb_size,
            'normal_entry_count': len(normal),
            'special_entry_count': special_count,
            'bounds_status': ('partial_special_records' if special_count
                              else 'normal_entries_validated'),
            'normal_payload_bytes': sum(entry.size_bytes for entry in normal),
            'bytes_outside_normal_entries': self.bpb_size - sum(
                entry.size_bytes for entry in normal),
            'leading_gap_bytes': (None if special_count else
                                  normal[0].offset_bytes if normal else self.bpb_size),
            'warnings': list(self.warnings),
            'entries': [asdict(entry) for entry in self.entries],
        }


def parse_bph(data: bytes, bpb_size: int) -> BphIndex:
    """Decode an index and validate normal entry ranges against logical BPB size.

    Normal entries have bit 31 set, a 19-bit length in four-byte units, and a
    32-bit sector offset. Unknown special records retain both raw words but
    receive no invented byte range. No fixed entry count or disc LBA is used.
    """
    if not isinstance(bpb_size, int) or isinstance(bpb_size, bool) or bpb_size < 0:
        raise BphFormatError('BPB size must be a nonnegative integer')
    if len(data) < HEADER.size:
        raise BphFormatError('Truncated BPH header: expected at least 20 bytes')
    if len(data) > MAX_INDEX_BYTES:
        raise BphFormatError('BPH index exceeds the 1 MiB reader limit')
    magic, archive_type, u08, u0a, u0c, count = HEADER.unpack_from(data)
    if magic != b'AC3E':
        raise BphFormatError('Invalid BPH magic: expected AC3E')
    if archive_type != 3:
        raise BphFormatError(f'Unsupported archive type {archive_type}; BPH requires 3')
    expected_size = HEADER.size + count * RECORD.size
    if len(data) != expected_size:
        raise BphFormatError(
            f'BPH count/length mismatch: {count} records require {expected_size} '
            f'bytes, received {len(data)}')

    entries: list[BphEntry] = []
    warnings: list[str] = []
    previous_normal: BphEntry | None = None
    for index in range(count):
        position = HEADER.size + index * RECORD.size
        descriptor, sector = RECORD.unpack_from(data, position)
        if not descriptor & NORMAL_BIT:
            entries.append(BphEntry(index, position, descriptor, sector,
                                    'special_uninterpreted', None, None, None))
            continue
        if not descriptor & REQUIRED_BIT or descriptor & UNSUPPORTED_BITS:
            raise BphFormatError(
                f'Entry {index}: unsupported normal BPH flags 0x{descriptor:08x}')
        offset = sector * SECTOR_BYTES
        size = (descriptor & SIZE_MASK) * 4
        if offset + size > bpb_size:
            raise BphFormatError(
                f'Entry {index}: range [{offset}, {offset + size}) exceeds BPB size {bpb_size}')
        if previous_normal is not None:
            if offset < previous_normal.offset_bytes:
                raise BphFormatError(f'Entry {index}: normal offsets are not ordered')
            if offset < previous_normal.offset_bytes + previous_normal.size_bytes:
                raise BphFormatError(f'Entry {index}: normal ranges overlap')
        entry = BphEntry(index, position, descriptor, sector, 'normal', offset,
                         size, bool(descriptor & UNKNOWN_BIT))
        entries.append(entry)
        previous_normal = entry

    special_count = sum(entry.kind != 'normal' for entry in entries)
    if special_count:
        warnings.append(
            'Special records are uninterpreted; only normal entry bounds were '
            'validated. Their effect on storage is unknown. Gap values are omitted.')
    else:
        for index, entry in enumerate(entries):
            next_offset = (entries[index + 1].offset_bytes
                           if index + 1 < len(entries) else bpb_size)
            entries[index] = replace(
                entry, gap_after_bytes=next_offset - entry.offset_bytes - entry.size_bytes)
    if any(entry.kind == 'normal' and entry.size_bytes == 0 for entry in entries):
        warnings.append('Zero-length normal entries are preserved; no sentinel meaning is assumed.')
    header = BphHeader('AC3E', archive_type, u08, u0a, u0c, count)
    return BphIndex(header, bpb_size, len(data), tuple(entries), tuple(warnings))


def inspect_bph(path: Path) -> dict:
    """Discover ACE.BPH/BPB via ISO metadata and emit only index metadata.

    BPB bounds use the ISO-recorded logical size. This does not read or verify
    every BPB payload sector, decompress assets, or validate game behavior.
    """
    disc = PlayStationDisc(path)
    inventory = disc.inspect()

    def find_file(name: str) -> FileEntry:
        candidates = [entry for entry in inventory['files'] if entry['path'] == name]
        if len(candidates) != 1:
            raise BphFormatError(f'Expected exactly one root {name}; found {len(candidates)}')
        entry = FileEntry(**candidates[0])
        disc.validate_file_extent(entry)
        return entry

    bph = find_file('ACE.BPH')
    bpb = find_file('ACE.BPB')
    if bph.size > MAX_INDEX_BYTES:
        raise BphFormatError('BPH index exceeds the 1 MiB reader limit')
    bph_end = bph.lba + (bph.size + SECTOR_BYTES - 1) // SECTOR_BYTES
    bpb_end = bpb.lba + (bpb.size + SECTOR_BYTES - 1) // SECTOR_BYTES
    if bph.size and bpb.size and max(bph.lba, bpb.lba) < min(bph_end, bpb_end):
        raise BphFormatError('ACE.BPH and ACE.BPB ISO extents overlap')
    data = disc.read_file(bph)
    index = parse_bph(data, bpb.size)
    return {
        'source_image': disc.path.name,
        'image_size_bytes': inventory['size_bytes'],
        'volume_id': inventory['volume_id'],
        'bph': {'path': bph.path, 'lba': bph.lba, 'size_bytes': bph.size,
                'sha256': hashlib.sha256(data).hexdigest()},
        'bpb': {'path': bpb.path, 'lba': bpb.lba, 'size_bytes': bpb.size,
                'validation': 'ISO extent bounds only; BPB payload not read'},
        **index.to_report(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Read-only AC3 ACE.BPH index report')
    parser.add_argument('bin', type=Path, help='Local raw MODE2/2352 disc image')
    parser.add_argument('--json', action='store_true', help='Full metadata-only index report')
    args = parser.parse_args(argv)
    try:
        report = inspect_bph(args.bin)
    except (OSError, DiscFormatError, BphFormatError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"{report['source_image']}: {report['normal_entry_count']} normal entries, "
              f"{report['special_entry_count']} uninterpreted special records")
        print(f"BPH: {report['index_size_bytes']} bytes; BPB: {report['bpb_size_bytes']} bytes")
        print(f"Bounds status: {report['bounds_status']}")
        print('   INDEX       OFFSET         SIZE    GAP AFTER')
        for entry in report['entries']:
            if entry['kind'] == 'normal':
                gap = '-' if entry['gap_after_bytes'] is None else str(entry['gap_after_bytes'])
                print(f"{entry['index']:8d} {entry['offset_bytes']:12d} "
                      f"{entry['size_bytes']:12d} {gap:>12}")
            else:
                print(f"{entry['index']:8d} special_uninterpreted "
                      f"0x{entry['descriptor']:08x} 0x{entry['sector_word']:08x}")
        for warning in report['warnings']:
            print(f'WARNING: {warning}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
