"""Ulz decoder contracts using hand-built blocks with known expected output.

Flag words, literals, and references below are written by hand; there is no
test-side encoder that could share a misunderstanding with the decoder.
"""
import struct
import unittest

from swallow_tools.ulz import UlzFormatError, decompress_ulz, parse_ulz_header


def block(version, size, disp_bits, flags, literals=b'', references=(),
          *, literal_offset=None, reference_offset=None, truncate=None,
          magic=b'Ulz\x1a'):
    """Lay out header + streams with 4-byte alignment like measured AC3 blocks."""
    flag_bytes = b''.join(struct.pack('<I', word) for word in flags)
    literal_offset = 16 + len(flag_bytes) if literal_offset is None else literal_offset
    padded = literals + b'\x00' * (-len(literals) % 4)
    reference_offset = (16 + len(flag_bytes) + len(padded)
                        if reference_offset is None else reference_offset)
    data = struct.pack('<4sIII', magic, size | version << 24,
                       literal_offset | disp_bits << 24, reference_offset)
    data += flag_bytes + padded + b''.join(struct.pack('<H', ref) for ref in references)
    return data if truncate is None else data[:truncate]


def ref(length, distance, disp_bits=10):
    return (length - 3) << disp_bits | (distance - 1)


# Version 2: four literals, then the zero flag word every measured v2 block ends with.
V2_LITERALS = block(2, 4, 10, [0xF0000000, 0], b'ABCD')
# Version 2: 'A', 'B', then a length-5 distance-2 overlapping reference.
V2_OVERLAP = block(2, 7, 10, [0xC0000000, 0], b'AB', [ref(5, 2)])
# Version 0: literal 'A', reference (3, 1), then the stop bit; a zero word ends it.
V0_RUN = block(0, 4, 10, [0xA0000000, 0], b'A', [ref(3, 1)])


class UlzDecoderTests(unittest.TestCase):
    def test_header_fields(self):
        header = parse_ulz_header(V2_OVERLAP)
        self.assertEqual((header.decompressed_size, header.version, header.literal_offset,
                          header.disp_bits, header.reference_offset), (7, 2, 24, 10, 28))

    def test_version2_literals_and_stream_accounting(self):
        result = decompress_ulz(V2_LITERALS)
        self.assertEqual(result.data, b'ABCD')
        self.assertEqual((result.flag_end, result.literal_end, result.reference_end,
                          result.flag_words), (20, 28, 28, 1))

    def test_overlapping_reference_repeats_output(self):
        result = decompress_ulz(V2_OVERLAP)
        self.assertEqual(result.data, b'ABABABA')
        self.assertEqual((result.flag_end, result.literal_end, result.reference_end),
                         (20, 26, 30))

    def test_nonoverlapping_reference(self):
        data = block(2, 7, 10, [0xF0000000, 0], b'WXYZ', [ref(3, 4)])
        self.assertEqual(decompress_ulz(data).data, b'WXYZWXY')

    def test_version0_stops_at_zero_flag_word(self):
        result = decompress_ulz(V0_RUN)
        self.assertEqual(result.data, b'AAAA')
        self.assertEqual((result.flag_end, result.flag_words), (24, 2))

    def test_version0_literals_only(self):
        self.assertEqual(decompress_ulz(block(0, 2, 10, [0xE0000000, 0], b'AB')).data, b'AB')

    def test_version0_trailing_reference_flags_are_counted_not_run(self):
        # 'A', 'B', two clear flags with no references behind them, stop bit at 27.
        result = decompress_ulz(block(0, 2, 10, [0xC8000000, 0], b'AB'))
        self.assertEqual(result.data, b'AB')
        self.assertEqual(result.unused_reference_flags, 2)
        self.assertEqual(decompress_ulz(V0_RUN).unused_reference_flags, 0)

    def test_each_measured_displacement_width(self):
        for bits in (10, 11, 12, 13):
            with self.subTest(bits=bits):
                data = block(2, 6, bits, [0xC0000000, 0], b'PQ', [ref(4, 2, bits)])
                self.assertEqual(decompress_ulz(data).data, b'PQPQPQ')
        # The same reference word means something else under another width.
        data = block(2, 6, 11, [0xC0000000, 0], b'PQ', [ref(4, 2, 10)])
        with self.assertRaises(UlzFormatError):
            decompress_ulz(data)

    def test_flag_word_boundary_uses_all_32_bits_in_version2(self):
        literals = bytes(range(33))
        data = block(2, 33, 10, [0xFFFFFFFF, 0x80000000, 0], literals)
        self.assertEqual(decompress_ulz(data).data, literals)

    def test_block_end_limits_reference_stream(self):
        padded = V2_OVERLAP + b'\x00\x00'
        self.assertEqual(decompress_ulz(padded, end=len(V2_OVERLAP)).reference_end, 30)
        self.assertEqual(decompress_ulz(b'xx' + V2_OVERLAP, 2).data, b'ABABABA')

    def test_rejections(self):
        cases = {
            'magic': block(2, 4, 10, [0xF0000000, 0], b'ABCD', magic=b'ULZ\x1a'),
            'version': block(1, 4, 10, [0xF0000000, 0], b'ABCD'),
            'disp_zero': block(2, 4, 0, [0xF0000000, 0], b'ABCD'),
            'disp_wide': block(2, 4, 16, [0xF0000000, 0], b'ABCD'),
            'literal_offset_zero': block(0, 2, 10, [0xE0000000, 0], b'AB', literal_offset=0),
            'offsets_reversed': block(2, 4, 10, [0xF0000000, 0], b'ABCD',
                                      literal_offset=28, reference_offset=24),
            'reference_offset_beyond': block(2, 4, 10, [0xF0000000, 0], b'ABCD',
                                             reference_offset=64),
            'truncated_header': V2_LITERALS[:15],
            'flags_exhausted': block(2, 4, 10, [], b'ABCD'),
            'literals_exhausted': block(2, 4, 10, [0xF0000000, 0], b'ABCD',
                                        reference_offset=26),
            'references_exhausted': V2_OVERLAP[:-1],
            'distance_before_start': block(2, 3, 10, [0x00000000, 0], b'', [ref(3, 1)]),
            'overrun': block(2, 6, 10, [0xC0000000, 0], b'AB', [ref(5, 2)]),
            'literal_overrun': block(0, 1, 10, [0xE0000000, 0], b'AB'),
            'version0_short': block(0, 5, 10, [0xA0000000, 0], b'A', [ref(3, 1)]),
        }
        for name, data in cases.items():
            with self.subTest(name=name), self.assertRaises(UlzFormatError):
                decompress_ulz(data)

    def test_output_limit(self):
        with self.assertRaises(UlzFormatError):
            decompress_ulz(V2_LITERALS, max_output=3)


if __name__ == '__main__':
    unittest.main()
