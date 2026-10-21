"""splat staging contract, exercised with a synthetic image and a fake splat."""
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
import hashlib
import json
import sys
import tempfile
import unittest

from swallow_tools.split import (
    DEFAULT_CONFIG_DIR, REPO_ROOT, SplitError, load_manifest, main, split_image,
    summarize_asm,
)
from test_exe import make_exe, make_image

# Stands in for `python -m splat split CONFIG`: records its arguments and
# writes one text file the way splat names it, from the config's stem.
FAKE_SPLAT = '''
import pathlib, sys
config = pathlib.Path(sys.argv[2])
if config.stem == 'fail':
    sys.exit(3)
if config.stem == 'empty':
    sys.exit(0)
asm = pathlib.Path('asm'); asm.mkdir(exist_ok=True)
(asm / f'{config.stem}_text.s').write_text(
    'glabel func_a\\n.L1:\\nglabel func_b\\ndlabel D_c\\n'
    '    /* 0 0 */ .word 0x00200000 /* invalid instruction */\\n')
with open('calls.txt', 'a') as log:
    log.write(' '.join(sys.argv[1:]) + ' ' + str(pathlib.Path(config.read_text().strip()).name) + '\\n')
print('fake splat ran')
'''


class SplitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.boot = make_exe(t_addr=0x80120000, pc=0x80120000)
        self.game = make_exe(text=b'\x11' * 4096)
        self.image = make_image(self.root, [('BOOT.EXE', self.boot), ('GAME.BIN', self.game)])
        self.configs = self.root / 'configs'
        self.configs.mkdir()
        self.fake = self.root / 'fake_splat.py'
        self.fake.write_text(FAKE_SPLAT)
        self.write_manifest()

    def tearDown(self):
        self.tmp.cleanup()

    def write_manifest(self, boot_hash=None, game_config='game.yaml'):
        entries = [('boot.yaml', 'boot.psexe', boot_hash or hashlib.sha256(self.boot).hexdigest()),
                   (game_config, 'game.psexe', hashlib.sha256(self.game).hexdigest())]
        for config, target, _ in entries:
            (self.configs / config).write_text(target + '\n')
            (self.configs / f'symbols.{config}.txt').write_text('// none\n')
        (self.configs / 'manifest.json').write_text(json.dumps({
            'schema_version': 1,
            'configs': [{'config': c, 'target': t, 'symbols': f'symbols.{c}.txt', 'sha256': [h]}
                        for c, t, h in entries]}))

    def split(self, work=None):
        return split_image(self.image, work or self.root / 'work', config_dir=self.configs,
                           splat=(sys.executable, str(self.fake)), repo_root=self.root / 'repo')

    def test_stages_each_executable_by_hash_and_counts_output(self):
        summary = self.split()
        work = self.root / 'work'
        self.assertEqual((work / 'boot.psexe').read_bytes(), self.boot)
        self.assertEqual((work / 'game.psexe').read_bytes(), self.game)
        self.assertTrue((work / 'symbols.game.yaml.txt').is_file())
        self.assertEqual(sorted((work / 'calls.txt').read_text().splitlines()),
                         ['split boot.yaml boot.psexe', 'split game.yaml game.psexe'])
        self.assertIn('fake splat ran', (work / 'game.splat.log').read_text())
        self.assertEqual({(e['path'], e['config'], e['is_boot']) for e in summary['executables']},
                         {('BOOT.EXE', 'boot.yaml', True), ('GAME.BIN', 'game.yaml', False)})
        self.assertEqual(summary['asm_files'][0], {
            'file': 'boot_text.s', 'lines': 5, 'functions': 2, 'data_labels': 1,
            'invalid_instruction_words': 1})
        self.assertEqual(json.loads((work / 'split-report.json').read_text()), summary)

    def test_unknown_executable_hash_is_an_error(self):
        self.write_manifest(boot_hash='0' * 64)
        with self.assertRaisesRegex(SplitError, 'BOOT.EXE: SHA256 .* matches no splat config'):
            self.split()
        self.assertFalse((self.root / 'work').exists())

    def test_two_files_matching_one_config_is_an_error(self):
        self.game = self.boot
        self.image = make_image(self.root, [('BOOT.EXE', self.boot), ('GAME.BIN', self.boot)])
        manifest = {'schema_version': 1, 'configs': [
            {'config': 'boot.yaml', 'target': 'boot.psexe', 'symbols': 'symbols.boot.yaml.txt',
             'sha256': [hashlib.sha256(self.boot).hexdigest()]}]}
        (self.configs / 'manifest.json').write_text(json.dumps(manifest))
        with self.assertRaisesRegex(SplitError, 'already matched another file'):
            self.split()

    def test_work_directory_must_be_empty_and_outside_the_repository(self):
        work = self.root / 'work'
        work.mkdir()
        (work / 'old.s').write_text('stale')
        with self.assertRaisesRegex(SplitError, 'not empty'):
            self.split()
        with self.assertRaisesRegex(SplitError, 'inside the repository'):
            split_image(self.image, self.root / 'repo' / 'out', config_dir=self.configs,
                        splat=(sys.executable, str(self.fake)), repo_root=self.root / 'repo')

    def test_splat_failure_is_reported_with_its_log(self):
        self.write_manifest(game_config='fail.yaml')
        with self.assertRaisesRegex(SplitError, 'splat failed on fail.yaml with exit code 3'):
            self.split()
        self.assertTrue((self.root / 'work' / 'fail.splat.log').is_file())

    def test_splat_run_without_assembly_is_an_error(self):
        self.write_manifest(game_config='empty.yaml')
        with self.assertRaisesRegex(SplitError, 'wrote no assembly with functions'):
            self.split()

    def test_manifest_rejects_missing_files_and_shared_hashes(self):
        (self.configs / 'symbols.game.yaml.txt').unlink()
        with self.assertRaisesRegex(SplitError, 'missing file symbols.game.yaml.txt'):
            load_manifest(self.configs)
        self.write_manifest(boot_hash=hashlib.sha256(self.game).hexdigest())
        with self.assertRaisesRegex(SplitError, 'listed under two configs'):
            load_manifest(self.configs)

    def test_manifest_rejects_paths_and_malformed_json(self):
        for bad in ('../escape.psexe', '/tmp/abs.psexe', 'sub/dir.psexe', '..\\win.psexe'):
            manifest = json.loads((self.configs / 'manifest.json').read_text())
            manifest['configs'][0]['target'] = bad
            (self.configs / 'manifest.json').write_text(json.dumps(manifest))
            with self.subTest(target=bad), self.assertRaisesRegex(SplitError, 'bare file name'):
                load_manifest(self.configs)
        (self.configs / 'manifest.json').write_text('{not json')
        with self.assertRaisesRegex(SplitError, 'Malformed splat manifest'):
            load_manifest(self.configs)
        stderr = StringIO()
        with redirect_stderr(stderr):
            code = main([str(self.image), '--work-dir', str(self.root / 'w'),
                         '--config-dir', str(self.configs)])
        self.assertEqual(code, 2)
        self.assertFalse((self.root / 'w').exists())

    def test_iso_versions_of_one_name_are_staged_from_their_own_extents(self):
        newer = make_exe(text=b'\x22' * 2048)
        self.game = newer
        self.image = make_image(self.root, [('BOOT.EXE;1', self.boot), ('BOOT.EXE;2', newer)],
                                cnf=b'BOOT = cdrom:BOOT.EXE;1\r\n')
        self.write_manifest()
        self.split()
        self.assertEqual((self.root / 'work' / 'boot.psexe').read_bytes(), self.boot)
        self.assertEqual((self.root / 'work' / 'game.psexe').read_bytes(), newer)

    def test_manifest_names_must_be_distinct(self):
        manifest = json.loads((self.configs / 'manifest.json').read_text())
        manifest['configs'][1]['target'] = manifest['configs'][0]['target']
        (self.configs / 'manifest.json').write_text(json.dumps(manifest))
        with self.assertRaisesRegex(SplitError, 'must all be distinct'):
            load_manifest(self.configs)

    def test_main_explains_a_missing_manifest(self):
        stderr = StringIO()
        with redirect_stderr(stderr):
            code = main([str(self.image), '--work-dir', str(self.root / 'w'),
                         '--config-dir', str(self.root / 'nowhere')])
        self.assertEqual(code, 2)
        self.assertIn('pass --config-dir', stderr.getvalue())

    def test_main_exits_2_on_errors(self):
        stderr = StringIO()
        with redirect_stderr(stderr):
            code = main([str(self.image), '--work-dir', str(REPO_ROOT / 'never-written')])
        self.assertEqual(code, 2)
        self.assertIn('inside the repository', stderr.getvalue())

    def test_summarize_ignores_data_directory(self):
        asm = self.root / 'asm'
        (asm / 'data').mkdir(parents=True)
        (asm / 'data' / 'x.data.s').write_text('glabel nope\n')
        (asm / 'header.s').write_text('.word 1\n')
        self.assertEqual(summarize_asm(asm), [{'file': 'header.s', 'lines': 1, 'functions': 0,
                                                'data_labels': 0,
                                                'invalid_instruction_words': 0}])


class CheckedInManifestTests(unittest.TestCase):
    def test_manifest_hashes_match_the_recorded_executable_reports(self):
        configs = load_manifest(DEFAULT_CONFIG_DIR)
        recorded = set()
        for key in ('jp1', 'jp2', 'en1', 'en2'):
            report = json.loads((REPO_ROOT / 'docs' / 'reports' / f'exe-{key}.json')
                                .read_text(encoding='utf-8'))
            recorded |= {exe['sha256'] for exe in report['executables']}
        self.assertEqual(set().union(*(entry.sha256 for entry in configs)), recorded)

    def test_each_config_names_its_target_and_symbols(self):
        for entry in load_manifest(DEFAULT_CONFIG_DIR):
            text = (DEFAULT_CONFIG_DIR / entry.config).read_text(encoding='utf-8')
            self.assertIn(f'target_path: {entry.target}\n', text)
            self.assertIn(f'    - {entry.symbols}\n', text)
            self.assertIn('platform: psx\n', text)


if __name__ == '__main__':
    unittest.main()
