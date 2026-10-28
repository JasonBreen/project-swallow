"""PsyQ signature matching, exercised with synthetic signatures and images."""
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import hashlib
import json
import tempfile
import unittest

from swallow_tools.psyq import (
    GENERATED_MARKER, SignatureError, identify_image, load_signatures, main,
    match_signatures, update_symbol_files, with_generated_symbols,
)
from swallow_tools.split import DEFAULT_CONFIG_DIR, load_manifest
from test_exe import make_exe, make_image

# Ten distinct words (40 fixed bytes), so the signature clears the minimum.
BODY = bytes(range(0x10, 0x10 + 40))


def sig_text(data: bytes, wildcards=()) -> str:
    return ' '.join('??' if i in wildcards else f'{b:02X}' for i, b in enumerate(data))


class PsyqTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.sigs = self.root / 'sigs'

    def tearDown(self):
        self.tmp.cleanup()

    def write_sigs(self, version, library, entries):
        directory = self.sigs / version
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f'{library}.json').write_text(json.dumps(entries))

    def obj(self, name, data, labels, wildcards=()):
        return {'name': name, 'sig': sig_text(data, wildcards),
                'labels': [{'name': n, 'offset': o} for n, o in labels]}

    def test_places_an_object_and_names_its_global_labels(self):
        self.write_sigs('430', 'LIBX.LIB', [self.obj('A.OBJ', BODY,
                        [('FuncA', 0), ('loc_8', 8), ('text_C', 12), ('FuncB', 16)],
                        wildcards=(0, 1))])
        text = b'\x00' * 64 + b'\xAA\xBB' + BODY[2:] + b'\x00' * 16
        result = match_signatures(text, 0x80010000, load_signatures(self.sigs))
        self.assertEqual(result['objects'], [{'library': 'LIBX.LIB', 'object': 'A.OBJ',
                                              'address': '0x80010040', 'size': 40,
                                              'versions': ['430']}])
        self.assertEqual([(l['name'], l['address']) for l in result['labels']],
                         [('FuncA', '0x80010040'), ('FuncB', '0x80010050')])
        self.assertEqual(result['library_range'], ['0x80010040', '0x80010068'])
        self.assertEqual(result['library_bytes'], 40)

    def test_ambiguous_unaligned_and_short_signatures_are_ignored(self):
        self.write_sigs('430', 'LIBX.LIB', [
            self.obj('TWICE.OBJ', BODY, [('Twice', 0)]),
            self.obj('SHORT.OBJ', BODY[:28], [('Short', 0)]),
        ])
        self.write_sigs('440', 'LIBY.LIB', [self.obj('ODD.OBJ', BODY[::-1], [('Odd', 0)])])
        text = BODY + BODY + b'\x00' * 2 + BODY[::-1] + b'\x00' * 2
        result = match_signatures(text, 0x80010000, load_signatures(self.sigs))
        self.assertEqual(result['objects'], [])
        self.assertIsNone(result['library_range'])

    def test_overlapping_placements_count_as_ambiguous(self):
        repeated = b'ABCD' * 10
        self.write_sigs('430', 'LIBX.LIB', [self.obj('REPEAT.OBJ', repeated, [('Repeat', 0)])])
        sigs = load_signatures(self.sigs)
        self.assertEqual(match_signatures(b'ABCD' * 11, 0x80010000, sigs)['objects'], [])
        # An unaligned match must not hide an aligned one that overlaps it.
        self.assertEqual(len(match_signatures(b'\x00\x00' + repeated + b'\x00' * 6, 0x80010000,
                                              sigs)['objects']), 0)
        self.write_sigs('440', 'LIBY.LIB', [self.obj('RUN.OBJ', b'A' * 40, [('Run', 0)])])
        result = match_signatures(b'\x00\x00' + b'A' * 42, 0x80010000, load_signatures(self.sigs))
        self.assertEqual([o['address'] for o in result['objects']], ['0x80010004'])

    def test_versions_merge_and_more_fixed_bytes_win_overlaps(self):
        same = self.obj('A.OBJ', BODY, [('FuncA', 0)])
        self.write_sigs('430', 'LIBX.LIB', [same])
        self.write_sigs('440', 'LIBX.LIB', [same, self.obj('B.OBJ', BODY[:36] + b'\x99' * 8,
                                                           [('FuncB', 0)], wildcards=range(36, 44))])
        result = match_signatures(BODY + b'\x00' * 8, 0x80010000, load_signatures(self.sigs))
        self.assertEqual([(o['object'], o['versions']) for o in result['objects']],
                         [('A.OBJ', ['430', '440'])])

    def test_signature_files_are_validated(self):
        self.write_sigs('430', 'LIBX.LIB', [{'name': 'BSS.OBJ', 'xbss': []},
                                            self.obj('A.OBJ', BODY, [('FuncA', 0)])])
        self.assertEqual(len(load_signatures(self.sigs)), 1)
        with self.assertRaisesRegex(SignatureError, 'version'):
            load_signatures(self.sigs, ['999'])
        self.write_sigs('440', 'LIBY.LIB', [{'name': 'BAD.OBJ', 'sig': 'ZZ ' * 40, 'labels': []}])
        with self.assertRaisesRegex(SignatureError, "Bad signature byte 'ZZ'"):
            load_signatures(self.sigs)
        (self.sigs / '440' / 'LIBY.LIB.json').write_text('{not json')
        with self.assertRaisesRegex(SignatureError, 'Malformed signature file'):
            load_signatures(self.sigs)
        with self.assertRaisesRegex(SignatureError, 'No version directories'):
            load_signatures(self.root)

    def test_generated_symbols_keep_manual_entries_and_replace_old_output(self):
        result = {'labels': [
            {'name': 'InitHeap', 'address': '0x80010000', 'library': 'LIBAPI.LIB', 'object': 'C57.OBJ'},
            {'name': 'other_name', 'address': '0x80010008', 'library': 'LIBAPI.LIB', 'object': 'C57.OBJ'},
            {'name': 'FuncA', 'address': '0x80010010', 'library': 'LIBX.LIB', 'object': 'A.OBJ'},
            {'name': 'FuncAlias', 'address': '0x80010010', 'library': 'LIBX.LIB', 'object': 'A.OBJ'},
            {'name': 'FuncB', 'address': '0x80010020', 'library': 'LIBX.LIB', 'object': 'B.OBJ'},
        ]}
        manual = ('// hand-written\nInitHeap = 0x80020000; // type:func\n'
                  'boot_main = 0x80010008; // type:func\n')
        text = with_generated_symbols(manual + '\n' + GENERATED_MARKER + '\nstale = 0x1;\n', result)
        self.assertEqual(text, manual + '\n' + GENERATED_MARKER + '\n'
                         '// LIBX.LIB A.OBJ\nFuncA = 0x80010010; // type:func\n'
                         '// LIBX.LIB B.OBJ\nFuncB = 0x80010020; // type:func\n')
        self.assertEqual(with_generated_symbols(text, result), text)

    def test_identifies_image_executables_and_updates_symbol_files(self):
        boot = make_exe(t_addr=0x80120000, pc=0x80120000, text=b'\x00' * 256 + BODY + b'\x00' * 1752)
        game = make_exe(text=b'\x11' * 4096)
        image = make_image(self.root, [('BOOT.EXE', boot), ('GAME.BIN', game)])
        self.write_sigs('430', 'LIBX.LIB', [self.obj('A.OBJ', BODY, [('FuncA', 4)])])
        report = identify_image(image, load_signatures(self.sigs))
        by_path = {e['path']: e for e in report['executables']}
        self.assertEqual(by_path['BOOT.EXE']['labels'][0]['address'], '0x80120104')
        self.assertEqual(by_path['GAME.BIN']['objects'], [])

        configs = self.root / 'configs'
        configs.mkdir()
        entries = []
        for stem, data in (('boot', boot), ('game', game)):
            (configs / f'{stem}.yaml').write_text('x\n')
            (configs / f'symbols.{stem}.txt').write_text(f'{stem}_entry = 0x80010000;\n')
            entries.append({'config': f'{stem}.yaml', 'target': f'{stem}.psexe',
                            'symbols': f'symbols.{stem}.txt',
                            'sha256': [hashlib.sha256(data).hexdigest()]})
        (configs / 'manifest.json').write_text(json.dumps({'schema_version': 1, 'configs': entries}))
        self.assertEqual(sorted(update_symbol_files(report, configs)),
                         ['symbols.boot.txt', 'symbols.game.txt'])
        self.assertIn('FuncA = 0x80120104; // type:func\n',
                      (configs / 'symbols.boot.txt').read_text())

        stdout = StringIO()
        with redirect_stdout(stdout):
            code = main([str(image), '--signatures', str(self.sigs)])
        self.assertEqual(code, 0)
        self.assertIn('BOOT.EXE: 1 library objects, 40 bytes, 1 named labels', stdout.getvalue())
        stderr = StringIO()
        with redirect_stderr(stderr):
            code = main([str(image), '--signatures', str(self.root / 'missing')])
        self.assertEqual(code, 2)


class CheckedInPsyqTests(unittest.TestCase):
    def test_symbol_files_match_the_recorded_report(self):
        report = json.loads((DEFAULT_CONFIG_DIR.parent / 'docs' / 'reports' / 'psyq-jp1.json')
                            .read_text(encoding='utf-8'))
        by_hash = {d: e for e in load_manifest(DEFAULT_CONFIG_DIR) for d in e.sha256}
        for exe in report['executables']:
            path = DEFAULT_CONFIG_DIR / by_hash[exe['sha256']].symbols
            text = path.read_text(encoding='utf-8')
            self.assertIn(GENERATED_MARKER, text)
            self.assertEqual(with_generated_symbols(text, exe), text)


if __name__ == '__main__':
    unittest.main()
