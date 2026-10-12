"""BPH contracts exercised entirely with synthetic indices and raw CD sectors."""
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import json
import struct
import tempfile
import unittest

from swallow_tools.bph import BphFormatError, inspect_bph, main, parse_bph
from swallow_tools.disc import DiscFormatError, FileEntry, PlayStationDisc
from test_disc import iso_record, raw_sector


def make_index(records, *, magic=b'AC3E', archive_type=3,
               unknown=(0x1234, 0x5678, 0x90ABCDEF), count=None):
    return struct.pack('<4sIHHII', magic, archive_type, *unknown,
                       len(records) if count is None else count) + b''.join(
                           struct.pack('<II', *record) for record in records)


def make_image(root, index, *, bph_lba=27, bpb_lba=32, bpb_size=8192,
               bpb_flags=0, missing_bpb=False, duplicate_bph=False,
               bad_bph_sector=None, bph_form2_sector=None):
    pvd = bytearray(2048)
    pvd[:7] = b'\x01CD001\x01'
    pvd[40:72] = b'SWALLOW_SYNTHETIC'.ljust(32, b' ')
    root_record = iso_record(b'\x00', 22, 2048, 2)
    pvd[156:156 + len(root_record)] = root_record
    directory = root_record + iso_record(b'\x01', 22, 2048, 2)
    directory += iso_record(b'ACE.BPH;1', bph_lba, len(index))
    if duplicate_bph:
        directory += iso_record(b'ACE.BPH;2', bph_lba, len(index))
    if not missing_bpb:
        directory += iso_record(b'ACE.BPB;1', bpb_lba, bpb_size, bpb_flags)
    # Deliberately fixed image capacity lets tests declare impossible extents.
    path = root / 'synthetic.bin'
    with path.open('wb') as stream:
        for lba in range(48):
            payload = b''
            if lba == 16:
                payload = pvd
            elif lba == 22:
                payload = directory
            elif bph_lba <= lba < bph_lba + (len(index) + 2047) // 2048:
                offset = (lba - bph_lba) * 2048
                payload = index[offset:offset + 2048]
            raw = bytearray(raw_sector(payload, lba))
            if lba == bad_bph_sector:
                raw[15] = 1
            if lba == bph_form2_sector:
                raw[18] = raw[22] = 0x20
            stream.write(raw)
    return path


class BphParserTests(unittest.TestCase):
    def test_sizes_are_explicit_not_distances_to_next_offset(self):
        result = parse_bph(make_index([(0xE0000003, 0), (0xE0000001, 2)]), 6144)
        first, last = result.entries
        self.assertEqual((first.offset_bytes, first.size_bytes, first.gap_after_bytes),
                         (0, 12, 4084))
        self.assertEqual((last.offset_bytes, last.size_bytes, last.gap_after_bytes),
                         (4096, 4, 2044))
        self.assertEqual(result.to_report()['normal_payload_bytes'], 16)

    def test_unaligned_bpb_eof_exact_fit_is_valid(self):
        result = parse_bph(make_index([(0xE0000003, 1)]), 2060)
        self.assertEqual(result.entries[0].gap_after_bytes, 0)
        self.assertEqual(result.to_report()['leading_gap_bytes'], 2048)

    def test_all_header_and_record_bytes_are_recoverable(self):
        raw = make_index([(0xA0000001, 0), (0x50000011, 0x12345678)])
        result = parse_bph(raw, 2048)
        h = result.header
        reconstructed = struct.pack('<4sIHHII', h.magic.encode(), h.archive_type,
                                    h.unknown_08, h.unknown_0a, h.unknown_0c,
                                    h.entry_count) + b''.join(
                                        struct.pack('<II', e.descriptor, e.sector_word)
                                        for e in result.entries)
        self.assertEqual(raw, reconstructed)
        self.assertEqual(result.to_report()['accounted_index_bytes'], len(raw))
        self.assertFalse(result.entries[0].unknown_bit30)

    def test_special_records_never_become_zero_offset_payloads(self):
        result = parse_bph(make_index([(0xE0000001, 1), (0x50001234, 0),
                                      (0xE0000002, 2)]), 8192)
        special = result.entries[1]
        self.assertEqual(special.kind, 'special_uninterpreted')
        self.assertIsNone(special.offset_bytes)
        self.assertIsNone(special.size_bytes)
        self.assertTrue(all(e.gap_after_bytes is None for e in result.entries))
        self.assertEqual(result.to_report()['bounds_status'], 'partial_special_records')
        self.assertEqual(result.to_report()['normal_payload_bytes'], 12)
        self.assertTrue(result.warnings)

    def test_special_only_preserves_all_words(self):
        result = parse_bph(make_index([(0, 0), (0x7FFFFFFF, 0xFFFFFFFF)]), 0)
        self.assertEqual(result.to_report()['special_entry_count'], 2)
        self.assertEqual(result.entries[1].sector_word, 0xFFFFFFFF)

    def test_zero_length_is_explicit_and_not_a_guessed_sentinel(self):
        result = parse_bph(make_index([(0xE0000000, 1)]), 2048)
        self.assertEqual(result.entries[0].size_bytes, 0)
        self.assertEqual(result.entries[0].kind, 'normal')
        self.assertIn('Zero-length', result.warnings[0])

    def test_empty_index(self):
        result = parse_bph(make_index([]), 0)
        self.assertEqual(result.entries, ())
        self.assertEqual(result.to_report()['accounted_index_bytes'], 20)

    def test_truncated_header(self):
        for length in (0, 4, 19):
            with self.subTest(length=length), self.assertRaises(BphFormatError):
                parse_bph(make_index([])[:length], 0)

    def test_bad_magic_and_sph_type_rejected(self):
        for raw in (make_index([], magic=b'NOPE'), make_index([], archive_type=1)):
            with self.subTest(raw=raw), self.assertRaises(BphFormatError):
                parse_bph(raw, 0)

    def test_count_mismatch_truncation_and_trailing_bytes(self):
        good = make_index([(0xE0000001, 0)])
        for raw in (good[:-1], good + b'\0', make_index([], count=0xFFFFFFFF),
                    make_index([(0xE0000001, 0)], count=0)):
            with self.subTest(length=len(raw)), self.assertRaises(BphFormatError):
                parse_bph(raw, 2048)

    def test_index_size_limit(self):
        with self.assertRaisesRegex(BphFormatError, '1 MiB'):
            parse_bph(b'\0' * (1024 * 1024 + 1), 0)

    def test_invalid_bpb_size(self):
        for size in (-1, 1.5, True):
            with self.subTest(size=size), self.assertRaises(BphFormatError):
                parse_bph(make_index([]), size)

    def test_offset_and_length_past_eof(self):
        for descriptor, sector in ((0xE0000001, 1), (0xE0000201, 0),
                                   (0xE0000000, 0xFFFFFFFF)):
            with self.subTest(sector=sector), self.assertRaisesRegex(BphFormatError, 'exceeds BPB'):
                parse_bph(make_index([(descriptor, sector)]), 2048)

    def test_overlap_and_reversed_offsets(self):
        cases = [[(0xE0000300, 0), (0xE0000001, 1)],
                 [(0xE0000001, 2), (0xE0000001, 1)],
                 [(0xE0000001, 0), (0xE0000001, 0)]]
        for records in cases:
            with self.subTest(records=records), self.assertRaises(BphFormatError):
                parse_bph(make_index(records), 8192)

    def test_unsupported_normal_flags(self):
        for descriptor in (0x80000001, 0xE0080001, 0xE1000001, 0xF0000001):
            with self.subTest(word=descriptor), self.assertRaisesRegex(BphFormatError, 'flags'):
                parse_bph(make_index([(descriptor, 0)]), 2048)

    def test_maximum_size_field_is_four_byte_units(self):
        result = parse_bph(make_index([(0xE007FFFF, 0)]), 2097148)
        self.assertEqual(result.entries[0].size_bytes, 2097148)
        self.assertEqual(result.entries[0].gap_after_bytes, 0)


class BphDiscTests(unittest.TestCase):
    def test_iso_relocation_does_not_change_archive_offsets(self):
        with tempfile.TemporaryDirectory() as folder:
            records = make_index([(0xE0000001, 1)])
            left = inspect_bph(make_image(Path(folder), records, bph_lba=25, bpb_lba=31))
            right = inspect_bph(make_image(Path(folder), records, bph_lba=28, bpb_lba=40))
        self.assertEqual(left['entries'], right['entries'])
        self.assertEqual(left['entries'][0]['offset_bytes'], 2048)
        self.assertNotEqual(left['bpb']['lba'], right['bpb']['lba'])

    def test_multisector_index_is_complete(self):
        raw = make_index([(0xE0000000, 0)] * 600)
        with tempfile.TemporaryDirectory() as folder:
            report = inspect_bph(make_image(Path(folder), raw))
        self.assertEqual(report['normal_entry_count'], 600)
        self.assertEqual(report['accounted_index_bytes'], 4820)

    def test_missing_or_duplicate_file_is_rejected(self):
        for options in ({'missing_bpb': True}, {'duplicate_bph': True}):
            with tempfile.TemporaryDirectory() as folder, self.subTest(options=options):
                path = make_image(Path(folder), make_index([]), **options)
                with self.assertRaisesRegex(BphFormatError, 'exactly one'):
                    inspect_bph(path)

    def test_bpb_multiextent_and_out_of_image_bounds(self):
        for options in ({'bpb_flags': 0x80}, {'bpb_lba': 47}):
            with tempfile.TemporaryDirectory() as folder, self.subTest(options=options):
                path = make_image(Path(folder), make_index([]), **options)
                with self.assertRaises(DiscFormatError):
                    inspect_bph(path)

    def test_overlapping_iso_extents_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = make_image(Path(folder), make_index([]), bph_lba=32)
            with self.assertRaisesRegex(BphFormatError, 'ISO extents overlap'):
                inspect_bph(path)

    def test_invalid_mode_or_form2_on_later_index_sector(self):
        raw = make_index([(0xE0000000, 0)] * 600)
        for options in ({'bad_bph_sector': 28}, {'bph_form2_sector': 28}):
            with tempfile.TemporaryDirectory() as folder, self.subTest(options=options):
                path = make_image(Path(folder), raw, **options)
                with self.assertRaises(DiscFormatError):
                    inspect_bph(path)

    def test_logical_range_crosses_sector_boundary(self):
        raw = make_index([(0xE0000000, 0)] * 600)
        with tempfile.TemporaryDirectory() as folder:
            disc = PlayStationDisc(make_image(Path(folder), raw))
            entry = FileEntry(**disc.inspect()['files'][0])
            self.assertEqual(disc.read_file(entry, offset=2045, length=20), raw[2045:2065])
            self.assertEqual(disc.read_file(entry, offset=len(raw), length=0), b'')
            for offset, length in ((-1, 1), (0, -1), (len(raw), 1)):
                with self.subTest(offset=offset), self.assertRaises(DiscFormatError):
                    disc.read_file(entry, offset=offset, length=length)

    def test_cli_json_and_text(self):
        with tempfile.TemporaryDirectory() as folder:
            path = make_image(Path(folder), make_index([(0xE0000003, 0)]))
            output = StringIO()
            with redirect_stdout(output):
                status = main([str(path), '--json'])
            self.assertEqual(status, 0)
            report = json.loads(output.getvalue())
            self.assertEqual(report['entries'][0]['size_bytes'], 12)
            self.assertNotIn(str(path.parent), output.getvalue())
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(main([str(path)]), 0)
            self.assertIn('1 normal entries', output.getvalue())

    def test_cli_invalid_input_has_nonzero_exit_and_no_json(self):
        with tempfile.TemporaryDirectory() as folder:
            invalid = make_image(Path(folder), b'AC3E')
            for path in (invalid, Path(folder) / 'absent.bin'):
                output, error = StringIO(), StringIO()
                with redirect_stdout(output), redirect_stderr(error):
                    self.assertEqual(main([str(path), '--json']), 2)
                self.assertEqual(output.getvalue(), '')
                self.assertIn('ERROR:', error.getvalue())


if __name__ == '__main__':
    unittest.main()
