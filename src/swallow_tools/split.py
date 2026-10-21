"""Extract PS-X EXE files from a disc image and disassemble them with splat.

Contract and measured boundaries: docs/disassembly.md. Executables are matched
to the configs in splat/manifest.json by SHA256, never by filename. splat
itself is an external tool (splat/requirements.txt); this module only stages
inputs, runs it, and counts what it produced. Extracted bytes and generated
assembly are game-derived, so they go to a work directory outside the
repository and are never committed.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

from .disc import DiscFormatError, FileEntry, PlayStationDisc
from .exe import ExeFormatError, _read_whole, inspect_executables

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_DIR = REPO_ROOT / 'splat'
DEFAULT_SPLAT = (sys.executable, '-m', 'splat')
SCHEMA_VERSION = 1


class SplitError(ValueError):
    pass


@dataclass(frozen=True)
class SplatConfig:
    config: str
    target: str
    symbols: str
    sha256: frozenset[str]


def load_manifest(config_dir: Path) -> list[SplatConfig]:
    """Read splat/manifest.json and check every file it names exists."""
    manifest = json.loads((config_dir / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('schema_version') != 1:
        raise SplitError('Unsupported splat manifest schema version')
    configs = [SplatConfig(item['config'], item['target'], item['symbols'],
                           frozenset(item['sha256'])) for item in manifest['configs']]
    seen: set[str] = set()
    for entry in configs:
        for name in (entry.config, entry.symbols):
            if not (config_dir / name).is_file():
                raise SplitError(f'Manifest names missing file {name}')
        if seen & entry.sha256:
            raise SplitError(f'{entry.config}: a SHA256 is listed under two configs')
        seen |= entry.sha256
    return configs


def summarize_asm(asm_dir: Path) -> list[dict]:
    """Count functions, data islands, and undecodable words per text file."""
    files = []
    for path in sorted(asm_dir.glob('*.s')):
        lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
        files.append({
            'file': path.name,
            'lines': len(lines),
            'functions': sum(line.startswith('glabel ') for line in lines),
            'data_labels': sum(line.startswith('dlabel ') for line in lines),
            'invalid_instruction_words': sum('/* invalid instruction */' in line
                                             for line in lines),
        })
    return files


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def split_image(image: Path, work_dir: Path, *, config_dir: Path = DEFAULT_CONFIG_DIR,
                splat: tuple[str, ...] | list[str] = DEFAULT_SPLAT,
                repo_root: Path = REPO_ROOT) -> dict:
    """Stage every executable on the image and run splat once per config.

    The work directory must be outside the repository and empty (or absent),
    so counts never mix with an earlier run's files.
    """
    if _inside(work_dir, repo_root):
        raise SplitError(f'Work directory {work_dir} is inside the repository; '
                         'game-derived output must stay outside commits')
    if work_dir.exists() and any(work_dir.iterdir()):
        raise SplitError(f'Work directory {work_dir} is not empty')
    configs = load_manifest(config_dir)
    report = inspect_executables(image)
    by_hash = {digest: entry for entry in configs for digest in entry.sha256}
    staged: list[tuple[dict, SplatConfig]] = []
    for exe in report['executables']:
        entry = by_hash.get(exe['sha256'])
        if entry is None:
            raise SplitError(f"{exe['path']}: SHA256 {exe['sha256']} matches no splat config")
        if any(other is entry for _, other in staged):
            raise SplitError(f"{exe['path']}: {entry.config} already matched another file")
        staged.append((exe, entry))

    work_dir.mkdir(parents=True, exist_ok=True)
    disc = PlayStationDisc(image)
    files = {item['path']: FileEntry(**item) for item in disc.inspect()['files']}
    results = []
    for exe, entry in staged:
        data = _read_whole(disc, files[exe['path']])
        if hashlib.sha256(data).hexdigest() != exe['sha256']:
            raise SplitError(f"{exe['path']}: bytes changed between report and extraction")
        (work_dir / entry.target).write_bytes(data)
        for name in (entry.config, entry.symbols):
            shutil.copyfile(config_dir / name, work_dir / name)
        completed = subprocess.run([*splat, 'split', entry.config], cwd=work_dir,
                                   capture_output=True, text=True, encoding='utf-8',
                                   errors='replace')
        (work_dir / f'{Path(entry.config).stem}.splat.log').write_text(
            completed.stdout + completed.stderr, encoding='utf-8')
        if completed.returncode != 0:
            raise SplitError(f'splat failed on {entry.config} with exit code '
                             f'{completed.returncode}; see {Path(entry.config).stem}.splat.log')
        results.append({'path': exe['path'], 'sha256': exe['sha256'],
                        'is_boot': exe['is_boot'], 'config': entry.config,
                        'target': entry.target})
    summary = {
        'schema_version': SCHEMA_VERSION,
        'source_image': image.name,
        'executables': results,
        'asm_files': summarize_asm(work_dir / 'asm'),
    }
    (work_dir / 'split-report.json').write_text(json.dumps(summary, indent=2) + '\n',
                                                encoding='utf-8')
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Disassemble disc executables with splat')
    parser.add_argument('bin', type=Path, help='Local raw MODE2/2352 disc image')
    parser.add_argument('--work-dir', type=Path, required=True,
                        help='Empty directory outside the repository for splat output')
    parser.add_argument('--config-dir', type=Path, default=DEFAULT_CONFIG_DIR)
    parser.add_argument('--python', default=sys.executable,
                        help='Python interpreter with splat installed (default: this one)')
    args = parser.parse_args(argv)
    try:
        summary = split_image(args.bin, args.work_dir, config_dir=args.config_dir,
                              splat=(args.python, '-m', 'splat'))
    except (OSError, KeyError, DiscFormatError, ExeFormatError, SplitError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 2
    for exe in summary['executables']:
        print(f"{exe['path']} -> {exe['config']}")
    for item in summary['asm_files']:
        print(f"asm/{item['file']}: {item['functions']} functions, "
              f"{item['data_labels']} data labels, "
              f"{item['invalid_instruction_words']} invalid words")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
