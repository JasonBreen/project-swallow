"""Optional local-disc BPB container/Ulz validation; synthetic CI never needs these inputs.

Run from the repository with PYTHONPATH=src. Supply one or more
--reference jp1 /path/to/image.bin pairs (keys: jp1, jp2, en1, en2). The
script checks the whole-image and BPH/BPB logical hashes against the
checked-in baselines, walks every normal entry with Ulz decoding, fails if
any Ulz blob is rejected, and writes a metadata summary. No decoded payload
is written to disk.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from swallow_tools.bpb import inspect_bpb
from swallow_tools.disc import FileEntry, PlayStationDisc, file_hashes

KEYS = ('jp1', 'jp2', 'en1', 'en2')


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
             'max_decompressed': 0, 'literal_count': 0, 'match_count': 0}
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
    return {
        'schema_version': 1,
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


if __name__ == '__main__':
    main()
