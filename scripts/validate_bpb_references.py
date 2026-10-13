"""Optional local-disc validation of the BPB walk; synthetic CI never needs it.

Run from the repository with PYTHONPATH=src. Supply one or more
--reference KEY /path/to/image.bin pairs (keys: jp1, jp2, en1, en2).
Writes metadata-only summaries (no asset bytes) to --output-dir.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys

from swallow_tools.bpb import inspect_bpb
from swallow_tools.disc import file_hashes


def iter_nodes(node: dict):
    yield node
    for child in node.get('children', ()):
        yield from iter_nodes(child)


def validate(key: str, path: Path, baseline_dir: Path, blocks: dict) -> dict:
    baseline = json.loads((baseline_dir / f'{key}.json').read_text(encoding='utf-8'))
    measured_hashes = file_hashes(path)
    if measured_hashes != baseline['hashes']:
        raise ValueError(f'{key}: full image hashes differ from baseline')
    full = inspect_bpb(path)
    completed = subprocess.run(
        [sys.executable, '-m', 'swallow_tools.bpb', str(path), '--json', '--no-trees'],
        check=True, capture_output=True, text=True, encoding='utf-8')
    summary = json.loads(completed.stdout)
    if [e.get('tree_sha256') for e in summary['entries']] != [
            e.get('tree_sha256') for e in full['entries']]:
        raise ValueError(f'{key}: CLI summary digests differ from the in-process walk')
    pads = Counter()
    max_depth = 0
    for entry in full['entries']:
        if 'tree' not in entry:
            continue
        stack = [(entry['tree'], 0)]
        while stack:
            node, depth = stack.pop()
            max_depth = max(max_depth, depth)
            stack.extend((child, depth + 1) for child in node.get('children', ()))
            if node['kind'] != 'ulz':
                continue
            info = node['ulz']
            # Every stream must be consumed up to alignment padding only.
            if (info['flag_pad_bytes'] not in (0, 4) or info['literal_pad_bytes'] > 3
                    or info['reference_pad_bytes'] > 3):
                raise ValueError(f'{key}: entry {entry["index"]}: Ulz stream not fully consumed')
            pads[f'v{info["version"]}_flag_pad_{info["flag_pad_bytes"]}'] += 1
            pads[f'literal_pad_{info["literal_pad_bytes"]}'] += 1
            pads[f'reference_pad_{info["reference_pad_bytes"]}'] += 1
            if info['unused_reference_flags']:
                pads[f'v{info["version"]}_blocks_with_unused_reference_flags'] += 1
                pads[f'v{info["version"]}_unused_reference_flags_total'] += info[
                    'unused_reference_flags']
            decoded = node['children'][0]['sha256']
            known = blocks.setdefault(node['sha256'], (decoded, key))
            if known[0] != decoded:
                raise ValueError(f'{key}: identical Ulz block decoded differently than in {known[1]}')
    summary['reference_validation'] = {
        'baseline_report': f'docs/reports/{key}.json',
        'whole_image_hashes_match': True,
        'whole_image_hashes': measured_hashes,
        'cli_summary_matches_full_walk': True,
        'all_ulz_streams_consumed_to_alignment': True,
        'max_node_depth': max_depth,
        'ulz_padding_histogram': dict(sorted(pads.items())),
    }
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', nargs=2, action='append', required=True,
                        metavar=('KEY', 'BIN'))
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    baseline_dir = Path(__file__).resolve().parents[1] / 'docs' / 'reports'
    blocks: dict = {}
    reports = {}
    for key, filename in args.reference:
        if key not in {'jp1', 'jp2', 'en1', 'en2'} or key in reports:
            parser.error('Reference keys must be unique and one of jp1, jp2, en1, en2')
        report = validate(key, Path(filename), baseline_dir, blocks)
        reports[key] = report
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / f'bpb-{key}.json').write_bytes(
            (json.dumps(report, indent=2) + '\n').encode('utf-8'))
        print(key, json.dumps(report['totals']), json.dumps(report['reference_validation']))
    digests = {key: [e.get('tree_sha256') for e in report['entries']]
               for key, report in reports.items()}
    for left, right in (('jp1', 'jp2'), ('en1', 'en2'), ('jp1', 'en1'), ('jp2', 'en2')):
        if left in digests and right in digests:
            same = sum(a == b for a, b in zip(digests[left], digests[right]))
            print(f'{left} vs {right}: {same} of {len(digests[left])} entry trees identical')
    print(f'distinct Ulz blocks decoded consistently across references: {len(blocks)}')


if __name__ == '__main__':
    main()
