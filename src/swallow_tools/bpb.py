"""Read-only structural walk of AC3 BPB entries: DOT1 tables and Ulz blocks.

Format evidence and limits: docs/bpb-format.md. Reports metadata and hashes
only; no asset bytes are written. Node kinds are structural classifications,
not decoded asset semantics.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import struct
import sys

from .bph import BphFormatError, parse_bph
from .disc import DiscFormatError, FileEntry, PlayStationDisc
from .ulz import MAGIC as ULZ_MAGIC, UlzFormatError, decompress_ulz

MAX_DEPTH = 12
MAX_TABLE_ENTRIES = 65536
MAX_ENTRY_OUTPUT = 64 * 1024 * 1024


class BpbFormatError(ValueError):
    """A BPB entry violates a structure this walker accepted, or a safety limit."""


@dataclass(frozen=True)
class BpbNode:
    """One byte range. Offsets are relative to the parent's (decoded) bytes.

    kind is 'dot1' (offset table passed every structural check), 'ulz'
    (block decoded with full stream accounting; its single child is the
    decoded payload at offset 0), 'empty' (zero-length table slot), or
    'unclassified' (no rule matched; meaning unknown).
    """
    kind: str
    offset: int
    size: int
    sha256: str
    children: tuple['BpbNode', ...] = ()
    ulz: dict | None = None
    signature_hint: str | None = None

    def to_report(self) -> dict:
        report = {'kind': self.kind, 'offset': self.offset, 'size': self.size,
                  'sha256': self.sha256}
        if self.ulz is not None:
            report['ulz'] = self.ulz
        if self.signature_hint is not None:
            report['signature_hint'] = self.signature_hint
        if self.children:
            report['children'] = [child.to_report() for child in self.children]
        return report


def parse_dot1(data: bytes) -> tuple[int, ...] | None:
    """Return child boundaries if data passes every DOT1 table check, else None.

    Accepted only if: 1 <= count <= limit; the table fits; the first offset is
    exactly 4 + 4*count (no size word, no padding); offsets are 4-byte aligned,
    nondecreasing, and within data. The returned tuple has count + 1 values;
    the last child ends at the end of data. Equal offsets mean empty slots.
    """
    if len(data) < 8:
        return None
    count = struct.unpack_from('<I', data)[0]
    if not 1 <= count <= MAX_TABLE_ENTRIES or 4 + 4 * count > len(data):
        return None
    offsets = struct.unpack_from(f'<{count}I', data, 4)
    if offsets[0] != 4 + 4 * count:
        return None
    if any(offset % 4 or offset > len(data) for offset in offsets):
        return None
    if any(left > right for left, right in zip(offsets, offsets[1:])):
        return None
    return offsets + (len(data),)


def signature_hint(data: bytes) -> str | None:
    """Unvalidated leaf hints; never treated as decoded formats."""
    if len(data) >= 8:
        first, second = struct.unpack_from('<II', data)
        if first == 0x10 and not second & ~0xB:
            return 'tim_header_candidate'
        if first == 0x41:
            return 'tmd_header_candidate'
    return None


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def walk_entry(data: bytes, *, offset: int = 0, depth: int = 0,
               budget: list[int] | None = None) -> BpbNode:
    """Classify data recursively: Ulz block, DOT1 table, or unclassified leaf."""
    if depth > MAX_DEPTH:
        raise BpbFormatError(f'BPB nesting exceeds the {MAX_DEPTH}-level safety limit')
    budget = [MAX_ENTRY_OUTPUT] if budget is None else budget
    digest = _sha256(data)
    if not data:
        return BpbNode('empty', offset, 0, digest)
    if data[:4] == ULZ_MAGIC:
        try:
            result = decompress_ulz(data)
        except UlzFormatError as exc:
            raise BpbFormatError(f'Ulz block at offset {offset}: {exc}') from exc
        budget[0] -= len(result.data)
        if budget[0] < 0:
            raise BpbFormatError('Decoded output for one BPB entry exceeds the safety limit')
        header = result.header
        info = {
            'version': header.version,
            'decompressed_size': header.decompressed_size,
            'disp_bits': header.disp_bits,
            'literal_offset': header.literal_offset,
            'reference_offset': header.reference_offset,
            'flag_words': result.flag_words,
            'unused_reference_flags': result.unused_reference_flags,
            'flag_pad_bytes': header.literal_offset - result.flag_end,
            'literal_pad_bytes': header.reference_offset - result.literal_end,
            'reference_pad_bytes': len(data) - result.reference_end,
            'nonzero_pad_bytes': sum(
                byte != 0 for byte in (data[result.flag_end:header.literal_offset]
                                       + data[result.literal_end:header.reference_offset]
                                       + data[result.reference_end:])),
        }
        child = walk_entry(result.data, depth=depth + 1, budget=budget)
        return BpbNode('ulz', offset, len(data), digest, (child,), info)
    bounds = parse_dot1(data)
    if bounds is None:
        return BpbNode('unclassified', offset, len(data), digest,
                       signature_hint=signature_hint(data))
    children = tuple(
        walk_entry(data[start:end], offset=start, depth=depth + 1, budget=budget)
        for start, end in zip(bounds, bounds[1:]))
    return BpbNode('dot1', offset, len(data), digest, children)


def count_kinds(node: BpbNode, counts: Counter | None = None) -> Counter:
    counts = Counter() if counts is None else counts
    counts[node.kind] += 1
    if node.kind == 'ulz':
        counts[f'ulz_v{node.ulz["version"]}'] += 1
        counts['ulz_decoded_bytes'] += node.ulz['decompressed_size']
        counts['ulz_nonzero_pad_blocks'] += bool(node.ulz['nonzero_pad_bytes'])
        counts['ulz_unused_reference_flag_blocks'] += bool(node.ulz['unused_reference_flags'])
    elif node.kind == 'unclassified':
        counts['unclassified_bytes'] += node.size
        if node.signature_hint:
            counts[node.signature_hint] += 1
    for child in node.children:
        count_kinds(child, counts)
    return counts


def tree_sha256(node: BpbNode) -> str:
    """Digest of the canonical JSON tree, for comparing walks across discs."""
    canonical = json.dumps(node.to_report(), sort_keys=True, separators=(',', ':'))
    return _sha256(canonical.encode('ascii'))


def inspect_bpb(path: Path, *, include_trees: bool = True) -> dict:
    """Walk every normal BPH entry's BPB range; metadata and hashes only."""
    disc = PlayStationDisc(path)
    inventory = disc.inspect()

    def find_file(name: str) -> FileEntry:
        candidates = [entry for entry in inventory['files'] if entry['path'] == name]
        if len(candidates) != 1:
            raise BpbFormatError(f'Expected exactly one root {name}; found {len(candidates)}')
        entry = FileEntry(**candidates[0])
        disc.validate_file_extent(entry)
        return entry

    bph, bpb = find_file('ACE.BPH'), find_file('ACE.BPB')
    index = parse_bph(disc.read_file(bph), bpb.size)
    totals: Counter = Counter()
    entries = []
    for entry in index.entries:
        if entry.kind != 'normal':
            entries.append({'index': entry.index, 'kind': entry.kind})
            totals['special_uninterpreted'] += 1
            continue
        data = disc.read_file(bpb, offset=entry.offset_bytes, length=entry.size_bytes)
        try:
            node = walk_entry(data)
        except BpbFormatError as exc:
            raise BpbFormatError(f'BPH entry {entry.index}: {exc}') from exc
        counts = count_kinds(node)
        totals.update(counts)
        record = {'index': entry.index, 'offset_bytes': entry.offset_bytes,
                  'size_bytes': entry.size_bytes, 'root_kind': node.kind,
                  'sha256': node.sha256, 'tree_sha256': tree_sha256(node),
                  'counts': dict(sorted(counts.items()))}
        if include_trees:
            record['tree'] = node.to_report()
        entries.append(record)
    return {
        'schema_version': 1,
        'source_image': disc.path.name,
        'volume_id': inventory['volume_id'],
        'bpb': {'path': bpb.path, 'size_bytes': bpb.size},
        'entry_count': len(entries),
        'totals': dict(sorted(totals.items())),
        'entries': entries,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description='Read-only AC3 ACE.BPB structure report (DOT1 tables, Ulz blocks)')
    parser.add_argument('bin', type=Path, help='Local raw MODE2/2352 disc image')
    parser.add_argument('--json', action='store_true', help='Metadata-only JSON report')
    parser.add_argument('--no-trees', action='store_true',
                        help='Omit per-node trees; keep per-entry digests and counts')
    args = parser.parse_args(argv)
    try:
        report = inspect_bpb(args.bin, include_trees=not args.no_trees)
    except (OSError, DiscFormatError, BphFormatError, BpbFormatError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    print(f"{report['source_image']}: {report['entry_count']} BPB entries walked")
    for key, value in report['totals'].items():
        print(f'{key:>28}: {value}')
    print('   INDEX  ROOT KIND            SIZE  NODES')
    for entry in report['entries']:
        if 'root_kind' not in entry:
            print(f"{entry['index']:8d}  {entry['kind']}")
            continue
        nodes = sum(entry['counts'].get(kind, 0)
                    for kind in ('dot1', 'ulz', 'empty', 'unclassified'))
        print(f"{entry['index']:8d}  {entry['root_kind']:<12} {entry['size_bytes']:12d} {nodes:6d}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
