"""Nested-container walker contracts, exercised with synthetic payloads and images."""
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import hashlib
import json
import struct
import tempfile
import unittest

from swallow_tools.bpb import (
    ContainerFormatError, ContainerLimitError, inspect_bpb, main, parse_offset_table,
    summarize_nodes, walk_payload,
)
from swallow_tools.bph import BphFormatError
from test_bph import make_index
from test_disc import iso_record, raw_sector
from test_tim import block as tim_block, tim as tim_image
from test_ulz import encode_ulz


def container(*items):
    header = 4 + 4 * len(items)
    offsets, position = [], header
    for item in items:
        offsets.append(position)
        position += len(item)
    return struct.pack(f'<{1 + len(items)}I', len(items), *offsets) + b''.join(items)


def padded(data):
    return data + b'\0' * (-len(data) % 4)


def sample_tim(width=16, height=64):
    """4bpp TIM with a 16-entry CLUT; width/height are pixel-block halfwords."""
    return tim_image(0x8, tim_block(320, 256, 16, 1), tim_block(640, 0, width, height))


TIM_STUB = b'\x10\x00\x00\x00' + b'\0' * 12      # candidate signature, invalid image


class OffsetTableTests(unittest.TestCase):
    def test_items_cover_payload_including_final_item(self):
        table = parse_offset_table(container(b'abcd', b'efghijkl', b'mn'))
        self.assertEqual(table.count, 3)
        self.assertEqual(table.offsets, (16, 20, 28))
        self.assertEqual(table.items, ((16, 20), (20, 28), (28, 30)))

    def test_empty_items_and_exact_fit(self):
        table = parse_offset_table(container(b'', b'abcd', b''))
        self.assertEqual(table.items, ((16, 16), (16, 20), (20, 20)))

    def test_rejections(self):
        cases = {
            'too_short': b'\x01\x00\x00\x00',
            'zero_count': struct.pack('<II', 0, 4),
            'count_exceeds_data': struct.pack('<II', 5, 24),
            'first_offset_mismatch': struct.pack('<II', 1, 12) + b'abcd',
            'unaligned': struct.pack('<III', 2, 12, 14) + b'abcdef',
            'unordered': struct.pack('<III', 2, 12, 8) + b'abcd',
            'last_past_end': struct.pack('<III', 2, 12, 20) + b'abcd',
        }
        for name, data in cases.items():
            with self.subTest(name=name), self.assertRaises(ContainerFormatError):
                parse_offset_table(data)


class WalkTests(unittest.TestCase):
    def test_nested_classification_without_decoding(self):
        blob = encode_ulz(b'leaf' * 50)
        payload = container(container(blob, padded(b'raw!')), padded(b'tail'))
        root = walk_payload(payload, path=(7,))
        self.assertEqual(root.kind, 'container')
        self.assertEqual([child.kind for child in root.children], ['container', 'opaque'])
        inner = root.children[0]
        self.assertEqual(inner.path, (7, 0))
        self.assertEqual([child.kind for child in inner.children], ['ulz', 'opaque'])
        ulz_node = inner.children[0]
        self.assertEqual(ulz_node.ulz['header']['decompressed_size'], 200)
        self.assertFalse(ulz_node.ulz['decoded'])
        self.assertIsNone(ulz_node.decoded)
        self.assertEqual(root.children[1].signature, b'tail'.hex())
        self.assertEqual((inner.offset, inner.size),
                         (12, len(container(blob, padded(b'raw!')))))

    def test_decode_classifies_decompressed_payload(self):
        inner_payload = container(padded(b'alpha'), padded(b'beta'))
        payload = container(encode_ulz(inner_payload))
        root = walk_payload(payload, decode=True)
        ulz_node = root.children[0]
        self.assertTrue(ulz_node.ulz['decoded'])
        self.assertEqual(ulz_node.ulz['payload_sha256'],
                         hashlib.sha256(inner_payload).hexdigest())
        self.assertEqual(ulz_node.decoded.kind, 'container')
        self.assertEqual(ulz_node.decoded.origin, 'decoded')
        self.assertEqual([child.signature for child in ulz_node.decoded.children],
                         [b'alph'.hex(), b'beta'.hex()])

    def test_decode_error_is_recorded_not_raised(self):
        broken = bytearray(encode_ulz(b'abcabcabcabcabc'))
        broken[4] = 0xFF                      # claim a larger output than the streams hold
        root = walk_payload(container(bytes(broken)), decode=True)
        node = root.children[0]
        self.assertEqual(node.kind, 'ulz')
        self.assertIn('exhausted', node.error)
        self.assertFalse(node.ulz['decoded'])
        self.assertIsNone(node.decoded)

    def test_invalid_ulz_header_is_recorded(self):
        root = walk_payload(container(b'Ulz\x1a' + b'\0' * 4), decode=True)
        self.assertIn('Truncated', root.children[0].error)
        self.assertIsNone(root.children[0].ulz)

    def test_empty_item_kind(self):
        root = walk_payload(container(b'', padded(b'x')))
        self.assertEqual(root.children[0].kind, 'empty')
        self.assertEqual(root.children[0].size, 0)

    def test_depth_limit_raises(self):
        payload = padded(b'deep')
        for _ in range(4):
            payload = container(payload)
        self.assertEqual(walk_payload(payload, max_depth=4).kind, 'container')
        with self.assertRaisesRegex(ContainerFormatError, 'depth'):
            walk_payload(payload, max_depth=3)

    def test_node_limit_raises(self):
        payload = container(*[padded(b'x')] * 10)
        with self.assertRaisesRegex(ContainerLimitError, 'node budget'):
            walk_payload(payload, max_nodes=10)
        self.assertEqual(len(walk_payload(payload, max_nodes=11).children), 10)
        # A decoded payload is charged one node beyond its Ulz leaf.
        nested = container(encode_ulz(b'z' * 40))
        with self.assertRaisesRegex(ContainerLimitError, 'Node count'):
            walk_payload(nested, decode=True, max_nodes=2)
        self.assertEqual(walk_payload(nested, decode=True, max_nodes=3).kind, 'container')

    def test_empty_items_count_against_node_limit(self):
        # Repeated offsets yield many zero-length items; each must be charged.
        payload = container(*([b''] * 50 + [padded(b'x')]))
        with self.assertRaisesRegex(ContainerLimitError, 'node budget'):
            walk_payload(payload, max_nodes=10)
        with self.assertRaisesRegex(ContainerLimitError, 'node budget'):
            walk_payload(payload, max_nodes=51)
        root = walk_payload(payload, max_nodes=52)
        self.assertEqual(sum(child.kind == 'empty' for child in root.children), 50)

    def test_huge_declared_count_is_rejected_before_unpacking(self):
        count = 200_000
        payload = struct.pack('<II', count, 4 + 4 * count) + bytes(4 * count - 4)
        with self.assertRaisesRegex(ContainerLimitError, 'declares 200000 items'):
            parse_offset_table(payload, max_count=99_999)
        with self.assertRaisesRegex(ContainerLimitError, 'node budget'):
            walk_payload(payload)
        # Without a budget the same bytes fail ordinary validation instead.
        with self.assertRaises(ContainerFormatError) as ctx:
            parse_offset_table(payload)
        self.assertNotIsInstance(ctx.exception, ContainerLimitError)

    def test_nested_tables_reserve_children_before_descending(self):
        inner = container(padded(b'x'), *([b''] * 9))
        middle = container(inner, *([b''] * 9))
        outer = container(middle, *([b''] * 9))
        # 1 root + 10 + 10 + 10 nodes. With 30, the innermost table must be
        # refused when declared, not after its siblings are built.
        with self.assertRaisesRegex(ContainerLimitError,
                                    'declares 10 items, exceeding the remaining node budget of 9'):
            walk_payload(outer, max_nodes=30)
        root = walk_payload(outer, max_nodes=31)
        self.assertEqual(root.children[0].children[0].children[0].kind, 'opaque')

    def test_large_count_with_wrong_first_offset_stays_opaque(self):
        payload = struct.pack('<II', 200_000, 12) + bytes(800_000)
        self.assertEqual(walk_payload(payload, max_nodes=5).kind, 'opaque')

    def test_summary_counts(self):
        payload = container(encode_ulz(container(padded(b'abc')), version=0),
                            encode_ulz(b'z' * 40), padded(b'raw'))
        root = walk_payload(payload, decode=True)
        summary = summarize_nodes([root])
        self.assertEqual(summary['kinds'], {'container': 1, 'ulz': 2, 'opaque': 1})
        self.assertEqual(summary['decoded_kinds'], {'container': 1, 'opaque': 2})
        self.assertEqual(summary['tim_modes'], {})
        self.assertEqual(summary['tim_candidates_rejected'], 0)
        self.assertEqual(summary['ulz_versions'], {'0': 1, '2': 1})
        self.assertEqual(summary['ulz_decoded'], 2)
        self.assertEqual(summary['ulz_errors'], 0)
        self.assertEqual(summary['ulz_decompressed_bytes'], 12 + 40)
        self.assertEqual(summary['max_depth'], 3)

    def test_tim_leaf_is_classified_with_metadata(self):
        image = sample_tim()
        root = walk_payload(container(image, padded(b'raw')), path=(9,))
        node = root.children[0]
        self.assertEqual(node.kind, 'tim')
        self.assertEqual(node.signature, '10000000')
        self.assertEqual(node.size, len(image))
        self.assertEqual(node.tim['mode'], '4bpp')
        self.assertEqual((node.tim['pixel_width'], node.tim['pixel_height']), (64, 64))
        self.assertEqual(node.tim['clut_entries'], 16)
        self.assertEqual(node.tim['trailing_bytes'], 0)
        self.assertIsNone(node.error)
        report = node.to_report()
        self.assertEqual(report['kind'], 'tim')
        self.assertEqual(report['tim']['pixels']['x'], 640)
        self.assertNotIn('children', report)
        self.assertEqual(root.children[1].kind, 'opaque')

    def test_tim_candidate_that_fails_validation_stays_opaque(self):
        bad = tim_image(0x8, tim_block(0, 0, 16, 1), tim_block(0, 0, 16, 16, length=516))
        root = walk_payload(container(bad, TIM_STUB))
        for node in root.children:
            self.assertEqual(node.kind, 'opaque')
            self.assertEqual(node.signature, '10000000')
            self.assertIsNone(node.tim)
            self.assertTrue(node.error.startswith('TIM candidate rejected: '))
        self.assertIn('516', root.children[0].error)
        self.assertNotIn('tim', root.children[1].to_report())
        self.assertIn('error', root.children[1].to_report())

    def test_tim_accepts_container_alignment_padding_only(self):
        short = tim_image(0x8, tim_block(0, 0, 16, 1), tim_block(0, 0, 1, 1))
        self.assertEqual(len(short) % 4, 2)
        root = walk_payload(container(padded(short), padded(b'next')))
        self.assertEqual(root.children[0].kind, 'tim')
        self.assertEqual(root.children[0].size, len(short) + 2)
        self.assertEqual(root.children[0].tim['trailing_bytes'], 2)
        root = walk_payload(container(short))         # last item: no padding needed
        self.assertEqual(root.children[0].tim['trailing_bytes'], 0)
        root = walk_payload(container(short + b'\0\x01', padded(b'next')))
        self.assertEqual(root.children[0].kind, 'opaque')
        self.assertIn('not zero', root.children[0].error)
        root = walk_payload(container(short + b'\0' * 6, padded(b'next')))
        self.assertEqual(root.children[0].kind, 'opaque')
        self.assertIn('blocks end', root.children[0].error)

    def test_tim_padding_needs_a_following_aligned_offset(self):
        tiny = tim_image(0x0, tim_block(0, 0, 1, 7))      # 8 + 12 + 14 = 34 bytes
        self.assertEqual(len(tiny), 34)
        exact = tim_image(0x0, tim_block(0, 0, 1, 1))     # 22 bytes
        # Review counterexample: a 22-byte TIM plus one zero byte as the only
        # (final) item of a 31-byte container must not count as padded.
        payload = container(exact + b'\0')
        self.assertEqual(len(payload), 31)
        node = walk_payload(payload).children[0]
        self.assertEqual(node.kind, 'opaque')
        self.assertIn('blocks end at 22', node.error)
        # A final item padded to four bytes still has no following offset.
        node = walk_payload(container(padded(tiny))).children[0]
        self.assertEqual(node.kind, 'opaque')
        # Decoded payloads and top-level ranges have no table offset either.
        root = walk_payload(container(encode_ulz(padded(tiny)), TIM_STUB), decode=True)
        self.assertEqual(root.children[0].decoded.kind, 'opaque')
        self.assertEqual(walk_payload(padded(tiny)).kind, 'opaque')
        # A non-final item whose end is the next aligned offset is padded.
        node = walk_payload(container(padded(tiny), TIM_STUB)).children[0]
        self.assertEqual((node.kind, node.tim['trailing_bytes']), ('tim', 2))
        # An empty item after it keeps the following offset aligned.
        node = walk_payload(container(padded(tiny), b'', TIM_STUB)).children[0]
        self.assertEqual(node.kind, 'tim')

    def test_decoded_tim_and_summary(self):
        image = sample_tim(4, 4)
        payload = container(encode_ulz(image), sample_tim(), TIM_STUB)
        root = walk_payload(payload, decode=True)
        decoded = root.children[0].decoded
        self.assertEqual((decoded.kind, decoded.origin), ('tim', 'decoded'))
        self.assertEqual(decoded.tim['pixel_width'], 16)
        summary = summarize_nodes([root])
        self.assertEqual(summary['kinds'], {'container': 1, 'ulz': 1, 'tim': 1, 'opaque': 1})
        self.assertEqual(summary['decoded_kinds'], {'tim': 1})
        self.assertEqual(summary['tim_modes'], {'4bpp': 2})
        self.assertEqual(summary['tim_with_clut'], 2)
        self.assertEqual(summary['tim_bytes'], len(image) + len(sample_tim()))
        self.assertEqual(summary['tim_candidates_rejected'], 1)
        self.assertEqual(summary['ulz_errors'], 0)

    def test_report_tree_shape(self):
        report = walk_payload(container(padded(b'ab')), path=(3,)).to_report()
        self.assertEqual(report['path'], '3')
        self.assertEqual(report['child_count'], 1)
        self.assertEqual(report['children'][0]['path'], '3.0')
        self.assertNotIn('ulz', report['children'][0])


def make_image(root, payloads, *, bph_lba=27, bpb_lba=32):
    """Synthetic image with a BPH index and BPB payloads packed at sector offsets."""
    records, blobs, sector = [], [], 0
    for payload in payloads:
        records.append((0xE0000000 | ((len(payload) + 3) // 4), sector))
        blobs.append((sector, payload))
        sector += (len(payload) + 2047) // 2048
    index = make_index(records)
    bpb_size = sector * 2048
    pvd = bytearray(2048)
    pvd[:7] = b'\x01CD001\x01'
    pvd[40:72] = b'SWALLOW_BPB'.ljust(32, b' ')
    root_record = iso_record(b'\x00', 22, 2048, 2)
    pvd[156:156 + len(root_record)] = root_record
    directory = (root_record + iso_record(b'\x01', 22, 2048, 2)
                 + iso_record(b'ACE.BPH;1', bph_lba, len(index))
                 + iso_record(b'ACE.BPB;1', bpb_lba, bpb_size))
    bpb = bytearray(bpb_size)
    for start, payload in blobs:
        bpb[start * 2048:start * 2048 + len(payload)] = payload
    path = root / 'synthetic.bin'
    with path.open('wb') as stream:
        for lba in range(bpb_lba + sector + 2):
            payload = b''
            if lba == 16:
                payload = pvd
            elif lba == 22:
                payload = directory
            elif bph_lba <= lba < bph_lba + (len(index) + 2047) // 2048:
                payload = index[(lba - bph_lba) * 2048:(lba - bph_lba + 1) * 2048]
            elif bpb_lba <= lba < bpb_lba + sector:
                payload = bytes(bpb[(lba - bpb_lba) * 2048:(lba - bpb_lba + 1) * 2048])
            stream.write(raw_sector(payload, lba))
    return path


class InspectAndCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.payloads = [
            container(encode_ulz(b'mission' * 30), padded(b'raw')),
            padded(b'opaque top-level record'),
            container(container(padded(b'a'), padded(b'b'))),
            container(sample_tim(8, 8), TIM_STUB),
        ]
        self.image = make_image(root, self.payloads)

    def tearDown(self):
        self.temp.cleanup()

    def test_inspect_walks_every_entry_with_iso_discovery(self):
        report = inspect_bpb(self.image)
        self.assertEqual(report['entry_count'], 4)
        self.assertEqual(report['schema_version'], 2)
        kinds = [entry['tree']['kind'] for entry in report['entries']]
        self.assertEqual(kinds, ['container', 'opaque', 'container', 'container'])
        tim_entry = report['entries'][3]['tree']['children']
        self.assertEqual([child['kind'] for child in tim_entry], ['tim', 'opaque'])
        self.assertEqual(tim_entry[0]['tim']['pixel_height'], 8)
        self.assertEqual(report['summary']['tim_modes'], {'4bpp': 1})
        self.assertEqual(report['summary']['tim_candidates_rejected'], 1)
        self.assertEqual(report['entries'][0]['sha256'],
                         hashlib.sha256(self.payloads[0]).hexdigest())
        self.assertEqual(report['entries'][2]['tree']['children'][0]['child_count'], 2)
        self.assertEqual(report['summary']['ulz_decoded'], 0)
        self.assertFalse(report['decode'])

    def test_entry_filter_and_decode(self):
        report = inspect_bpb(self.image, entries=[0], decode=True)
        self.assertEqual([entry['index'] for entry in report['entries']], [0])
        ulz_node = report['entries'][0]['tree']['children'][0]
        self.assertTrue(ulz_node['ulz']['decoded'])
        self.assertEqual(ulz_node['decoded']['size'], 210)
        self.assertEqual(report['summary']['ulz_decompressed_bytes'], 210)

    def test_unknown_entry_index_is_error(self):
        with self.assertRaises(Exception):
            inspect_bpb(self.image, entries=[9])
        with redirect_stderr(StringIO()):
            self.assertEqual(main([str(self.image), '--entry', '9']), 2)

    def test_cli_json_text_and_tree(self):
        out = StringIO()
        with redirect_stdout(out):
            self.assertEqual(main([str(self.image), '--json', '--decode']), 0)
        report = json.loads(out.getvalue())
        self.assertEqual(report['summary']['ulz_versions'], {'2': 1})
        out = StringIO()
        with redirect_stdout(out):
            self.assertEqual(main([str(self.image)]), 0)
        self.assertIn('4 entries walked', out.getvalue())
        self.assertIn("TIM modes: {'4bpp': 1}; with CLUT: 1; candidates rejected: 1",
                      out.getvalue())
        out = StringIO()
        with redirect_stdout(out):
            self.assertEqual(main([str(self.image), '--tree', '--entry', '3']), 0)
        self.assertIn('[3.0] tim', out.getvalue())
        self.assertIn('4bpp 32x8 clut=16', out.getvalue())
        self.assertIn('ERROR: TIM candidate rejected', out.getvalue())
        out = StringIO()
        with redirect_stdout(out):
            self.assertEqual(main([str(self.image), '--tree', '--entry', '2']), 0)
        self.assertIn('[2.0.1] opaque', out.getvalue())

    def test_missing_image_returns_2(self):
        with redirect_stderr(StringIO()):
            self.assertEqual(main([str(Path(self.temp.name) / 'absent.bin')]), 2)

    def test_overlapping_bph_and_bpb_extents_are_rejected(self):
        root = Path(self.temp.name) / 'overlap'
        root.mkdir()
        image = make_image(root, self.payloads, bph_lba=27, bpb_lba=27)
        with self.assertRaisesRegex(BphFormatError, 'overlap'):
            inspect_bpb(image)
        err = StringIO()
        with redirect_stderr(err):
            self.assertEqual(main([str(image)]), 2)
        self.assertIn('overlap', err.getvalue())


if __name__ == '__main__':
    unittest.main()
