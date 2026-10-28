"""Find PsyQ library objects in PS-X EXE text by signature matching.

Contract and measured results: docs/psyq-libraries.md. The signatures are
external data (one JSON file per library, grouped by SDK version directory),
in the format of github.com/lab313ru/psx_psyq_signatures. They describe
Sony's object code, carry no license, and are never committed; this module
only reads them. The report holds names, addresses, and sizes, never game
bytes.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys

from .disc import DiscFormatError, FileEntry, PlayStationDisc
from .exe import HEADER_BYTES, ExeFormatError, _read_whole, inspect_executables

SCHEMA_VERSION = 1
# A signature must pin at least this many bytes (eight instructions) to count.
# Shorter ones, such as single BIOS call stubs, match too many places.
MIN_FIXED_BYTES = 32
# Generated names for local labels; they say nothing about the function.
PLACEHOLDER_PREFIXES = ('loc_', 'text_')


class SignatureError(ValueError):
    pass


@dataclass(frozen=True)
class Signature:
    version: str
    library: str
    obj: str
    pattern: re.Pattern
    size: int
    fixed: int
    labels: tuple[tuple[str, int], ...]


def _compile(sig: str) -> tuple[re.Pattern, int, int]:
    tokens = sig.split()
    parts = []
    for token in tokens:
        if token == '??':
            parts.append(b'.')
        elif re.fullmatch(r'[0-9A-Fa-f]{2}', token):
            parts.append(re.escape(bytes([int(token, 16)])))
        else:
            raise SignatureError(f'Bad signature byte {token!r}')
    return re.compile(b''.join(parts), re.DOTALL), len(tokens), sum(t != '??' for t in tokens)


def load_signatures(root: Path, versions: list[str] | None = None) -> list[Signature]:
    """Read every <version>/<LIBRARY>.json file under root.

    Entries without a byte pattern (BSS-only objects) or with fewer than
    MIN_FIXED_BYTES fixed bytes are skipped.
    """
    dirs = sorted(path for path in root.iterdir() if path.is_dir() and path.name.isdigit())
    if versions is not None:
        dirs = [path for path in dirs if path.name in versions]
        missing = set(versions) - {path.name for path in dirs}
        if missing:
            raise SignatureError(f'No signature directory for version(s) {", ".join(sorted(missing))}')
    if not dirs:
        raise SignatureError(f'No version directories in {root}')
    signatures = []
    for directory in dirs:
        for path in sorted(directory.glob('*.json')):
            try:
                entries = json.loads(path.read_text(encoding='utf-8'))
                for entry in entries:
                    if not entry.get('sig'):
                        continue
                    pattern, size, fixed = _compile(entry['sig'])
                    if fixed < MIN_FIXED_BYTES:
                        continue
                    labels = tuple((label['name'], int(label['offset']))
                                   for label in entry.get('labels', []))
                    signatures.append(Signature(directory.name, path.stem, entry['name'],
                                                pattern, size, fixed, labels))
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                if isinstance(exc, SignatureError):
                    raise SignatureError(f'{path}: {exc}') from exc
                raise SignatureError(f'Malformed signature file {path}: {exc}') from exc
    return signatures


def _aligned_hits(pattern: re.Pattern, text: bytes, limit: int) -> list[int]:
    """Word-aligned match starts, overlapping ones included, up to limit.

    finditer() skips matches that start inside an earlier match, which would
    hide a second placement of a repetitive signature.
    """
    hits, pos = [], 0
    while len(hits) < limit:
        match = pattern.search(text, pos)
        if match is None:
            break
        if match.start() % 4 == 0:
            hits.append(match.start())
        pos = match.start() + 1
    return hits


def match_signatures(text: bytes, text_address: int, signatures: list[Signature]) -> dict:
    """Place library objects in text and name their global labels.

    A signature counts only where it matches exactly once, word-aligned.
    The same object often has identical bytes in several SDK versions; those
    are merged and every matching version is listed. Overlapping candidates
    are resolved by keeping the one with the most fixed bytes.
    """
    found: dict[tuple, dict] = {}
    for sig in signatures:
        hits = _aligned_hits(sig.pattern, text, limit=2)
        if len(hits) != 1:
            continue
        key = (sig.library, sig.obj, hits[0], sig.size, sig.labels)
        if key in found:
            found[key]['versions'].append(sig.version)
        else:
            found[key] = {'sig': sig, 'offset': hits[0], 'versions': [sig.version]}
    taken: list[tuple[int, int]] = []
    chosen = []
    for item in sorted(found.values(), key=lambda i: (-i['sig'].fixed, -i['sig'].size,
                                                      i['sig'].library, i['sig'].obj)):
        start, end = item['offset'], item['offset'] + item['sig'].size
        if any(start < b and a < end for a, b in taken):
            continue
        taken.append((start, end))
        chosen.append(item)
    chosen.sort(key=lambda i: i['offset'])
    objects, functions = [], []
    for item in chosen:
        sig = item['sig']
        address = text_address + item['offset']
        objects.append({'library': sig.library, 'object': sig.obj, 'address': f'0x{address:08x}',
                        'size': sig.size, 'versions': sorted(item['versions'])})
        for name, offset in sig.labels:
            if not name.startswith(PLACEHOLDER_PREFIXES):
                functions.append({'name': name, 'address': f'0x{address + offset:08x}',
                                  'library': sig.library, 'object': sig.obj})
    return {
        'library_bytes': sum(item['sig'].size for item in chosen),
        'library_range': ([objects[0]['address'],
                           f"0x{text_address + chosen[-1]['offset'] + chosen[-1]['sig'].size:08x}"]
                          if objects else None),
        'objects': objects,
        'labels': functions,
    }


def identify_image(image: Path, signatures: list[Signature]) -> dict:
    report = inspect_executables(image)
    disc = PlayStationDisc(image)
    files = {(item['lba'], item['size']): FileEntry(**item)
             for item in disc.inspect()['files'] if not item['is_directory']}
    results = []
    for exe in report['executables']:
        data = _read_whole(disc, files[(exe['lba'], exe['size_bytes'])])
        text_address = int(exe['header']['text_address'], 16)
        text = data[HEADER_BYTES:HEADER_BYTES + exe['header']['text_size']]
        results.append({'path': exe['path'], 'sha256': exe['sha256'], 'is_boot': exe['is_boot'],
                        'text_address': exe['header']['text_address'],
                        **match_signatures(text, text_address, signatures)})
    return {'schema_version': SCHEMA_VERSION, 'source_image': image.name, 'executables': results}


GENERATED_MARKER = '// --- PsyQ library labels: generated by swallow-psyq --update-symbols ---'
_SYMBOL_LINE = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(0x[0-9A-Fa-f]+)\s*;')


def symbol_lines(result: dict, skip_addresses: set[int] = frozenset(),
                 skip_names: set[str] = frozenset()) -> list[str]:
    """splat symbol_addrs lines for the named labels in one executable result.

    Each object's labels follow a comment naming its library and object. The
    first name at an address wins; later names at the same address,
    names already used, and addresses in skip_addresses are left out.
    """
    lines, seen_addresses, seen_names = [], set(skip_addresses), set(skip_names)
    current = None
    for label in result['labels']:
        address = int(label['address'], 16)
        if address in seen_addresses or label['name'] in seen_names:
            continue
        seen_addresses.add(address)
        seen_names.add(label['name'])
        # splat reads key:value pairs in symbol comments, so the object name
        # goes on its own comment line.
        if (label['library'], label['object']) != current:
            current = (label['library'], label['object'])
            lines.append(f'// {current[0]} {current[1]}')
        lines.append(f"{label['name']} = 0x{address:08X}; // type:func")
    return lines


def with_generated_symbols(text: str, result: dict) -> str:
    """Replace the generated section of a symbol file, keeping the rest.

    Hand-written entries above the marker win: their names and addresses
    are never repeated in the generated section.
    """
    manual = text.split(GENERATED_MARKER)[0].rstrip('\n') + '\n'
    names, addresses = set(), set()
    for line in manual.splitlines():
        match = _SYMBOL_LINE.match(line)
        if match:
            names.add(match.group(1))
            addresses.add(int(match.group(2), 16))
    lines = symbol_lines(result, addresses, names)
    return manual + '\n' + GENERATED_MARKER + '\n' + ''.join(line + '\n' for line in lines)


def update_symbol_files(report: dict, config_dir: Path) -> list[str]:
    """Rewrite the generated section of each matched config's symbol file."""
    from .split import load_manifest
    configs = load_manifest(config_dir)
    by_hash = {digest: entry for entry in configs for digest in entry.sha256}
    updated = []
    for exe in report['executables']:
        entry = by_hash.get(exe['sha256'])
        if entry is None:
            raise SignatureError(f"{exe['path']}: SHA256 {exe['sha256']} matches no splat config")
        if entry.symbols in updated:
            continue
        path = config_dir / entry.symbols
        path.write_text(with_generated_symbols(path.read_text(encoding='utf-8'), exe),
                        encoding='utf-8', newline='\n')
        updated.append(entry.symbols)
    return updated


def main(argv: list[str] | None = None) -> int:
    from .split import DEFAULT_CONFIG_DIR, SplitError
    parser = argparse.ArgumentParser(description='Find PsyQ library code in disc executables')
    parser.add_argument('bin', type=Path, help='Local raw MODE2/2352 disc image')
    parser.add_argument('--signatures', type=Path, required=True,
                        help='Checkout of the psx_psyq_signatures data (version directories)')
    parser.add_argument('--versions', nargs='+', help='Only these version directories, e.g. 430 440')
    parser.add_argument('--json', action='store_true', help='Print the full report')
    parser.add_argument('--update-symbols', nargs='?', type=Path, const=DEFAULT_CONFIG_DIR,
                        metavar='CONFIG_DIR',
                        help='Rewrite the generated section of the splat symbol files '
                             '(default: splat/ in this checkout)')
    args = parser.parse_args(argv)
    try:
        report = identify_image(args.bin, load_signatures(args.signatures, args.versions))
        updated = update_symbol_files(report, args.update_symbols) if args.update_symbols else []
    except (OSError, KeyError, DiscFormatError, ExeFormatError, SignatureError, SplitError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    for exe in report['executables']:
        span = '-'.join(exe['library_range']) if exe['library_range'] else 'none'
        print(f"{exe['path']}: {len(exe['objects'])} library objects, "
              f"{exe['library_bytes']} bytes, {len(exe['labels'])} named labels, span {span}")
    for name in updated:
        print(f'updated {name}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
