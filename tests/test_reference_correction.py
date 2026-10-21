"""Synthetic coverage for the measured supplementary-disc transformation."""
from io import BytesIO
from pathlib import Path
import importlib.util
import json
import tempfile
import unittest

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
