"""BPB structural-walk contracts on synthetic entries and a synthetic disc."""
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import json
import struct
import tempfile
import unittest

from swallow_tools.bpb import (BpbFormatError, MAX_DEPTH, count_kinds, inspect_bpb, main,
                               parse_dot1, signature_hint, tree_sha256, walk_entry)
from test_disc import iso_record, raw_sector
from test_ulz import V2_OVERLAP, V0_RUN, block, ref


def dot1(*children):
    offsets, position = [], 4 + 4 * len(children)
    for child in children:
        offsets.append(position)
        position += len(child)
    return struct.pack(f'<I{len(children)}I', len(children), *offsets) + b''.join(children)


TIM = struct.pack('<II', 0x10, 0x08) + b'\x00' * 8


class Dot1Tests(unittest.TestCase):
    def test_accepts_exact_table(self):
        data = dot1(b'abcd', b'efghijkl', b'')
        self.assertEqual(parse_dot1(data), (16, 20, 28, 28))

    def test_rejects_measured_counterexample_shapes(self):
        cases = {
            # BPB entry 1 shape: a small count followed by ASCII text.
            'text_after_count': struct.pack('<I', 4) + b'LOG OUT\x00easy\x00\x00\x00\x00',
            # BPB entry 3 shape: a count followed by all-zero offsets.
            'zero_offsets': struct.pack('<I', 6) + b'\x00' * 40,
            # BPB entry 4 shape: a count, then label text and code-like words.
            'code_like': struct.pack('<I', 7) + b'BGOK02\x00\x00' + bytes.fromhex(
                '0000a28c00f00324241583ac090042340000a2ac'),
        }
        for name, data in cases.items():
            with self.subTest(name=name):
                self.assertIsNone(parse_dot1(data))

    def test_rejects_malformed_tables(self):
        good = dot1(b'abcd', b'efgh')
        cases = {
            'short': b'\x01\x00\x00\x00',
            'zero_count': struct.pack('<II', 0, 8),
            'table_overflows': struct.pack('<II', 5, 24),
            'size_word_layout': struct.pack('<IIII', 2, 16, 20, 24) + b'\x00' * 8,
            'decreasing': struct.pack('<III', 2, 12, 11) + b'\x00' * 8,
            'misaligned': struct.pack('<III', 2, 12, 14) + b'\x00' * 8,
            'beyond_end': good[:8] + struct.pack('<I', 64) + good[12:],
            'ulz_magic': V2_OVERLAP,
        }
        for name, data in cases.items():
            with self.subTest(name=name):
                self.assertIsNone(parse_dot1(data))

    def test_signature_hints_are_only_hints(self):
        self.assertEqual(signature_hint(TIM), 'tim_header_candidate')
        self.assertEqual(signature_hint(struct.pack('<II', 0x41, 0)), 'tmd_header_candidate')
        self.assertIsNone(signature_hint(struct.pack('<II', 0x10, 0x10)))
        self.assertIsNone(signature_hint(b'\x10'))


class WalkTests(unittest.TestCase):
    def test_nested_tables_ulz_children_and_empty_slots(self):
        inner = dot1(TIM, b'')
        data = dot1(inner, V2_OVERLAP + b'\x00\x00', V0_RUN + b'\x00\x00', b'loose bytes!')
        node = walk_entry(data, offset=4096)
        self.assertEqual((node.kind, node.offset, node.size), ('dot1', 4096, len(data)))
        table, ulz2, ulz0, loose = node.children
        self.assertEqual([child.kind for child in table.children], ['unclassified', 'empty'])
        self.assertEqual(table.children[0].signature_hint, 'tim_header_candidate')
        self.assertEqual(ulz2.kind, 'ulz')
        self.assertEqual(ulz2.ulz['flag_pad_bytes'], 4)
        self.assertEqual(ulz2.ulz['reference_pad_bytes'], 2)
        self.assertEqual(ulz2.ulz['nonzero_pad_bytes'], 0)
        self.assertEqual(ulz2.children[0].kind, 'unclassified')
        self.assertEqual(ulz2.children[0].size, 7)
        self.assertEqual(ulz0.ulz['version'], 0)
        self.assertEqual(loose.kind, 'unclassified')
        counts = count_kinds(node)
        self.assertEqual((counts['dot1'], counts['ulz'], counts['empty'],
                          counts['ulz_decoded_bytes']), (2, 2, 1, 11))
        self.assertEqual(tree_sha256(node), tree_sha256(walk_entry(data, offset=4096)))

    def test_compressed_table_is_walked_after_decoding(self):
        payload = dot1(b'abcd')   # 01 00 00 00 08 00 00 00 61 62 63 64
        literals = payload[:4] + b'\x08' + payload[8:]
        flags = [0xF8000000 | 0x03C00000, 0]   # 5 literals, 3-byte reference, 4 literals
        data = block(2, 12, 10, flags, literals, [ref(3, 4)])
        node = walk_entry(data)
        self.assertEqual(node.kind, 'ulz')
        self.assertEqual(node.children[0].kind, 'dot1')
        self.assertEqual(node.children[0].children[0].size, 4)

    def test_nonzero_padding_is_reported_not_rejected(self):
        node = walk_entry(dot1(V2_OVERLAP + b'\x99\x00'))
        self.assertEqual(node.children[0].ulz['nonzero_pad_bytes'], 1)

    def test_bad_ulz_child_is_an_error(self):
        with self.assertRaises(BpbFormatError):
            walk_entry(dot1(V2_OVERLAP[:-1] + b'\x00'))

    def test_nesting_limit(self):
        data = b'leaf'
        for _ in range(MAX_DEPTH):
            data = dot1(data)
        self.assertEqual(walk_entry(data).kind, 'dot1')
        with self.assertRaises(BpbFormatError):
            walk_entry(dot1(data))


def make_image(root, records, bpb_data):
    """Synthetic disc: ACE.BPH at LBA 23, ACE.BPB payload from LBA 24."""
    index = struct.pack('<4sIHHII', b'AC3E', 3, 0, 0, 0, len(records))
    index += b''.join(struct.pack('<II', *record) for record in records)
    bpb_sectors = (len(bpb_data) + 2047) // 2048
    pvd = bytearray(2048)
    pvd[:7] = b'\x01CD001\x01'
    pvd[40:72] = b'SWALLOW_SYNTHETIC'.ljust(32, b' ')
    root_record = iso_record(b'\x00', 22, 2048, 2)
    pvd[156:156 + len(root_record)] = root_record
    directory = root_record + iso_record(b'\x01', 22, 2048, 2)
    directory += iso_record(b'ACE.BPH;1', 23, len(index))
    directory += iso_record(b'ACE.BPB;1', 24, len(bpb_data))
    path = root / 'synthetic.bin'
    with path.open('wb') as stream:
        for lba in range(24 + bpb_sectors):
            payload = {16: bytes(pvd), 22: directory, 23: index}.get(lba, b'')
            if lba >= 24:
                payload = bpb_data[(lba - 24) * 2048:(lba - 23) * 2048]
            stream.write(raw_sector(payload, lba))
    return path


class BpbCliTests(unittest.TestCase):
    def test_cli_reports_entries_across_sectors(self):
        first = dot1(TIM, V2_OVERLAP + b'\x00\x00')
        second = b'unclassified data'.ljust(20, b'\x00')
        bpb = first.ljust(2048, b'\x00') + second
        records = [(0xE0000000 | len(first) // 4, 0), (0xE0000000 | len(second) // 4, 1)]
        with tempfile.TemporaryDirectory() as folder:
            path = make_image(Path(folder), records, bpb)
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(main([str(path), '--json']), 0)
            report = json.loads(output.getvalue())
            self.assertEqual(report['entry_count'], 2)
            self.assertEqual(report['entries'][0]['root_kind'], 'dot1')
            self.assertEqual(report['entries'][0]['tree']['children'][1]['kind'], 'ulz')
            self.assertEqual(report['entries'][1]['root_kind'], 'unclassified')
            self.assertEqual(report['totals']['ulz'], 1)
            self.assertNotIn(str(path.parent), output.getvalue())
            summary = inspect_bpb(path, include_trees=False)
            self.assertNotIn('tree', summary['entries'][0])
            self.assertEqual(summary['entries'][0]['tree_sha256'],
                             report['entries'][0]['tree_sha256'])
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(main([str(path)]), 0)
            self.assertIn('2 BPB entries walked', output.getvalue())

    def test_cli_malformed_ulz_exits_2_without_output(self):
        broken = dot1(V2_OVERLAP[:-1] + b'\x00\x00\x00')
        with tempfile.TemporaryDirectory() as folder:
            path = make_image(Path(folder), [(0xE0000000 | len(broken) // 4, 0)], broken)
            for target in (path, Path(folder) / 'absent.bin'):
                output, error = StringIO(), StringIO()
                with redirect_stdout(output), redirect_stderr(error):
                    self.assertEqual(main([str(target), '--json']), 2)
                self.assertEqual(output.getvalue(), '')
                self.assertIn('ERROR:', error.getvalue())


if __name__ == '__main__':
    unittest.main()
