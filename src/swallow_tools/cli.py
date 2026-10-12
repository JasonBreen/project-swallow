"""swallow-disc: reproducible, read-only PS1 disc inspection."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
from .disc import PlayStationDisc, DiscFormatError, cue_references, file_hashes
from .fingerprints import add_file_hashes
from .profiles import identify_disc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Read-only PlayStation MODE2/2352 disc inventory')
    parser.add_argument('bin', type=Path, help='Path to a local raw BIN image')
    parser.add_argument('--cue', type=Path, help='Optional CUE descriptor; check referenced filenames only')
    parser.add_argument('--strict-cue', action='store_true', help='Exit 3 for missing/empty CUE FILE references')
    parser.add_argument('--hash', action='store_true', help='Calculate whole-image MD5, SHA1, SHA256')
    parser.add_argument('--file-hashes', action='store_true', help='SHA256 logical Form 1 files; flag Form 2 as unsupported')
    parser.add_argument('--json', action='store_true', help='Machine-readable report')
    args = parser.parse_args(argv)
    if args.strict_cue and not args.cue:
        parser.error('--strict-cue requires --cue')
    try:
        disc = PlayStationDisc(args.bin)
        report = disc.inspect()
        if args.cue:
            refs = [vars(ref) for ref in cue_references(args.cue)]
            report['cue'] = {'filename': args.cue.name, 'references': refs,
                             'references_ok': bool(refs) and all(ref['present'] for ref in refs)}
        if args.hash:
            report['hashes'] = file_hashes(args.bin)
            report['profile'] = identify_disc(report['hashes'], report['size_bytes'])
            report['known_revision'] = report['profile']['label'] if report['profile'] else 'unknown'
        if args.file_hashes:
            add_file_hashes(disc, report)
    except (DiscFormatError, OSError) as exc:
        print(f'swallow-disc: {exc}', file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(f"Image: {report['source_image']}")
        print(f"Volume: {report['volume_id']} | Sectors: {report['sector_count']}")
        for item in report['files']:
            kind = 'dir ' if item['is_directory'] else 'file'
            print(f"  {kind}  {item['size']:>11} B  LBA {item['lba']:>7}  {item['path']}")
            if 'content_hash' in item:
                result = item['content_hash']
                print(f"        {result['status']}: {result.get('sha256') or result.get('reason', 'not hashed')}")
        if 'cue' in report:
            for ref in report['cue']['references']:
                label = 'OK' if ref['present'] else 'MISSING (CUE filename mismatch?)'
                print(f"CUE: {ref['filename']} - {label}")
            if not report['cue']['references']:
                print('CUE: no FILE references found')
        if 'hashes' in report:
            for key, value in report['hashes'].items():
                print(f'{key.upper()}: {value}')
            print('Known baseline:', report['known_revision'])
    return 3 if args.strict_cue and not report['cue']['references_ok'] else 0

if __name__ == '__main__':
    raise SystemExit(main())
