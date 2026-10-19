"""Optional local-disc validation; synthetic CI never needs these inputs.

Run from the repository with PYTHONPATH=src. Supply one or more
--reference jp1 /path/to/image.bin pairs (keys: jp1, jp2, en1, en2).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

from swallow_tools.disc import file_hashes


def validate(key: str, path: Path, baseline_dir: Path) -> dict:
    baseline = json.loads((baseline_dir / f'{key}.json').read_text(encoding='utf-8'))
    measured_hashes = file_hashes(path)
    if measured_hashes != baseline['hashes']:
        raise ValueError(f'{key}: full image hashes differ from baseline')
    completed = subprocess.run(
        [sys.executable, '-m', 'swallow_tools.exe', str(path), '--json'],
        check=True, capture_output=True, text=True, encoding='utf-8')
    report = json.loads(completed.stdout)
    baseline_files = {e['path']: e for e in baseline['files']}
    for exe in report['executables']:
        expected = baseline_files[exe['path']]['content_hash']['sha256']
        if exe['sha256'] != expected:
            raise ValueError(f"{key}: {exe['path']} hash differs from baseline")
        if exe['warnings']:
            raise ValueError(f"{key}: {exe['path']} has warnings {exe['warnings']}")
    if report['text_range_overlaps']:
        raise ValueError(f'{key}: executable text ranges overlap')
    report['reference_validation'] = {
        'baseline_report': f'docs/reports/{key}.json',
        'whole_image_hashes_match': True,
        'whole_image_hashes': measured_hashes,
        'executable_hashes_match_baseline': True,
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', nargs=2, action='append', required=True,
                        metavar=('KEY', 'BIN'))
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    baseline_dir = Path(__file__).resolve().parents[1] / 'docs' / 'reports'
    seen = set()
    for key, filename in args.reference:
        if key not in {'jp1', 'jp2', 'en1', 'en2'} or key in seen:
            parser.error('Reference keys must be unique and one of jp1, jp2, en1, en2')
        seen.add(key)
        report = validate(key, Path(filename), baseline_dir)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / f'exe-{key}.json').write_bytes(
            (json.dumps(report, indent=2) + '\n').encode('utf-8'))
        for exe in report['executables']:
            print(key, exe['path'], 'boot' if exe['is_boot'] else 'exe',
                  exe['text_range'], exe['header']['initial_pc'],
                  [ref['string'] for ref in exe['path_references']])


if __name__ == '__main__':
    main()
