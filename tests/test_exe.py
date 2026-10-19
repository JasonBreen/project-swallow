"""PS-X EXE and SYSTEM.CNF contracts exercised with synthetic headers and images."""
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import hashlib
import json
import struct
import tempfile
import unittest

from swallow_tools.exe import (
    ExeFormatError, find_path_references, inspect_executables, main,
    parse_psx_exe, parse_system_cnf,
)
from test_disc import iso_record, raw_sector

MARKER = b'Sony Computer Entertainment Inc. for Japan area'


def make_exe(*, text=b'\x00' * 2048, pc=0x80010010, gp=0, t_addr=0x80010000,
             t_size=None, data=(0, 0), bss=(0, 0), stack=(0x801FFFF0, 0),
             magic=b'PS-X EXE', reserved_08=b'\x00' * 8, reserved_38=b'\x00' * 20,
             marker=MARKER):
    header = bytearray(2048)
    struct.pack_into('<8s8sIIIIIIIIII20s', header, 0, magic, reserved_08, pc, gp,
                     t_addr, len(text) if t_size is None else t_size, *data, *bss,
                     *stack, reserved_38)
    header[0x4C:0x4C + len(marker)] = marker
    return bytes(header) + text


class ExeHeaderTests(unittest.TestCase):
    def test_valid_header_fields_and_report(self):
        raw = make_exe(text=b'\x00' * 4096, pc=0x80010800, gp=0x12345678)
        exe = parse_psx_exe(raw, len(raw))
        self.assertEqual(exe.header.text_size, 4096)
        self.assertEqual(exe.header.initial_gp, 0x12345678)
        self.assertEqual(exe.header.marker, MARKER.decode())
        self.assertEqual((exe.ram_mirror, exe.text_end), ('kseg0', 0x80011000))
        self.assertTrue(exe.entry_in_text)
        self.assertEqual(exe.warnings, ())
        report = exe.to_report()
        self.assertEqual(report['text_range'], ['0x80010000', '0x80011000'])
        self.assertEqual(report['header']['initial_pc'], '0x80010800')

    def test_all_three_ram_mirrors_are_accepted(self):
        for base, name in ((0x00000000, 'kuseg'), (0x80000000, 'kseg0'),
                           (0xA0000000, 'kseg1')):
            raw = make_exe(t_addr=base + 0x10000, pc=base + 0x10000)
            with self.subTest(name=name):
                self.assertEqual(parse_psx_exe(raw, len(raw)).ram_mirror, name)

    def test_text_may_end_exactly_at_top_of_ram(self):
        raw = make_exe(t_addr=0x801FF800, pc=0x801FF800)
        self.assertEqual(parse_psx_exe(raw, len(raw)).text_end, 0x80200000)

    def test_rejections(self):
        cases = {
            'magic': make_exe(magic=b'PS-X EXF'),
            'text size not sector multiple': make_exe(t_size=2047),
            'unaligned text address': make_exe(t_addr=0x80010002),
            'unaligned entry': make_exe(pc=0x80010001),
            'outside RAM': make_exe(t_addr=0x80200000, pc=0x80200000),
            'kernel area': make_exe(t_addr=0x8000F800, pc=0x8000F800),
            'past top of RAM': make_exe(t_addr=0x801FF800, text=b'\x00' * 4096),
            'unprintable marker': make_exe(marker=b'Sony\x07'),
        }
        for name, raw in cases.items():
            with self.subTest(name=name), self.assertRaises(ExeFormatError):
                parse_psx_exe(raw, len(raw))

    def test_size_must_match_text_exactly(self):
        raw = make_exe()
        for size in (len(raw) - 2048, len(raw) + 2048, 100):
            with self.subTest(size=size), self.assertRaises(ExeFormatError):
                parse_psx_exe(raw, size)
        with self.assertRaises(ExeFormatError):
            parse_psx_exe(raw[:2047], len(raw))

    def test_uninterpreted_fields_warn_and_are_preserved(self):
        raw = make_exe(pc=0x80020000, reserved_08=b'\x01' * 8, data=(0x80030000, 4),
                       reserved_38=b'\x02' * 20)
        exe = parse_psx_exe(raw, len(raw))
        self.assertFalse(exe.entry_in_text)
        self.assertEqual(len(exe.warnings), 3)
        self.assertEqual(exe.header.reserved_08_hex, '01' * 8)
        self.assertEqual(exe.header.reserved_38_hex, '02' * 20)
        self.assertEqual((exe.header.data_address, exe.header.data_size), (0x80030000, 4))

    def test_bytes_after_marker_warn(self):
        raw = bytearray(make_exe())
        raw[0x7F0] = 1
        self.assertIn('after the region marker', parse_psx_exe(raw, len(raw)).warnings[0])


class SystemCnfTests(unittest.TestCase):
    def test_boot_path_and_settings(self):
        cnf = parse_system_cnf(
            b'BOOT = cdrom:SLPS_020.20;1\r\nTCB = 4\r\nEVENT = 16\r\nSTACK = 801FFF00\r\n')
        self.assertEqual(cnf.boot_path, 'SLPS_020.20')
        self.assertEqual(cnf.settings['STACK'], '801FFF00')

    def test_backslashes_subdirectories_and_case(self):
        self.assertEqual(parse_system_cnf(b'boot=CDROM:\\\\dir\\main.exe;1').boot_path,
                         'DIR/MAIN.EXE')
        self.assertEqual(parse_system_cnf(b'BOOT = cdrom:MAIN.EXE').boot_path, 'MAIN.EXE')

    def test_rejections(self):
        for raw in (b'TCB = 4\n', b'BOOT = host:MAIN.EXE;1\n', b'BOOT cdrom:A;1\n',
                    b'BOOT = cdrom:A;1\nBOOT = cdrom:B;1\n', b'BOOT = cdrom:\xff;1',
                    b'B' * 2049):
            with self.subTest(raw=raw[:20]), self.assertRaises(ExeFormatError):
                parse_system_cnf(raw)


class PathReferenceTests(unittest.TestCase):
    def test_header_and_text_offsets(self):
        data = bytearray(make_exe(text=b'\x00' * 12 + b'\\GAME.BIN;1\x00' + b'\x00' * 2024))
        data[0x700:0x70B] = b'\\GAME.BIN;1'
        refs = find_path_references(bytes(data), ['GAME.BIN', 'OTHER.BIN'], 0x80010000)
        self.assertEqual([ref['file_offset'] for ref in refs], [0x700, 0x80C])
        self.assertEqual([ref['address'] for ref in refs], [None, '0x8001000c'])

    def test_name_must_be_a_full_path_component(self):
        data = make_exe(text=b'\\XGAME.BIN;1\\GAME.BINX;1' + b'\x00' * 2024)
        self.assertEqual(find_path_references(data, ['GAME.BIN'], 0x80010000), [])


def make_image(root, files, *, cnf=b'BOOT = cdrom:BOOT.EXE;1\r\nTCB = 4\r\n',
               form2_files=(), extra_cnf=False):
    """Lay out a root directory of named files on consecutive sectors from LBA 24."""
    pvd = bytearray(2048)
    pvd[:7] = b'\x01CD001\x01'
    pvd[40:72] = b'SWALLOW_SYNTHETIC'.ljust(32, b' ')
    root_record = iso_record(b'\x00', 22, 2048, 2)
    pvd[156:156 + len(root_record)] = root_record
    directory = root_record + iso_record(b'\x01', 22, 2048, 2)
    contents = {}
    lba = 24
    entries = [('SYSTEM.CNF', cnf)] + list(files)
    if extra_cnf:
        entries.append(('SYSTEM.CNF', cnf))
    for name, data in entries:
        directory += iso_record(name.encode() + b';1', lba, len(data))
        for index in range(max(1, (len(data) + 2047) // 2048)):
            contents[lba + index] = (name, data[index * 2048:(index + 1) * 2048])
        lba += max(1, (len(data) + 2047) // 2048)
    path = root / 'synthetic.bin'
    with path.open('wb') as stream:
        for sector in range(lba + 1):
            name, payload = contents.get(sector, (None, b''))
            if sector == 16:
                payload = pvd
            elif sector == 22:
                payload = directory
            raw = bytearray(raw_sector(payload, sector))
            if name in form2_files:
                raw[18] = raw[22] = 0x20
            stream.write(raw)
    return path


class InspectExecutablesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.boot = make_exe(t_addr=0x80120000, pc=0x80120000,
                             text=b'\x00' * 12 + b'\\GAME.BIN;1' + b'\x00' * 2025)
        self.game = make_exe(text=b'\x00' * 2048 + b'\\DATA.BPB;1' + b'\x00' * 2037)

    def tearDown(self):
        self.tmp.cleanup()

    def test_boot_from_system_cnf_and_executables_found_by_content(self):
        path = make_image(self.root, [('BOOT.EXE', self.boot), ('GAME.BIN', self.game),
                                      ('DATA.BPB', b'\x00' * 100),
                                      ('STREAM.SPB', b'PS-X EXE' + b'\x00' * 4000)],
                          form2_files=('STREAM.SPB',))
        report = inspect_executables(path)
        self.assertEqual(report['system_cnf']['boot_path'], 'BOOT.EXE')
        self.assertEqual(report['system_cnf']['settings']['TCB'], '4')
        by_path = {exe['path']: exe for exe in report['executables']}
        self.assertEqual(set(by_path), {'BOOT.EXE', 'GAME.BIN'})
        self.assertTrue(by_path['BOOT.EXE']['is_boot'])
        self.assertFalse(by_path['GAME.BIN']['is_boot'])
        self.assertEqual(by_path['GAME.BIN']['sha256'], hashlib.sha256(self.game).hexdigest())
        self.assertEqual(by_path['BOOT.EXE']['path_references'][0]['address'], '0x8012000c')
        self.assertEqual(by_path['GAME.BIN']['path_references'][0]['path'], 'DATA.BPB')
        self.assertEqual(report['form2_skipped'], ['STREAM.SPB'])
        self.assertEqual(report['text_range_overlaps'], [])

    def test_overlap_uses_physical_addresses_across_mirrors(self):
        other = make_exe(t_addr=0x00010800, pc=0x00010800)
        path = make_image(self.root, [('BOOT.EXE', self.boot), ('GAME.BIN', self.game),
                                      ('OTHER.EXE', other)])
        self.assertEqual(inspect_executables(path)['text_range_overlaps'],
                         [['GAME.BIN', 'OTHER.EXE']])

    def test_disc_level_rejections(self):
        cases = {
            'missing boot file': ([('GAME.BIN', self.game)], {}),
            'boot is not an exe': ([('BOOT.EXE', b'\x00' * 3000)], {}),
            'duplicate SYSTEM.CNF': ([('BOOT.EXE', self.boot)], {'extra_cnf': True}),
            'malformed exe': ([('BOOT.EXE', self.boot), ('BAD.EXE', self.boot[:-2048])], {}),
        }
        for name, (files, options) in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                path = make_image(Path(directory), files, **options)
                with self.assertRaises(ExeFormatError):
                    inspect_executables(path)

    def test_cli_text_json_and_error_exit(self):
        path = make_image(self.root, [('BOOT.EXE', self.boot), ('GAME.BIN', self.game)])
        out = StringIO()
        with redirect_stdout(out):
            self.assertEqual(main([str(path), '--json']), 0)
        self.assertEqual(json.loads(out.getvalue())['schema_version'], 1)
        out = StringIO()
        with redirect_stdout(out):
            self.assertEqual(main([str(path)]), 0)
        self.assertIn('references \\GAME.BIN;1 at 0x8012000c', out.getvalue())
        err = StringIO()
        with redirect_stderr(err):
            self.assertEqual(main([str(self.root / 'missing.bin')]), 2)
        self.assertIn('ERROR', err.getvalue())


if __name__ == '__main__':
    unittest.main()
