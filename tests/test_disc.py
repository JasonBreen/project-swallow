import tempfile
import unittest
from pathlib import Path

from swallow_tools.disc import (
    PlayStationDisc, DiscFormatError, cue_references, RAW_SECTOR_SIZE,
)


def iso_record(name: bytes, lba: int, size: int, flags: int = 0) -> bytes:
    total = 33 + len(name) + (len(name) % 2 == 0)
    r = bytearray(total)
    r[0] = total
    r[2:6] = lba.to_bytes(4, 'little')
    r[6:10] = lba.to_bytes(4, 'big')
    r[10:14] = size.to_bytes(4, 'little')
    r[14:18] = size.to_bytes(4, 'big')
    r[25] = flags
    r[28:30] = (1).to_bytes(2, 'little')
    r[30:32] = (1).to_bytes(2, 'big')
    r[32] = len(name)
    r[33:33 + len(name)] = name
    return bytes(r)


def raw_sector(user_data: bytes, lba: int) -> bytes:
    raw = bytearray(RAW_SECTOR_SIZE)
    raw[:12] = b'\x00' + b'\xff' * 10 + b'\x00'
    raw[15] = 2
    raw[16:24] = b'\x00' * 8
    raw[24:24 + min(len(user_data), 2048)] = user_data[:2048]
    return bytes(raw)


class DiscTests(unittest.TestCase):
    def test_minimal_iso_root_and_volume(self):
        with tempfile.TemporaryDirectory() as directory:
            disc = Path(directory) / 'test.bin'
            pvd = bytearray(2048)
            pvd[:7] = b'\x01CD001\x01'
            pvd[40:72] = b'TEST_SWALLOW'.ljust(32, b' ')
            root_record = iso_record(b'\x00', 22, 2048, flags=2)
            pvd[156:156 + len(root_record)] = root_record
            directory_data = (iso_record(b'\x00', 22, 2048, 2) +
                              iso_record(b'\x01', 22, 2048, 2) +
                              iso_record(b'FILE.BIN;1', 42, 1234))
            with disc.open('wb') as f:
                for sector in range(45):
                    data = pvd if sector == 16 else directory_data if sector == 22 else b''
                    f.write(raw_sector(data, sector))
            info = PlayStationDisc(disc).inspect()
            self.assertEqual(info['volume_id'], 'TEST_SWALLOW')
            self.assertEqual(info['files'][0]['path'], 'FILE.BIN')
            self.assertEqual(info['files'][0]['lba'], 42)
            self.assertEqual(info['files'][0]['size'], 1234)

    def test_cue_detects_missing_referenced_bin(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cue = root / 'disc.cue'
            cue.write_text('FILE "wrong name.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n')
            self.assertEqual(cue_references(cue)[0].present, False)
            (root / 'wrong name.bin').write_bytes(b'')
            self.assertEqual(cue_references(cue)[0].present, True)

    def test_rejects_wrong_sector_size(self):
        with tempfile.TemporaryDirectory() as directory:
            disc = Path(directory) / 'wrong.bin'
            disc.write_bytes(b'x' * 40000)
            with self.assertRaises(DiscFormatError):
                PlayStationDisc(disc)


if __name__ == '__main__':
    unittest.main()
