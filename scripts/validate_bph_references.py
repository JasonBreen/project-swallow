"""Optional local-disc validation; synthetic CI never needs these inputs.

Run from the repository with PYTHONPATH=src. Supply one or more
--reference jp1 /path/to/image.bin pairs (keys: jp1, jp2, en1, en2).
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys

from swallow_tools.disc import FileEntry, PlayStationDisc, file_hashes
from swallow_tools.bph import parse_bph


def validate(key: str, path: Path, baseline_dir: Path) -> dict:
    baseline = json.loads((baseline_dir / f'{key}.json').read_text(encoding='utf-8'))
    measured_hashes = file_hashes(path)
    if measured_hashes != baseline['hashes']:
        raise ValueError(f'{key}: full image hashes differ from baseline')
    completed = subprocess.run(
        [sys.executable, '-m', 'swallow_tools.bph', str(path), '--json'],
        check=True, capture_output=True, text=True, encoding='utf-8')
    report = json.loads(completed.stdout)
    disc = PlayStationDisc(path)
    entries = {e['path']: FileEntry(**e) for e in disc.inspect()['files']}
    raw = disc.read_file(entries['ACE.BPH'])
    decoded = parse_bph(raw, entries['ACE.BPB'].size)
    h = decoded.header
    rebuilt = struct.pack('<4sIHHII', h.magic.encode('ascii'), h.archive_type,
                          h.unknown_08, h.unknown_0a, h.unknown_0c, h.entry_count)
    rebuilt += b''.join(struct.pack('<II', e.descriptor, e.sector_word)
                        for e in decoded.entries)
    if rebuilt != raw:
        raise ValueError(f'{key}: raw index reconstruction differs')
    baseline_files = {e['path']: e for e in baseline['files']}
    if report['bph']['sha256'] != baseline_files['ACE.BPH']['content_hash']['sha256']:
        raise ValueError(f'{key}: BPH hash differs from baseline')
    bpb = entries['ACE.BPB']
    digest = hashlib.sha256()
    for offset in range(0, bpb.size, 4 * 1024 * 1024):
        digest.update(disc.read_file(bpb, offset=offset,
                                    length=min(4 * 1024 * 1024, bpb.size - offset)))
    if digest.hexdigest() != baseline_files['ACE.BPB']['content_hash']['sha256']:
        raise ValueError(f'{key}: BPB hash differs from baseline')
    zero_gaps = nonzero_gaps = 0
    gaps = [e.gap_after_bytes for e in decoded.entries if e.gap_after_bytes is not None]
    for entry in decoded.entries:
        if entry.gap_after_bytes:
            data = disc.read_file(bpb, offset=entry.offset_bytes + entry.size_bytes,
                                  length=entry.gap_after_bytes)
            if any(data):
                nonzero_gaps += 1
            else:
                zero_gaps += 1
    report['reference_validation'] = {
        'baseline_report': f'docs/reports/{key}.json',
        'whole_image_hashes_match': True,
        'whole_image_hashes': measured_hashes,
        'index_reconstructed_byte_for_byte': True,
        'bpb_sha256': digest.hexdigest(),
        'bpb_hash_matches_baseline': True,
        'bpb_all_logical_bytes_read_as_form1': True,
        'gap_min_bytes': min(gaps, default=None),
        'gap_max_bytes': max(gaps, default=None),
        'positive_gaps_containing_only_zero_bytes': zero_gaps,
        'positive_gaps_containing_nonzero_bytes': nonzero_gaps,
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
        (args.output_dir / f'bph-{key}.json').write_bytes(
            (json.dumps(report, indent=2) + '\n').encode('utf-8'))
        print(key, report['normal_entry_count'], report['bpb_size_bytes'],
              report['normal_payload_bytes'], report['bytes_outside_normal_entries'],
              json.dumps(report['reference_validation']))


if __name__ == '__main__':
    main()
