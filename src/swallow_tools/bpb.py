"""Read-only walker for nested AC3 BPB entry containers; no asset export.

Each BPH entry's payload is classified as an offset-table container (the
psx-spx "DOT1" shape: count, then count offsets whose first value is
4 + 4 * count), a ``Ulz`` compressed leaf, a validated PlayStation ``tim``
image leaf (metadata only), or an opaque leaf that this tool does not
interpret. Format evidence and limits: docs/bpb-format.md, docs/tim-format.md.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import struct
import sys

from .bph import BphFormatError, SECTOR_BYTES, locate_archive_files, parse_bph
from .disc import DiscFormatError, PlayStationDisc
from .tim import TimFormatError, is_tim_candidate, parse_tim
from .ulz import UlzFormatError, decompress_ulz, is_ulz, parse_ulz_header

MAX_DEPTH = 8
MAX_NODES = 100_000
MAX_ENTRY_BYTES = 16 * 1024 * 1024
TABLE_ALIGNMENT = 4


class ContainerFormatError(ValueError):
    """Data is not an offset-table container this walker validates."""


class ContainerLimitError(ContainerFormatError):
    """A walker resource limit was reached; the walk stops instead of classifying."""


@dataclass(frozen=True)
class OffsetTable:
    count: int
    offsets: tuple[int, ...]
    items: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class Node:
    path: tuple[int, ...]
    offset: int
    size: int
    kind: str
    signature: str
    origin: str = 'stored'
    children: tuple['Node', ...] = ()
    ulz: dict | None = None
    tim: dict | None = None
    decoded: 'Node | None' = None
    error: str | None = None

    def to_report(self) -> dict:
        report = {
            'path': '.'.join(str(part) for part in self.path),
            'origin': self.origin,
            'offset': self.offset,
            'size': self.size,
            'kind': self.kind,
            'signature': self.signature,
        }
        if self.kind == 'container':
            report['child_count'] = len(self.children)
            report['children'] = [child.to_report() for child in self.children]
        if self.ulz is not None:
            report['ulz'] = self.ulz
        if self.tim is not None:
            report['tim'] = self.tim
        if self.decoded is not None:
            report['decoded'] = self.decoded.to_report()
        if self.error is not None:
            report['error'] = self.error
        return report


def parse_offset_table(data: bytes, *, max_count: int | None = None) -> OffsetTable:
    """Validate a count-prefixed offset table; offsets are relative to data[0].

    Rules measured on all four references: count >= 1, first offset equals
    4 + 4 * count, offsets are four-byte aligned, nondecreasing, and end
    within the data. The final item extends to the end of the data. A leaf
    whose first words happen to satisfy these rules is indistinguishable
    from a container; callers must treat the classification as structural.

    With ``max_count``, a table that passes the cheap header checks but
    declares more items raises ContainerLimitError before any offsets are
    unpacked, so a huge declared count cannot force a large allocation.
    """
    if len(data) < 8:
        raise ContainerFormatError('Fewer than 8 bytes cannot hold a count and offset')
    count, first = struct.unpack_from('<II', data)
    if count == 0:
        raise ContainerFormatError('Offset table count is zero')
    header_size = 4 + 4 * count
    if header_size > len(data):
        raise ContainerFormatError(f'Offset table count {count} exceeds {len(data)} bytes')
    if first != header_size:
        raise ContainerFormatError(
            f'First offset {first} does not equal header size {header_size}')
    if max_count is not None and count > max_count:
        raise ContainerLimitError(
            f'Offset table declares {count} items, exceeding the remaining node budget '
            f'of {max_count}')
    offsets = struct.unpack_from(f'<{count}I', data, 4)
    previous = 0
    for position, offset in enumerate(offsets):
        if offset % TABLE_ALIGNMENT:
            raise ContainerFormatError(f'Offset {position} ({offset}) is not four-byte aligned')
        if offset < previous:
            raise ContainerFormatError(f'Offset {position} ({offset}) precedes offset {previous}')
        previous = offset
    if offsets[-1] > len(data):
        raise ContainerFormatError(f'Last offset {offsets[-1]} exceeds {len(data)} bytes')
    ends = tuple(offsets[1:]) + (len(data),)
    return OffsetTable(count, tuple(offsets), tuple(zip(offsets, ends)))


class _Budget:
    def __init__(self, max_nodes: int):
        self.limit = max_nodes
        self.remaining = max_nodes

    def reserve(self, count: int = 1) -> None:
        if count > self.remaining:
            raise ContainerLimitError(f'Node count exceeds the {self.limit} limit')
        self.remaining -= count


def walk_payload(data: bytes, *, decode: bool = False, path: tuple[int, ...] = (),
                 max_depth: int = MAX_DEPTH, max_nodes: int = MAX_NODES) -> Node:
    """Classify one entry payload recursively without exporting any bytes.

    With ``decode`` the walker decompresses each Ulz leaf, records its stream
    accounting and payload SHA256, and classifies the decoded bytes with the
    same rules. Decoding failures are recorded on the node rather than raised
    so a report can still describe the rest of the entry.

    Every node is charged to the budget before it is built: a container
    reserves all of its declared children before descending into any of them,
    so nested tables cannot together exceed ``max_nodes``.
    """
    budget = _Budget(max_nodes)
    budget.reserve()
    return _walk(data, decode, path, 0, 0, 'stored', max_depth, budget)


def _walk(data: bytes, decode: bool, path: tuple[int, ...], offset: int, depth: int,
          origin: str, max_depth: int, budget: _Budget, *, aligned_item: bool = False) -> Node:
    """Build one node that the caller has already charged to the budget.

    ``aligned_item`` is true only for a container item that is not the last
    one: its end is the next table offset, so the container proves that any
    bytes between the item's own data and that offset are alignment padding.
    """
    signature = data[:4].hex()
    if is_ulz(data):
        return _ulz_node(data, decode, path, offset, depth, origin, max_depth, budget)
    try:
        table = parse_offset_table(data, max_count=budget.remaining)
    except ContainerLimitError:
        raise
    except ContainerFormatError:
        return _leaf_node(data, path, offset, origin, aligned_item=aligned_item)
    if depth >= max_depth:
        raise ContainerFormatError(f'Container nesting exceeds depth {max_depth}')
    budget.reserve(table.count)
    children = []
    last = len(table.items) - 1
    for index, (start, end) in enumerate(table.items):
        if end > start:
            children.append(_walk(data[start:end], decode, path + (index,), start,
                                  depth + 1, origin, max_depth, budget,
                                  aligned_item=index < last))
        else:
            children.append(Node(path + (index,), start, 0, 'empty', '', origin))
    children = tuple(children)
    return Node(path, offset, len(data), 'container', signature, origin, children)


def _leaf_node(data: bytes, path: tuple[int, ...], offset: int, origin: str, *,
               aligned_item: bool = False) -> Node:
    """Classify a non-container, non-Ulz range: a validated TIM or an opaque leaf.

    A range that starts with the TIM id word but fails validation stays
    opaque and records the rejection reason, so candidate mismatches can be
    counted without claiming the format. Trailing zero bytes are accepted
    only where the container proves they are alignment padding: in a
    non-final item whose range is a whole number of four-byte units, and
    then only fewer than four of them, so they are exactly the bytes up to
    the next aligned offset. They are reported as ``trailing_bytes``. A
    final item, a decoded payload, or a top-level entry gets the strict rule.
    """
    signature = data[:4].hex()
    if not is_tim_candidate(data):
        return Node(path, offset, len(data), 'opaque', signature, origin)
    padding_proven = aligned_item and len(data) % TABLE_ALIGNMENT == 0
    try:
        info = parse_tim(data, max_trailing_bytes=TABLE_ALIGNMENT - 1 if padding_proven else 0)
    except TimFormatError as exc:
        return Node(path, offset, len(data), 'opaque', signature, origin,
                    error=f'TIM candidate rejected: {exc}')
    return Node(path, offset, len(data), 'tim', signature, origin, tim=info.to_report())


def _ulz_node(data: bytes, decode: bool, path: tuple[int, ...], offset: int, depth: int,
              origin: str, max_depth: int, budget: _Budget) -> Node:
    signature = data[:4].hex()
    try:
        header = parse_ulz_header(data)
    except UlzFormatError as exc:
        return Node(path, offset, len(data), 'ulz', signature, origin, error=str(exc))
    info = {'header': {'decompressed_size': header.decompressed_size,
                       'version': header.version,
                       'literal_offset': header.literal_offset,
                       'offset_bits': header.offset_bits,
                       'length_bits': header.length_bits,
                       'pair_offset': header.pair_offset},
            'decoded': False}
    if not decode:
        return Node(path, offset, len(data), 'ulz', signature, origin, ulz=info)
    try:
        result = decompress_ulz(data)
    except UlzFormatError as exc:
        return Node(path, offset, len(data), 'ulz', signature, origin, ulz=info,
                    error=str(exc))
    info = result.to_report()
    info['decoded'] = True
    info['payload_sha256'] = hashlib.sha256(result.payload).hexdigest()
    if depth >= max_depth:
        raise ContainerFormatError(f'Decoded nesting exceeds depth {max_depth}')
    budget.reserve()
    decoded = _walk(result.payload, decode, path, 0, depth + 1, 'decoded', max_depth, budget)
    return Node(path, offset, len(data), 'ulz', signature, origin, ulz=info, decoded=decoded)


def summarize_nodes(roots: list[Node]) -> dict:
    """Aggregate counts over node trees; metadata only."""
    summary = {
        'stored_nodes': 0, 'decoded_nodes': 0, 'max_depth': 0,
        'kinds': {}, 'decoded_kinds': {},
        'ulz_versions': {}, 'ulz_offset_bits': {},
        'ulz_decoded': 0, 'ulz_errors': 0,
        'ulz_compressed_bytes': 0, 'ulz_decompressed_bytes': 0,
        'flag_words_unused': {}, 'unused_flag_words_nonzero': 0,
        'v0_final_word_zero_padding_blobs': 0,
        'literal_bytes_unused': {}, 'pair_bytes_unused': {},
        'tim_modes': {}, 'tim_with_clut': 0, 'tim_bytes': 0,
        'tim_candidates_rejected': 0,
    }

    def bump(table: dict, key) -> None:
        table[str(key)] = table.get(str(key), 0) + 1

    def visit(node: Node, depth: int) -> None:
        stored = node.origin == 'stored'
        summary['stored_nodes' if stored else 'decoded_nodes'] += 1
        summary['max_depth'] = max(summary['max_depth'], depth)
        bump(summary['kinds'] if stored else summary['decoded_kinds'], node.kind)
        if node.kind == 'ulz':
            if node.error:
                summary['ulz_errors'] += 1
            if node.ulz:
                bump(summary['ulz_versions'], node.ulz['header']['version'])
                bump(summary['ulz_offset_bits'], node.ulz['header']['offset_bits'])
                summary['ulz_compressed_bytes'] += node.size
                if node.ulz.get('decoded'):
                    summary['ulz_decoded'] += 1
                    summary['ulz_decompressed_bytes'] += node.ulz['header']['decompressed_size']
                    bump(summary['flag_words_unused'], node.ulz['flag_words_unused'])
                    if (node.ulz['header']['version'] == 0
                            and node.ulz['final_word_unused_flag_bits']):
                        summary['v0_final_word_zero_padding_blobs'] += 1
                    bump(summary['literal_bytes_unused'], node.ulz['literal_bytes_unused'])
                    bump(summary['pair_bytes_unused'], node.ulz['pair_bytes_unused'])
                    if not node.ulz['unused_flag_words_zero']:
                        summary['unused_flag_words_nonzero'] += 1
        if node.kind == 'tim' and node.tim:
            bump(summary['tim_modes'], node.tim['mode'])
            summary['tim_bytes'] += node.size
            if 'clut' in node.tim:
                summary['tim_with_clut'] += 1
        if node.kind == 'opaque' and node.error:
            summary['tim_candidates_rejected'] += 1
        for child in node.children:
            visit(child, depth + 1)
        if node.decoded is not None:
            visit(node.decoded, depth + 1)

    for root in roots:
        visit(root, 0)
    return summary


def inspect_bpb(path: Path, *, entries: list[int] | None = None,
                decode: bool = False) -> dict:
    """Walk BPB entries discovered through the ISO directory and BPH index."""
    disc = PlayStationDisc(path)
    inventory = disc.inspect()
    bph, bpb = locate_archive_files(disc, inventory)
    index = parse_bph(disc.read_file(bph), bpb.size)
    wanted = None if entries is None else set(entries)
    selected = [entry for entry in index.entries if entry.kind == 'normal'
                and (wanted is None or entry.index in wanted)]
    if wanted is not None and len(selected) != len(wanted):
        raise BphFormatError('Requested entry indices are not all normal BPH records')
    roots: list[Node] = []
    reports = []
    for entry in selected:
        if entry.size_bytes > MAX_ENTRY_BYTES:
            raise ContainerFormatError(
                f'Entry {entry.index} exceeds the {MAX_ENTRY_BYTES} byte walker limit')
        data = disc.read_file(bpb, offset=entry.offset_bytes, length=entry.size_bytes)
        node = walk_payload(data, decode=decode, path=(entry.index,))
        roots.append(node)
        reports.append({'index': entry.index, 'bpb_offset': entry.offset_bytes,
                        'size': entry.size_bytes,
                        'sha256': hashlib.sha256(data).hexdigest(),
                        'tree': node.to_report()})
    return {
        'schema_version': 2,
        'source_image': disc.path.name,
        'volume_id': inventory['volume_id'],
        'bph': {'lba': bph.lba, 'size_bytes': bph.size},
        'bpb': {'lba': bpb.lba, 'size_bytes': bpb.size, 'sector_bytes': SECTOR_BYTES},
        'decode': decode,
        'entry_count': len(reports),
        'summary': summarize_nodes(roots),
        'entries': reports,
    }


def _print_tree(node: dict, indent: int) -> None:
    label = f"{'  ' * indent}[{node['path']}] {node['kind']} {node['size']} B"
    if node['origin'] == 'decoded':
        label += ' (decoded)'
    if node['kind'] == 'ulz' and node.get('ulz'):
        header = node['ulz']['header']
        label += (f" v{header['version']} -> {header['decompressed_size']} B,"
                  f" {header['offset_bits']}-bit offsets")
    elif node['kind'] == 'tim':
        tim = node['tim']
        label += f" {tim['mode']} {tim['pixel_width']}x{tim['pixel_height']}"
        if 'clut' in tim:
            label += f" clut={tim['clut_entries']}"
    elif node['kind'] == 'opaque':
        label += f" signature {node['signature']}"
    if node.get('error'):
        label += f" ERROR: {node['error']}"
    print(label)
    for child in node.get('children', []):
        _print_tree(child, indent + 1)
    if node.get('decoded'):
        _print_tree(node['decoded'], indent + 1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description='Read-only AC3 ACE.BPB nested container report (metadata only)')
    parser.add_argument('bin', type=Path, help='Local raw MODE2/2352 disc image')
    parser.add_argument('--entry', type=int, action='append',
                        help='BPH entry index to walk; repeatable (default: all)')
    parser.add_argument('--decode', action='store_true',
                        help='Decompress Ulz leaves in memory and classify their payloads')
    parser.add_argument('--json', action='store_true', help='Full metadata-only report')
    parser.add_argument('--tree', action='store_true', help='Print every node (text mode)')
    args = parser.parse_args(argv)
    try:
        report = inspect_bpb(args.bin, entries=args.entry, decode=args.decode)
    except (OSError, DiscFormatError, BphFormatError, ContainerFormatError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    summary = report['summary']
    print(f"{report['source_image']}: {report['entry_count']} entries walked; "
          f"{summary['stored_nodes']} stored nodes, {summary['decoded_nodes']} decoded nodes")
    print(f"Kinds: {summary['kinds']}; Ulz versions: {summary['ulz_versions']}; "
          f"Ulz errors: {summary['ulz_errors']}")
    print(f"TIM modes: {summary['tim_modes']}; with CLUT: {summary['tim_with_clut']}; "
          f"candidates rejected: {summary['tim_candidates_rejected']}")
    for entry in report['entries']:
        tree = entry['tree']
        if args.tree:
            _print_tree(tree, 0)
        else:
            print(f"{entry['index']:5d} {entry['size']:9d} B {tree['kind']:9s} "
                  f"children={tree.get('child_count', 0)}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
