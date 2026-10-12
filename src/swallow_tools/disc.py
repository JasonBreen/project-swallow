"""Read ISO 9660 directory metadata from a raw PlayStation MODE2/2352 BIN.

This tool deliberately does not copy or extract copyrighted game files.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import hashlib
import re

RAW_SECTOR_SIZE = 2352
USER_DATA_OFFSET = 24
USER_DATA_SIZE = 2048
PRIMARY_VOLUME_DESCRIPTOR_LBA = 16
REV1_JP_DISC1_MD5 = '30f7dce98b6901290cb26c9baf27268f'
REV1_JP_DISC2_MD5 = '50760963ae8a26ea0188b165f05b33a1'


class DiscFormatError(ValueError):
    """The provided file does not have the expected disc structure."""


class Form2SectorError(DiscFormatError):
    """A valid MODE2 Form 2 sector needs a separate media reader."""


@dataclass(frozen=True)
class FileEntry:
    path: str
    size: int
    lba: int
    is_directory: bool
    raw_name: str
    flags: int


@dataclass(frozen=True)
class CueReference:
    filename: str
    present: bool


def cue_references(path: Path) -> list[CueReference]:
    """Find FILE entries and report whether their named files exist.

    A mismatch is common in redistributed archives. Never modify an original
    archive or disc to repair it without preserving its original state.
    """
    data = path.read_text(encoding='utf-8-sig', errors='replace')
    file_re = re.compile(r'^\s*FILE\s+(?:"([^"]+)"|(\S+))\s+\S+', re.I)
    refs: list[CueReference] = []
    for line in data.splitlines():
        found = file_re.match(line)
        if found:
            name = found.group(1) or found.group(2)
            # Disallow traversal/outside paths for metadata lookups.
            candidate = Path(name.replace('\\', '/'))
            safe = not candidate.is_absolute() and '..' not in candidate.parts and ':' not in name
            resolved = (path.parent / candidate).resolve()
            present = safe and resolved.is_relative_to(path.parent.resolve()) and resolved.is_file()
            refs.append(CueReference(filename=name, present=present))
    return refs


def file_hashes(path: Path) -> dict[str, str]:
    """Optional full image checksums, streaming in bounded memory."""
    digests = {'md5': hashlib.md5(usedforsecurity=False), 'sha1': hashlib.sha1(usedforsecurity=False), 'sha256': hashlib.sha256()}
    with path.open('rb') as file:
        while block := file.read(4 * 1024 * 1024):
            for digest in digests.values():
                digest.update(block)
    return {name: digest.hexdigest() for name, digest in digests.items()}


class PlayStationDisc:
    """Minimal read-only ISO 9660 directory scanner for MODE2/2352 tracks."""

    def __init__(self, path: Path):
        self.path = Path(path)
        if not self.path.is_file():
            raise FileNotFoundError(self.path)
        size = self.path.stat().st_size
        if size % RAW_SECTOR_SIZE:
            raise DiscFormatError('Not a whole number of 2352-byte raw CD sectors')
        if size < (PRIMARY_VOLUME_DESCRIPTOR_LBA + 1) * RAW_SECTOR_SIZE:
            raise DiscFormatError('Disc is too small to contain a primary volume descriptor')
        self.sector_count = size // RAW_SECTOR_SIZE

    def _sector(self, file, lba: int) -> bytes:
        if lba < 0 or lba >= self.sector_count:
            raise DiscFormatError(f'LBA {lba} is outside the disc')
        file.seek(lba * RAW_SECTOR_SIZE)
        raw = file.read(RAW_SECTOR_SIZE)
        if len(raw) != RAW_SECTOR_SIZE or raw[:12] != b'\x00' + b'\xff' * 10 + b'\x00':
            raise DiscFormatError(f'Invalid raw CD sector at LBA {lba}')
        if raw[15] != 2:
            raise DiscFormatError(f'Expected MODE2 sector at LBA {lba}')
        # MODE2 Form 2 sectors carry 2324 payload bytes, not 2048;
        # ISO filesystem metadata should be stored in Form 1 sectors.
        if raw[18] & 0x20:
            raise Form2SectorError(f'MODE2 Form 2 sector at LBA {lba}; XA-aware hashing is not implemented')
        return raw[USER_DATA_OFFSET: USER_DATA_OFFSET + USER_DATA_SIZE]

    def _logical_bytes(self, file, lba: int, length: int) -> bytes:
        if length > 16 * 1024 * 1024:
            raise DiscFormatError('Directory metadata exceeds 16 MiB safety limit')
        blocks = (length + USER_DATA_SIZE - 1) // USER_DATA_SIZE
        if lba < 0 or length < 0 or lba + blocks > self.sector_count:
            raise DiscFormatError('Directory extends beyond disc')
        return b''.join(self._sector(file, lba + i) for i in range(blocks))[:length]

    @staticmethod
    def _record(data: bytes) -> tuple[int, int, int, str] | None:
        if len(data) < 34 or data[0] > len(data):
            raise DiscFormatError('Malformed ISO 9660 directory record')
        name_len = data[32]
        if 33 + name_len > len(data):
            raise DiscFormatError('Truncated ISO 9660 filename')
        raw_name = data[33:33 + name_len].decode('latin-1')
        if raw_name in ('\x00', '\x01'):
            return None
        lba = int.from_bytes(data[2:6], 'little')
        size = int.from_bytes(data[10:14], 'little')
        return lba, size, data[25], raw_name

    def inspect(self) -> dict:
        """Return disc metadata and filenames, never copyrighted file bytes."""
        with self.path.open('rb') as file:
            pvd = self._sector(file, PRIMARY_VOLUME_DESCRIPTOR_LBA)
            if pvd[:7] != b'\x01CD001\x01':
                raise DiscFormatError('ISO 9660 primary volume descriptor not found at LBA 16')
            volume_id = pvd[40:72].decode('latin-1').strip()
            root_len = pvd[156]
            root = pvd[156:156 + root_len]
            root_lba = int.from_bytes(root[2:6], 'little')
            root_size = int.from_bytes(root[10:14], 'little')
            entries: list[FileEntry] = []
            seen: set[tuple[int, int]] = set()

            def walk(prefix: str, lba: int, size: int, depth: int) -> None:
                if depth > 12:
                    raise DiscFormatError('Directory nesting exceeds safety limit')
                if (lba, size) in seen:
                    return
                seen.add((lba, size))
                data = self._logical_bytes(file, lba, size)
                pos = 0
                while pos < len(data):
                    rec_len = data[pos]
                    if rec_len == 0:
                        pos = (pos // USER_DATA_SIZE + 1) * USER_DATA_SIZE
                        continue
                    if rec_len < 34 or (pos % USER_DATA_SIZE) + rec_len > USER_DATA_SIZE:
                        raise DiscFormatError(f'Invalid ISO 9660 record at directory offset {pos}')
                    record = self._record(data[pos:pos + rec_len])
                    pos += rec_len
                    if record is None:
                        continue
                    entry_lba, entry_size, flags, raw_name = record
                    name = raw_name.split(';')[0]
                    name_path = f'{prefix}/{name}' if prefix else name
                    is_dir = bool(flags & 0x02)
                    entries.append(FileEntry(name_path, entry_size, entry_lba, is_dir, raw_name, flags))
                    if is_dir:
                        walk(name_path, entry_lba, entry_size, depth + 1)

            walk('', root_lba, root_size, 0)
        return {
            'source_image': self.path.name,
            'size_bytes': self.path.stat().st_size,
            'sector_size': RAW_SECTOR_SIZE,
            'sector_count': self.sector_count,
            'volume_id': volume_id,
            'files': [asdict(e) for e in entries],
        }
