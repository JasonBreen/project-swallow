"""Synthetic coverage for the measured supplementary-disc transformation."""
from io import BytesIO
from pathlib import Path
import hashlib
import importlib.util
import json
import tempfile
import unittest
import zlib

spec = importlib.util.spec_from_file_location('reference_correction',
    Path(__file__).resolve().parents[1] / 'scripts/prepare_acecombat_reference.py')
correction = importlib.util.module_from_spec(spec)
spec.loader.exec_module(correction)


class CorrectionTests(unittest.TestCase):
    def test_unrecognized_source_is_refused_before_creating_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.bin'
            source.write_bytes(b'unknown synthetic disc')
            reference = root / 'reference.json'
            reference.write_text(json.dumps({'source_image_sha256': '0' * 64}))
            output = root / 'derived'
            with self.assertRaisesRegex(ValueError, 'fingerprinted source'):
                correction.prepare(source, root / 'unused.cue', output, reference)
            self.assertFalse(output.exists())

    def test_data_unchanged_and_audio_advanced_inside_materialized_gap(self):
        source = BytesIO(b'DATAabcdef')
        target = BytesIO()
        correction.materialize_audio(source, target, source_size=10, data_bytes=4,
                                      pregap_bytes=4, offset_bytes=1)
        self.assertEqual(target.getvalue(), b'DATA\x00\x00\x00abcdef\x00')
        self.assertEqual(source.getvalue(), b'DATAabcdef')

    def test_zero_offset_materializes_full_gap(self):
        target = BytesIO()
        correction.materialize_audio(BytesIO(b'DA'), target, source_size=2,
                                      data_bytes=1, pregap_bytes=3, offset_bytes=0)
        self.assertEqual(target.getvalue(), b'D\x00\x00\x00A')

    def test_bad_bounds_and_short_or_long_source_are_rejected(self):
        for args in ({'source_size': 2, 'data_bytes': 3, 'pregap_bytes': 4, 'offset_bytes': 1},
                     {'source_size': 2, 'data_bytes': 1, 'pregap_bytes': 1, 'offset_bytes': 2},
                     {'source_size': 3, 'data_bytes': 1, 'pregap_bytes': 4, 'offset_bytes': 1},
                     {'source_size': 1, 'data_bytes': 1, 'pregap_bytes': 4, 'offset_bytes': 1}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                correction.materialize_audio(BytesIO(b'DA'), BytesIO(), **args)

    def synthetic_reference(self, root, *, corrupt_track=None):
        """A tiny source BIN, CUE, and 63-track reference that prepare() accepts."""
        data, audio = b'DATA' * 2, b'audio' * 4
        source = root / 'source.bin'
        source.write_bytes(data + audio)
        cue = root / 'source.cue'
        cue.write_text('FILE "source.bin" BINARY\n  TRACK 01 MODE2/2352\n'
                       '    INDEX 01 00:00:00\n  TRACK 02 AUDIO\n'
                       '    PREGAP 00:02:00\n    INDEX 01 00:00:01\n', encoding='ascii')
        gap, offset = 150 * 2352, 32
        derived = data + bytes(gap - offset) + audio + bytes(offset)
        pieces = [data, derived[len(data):]] + [b''] * 61
        tracks = [{'track': number, 'reference_size_bytes': len(piece),
                   'reference_sha1': hashlib.sha1(piece).hexdigest(),
                   'reference_crc32': f'{zlib.crc32(piece):08x}'}
                  for number, piece in enumerate(pieces, 1)]
        if corrupt_track:
            tracks[corrupt_track - 1]['reference_sha1'] = '0' * 40
        reference = root / 'reference.json'
        reference.write_text(json.dumps({
            'source_image_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
            'source_cue_sha256': hashlib.sha256(cue.read_bytes()).hexdigest(),
            'reference_commit': 'synthetic', 'tracks': tracks,
            'correction': {'materialized_pregap_bytes': gap, 'audio_read_offset_bytes': offset}}))
        return source, cue, reference, derived

    def test_success_publishes_one_complete_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, cue, reference, derived = self.synthetic_reference(root)
            output = root / 'derived'
            result = correction.prepare(source, cue, output, reference)
            self.assertTrue(result['all_63_reference_tracks_match'])
            self.assertEqual(sorted(path.name for path in output.iterdir()),
                             ['ACECOMBAT.reference.bin', 'ACECOMBAT.reference.cue',
                              'verification.json'])
            self.assertEqual((output / 'ACECOMBAT.reference.bin').read_bytes(), derived)
            self.assertEqual(sorted(path.name for path in root.iterdir()),
                             ['derived', 'reference.json', 'source.bin', 'source.cue'])

    def test_failed_verification_leaves_no_output_and_allows_a_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, cue, reference, _ = self.synthetic_reference(root, corrupt_track=2)
            output = root / 'derived'
            with self.assertRaisesRegex(ValueError, 'Derived track 2 does not match'):
                correction.prepare(source, cue, output, reference)
            self.assertEqual(sorted(path.name for path in root.iterdir()),
                             ['reference.json', 'source.bin', 'source.cue'])
            source, cue, reference, _ = self.synthetic_reference(root)
            self.assertTrue(correction.prepare(source, cue, output, reference)
                            ['all_63_reference_tracks_match'])

    def test_existing_output_is_refused_and_left_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, cue, reference, _ = self.synthetic_reference(root)
            output = root / 'derived'
            output.mkdir()
            (output / 'keep.txt').write_text('mine')
            with self.assertRaisesRegex(ValueError, 'already exists'):
                correction.prepare(source, cue, output, reference)
            self.assertEqual([path.name for path in output.iterdir()], ['keep.txt'])
            self.assertEqual(len(list(root.iterdir())), 4)

    def test_materialized_cue_preserves_data_and_shifts_audio_indexes(self):
        source = ('FILE "original.bin" BINARY\n  TRACK 01 MODE2/2352\n'
                  '    INDEX 01 00:00:00\n  TRACK 02 AUDIO\n'
                  '    PREGAP 00:02:00\n    INDEX 01 00:59:74\n'
                  '  TRACK 03 AUDIO\n    INDEX 00 01:10:00\n'
                  '    INDEX 01 01:12:00\n')
        result = correction.materialized_cue(source, 'derived.bin', 150)
        self.assertIn('FILE "derived.bin" BINARY', result)
        self.assertIn('TRACK 01 MODE2/2352\n    INDEX 01 00:00:00', result)
        self.assertIn('TRACK 02 AUDIO\n    INDEX 00 00:59:74\n    INDEX 01 01:01:74', result)
        self.assertIn('TRACK 03 AUDIO\n    INDEX 00 01:12:00\n    INDEX 01 01:14:00', result)
        self.assertNotIn('PREGAP', result)


if __name__ == '__main__':
    unittest.main()
