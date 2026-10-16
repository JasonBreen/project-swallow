"""Read-only PlayStation TIM image validator for AC3 BPB opaque leaves.

``parse_tim`` checks the id word, the flags word, the optional CLUT block,
and the pixel block of one byte range and returns metadata only. It never
converts, writes, or exports pixel data. Rules and measurements:
docs/tim-format.md.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import struct

TIM_ID = 0x10
TIM_SIGNATURE = b'\x10\x00\x00\x00'
BLOCK_HEADER = struct.Struct('<IHHHH')
BLOCK_HEADER_BYTES = BLOCK_HEADER.size          # 12: length word + x, y, w, h
FILE_HEADER_BYTES = 8                           # id word + flags word
VRAM_WIDTH_HALFWORDS = 1024
VRAM_HEIGHT = 512
FLAG_PMODE_MASK = 0x7
FLAG_CLUT = 0x8
FLAG_KNOWN_MASK = FLAG_PMODE_MASK | FLAG_CLUT
PMODE_NAMES = {0: '4bpp', 1: '8bpp', 2: '16bpp', 3: '24bpp'}
# Halfwords per pixel column: 4bpp packs 4 pixels per halfword, 8bpp packs
# 2, 16bpp is 1:1, 24bpp uses 3 bytes (1.5 halfwords) per pixel.
PIXELS_PER_HALFWORD = {0: 4, 1: 2, 2: 1}


class TimFormatError(ValueError):
    """Data is not a TIM image this validator accepts."""


@dataclass(frozen=True)
class TimBlock:
    offset: int
    length: int
    x: int
    y: int
    width: int
    height: int

    @property
    def data_bytes(self) -> int:
        return self.length - BLOCK_HEADER_BYTES


@dataclass(frozen=True)
class TimInfo:
    size: int
    flags: int
    pmode: int
    bits_per_pixel: int
    clut: TimBlock | None
    pixels: TimBlock
    pixel_width: int
    pixel_height: int
    trailing_bytes: int = 0

    @property
    def clut_entries(self) -> int:
        return 0 if self.clut is None else self.clut.width * self.clut.height

    def to_report(self) -> dict:
        report = {
            'size': self.size,
            'flags': self.flags,
            'pmode': self.pmode,
            'mode': PMODE_NAMES[self.pmode],
            'bits_per_pixel': self.bits_per_pixel,
            'pixel_width': self.pixel_width,
            'pixel_height': self.pixel_height,
            'pixels': asdict(self.pixels),
            'trailing_bytes': self.trailing_bytes,
        }
        if self.clut is not None:
            report['clut'] = asdict(self.clut)
            report['clut_entries'] = self.clut_entries
        return report


def is_tim_candidate(data: bytes) -> bool:
    """True when the data starts with the TIM id word; no further validation."""
    return data[:4] == TIM_SIGNATURE


def _parse_block(data: bytes, offset: int, name: str) -> TimBlock:
    if offset + BLOCK_HEADER_BYTES > len(data):
        raise TimFormatError(
            f'Truncated {name} block header at {offset} in {len(data)} bytes')
    length, x, y, width, height = BLOCK_HEADER.unpack_from(data, offset)
    if width == 0 or height == 0:
        raise TimFormatError(f'{name} block has zero width or height ({width}x{height})')
    if x + width > VRAM_WIDTH_HALFWORDS or y + height > VRAM_HEIGHT:
        raise TimFormatError(
            f'{name} block {x},{y} {width}x{height} exceeds 1024x512 halfword VRAM')
    expected = BLOCK_HEADER_BYTES + 2 * width * height
    if length != expected:
        raise TimFormatError(
            f'{name} block length {length} does not equal 12 + 2 * {width} * {height}'
            f' = {expected}')
    if offset + length > len(data):
        raise TimFormatError(
            f'{name} block of {length} bytes at {offset} exceeds {len(data)} bytes')
    return TimBlock(offset, length, x, y, width, height)


def parse_tim(data: bytes, *, max_trailing_bytes: int = 0) -> TimInfo:
    """Validate one TIM image and return its metadata; pixels are never copied.

    Rules: the id word is exactly 0x00000010 (id 0x10, version 0, reserved
    zero); the flags word uses only bits 0-3 with pmode 0-3 (pmode 4,
    "mixed", is rejected as unvalidated); a CLUT block is present exactly
    when bit 3 is set and only for 4bpp/8bpp; each block's length word
    equals 12 + 2 * w * h, lies within the data, and the blocks together
    span the data exactly. ``max_trailing_bytes`` lets a caller that knows
    its ranges are padded (the BPB walker, whose containers align items to
    four bytes) accept up to that many zero bytes after the pixel block;
    their count is reported as ``trailing_bytes``.
    """
    if len(data) < FILE_HEADER_BYTES:
        raise TimFormatError('Fewer than 8 bytes cannot hold a TIM header')
    id_word, flags = struct.unpack_from('<II', data)
    if id_word != TIM_ID:
        raise TimFormatError(f'TIM id word 0x{id_word:08x} is not 0x00000010')
    if flags & ~FLAG_KNOWN_MASK:
        raise TimFormatError(f'TIM flags 0x{flags:08x} set bits above bit 3')
    pmode = flags & FLAG_PMODE_MASK
    if pmode not in PMODE_NAMES:
        raise TimFormatError(f'TIM pmode {pmode} is not validated (0-3 only)')
    has_clut = bool(flags & FLAG_CLUT)
    if has_clut and pmode > 1:
        raise TimFormatError(f'TIM CLUT flag set with direct-colour pmode {pmode}')
    offset = FILE_HEADER_BYTES
    clut = None
    if has_clut:
        clut = _parse_block(data, offset, 'CLUT')
        offset += clut.length
    pixels = _parse_block(data, offset, 'pixel')
    offset += pixels.length
    trailing = len(data) - offset
    if trailing > max_trailing_bytes:
        raise TimFormatError(
            f'TIM blocks end at {offset} but the data holds {len(data)} bytes')
    if any(data[offset:]):
        raise TimFormatError(
            f'TIM trailing {trailing} bytes after the pixel block are not zero')
    if pmode == 3:
        if (2 * pixels.width) % 3:
            raise TimFormatError(
                f'24bpp pixel block width {pixels.width} halfwords is not a whole pixel count')
        pixel_width = 2 * pixels.width // 3
        bits = 24
    else:
        pixel_width = pixels.width * PIXELS_PER_HALFWORD[pmode]
        bits = 16 // PIXELS_PER_HALFWORD[pmode]
    return TimInfo(len(data), flags, pmode, bits, clut, pixels, pixel_width, pixels.height,
                   trailing)
