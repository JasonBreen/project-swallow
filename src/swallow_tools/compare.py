"""Compare logical file content, not offsets or raw-sector wrapper bytes."""
from __future__ import annotations
import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from typing import Any
from .disc import PlayStationDisc, DiscFormatError
from .fingerprints import add_file_hashes


def compare_reports(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    a = {f['path']: f for f in left['files'] if not f['is_directory']}
    b = {f['path']: f for f in right['files'] if not f['is_directory']}
    rows = []
    for name in sorted(a.keys() | b.keys()):
        x, y = a.get(name), b.get(name)
        if x is None:
            status = 'only_right'
        elif y is None:
            status = 'only_left'
        else:
            hx, hy = x.get('content_hash', {}), y.get('content_hash', {})
            if hx.get('status') == hy.get('status') == 'verified_form1':
                status = 'identical_form1_content' if x['size'] == y['size'] and hx.get('sha256') == hy.get('sha256') else 'different_form1_content'
            else:
                status = 'content_unverified'
        rows.append({'path': name, 'status': status,
                     'left_size': x['size'] if x else None, 'right_size': y['size'] if y else None,
                     'left_lba': x['lba'] if x else None, 'right_lba': y['lba'] if y else None,
                     'left_sha256': x.get('content_hash', {}).get('sha256') if x else None,
                     'right_sha256': y.get('content_hash', {}).get('sha256') if y else None})
    return {'left_image': left['source_image'], 'right_image': right['source_image'],
            'comparison_basis': 'ISO path plus logical MODE2 Form 1 file SHA256; unsupported files are never declared identical.',
            'summary': dict(Counter(row['status'] for row in rows)), 'files': rows}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Compare supported logical file contents of two local PS1 BINs')
    parser.add_argument('left', type=Path); parser.add_argument('right', type=Path)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    try:
        reports = []
        for path in (args.left, args.right):
            disc = PlayStationDisc(path); report = disc.inspect()
            add_file_hashes(disc, report); reports.append(report)
        comparison = compare_reports(*reports)
    except (DiscFormatError, OSError) as exc:
        print(f'swallow-compare: {exc}', file=sys.stderr); return 2
    if args.json:
        print(json.dumps(comparison, indent=2))
    else:
        print(f"{comparison['left_image']} -> {comparison['right_image']}")
        for row in comparison['files']:
            print(f"{row['path']:<16} {row['status']:<26} LBA {row['left_lba']} -> {row['right_lba']}")
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
