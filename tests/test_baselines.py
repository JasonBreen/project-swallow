"""Synthetic tests: no commercial bytes, source discs, or external services."""
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from pathlib import Path
import hashlib
import json
import tempfile
import unittest

from swallow_tools.cli import main as inspect_main
from swallow_tools.compare import compare_reports
from swallow_tools.disc import PlayStationDisc, DiscFormatError, cue_references, file_hashes
from swallow_tools.fingerprints import add_file_hashes
from swallow_tools.profiles import PROFILES, identify_disc
from test_disc import iso_record, raw_sector


def make_disc(root: Path, content: bytes = b'known synthetic data', *, form2=False, bad_mode=False) -> Path:
    path = root / 'sample.bin'
    pvd = bytearray(2048); pvd[:7] = b'\x01CD001\x01'
    pvd[40:72] = b'SWALLOW_TEST'.ljust(32, b' ')
    rec = iso_record(b'\x00', 22, 2048, 2)
    pvd[156:156+len(rec)] = rec
    directory = rec + iso_record(b'\x01', 22, 2048, 2) + iso_record(b'FILE.BIN;1', 30, len(content))
    with path.open('wb') as stream:
        for lba in range(50):
            payload = pvd if lba == 16 else directory if lba == 22 else content[(lba-30)*2048:(lba-29)*2048] if 30 <= lba < 40 else b''
            raw = bytearray(raw_sector(payload, lba))
            if lba == 30:
                if form2: raw[18] = raw[22] = 0x20
                if bad_mode: raw[15] = 1
            stream.write(raw)
    return path


def report_entry(name='FILE.BIN', digest='a', status='verified_form1', lba=30, size=20):
    return {'path':name, 'size':size, 'lba':lba, 'is_directory':False,
            'content_hash':{'status':status, 'sha256':digest}}


def compare_one(left, right):
    return compare_reports({'source_image':'left.bin','files':left},
                           {'source_image':'right.bin','files':right})


class ProfileTests(unittest.TestCase):
    def test_japanese_disc2_is_recognized(self):
        digest, p = next((h,p) for h,p in PROFILES.items() if p['id']=='jp2')
        result = identify_disc({'sha256':digest,'md5':p['md5']},p['size_bytes'])
        self.assertEqual(result['disc_number'],2)
        self.assertIn('Asian',result['provenance'])

    def test_wrong_size_not_recognized(self):
        h,p = next(iter(PROFILES.items()))
        self.assertIsNone(identify_disc({'sha256':h,'md5':p['md5']},p['size_bytes']-1))

    def test_wrong_md5_not_recognized(self):
        h,p = next(iter(PROFILES.items()))
        self.assertIsNone(identify_disc({'sha256':h,'md5':'bad'},p['size_bytes']))

    def test_unknown_not_recognized(self):
        self.assertIsNone(identify_disc({'sha256':'bad'},123))

    def test_patched_profile_is_not_certified_release(self):
        for p in PROFILES.values():
            if p['id'].startswith('en'):
                self.assertIn('not independently authenticated',p['provenance'])


class FingerprintTests(unittest.TestCase):
    def test_multisector_content_excludes_padding(self):
        with tempfile.TemporaryDirectory() as t:
            content = bytes(range(256))*13+b'partial-sector'
            d=PlayStationDisc(make_disc(Path(t),content)); r=d.inspect(); add_file_hashes(d,r)
            value=r['files'][0]['content_hash']
            self.assertEqual(value['sha256'],hashlib.sha256(content).hexdigest())
            self.assertEqual(value['hashed_bytes'],len(content))

    def test_empty_file_hash(self):
        with tempfile.TemporaryDirectory() as t:
            d=PlayStationDisc(make_disc(Path(t),b''));r=d.inspect();add_file_hashes(d,r)
            self.assertEqual(r['files'][0]['content_hash']['sha256'],hashlib.sha256(b'').hexdigest())

    def test_form2_is_explicitly_unhashed(self):
        with tempfile.TemporaryDirectory() as t:
            d=PlayStationDisc(make_disc(Path(t),form2=True));r=d.inspect();add_file_hashes(d,r)
            self.assertEqual(r['files'][0]['content_hash']['status'],'unsupported_form2')
            self.assertIsNone(r['files'][0]['content_hash']['sha256'])

    def test_malformed_sector_is_error_not_skip(self):
        with tempfile.TemporaryDirectory() as t:
            d=PlayStationDisc(make_disc(Path(t),bad_mode=True));r=d.inspect()
            with self.assertRaises(DiscFormatError):add_file_hashes(d,r)

    def test_out_of_bounds_file_is_error(self):
        with tempfile.TemporaryDirectory() as t:
            d=PlayStationDisc(make_disc(Path(t)));r=d.inspect();r['files'][0]['lba']=100
            with self.assertRaises(DiscFormatError):add_file_hashes(d,r)

    def test_multi_extent_not_silently_flattened(self):
        with tempfile.TemporaryDirectory() as t:
            d=PlayStationDisc(make_disc(Path(t)));r=d.inspect();r['files'][0]['flags']|=0x80
            add_file_hashes(d,r)
            self.assertEqual(r['files'][0]['content_hash']['status'],'unsupported_multi_extent')

    def test_streaming_whole_image_hashes(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'small.bin';data=b'local hash fixture';p.write_bytes(data)
            result=file_hashes(p)
            self.assertEqual(result['md5'],hashlib.md5(data).hexdigest())
            self.assertEqual(result['sha1'],hashlib.sha1(data).hexdigest())
            self.assertEqual(result['sha256'],hashlib.sha256(data).hexdigest())


class ComparisonTests(unittest.TestCase):
    def test_identical_at_different_offsets(self):
        r=compare_one([report_entry(lba=30)],[report_entry(lba=40)])
        self.assertEqual(r['files'][0]['status'],'identical_form1_content')

    def test_changed_digest(self):
        r=compare_one([report_entry(digest='a')],[report_entry(digest='b')])
        self.assertEqual(r['files'][0]['status'],'different_form1_content')

    def test_unhashed_is_not_identical(self):
        r=compare_one([report_entry(digest=None,status='unsupported_form2')],
                      [report_entry(digest=None,status='unsupported_form2')])
        self.assertEqual(r['files'][0]['status'],'content_unverified')

    def test_missing_files(self):
        r=compare_one([report_entry(name='LEFT')],[report_entry(name='RIGHT')])
        self.assertEqual({x['status'] for x in r['files']},{'only_left','only_right'})

    def test_mismatched_sizes_not_identical(self):
        r=compare_one([report_entry(size=20)],[report_entry(size=21)])
        self.assertEqual(r['files'][0]['status'],'different_form1_content')


class CueAndCliTests(unittest.TestCase):
    def test_traversal_reference_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);(root/'outside.bin').write_bytes(b'');(root/'sub').mkdir()
            cue=root/'sub/disc.cue';cue.write_text('FILE "../outside.bin" BINARY\n')
            self.assertFalse(cue_references(cue)[0].present)

    def test_windows_absolute_reference_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            cue=Path(t)/'disc.cue';cue.write_text('FILE "C:\\game.bin" BINARY\n')
            self.assertFalse(cue_references(cue)[0].present)

    def test_strict_missing_cue_returns_3(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);p=make_disc(root);cue=root/'disc.cue'
            cue.write_text('FILE "missing.bin" BINARY\n')
            out=StringIO()
            with redirect_stdout(out):code=inspect_main([str(p),'--cue',str(cue),'--strict-cue','--json'])
            self.assertEqual(code,3);self.assertFalse(json.loads(out.getvalue())['cue']['references_ok'])

    def test_strict_empty_cue_returns_3(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);p=make_disc(root);cue=root/'disc.cue';cue.write_text('REM no FILE\n')
            with redirect_stdout(StringIO()):code=inspect_main([str(p),'--cue',str(cue),'--strict-cue'])
            self.assertEqual(code,3)

    def test_strict_valid_cue_returns_0(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);p=make_disc(root);cue=root/'disc.cue';cue.write_text('FILE "sample.bin" BINARY\n')
            with redirect_stdout(StringIO()):code=inspect_main([str(p),'--cue',str(cue),'--strict-cue'])
            self.assertEqual(code,0)

    def test_missing_image_returns_2(self):
        with tempfile.TemporaryDirectory() as t, redirect_stderr(StringIO()):
            self.assertEqual(inspect_main([str(Path(t)/'absent.bin')]),2)

    def test_strict_cue_requires_cue(self):
        with redirect_stderr(StringIO()),self.assertRaises(SystemExit) as ctx:
            inspect_main(['unused.bin','--strict-cue'])
        self.assertEqual(ctx.exception.code,2)

    def test_json_includes_hash_limits_and_unknown_profile(self):
        with tempfile.TemporaryDirectory() as t:
            p=make_disc(Path(t),form2=True);out=StringIO()
            with redirect_stdout(out):code=inspect_main([str(p),'--hash','--file-hashes','--json'])
            r=json.loads(out.getvalue())
            self.assertEqual(code,0);self.assertIsNone(r['profile'])
            self.assertEqual(r['files'][0]['content_hash']['status'],'unsupported_form2')

if __name__=='__main__':unittest.main()
