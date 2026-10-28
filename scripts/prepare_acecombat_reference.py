"""Prepare a hash-verified derived reference from the supplied Japanese Ace Combat.

This is a correction for one fingerprinted input, not a general disc repair tool.
Original files are opened read-only. The pinned MAME track hashes are the gate
for publishing the derived outputs; no missing game payload is downloaded.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import zlib

CHUNK = 4 * 1024 * 1024


def copy_exact(source, target, length: int) -> None:
    while length:
        block = source.read(min(CHUNK, length))
        if not block:
            raise ValueError('Source ended before its recorded size')
        target.write(block)
        length -= len(block)


def materialize_audio(source, target, *, source_size: int, data_bytes: int,
                      pregap_bytes: int, offset_bytes: int) -> None:
    """Advance audio within a materialized pregap, retaining all source bytes."""
    if not (0 <= data_bytes <= source_size and 0 <= offset_bytes <= pregap_bytes
            and pregap_bytes <= CHUNK):
        raise ValueError('Invalid correction bounds')
    copy_exact(source, target, data_bytes)
    target.write(bytes(pregap_bytes - offset_bytes))
    copy_exact(source, target, source_size - data_bytes)
    if source.read(1):
        raise ValueError('Source exceeds its recorded size')
    target.write(bytes(offset_bytes))


def sector_time(sectors: int) -> str:
    minutes, remainder = divmod(sectors, 75 * 60)
    seconds, frames = divmod(remainder, 75)
    return f'{minutes:02}:{seconds:02}:{frames:02}'


def time_sector(value: str) -> int:
    minutes, seconds, frames = map(int, value.split(':'))
    if minutes < 0 or not (0 <= seconds < 60 and 0 <= frames < 75):
        raise ValueError('Invalid CUE time')
    return (minutes * 60 + seconds) * 75 + frames


def materialized_cue(text: str, output_name: str, gap_sectors: int) -> str:
    lines = text.splitlines()
    result = []
    track = 0
    gaps = 0
    files = 0
    for pos, line in enumerate(lines):
        if re.match(r'^\s*FILE\s', line):
            result.append(f'FILE "{output_name}" BINARY')
            files += 1
            continue
        found = re.match(r'^\s*TRACK\s+(\d+)\s+(\S+)', line)
        if found:
            track = int(found[1])
        if re.match(r'^\s*PREGAP\s', line):
            if track != 2 or time_sector(line.split()[1]) != gap_sectors:
                raise ValueError('Unexpected synthetic pregap')
            following = re.fullmatch(r'\s*INDEX 01 (\d+:\d+:\d+)\s*', lines[pos + 1])
            if not following:
                raise ValueError('Pregap must precede INDEX 01')
            result.append(f'    INDEX 00 {following[1]}')
            gaps += 1
            continue
        found = re.fullmatch(r'\s*INDEX (\d+) (\d+:\d+:\d+)\s*', line)
        if found and track >= 2:
            result.append(f'    INDEX {found[1]} {sector_time(time_sector(found[2]) + gap_sectors)}')
        else:
            result.append(line)
    if files != 1 or gaps != 1:
        raise ValueError('Expected one source file and one synthetic pregap')
    return '\n'.join(result) + '\n'


def hashes(path: Path) -> dict[str, str]:
    values = {name: hashlib.new(name) for name in ('md5', 'sha1', 'sha256')}
    with path.open('rb') as stream:
        while block := stream.read(CHUNK):
            for digest in values.values():
                digest.update(block)
    return {name: digest.hexdigest() for name, digest in values.items()}


def prepare(source: Path, cue: Path, output: Path, reference: Path) -> dict:
    evidence = json.loads(reference.read_text(encoding='utf-8'))
    if hashes(source)['sha256'] != evidence['source_image_sha256']:
        raise ValueError('BIN does not match the fingerprinted source')
    if hashes(cue)['sha256'] != evidence['source_cue_sha256']:
        raise ValueError('CUE does not match the fingerprinted source')
    tracks = evidence['tracks']
    if [t['track'] for t in tracks] != list(range(1, 64)):
        raise ValueError('Expected the pinned 63-track reference')
    correction = evidence['correction']
    gap = correction['materialized_pregap_bytes']
    offset = correction['audio_read_offset_bytes']
    if gap != 150 * 2352 or offset != 32:
        raise ValueError('Unexpected correction parameters')
    source_size = source.stat().st_size
    data_bytes = tracks[0]['reference_size_bytes']
    cue_data = materialized_cue(cue.read_text(encoding='ascii'),
                                'ACECOMBAT.reference.bin', gap // 2352)
    output.mkdir(exist_ok=False)
    image = output / 'ACECOMBAT.reference.bin'
    descriptor = output / 'ACECOMBAT.reference.cue'
    partial = output / 'ACECOMBAT.reference.bin.partial'
    try:
        with source.open('rb') as stream, partial.open('xb') as target:
            materialize_audio(stream, target, source_size=source_size,
                              data_bytes=data_bytes, pregap_bytes=gap,
                              offset_bytes=offset)
        checked = []
        with partial.open('rb') as stream:
            for track in tracks:
                remaining = track['reference_size_bytes']
                digest = hashlib.sha1()
                crc = 0
                while remaining:
                    block = stream.read(min(CHUNK, remaining))
                    if not block:
                        raise ValueError(f"Truncated derived track {track['track']}")
                    digest.update(block)
                    crc = zlib.crc32(block, crc)
                    remaining -= len(block)
                if (digest.hexdigest() != track['reference_sha1'] or
                        f'{crc:08x}' != track['reference_crc32']):
                    raise ValueError(f"Derived track {track['track']} does not match the reference")
                checked.append(track['track'])
            if stream.read(1):
                raise ValueError('Derived image has trailing bytes')
        result = {'source_image_sha256': evidence['source_image_sha256'],
                  'reference_commit': evidence['reference_commit'],
                  'derived_image': image.name, 'derived_size_bytes': partial.stat().st_size,
                  'derived_hashes': hashes(partial), 'verified_tracks': checked,
                  'all_63_reference_tracks_match': len(checked) == 63,
                  'derived_cue': descriptor.name, 'emulator_boot_tested': False}
        partial.rename(image)
        descriptor.write_text(cue_data, encoding='ascii', newline='\n')
        (output / 'verification.json').write_text(json.dumps(result, indent=2) + '\n',
                                                encoding='utf-8', newline='\n')
        return result
    finally:
        partial.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bin', type=Path)
    parser.add_argument('cue', type=Path)
    parser.add_argument('output', type=Path, help='New output directory; existing directories are refused')
    parser.add_argument('--reference', type=Path, default=Path(__file__).resolve().parents[1] /
                        'docs/reports/supplementary-ps1-track-validation.json')
    args = parser.parse_args()
    try:
        result = prepare(args.bin, args.cue, args.output, args.reference)
    except (ValueError, OSError) as exc:
        print(f'prepare-acecombat-reference: {exc}', file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
