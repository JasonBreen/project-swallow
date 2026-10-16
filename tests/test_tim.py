"""TIM validator contracts exercised with synthetic images; metadata only."""
import struct
import unittest

from swallow_tools.tim import (
    TimFormatError, is_tim_candidate, parse_tim,
)


def block(x, y, width, height, *, length=None, fill=b'\x11'):
    data = fill * (2 * width * height)
    return struct.pack('<IHHHH', 12 + len(data) if length is None else length,
                       x, y, width, height) + data


def tim(flags, *blocks, id_word=0x10):
    return struct.pack('<II', id_word, flags) + b''.join(blocks)


class CandidateTests(unittest.TestCase):
    def test_signature_only(self):
        self.assertTrue(is_tim_candidate(b'\x10\x00\x00\x00'))
        self.assertTrue(is_tim_candidate(b'\x10\x00\x00\x00garbage'))
        self.assertFalse(is_tim_candidate(b'\x10\x00\x00'))
        self.assertFalse(is_tim_candidate(b'\x10\x01\x00\x00'))
        self.assertFalse(is_tim_candidate(b'Ulz\x1a'))


class ParseTests(unittest.TestCase):
    def test_4bpp_with_clut(self):
        data = tim(0x8, block(320, 256, 16, 1), block(640, 0, 16, 64))
        info = parse_tim(data)
        self.assertEqual((info.pmode, info.bits_per_pixel, info.flags), (0, 4, 8))
        self.assertEqual((info.pixel_width, info.pixel_height), (64, 64))
        self.assertEqual((info.clut.x, info.clut.y, info.clut.width, info.clut.height),
                         (320, 256, 16, 1))
        self.assertEqual(info.clut.offset, 8)
        self.assertEqual(info.clut.length, 44)
        self.assertEqual(info.clut_entries, 16)
        self.assertEqual((info.pixels.offset, info.pixels.length), (52, 12 + 2048))
        self.assertEqual(info.pixels.data_bytes, 2048)
        self.assertEqual(info.size, len(data))
        self.assertEqual(info.trailing_bytes, 0)
        report = info.to_report()
        self.assertEqual(report['mode'], '4bpp')
        self.assertEqual(report['clut']['width'], 16)
        self.assertEqual(report['pixels']['x'], 640)
        self.assertNotIn('data', report)

    def test_8bpp_with_clut(self):
        info = parse_tim(tim(0x9, block(0, 480, 256, 1), block(512, 0, 32, 8)))
        self.assertEqual((info.pmode, info.bits_per_pixel), (1, 8))
        self.assertEqual((info.pixel_width, info.pixel_height), (64, 8))
        self.assertEqual(info.clut_entries, 256)

    def test_4bpp_without_clut(self):
        info = parse_tim(tim(0x0, block(0, 0, 2, 2)))
        self.assertIsNone(info.clut)
        self.assertEqual(info.to_report().get('clut'), None)
        self.assertEqual((info.pixel_width, info.pixel_height), (8, 2))
        self.assertEqual(info.pixels.offset, 8)

    def test_16bpp_direct(self):
        info = parse_tim(tim(0x2, block(0, 0, 10, 3)))
        self.assertEqual((info.bits_per_pixel, info.pixel_width, info.pixel_height),
                         (16, 10, 3))

    def test_24bpp_direct(self):
        info = parse_tim(tim(0x3, block(0, 0, 48, 2)))
        self.assertEqual((info.bits_per_pixel, info.pixel_width), (24, 32))
        with self.assertRaisesRegex(TimFormatError, 'whole pixel'):
            parse_tim(tim(0x3, block(0, 0, 7, 2)))

    def test_vram_edges_are_inclusive(self):
        parse_tim(tim(0x2, block(1023, 511, 1, 1)))
        parse_tim(tim(0x8, block(1008, 511, 16, 1), block(0, 0, 1024, 512)))

    def test_pixels_are_never_copied(self):
        payload = bytes(range(256)) * 8
        header = struct.pack('<IHHHH', 12 + len(payload), 0, 0, 32, 32)
        info = parse_tim(tim(0x2, header + payload))
        self.assertEqual(info.pixels.data_bytes, 2048)
        self.assertNotIn(repr(payload), repr(info))
        self.assertLess(len(repr(info)), 400)
        self.assertNotIn(payload.hex(), repr(info.to_report()))

    def test_trailing_padding_allowance(self):
        image = tim(0x8, block(0, 0, 16, 1), block(0, 0, 1, 1))
        self.assertEqual(len(image) % 4, 2)
        with self.assertRaisesRegex(TimFormatError, 'blocks end at 66 but the data holds 68'):
            parse_tim(image + b'\0\0')
        info = parse_tim(image + b'\0\0', max_trailing_bytes=3)
        self.assertEqual(info.trailing_bytes, 2)
        self.assertEqual(info.size, 68)
        self.assertEqual(info.to_report()['trailing_bytes'], 2)
        with self.assertRaisesRegex(TimFormatError, 'not zero'):
            parse_tim(image + b'\0\x01', max_trailing_bytes=3)
        with self.assertRaisesRegex(TimFormatError, 'blocks end at 66'):
            parse_tim(image + b'\0' * 4, max_trailing_bytes=3)

    def test_rejections(self):
        good_clut = block(0, 0, 16, 1)
        cases = {
            'too_short': b'\x10\x00\x00\x00',
            'wrong_id': tim(0x8, good_clut, block(0, 0, 4, 4), id_word=0x11),
            'version_byte': tim(0x8, good_clut, block(0, 0, 4, 4), id_word=0x0110),
            'high_flag_bits': tim(0x18, good_clut, block(0, 0, 4, 4)),
            'pmode_mixed': tim(0x4, block(0, 0, 4, 4)),
            'pmode_invalid': tim(0x7, block(0, 0, 4, 4)),
            'clut_with_16bpp': tim(0xA, good_clut, block(0, 0, 4, 4)),
            'clut_with_24bpp': tim(0xB, good_clut, block(0, 0, 6, 4)),
            'truncated_clut_header': tim(0x8, good_clut[:8]),
            'truncated_pixel_header': tim(0x8, good_clut, block(0, 0, 4, 4)[:10]),
            'clut_zero_height': tim(0x8, block(0, 0, 16, 0), block(0, 0, 4, 4)),
            'pixel_zero_width': tim(0x0, block(0, 0, 0, 4)),
            'clut_beyond_vram': tim(0x9, block(960, 256, 256, 1), block(0, 0, 4, 4)),
            'pixel_beyond_vram_x': tim(0x2, block(1020, 0, 5, 1)),
            'pixel_beyond_vram_y': tim(0x2, block(0, 510, 1, 3)),
            'clut_length_mismatch': tim(0x8, block(0, 0, 16, 1, length=40), block(0, 0, 4, 4)),
            'pixel_length_short': tim(0x8, good_clut, block(0, 0, 16, 16, length=516)),
            'pixel_length_long': tim(0x0, block(0, 0, 4, 4, length=48)),
            'pixel_data_truncated': tim(0x0, block(0, 0, 4, 4)[:-2]),
            'trailing_bytes': tim(0x0, block(0, 0, 4, 4)) + b'\0\0\0\0',
            'missing_pixel_block': tim(0x8, good_clut),
        }
        for name, data in cases.items():
            with self.subTest(name=name), self.assertRaises(TimFormatError):
                parse_tim(data)

    def test_default_rejects_any_trailing_byte(self):
        with self.assertRaisesRegex(TimFormatError, 'blocks end at 52'):
            parse_tim(tim(0x0, block(0, 0, 4, 4)) + b'\0')


if __name__ == '__main__':
    unittest.main()
