"""Optional local-disc BPB container/Ulz validation; synthetic CI never needs these inputs.

Run from the repository with PYTHONPATH=src. Supply one or more
--reference jp1 /path/to/image.bin pairs (keys: jp1, jp2, en1, en2). The
script checks the whole-image and BPH/BPB logical hashes against the
checked-in baselines, walks every normal entry with Ulz decoding, fails if
any Ulz blob is rejected, counts validated TIM leaves and rejected TIM
candidates, measures the header words of the unexplained ``00002311``
leaves in a second in-memory pass, and writes a metadata summary. No
decoded payload is written to disk.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import struct

from swallow_tools.bph import parse_bph
from swallow_tools.bpb import ContainerFormatError, inspect_bpb, parse_offset_table
from swallow_tools.disc import FileEntry, PlayStationDisc, file_hashes
from swallow_tools.ulz import decompress_ulz, is_ulz

KEYS = ('jp1', 'jp2', 'en1', 'en2')
SIGNATURE_2311 = bytes.fromhex('00002311')
HEADER_2311 = struct.Struct('<5I')


def logical_sha256(disc: PlayStationDisc, entry: FileEntry) -> str:
    digest = hashlib.sha256()
    for offset in range(0, entry.size, 4 * 1024 * 1024):
        digest.update(disc.read_file(entry, offset=offset,
                                     length=min(4 * 1024 * 1024, entry.size - offset)))
    return digest.hexdigest()


def collect(node: dict, stats: dict) -> None:
    kind, origin = node['kind'], node['origin']
    if kind == 'opaque':
        stats['opaque_signatures_' + origin][node['signature']] += 1
        if node.get('error'):
            stats['tim_rejected'] += 1
            stats['tim_rejected_' + origin] += 1
            stats['tim_rejection_reasons'][re.sub(r'\d+', '#', node['error'])] += 1
            if len(stats['tim_rejected_examples']) < 40:
                stats['tim_rejected_examples'].append(
                    {'path': node['path'], 'origin': origin, 'size': node['size'],
                     'error': node['error']})
    if kind == 'tim':
        tim = node['tim']
        stats['tim_' + origin] += 1
        stats['tim_modes'][tim['mode']] += 1
        stats['tim_flags'][str(tim['flags'])] += 1
        stats['tim_bytes'] += node['size']
        stats['tim_pixel_dims'][f"{tim['pixel_width']}x{tim['pixel_height']}"] += 1
        if tim['trailing_bytes']:
            stats['tim_trailing_padded'] += 1
        if 'clut' in tim:
            stats['tim_clut_dims'][f"{tim['clut']['width']}x{tim['clut']['height']}"] += 1
            if tim['clut']['x'] % 16:
                stats['tim_clut_x_not_16_aligned'] += 1
    if kind == 'ulz':
        info = node.get('ulz') or {}
        if node.get('error'):
            stats['ulz_error_paths'].append(node['path'] + ': ' + node['error'])
        elif info.get('decoded'):
            stats['decoded_payload_kinds'][node['decoded']['kind']] += 1
            stats['max_decompressed'] = max(stats['max_decompressed'],
                                            info['header']['decompressed_size'])
            stats['literal_count'] += info['literal_count']
            stats['match_count'] += info['match_count']
    for child in node.get('children', []):
        collect(child, stats)
    if node.get('decoded'):
        collect(node['decoded'], stats)


def iter_leaves(data: bytes, origin: str, path: str):
    """Yield (origin, path, bytes) for every non-container, non-Ulz range."""
    if is_ulz(data):
        yield from iter_leaves(decompress_ulz(data).payload, 'decoded', path)
        return
    try:
        table = parse_offset_table(data)
    except ContainerFormatError:
        yield origin, path, data
        return
    for index, (start, end) in enumerate(table.items):
        if end > start:
            yield from iter_leaves(data[start:end], origin, f'{path}.{index}')


def measure_2311(disc: PlayStationDisc, bph: FileEntry, bpb: FileEntry) -> dict:
    """Measure the open ``00002311`` hypothesis (docs/tim-format.md); no semantics claimed."""
    index = parse_bph(disc.read_file(bph), bpb.size)
    checks = Counter()
    sizes = Counter()
    origins = Counter()
    parents = Counter()
    word3_strides = Counter()
    tail_strides = Counter()
    count = 0
    for entry in index.entries:
        if entry.kind != 'normal':
            continue
        data = disc.read_file(bpb, offset=entry.offset_bytes, length=entry.size_bytes)
        for origin, path, leaf in iter_leaves(data, 'stored', str(entry.index)):
            if leaf[:4] != SIGNATURE_2311:
                continue
            count += 1
            origins[origin] += 1
            sizes[len(leaf)] += 1
            parents[path.rsplit('.', 1)[0]] += 1
            if len(leaf) < HEADER_2311.size:
                checks['shorter_than_20_bytes'] += 1
                continue
            _, word1, word2, word3, word4 = HEADER_2311.unpack_from(leaf)
            checks['word1_is_24'] += word1 == 24
            checks['word2_is_0'] += word2 == 0
            checks['word3_is_68_plus_20n'] += word3 >= 68 and (word3 - 68) % 20 == 0
            checks['word3_lt_word4'] += word3 < word4
            checks['word4_lt_size'] += word4 < len(leaf)
            checks['word4_aligned_4'] += word4 % 4 == 0
            checks['size_minus_word4_is_4_plus_60n'] += (
                len(leaf) >= word4 + 4 and (len(leaf) - word4 - 4) % 60 == 0)
            checks['size_aligned_4'] += len(leaf) % 4 == 0
            checks['last_word_is_0'] += leaf[-4:] == bytes(4)
            word3_strides[(word3 - 68) // 20] += 1
            tail_strides[(len(leaf) - word4 - 4) // 60] += 1
    return {
        'count': count,
        'origins': dict(origins),
        'size_min': min(sizes) if sizes else None,
        'size_max': max(sizes) if sizes else None,
        'sizes_top20': sizes.most_common(20),
        'parent_containers': len(parents),
        'header_checks': dict(checks),
        'word3_stride_counts_top10': word3_strides.most_common(10),
        'tail_stride_counts_top10': tail_strides.most_common(10),
    }


def validate(key: str, path: Path, baseline_dir: Path) -> dict:
    baseline = json.loads((baseline_dir / f'{key}.json').read_text(encoding='utf-8'))
    baseline_files = {entry['path']: entry for entry in baseline['files']}
    # Archive files are shared between discs (both Japanese BPBs are identical),
    # so reference identity must come from the whole-image fingerprint.
    if file_hashes(path) != baseline['hashes']:
        raise ValueError(f'{key}: whole-image hashes differ from baseline')
    disc = PlayStationDisc(path)
    entries = {entry['path']: FileEntry(**entry) for entry in disc.inspect()['files']}
    for name in ('ACE.BPH', 'ACE.BPB'):
        measured = logical_sha256(disc, entries[name])
        if measured != baseline_files[name]['content_hash']['sha256']:
            raise ValueError(f'{key}: {name} logical hash differs from baseline')
    report = inspect_bpb(path, decode=True)
    stats = {'opaque_signatures_stored': Counter(), 'opaque_signatures_decoded': Counter(),
             'decoded_payload_kinds': Counter(), 'ulz_error_paths': [],
             'max_decompressed': 0, 'literal_count': 0, 'match_count': 0,
             'tim_stored': 0, 'tim_decoded': 0, 'tim_modes': Counter(), 'tim_flags': Counter(),
             'tim_bytes': 0, 'tim_pixel_dims': Counter(), 'tim_clut_dims': Counter(),
             'tim_trailing_padded': 0, 'tim_clut_x_not_16_aligned': 0,
             'tim_rejected': 0, 'tim_rejected_stored': 0, 'tim_rejected_decoded': 0,
             'tim_rejection_reasons': Counter(), 'tim_rejected_examples': []}
    per_entry = []
    for entry in report['entries']:
        tree = entry['tree']
        collect(tree, stats)
        per_entry.append({'index': entry['index'], 'size': entry['size'],
                          'kind': tree['kind'], 'child_count': tree.get('child_count', 0),
                          'signature': tree['signature']})
    if stats['ulz_error_paths'] or report['summary']['ulz_errors']:
        raise ValueError(f'{key}: {report["summary"]["ulz_errors"]} Ulz blob(s) failed to '
                         f'decode: {stats["ulz_error_paths"][:5]}')
    non_container = [entry['index'] for entry in per_entry if entry['kind'] != 'container']
    tim = {
        'count': stats['tim_stored'] + stats['tim_decoded'],
        'stored': stats['tim_stored'],
        'decoded': stats['tim_decoded'],
        'modes': dict(stats['tim_modes']),
        'flags': dict(stats['tim_flags']),
        'bytes': stats['tim_bytes'],
        'trailing_padded': stats['tim_trailing_padded'],
        'clut_x_not_16_aligned': stats['tim_clut_x_not_16_aligned'],
        'pixel_dims_top20': stats['tim_pixel_dims'].most_common(20),
        'clut_dims': stats['tim_clut_dims'].most_common(20),
        'candidates_rejected': stats['tim_rejected'],
        'candidates_rejected_stored': stats['tim_rejected_stored'],
        'candidates_rejected_decoded': stats['tim_rejected_decoded'],
        'rejection_reasons': dict(stats['tim_rejection_reasons']),
        'rejected_examples': stats['tim_rejected_examples'],
    }
    return {
        'schema_version': 2,
        'baseline_report': f'docs/reports/{key}.json',
        'source_image': report['source_image'],
        'whole_image_hashes_match_baseline': True,
        'bph_bpb_logical_hashes_match_baseline': True,
        'entry_count': report['entry_count'],
        'top_level_kinds': dict(Counter(entry['kind'] for entry in per_entry)),
        'top_level_non_container_entries': non_container,
        'summary': report['summary'],
        'decoded_payload_kinds': dict(stats['decoded_payload_kinds']),
        'max_decompressed_bytes': stats['max_decompressed'],
        'literal_count': stats['literal_count'],
        'match_count': stats['match_count'],
        'ulz_error_paths': stats['ulz_error_paths'],
        'tim': tim,
        'signature_00002311': measure_2311(disc, entries['ACE.BPH'], entries['ACE.BPB']),
        'opaque_signatures_stored_top20': stats['opaque_signatures_stored'].most_common(20),
        'opaque_signatures_decoded_top20': stats['opaque_signatures_decoded'].most_common(20),
        'entries': per_entry,
    }, report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', nargs=2, action='append', required=True,
                        metavar=('KEY', 'BIN'))
    parser.add_argument('--output-dir', type=Path, required=True,
                        help='Ignored local directory for the full tree reports')
    parser.add_argument('--summary-dir', type=Path,
                        help='Optional directory for compact summaries (e.g. docs/reports)')
    args = parser.parse_args()
    baseline_dir = Path(__file__).resolve().parents[1] / 'docs' / 'reports'
    seen = set()
    for key, filename in args.reference:
        if key not in KEYS or key in seen:
            parser.error(f'Reference keys must be unique and one of {", ".join(KEYS)}')
        seen.add(key)
        summary, full = validate(key, Path(filename), baseline_dir)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / f'bpb-{key}-full.json').write_text(
            json.dumps(full, indent=1) + '\n', encoding='utf-8', newline='\n')
        if args.summary_dir:
            args.summary_dir.mkdir(parents=True, exist_ok=True)
            (args.summary_dir / f'bpb-{key}.json').write_text(
                json.dumps(summary, indent=2) + '\n', encoding='utf-8', newline='\n')
        print(key, json.dumps({k: v for k, v in summary.items()
                               if k not in ('entries', 'opaque_signatures_stored_top20',
                                            'opaque_signatures_decoded_top20')}))
        print(key, 'tim', json.dumps({k: v for k, v in summary['tim'].items()
                                      if k not in ('rejected_examples', 'pixel_dims_top20')}))
        print(key, '00002311', json.dumps(summary['signature_00002311']))


if __name__ == '__main__':
    main()
