"""Ulz decoder contracts exercised with synthetic blobs and a local test encoder."""
import random
import struct
import unittest

from swallow_tools.ulz import (
    MAGIC, UlzFormatError, decompress_ulz, is_ulz, parse_ulz_header,
)


def pack_flags(bits, version):
    """Pack flag bits into little-endian words the way the decoder expects."""
    words = []
    if version == 2:
        for start in range(0, len(bits), 32):
            chunk = bits[start:start + 32]
            word = 0
            for bit in chunk:
                word = (word << 1) | bit
            words.append(word << (32 - len(chunk)))
    else:
        for start in range(0, len(bits), 31):
            chunk = bits[start:start + 31]
            word = 0
            for bit in chunk:
                word = (word << 1) | bit
            word = (word << 1) | 1            # sentinel directly below the last flag
            words.append(word << (31 - len(chunk)))
    words.append(0)                           # observed trailing zero terminator
    return b''.join(word.to_bytes(4, 'little') for word in words)


def build_ulz(payload_size, version, offset_bits, flags, literals, pairs,
              *, literal_pad=None, pair_pad=None, terminator=True):
    flag_bytes = pack_flags(flags, version)
    if not terminator:
        flag_bytes = flag_bytes[:-4]
    literal_bytes = bytes(literals)
    literal_bytes += b'\0' * (-len(literal_bytes) % 4 if literal_pad is None else literal_pad)
    pair_bytes = b''.join(struct.pack('<H', value) for value in pairs)
    pair_bytes += b'\0' * (-len(pair_bytes) % 4 if pair_pad is None else pair_pad)
    literal_offset = 16 + len(flag_bytes)
    pair_offset = literal_offset + len(literal_bytes)
    header = MAGIC + struct.pack('<III', payload_size | (version << 24),
                                 literal_offset | (offset_bits << 24), pair_offset)
    return header + flag_bytes + literal_bytes + pair_bytes


def encode_ulz(payload, version=2, offset_bits=10):
    """Greedy reference encoder used only to produce round-trip fixtures."""
    max_distance = 1 << offset_bits
    max_length = (1 << (16 - offset_bits)) + 2
    flags, literals, pairs = [], bytearray(), []
    position = 0
    while position < len(payload):
        best_length = best_distance = 0
        for candidate in range(max(0, position - max_distance), position):
            length = 0
            while (length < max_length and position + length < len(payload)
                   and payload[candidate + length] == payload[position + length]):
                length += 1
            if length > best_length:
                best_length, best_distance = length, position - candidate
        if best_length >= 3:
            flags.append(0)
            pairs.append((best_distance - 1) | ((best_length - 3) << offset_bits))
            position += best_length
        else:
            flags.append(1)
            literals.append(payload[position])
            position += 1
    return build_ulz(len(payload), version, offset_bits, flags, literals, pairs)


class UlzHandVectorTests(unittest.TestCase):
    def test_version2_literal_then_overlapping_match(self):
        # One literal zero, then distance 1 / length 39 copies it 39 more times.
        blob = build_ulz(40, 2, 10, [1, 0], [0], [(0) | (36 << 10)])
        result = decompress_ulz(blob)
        self.assertEqual(result.payload, bytes(40))
        self.assertEqual((result.literal_count, result.match_count), (1, 1))
        self.assertEqual((result.flag_words_used, result.flag_words_unused), (1, 1))
        self.assertTrue(result.unused_flag_words_zero)
        self.assertEqual((result.literal_bytes_used, result.literal_bytes_unused), (1, 3))
        self.assertEqual((result.pair_bytes_used, result.pair_bytes_unused), (2, 2))

    def test_flag_bits_are_consumed_from_bit31_of_little_endian_words(self):
        blob = build_ulz(4, 2, 10, [1, 1, 1, 1], b'abcd', [])
        self.assertEqual(blob[16:20], b'\x00\x00\x00\xf0')
        self.assertEqual(decompress_ulz(blob).payload, b'abcd')

    def test_offset_bits_split_distance_and_length(self):
        # 12 offset bits: distance field 2 -> distance 3, length field 1 -> length 4.
        blob = build_ulz(7, 2, 12, [1, 1, 1, 0], b'xyz', [2 | (1 << 12)])
        self.assertEqual(decompress_ulz(blob).payload, b'xyzxyzx')

    def test_version0_sentinel_marks_end_of_each_word(self):
        payload = bytes(range(1, 41))
        blob = build_ulz(40, 0, 10, [1] * 40, payload, [])
        words = struct.unpack_from('<III', blob, 16)
        self.assertEqual(words[0], 0xFFFFFFFF)          # 31 flags plus sentinel
        self.assertEqual(words[1], 0xFFC00000)          # 9 flags, sentinel at bit 22
        self.assertEqual(words[2], 0)
        result = decompress_ulz(blob)
        self.assertEqual(result.payload, payload)
        self.assertEqual(result.flag_words_used, 2)

    def test_version0_sentinel_only_word_contributes_no_flags(self):
        flags = pack_flags([1, 1], 0)
        flag_bytes = (0x80000000).to_bytes(4, 'little') + flags
        blob = (MAGIC + struct.pack('<III', 2, (16 + len(flag_bytes)) | (10 << 24),
                                    16 + len(flag_bytes) + 4) + flag_bytes + b'ab\0\0')
        self.assertEqual(decompress_ulz(blob).payload, b'ab')

    def test_version0_zero_word_before_completion_is_error(self):
        blob = build_ulz(3, 0, 10, [1, 1], b'ab', [])
        with self.assertRaisesRegex(UlzFormatError, 'zero flag word'):
            decompress_ulz(blob)

    def test_version2_uses_all_32_bits(self):
        payload = bytes(range(33))
        blob = build_ulz(33, 2, 10, [1] * 33, payload, [])
        result = decompress_ulz(blob)
        self.assertEqual(result.payload, payload)
        self.assertEqual(result.flag_words_used, 2)


class UlzRoundTripTests(unittest.TestCase):
    def test_round_trip_versions_and_offset_bits(self):
        rng = random.Random(7)
        words = [bytes(rng.randrange(256) for _ in range(rng.randrange(1, 6)))
                 for _ in range(12)]
        payload = b''.join(rng.choice(words) for _ in range(600))
        for version in (0, 2):
            for offset_bits in (10, 11, 12, 13):
                with self.subTest(version=version, offset_bits=offset_bits):
                    blob = encode_ulz(payload, version, offset_bits)
                    result = decompress_ulz(blob)
                    self.assertEqual(result.payload, payload)
                    self.assertLess(len(blob), len(payload))
                    self.assertEqual(result.header.version, version)
                    self.assertEqual(result.header.length_bits, 16 - offset_bits)
                    self.assertEqual(result.flag_words_unused, 1)
                    self.assertLessEqual(result.literal_bytes_unused, 3)
                    self.assertIn(result.pair_bytes_unused, (0, 2))

    def test_round_trip_incompressible_and_empty(self):
        rng = random.Random(3)
        noise = bytes(rng.randrange(256) for _ in range(500))
        self.assertEqual(decompress_ulz(encode_ulz(noise)).payload, noise)
        self.assertEqual(decompress_ulz(encode_ulz(noise, version=0)).payload, noise)
        empty = encode_ulz(b'')
        self.assertEqual(decompress_ulz(empty).payload, b'')

    def test_report_omits_payload(self):
        report = decompress_ulz(encode_ulz(b'swallow' * 20)).to_report()
        self.assertNotIn('payload', report)
        self.assertEqual(report['header']['decompressed_size'], 140)
        self.assertEqual(report['header']['length_bits'], 6)


class UlzRejectionTests(unittest.TestCase):
    def test_signature_check(self):
        self.assertTrue(is_ulz(MAGIC + b'rest'))
        self.assertFalse(is_ulz(b'ULZ\x1a'))
        self.assertFalse(is_ulz(b''))

    def test_header_rejections(self):
        good = encode_ulz(b'abcabcabcabc')
        cases = {
            'truncated': good[:15],
            'magic': b'ULZ\x1a' + good[4:],
            'version': good[:7] + b'\x01' + good[8:],
            'offset_bits_zero': good[:11] + b'\x00' + good[12:],
            'offset_bits_16': good[:11] + b'\x10' + good[12:],
            'literal_misaligned': good[:8] + struct.pack('<I', 18 | (10 << 24)) + good[12:],
            'pair_before_literal': good[:12] + struct.pack('<I', 16) + good[16:],
            'pair_past_end': good[:12] + struct.pack('<I', len(good) + 1) + good[16:],
        }
        for name, blob in cases.items():
            with self.subTest(name=name), self.assertRaises(UlzFormatError):
                parse_ulz_header(blob)

    def test_size_limit(self):
        blob = build_ulz(100, 2, 10, [1], b'a', [])
        with self.assertRaisesRegex(UlzFormatError, 'exceeds'):
            parse_ulz_header(blob, max_output=99)
        with self.assertRaises(UlzFormatError):
            decompress_ulz(blob, max_output=99)

    def test_stream_exhaustion_is_error(self):
        with self.assertRaisesRegex(UlzFormatError, 'flag stream'):
            decompress_ulz(build_ulz(40, 2, 10, [1] * 32, b'a' * 40, [], terminator=False))
        with self.assertRaisesRegex(UlzFormatError, 'literal stream'):
            decompress_ulz(build_ulz(2, 2, 10, [1, 1], b'a', [], literal_pad=0))
        with self.assertRaisesRegex(UlzFormatError, 'pair stream'):
            decompress_ulz(build_ulz(4, 2, 10, [1, 0], b'a', []))

    def test_reference_before_output_start_is_error(self):
        with self.assertRaisesRegex(UlzFormatError, 'precedes output start'):
            decompress_ulz(build_ulz(3, 2, 10, [0], b'', [0]))
        with self.assertRaisesRegex(UlzFormatError, 'precedes output start'):
            decompress_ulz(build_ulz(4, 2, 10, [1, 0], b'a', [1]))

    def test_match_overrun_is_error_not_truncation(self):
        with self.assertRaisesRegex(UlzFormatError, 'overruns'):
            decompress_ulz(build_ulz(3, 2, 10, [1, 0], b'a', [0]))


if __name__ == '__main__':
    unittest.main()
