"""Read-only decoder for Namco 'Ulz' compression as stored in AC3 BPB entries.

Format evidence and limits: docs/bpb-format.md. Follows the psx-spx description
of the AC3/Klonoa 'Ulz' variant; this is original project code. Agreement with
the game's own routine has not been proven yet (see docs/next-task-executable.md).
"""
from __future__ import annotations

from dataclasses import dataclass
import struct

MAGIC = b'Ulz\x1a'
HEADER = struct.Struct('<4sIII')
FLAGS_OFFSET = HEADER.size
SUPPORTED_VERSIONS = (0, 2)
MAX_OUTPUT = 16 * 1024 * 1024
MIN_MATCH = 3


class UlzFormatError(ValueError):
    """Malformed Ulz data, or a variant this decoder has not validated."""


@dataclass(frozen=True)
class UlzHeader:
    """Header fields; offsets are relative to the start of the Ulz block."""
    decompressed_size: int
    version: int
    literal_offset: int
    disp_bits: int
    reference_offset: int


@dataclass(frozen=True)
class UlzResult:
    """Decoded bytes plus where each input stream stopped (block-relative).

    Stream end positions let callers prove every input byte was consumed;
    the decoder itself does not require it.
    """
    header: UlzHeader
    data: bytes
    flag_end: int
    literal_end: int
    reference_end: int
    flag_words: int
    unused_reference_flags: int = 0


def parse_ulz_header(data: bytes, offset: int = 0, end: int | None = None) -> UlzHeader:
    """Decode and bounds-check a header; `end` limits the block (default: data end)."""
    end = len(data) if end is None else end
    if offset < 0 or end > len(data) or offset + HEADER.size > end:
        raise UlzFormatError('Truncated Ulz header: expected at least 16 bytes')
    magic, size_word, literal_word, reference_offset = HEADER.unpack_from(data, offset)
    if magic != MAGIC:
        raise UlzFormatError('Invalid Ulz magic: expected 55 6c 7a 1a')
    header = UlzHeader(size_word & 0xFFFFFF, size_word >> 24, literal_word & 0xFFFFFF,
                       literal_word >> 24, reference_offset)
    if header.version not in SUPPORTED_VERSIONS:
        raise UlzFormatError(f'Unsupported Ulz version {header.version}')
    if not 1 <= header.disp_bits <= 15:
        raise UlzFormatError(f'Unsupported Ulz displacement width {header.disp_bits}')
    if header.literal_offset == 0:
        # psx-spx notes version 0 may store 0 here; no measured example exists.
        raise UlzFormatError('Ulz literal offset 0 is unsupported')
    if not FLAGS_OFFSET <= header.literal_offset <= header.reference_offset <= end - offset:
        raise UlzFormatError(
            f'Ulz stream offsets out of order or bounds: literals {header.literal_offset}, '
            f'references {header.reference_offset}, block {end - offset}')
    return header


def decompress_ulz(data: bytes, offset: int = 0, end: int | None = None, *,
                   max_output: int = MAX_OUTPUT) -> UlzResult:
    """Decompress one block occupying data[offset:end].

    Streams: 32-bit flag words [16, literal_offset), literal bytes
    [literal_offset, reference_offset), 16-bit references [reference_offset, end).
    Flags are consumed from bit 31 down. A set flag copies one literal; a
    clear flag reads a reference: length = (value >> N) + 3, distance =
    (value & (2**N - 1)) + 1, copied bytewise so overlaps repeat output.
    Version 2 stops at the declared size; version 0 stops at a zero flag word
    and must then equal the declared size. Reads never leave their stream.

    Version 0 blocks from the English-labeled references contain clear
    (reference) flags after the declared size is reached, but no matching
    references. They are counted as unused_reference_flags and not executed.
    What the game's own routine does with them is unverified. A set
    (literal) flag after the declared size is still an error.
    """
    end = len(data) if end is None else end
    header = parse_ulz_header(data, offset, end)
    size = header.decompressed_size
    if size > max_output:
        raise UlzFormatError(f'Ulz output {size} exceeds the {max_output}-byte limit')
    flag_pos, flag_limit = offset + FLAGS_OFFSET, offset + header.literal_offset
    literal_pos, literal_limit = flag_limit, offset + header.reference_offset
    reference_pos, reference_limit = literal_limit, end
    disp_mask = (1 << header.disp_bits) - 1
    version = header.version
    out = bytearray()
    collected = 0x80000000
    words = unused_reference_flags = 0
    while True:
        if version == 2 and len(out) == size:
            break
        flag = collected & 0x80000000
        collected = (collected << 1) & 0xFFFFFFFF
        if not collected:
            if flag_pos + 4 > flag_limit:
                raise UlzFormatError('Ulz flag stream exhausted before output completed')
            word = int.from_bytes(data[flag_pos:flag_pos + 4], 'little')
            flag_pos += 4
            words += 1
            flag = word & 0x80000000
            if version == 0:
                if not word:
                    break
                collected = (word << 1) & 0xFFFFFFFF
            else:
                collected = ((word << 1) | 1) & 0xFFFFFFFF
        if version == 0 and len(out) == size and not flag:
            unused_reference_flags += 1
            continue
        if flag:
            if literal_pos >= literal_limit:
                raise UlzFormatError('Ulz literal stream exhausted')
            if len(out) >= size:
                raise UlzFormatError('Ulz output exceeds declared size')
            out.append(data[literal_pos])
            literal_pos += 1
            continue
        if reference_pos + 2 > reference_limit:
            raise UlzFormatError('Ulz reference stream exhausted')
        value = data[reference_pos] | data[reference_pos + 1] << 8
        reference_pos += 2
        length = (value >> header.disp_bits) + MIN_MATCH
        distance = (value & disp_mask) + 1
        if distance > len(out):
            raise UlzFormatError(
                f'Ulz reference distance {distance} precedes output start at {len(out)}')
        if len(out) + length > size:
            raise UlzFormatError('Ulz output exceeds declared size')
        start = len(out) - distance
        if distance >= length:
            out += out[start:start + length]
        else:
            for index in range(start, start + length):
                out.append(out[index])
    if len(out) != size:
        raise UlzFormatError(f'Ulz output {len(out)} bytes differs from declared {size}')
    return UlzResult(header, bytes(out), flag_pos - offset, literal_pos - offset,
                     reference_pos - offset, words, unused_reference_flags)
