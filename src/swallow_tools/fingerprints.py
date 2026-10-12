"""Streaming file hashes, explicitly limited to contiguous MODE2 Form 1 files."""
from __future__ import annotations
import hashlib
from typing import Any
from .disc import PlayStationDisc, DiscFormatError, Form2SectorError, USER_DATA_SIZE


def add_file_hashes(disc: PlayStationDisc, report: dict[str, Any]) -> None:
    """Attach logical file hashes in place. Never emit file contents.

    Form 2 media is deliberately reported as unsupported, rather than silently
    losing bytes by treating its payload as a 2048-byte Form 1 sector.
    Malformed sectors/bounds remain hard errors, not an 'unsupported' result.
    """
    with disc.path.open('rb') as stream:
        for entry in report['files']:
            if entry['is_directory']:
                continue
            if entry['flags'] & 0x80:
                entry['content_hash'] = {'status': 'unsupported_multi_extent', 'sha256': None}
                continue
            size, lba = entry['size'], entry['lba']
            sectors = (size + USER_DATA_SIZE - 1) // USER_DATA_SIZE
            if size < 0 or lba < 0 or lba + sectors > disc.sector_count:
                raise DiscFormatError(f"File extent outside image: {entry['path']}")
            digest = hashlib.sha256()
            remaining = size
            try:
                for offset in range(sectors):
                    payload = disc._sector(stream, lba + offset)
                    count = min(remaining, USER_DATA_SIZE)
                    digest.update(payload[:count]); remaining -= count
            except Form2SectorError as exc:
                entry['content_hash'] = {
                    'status': 'unsupported_form2', 'sha256': None, 'reason': str(exc)
                }
            else:
                entry['content_hash'] = {
                    'status': 'verified_form1', 'sha256': digest.hexdigest(),
                    'hashed_bytes': size,
                }
