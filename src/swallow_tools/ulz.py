"""Read-only Namco ``Ulz`` decompression for AC3 BPB leaves (versions 0 and 2).

Format evidence and limits: docs/bpb-format.md. The bit layout was derived
from local measurements and cross-checked against the public psx-spx notes;
this is original project code and incorporates no third-party unpacker.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import struct

MAGIC = b'Ulz\x1a'
HEADER = struct.Struct('<4sIII')
MAX_OUTPUT_BYTES = 16 * 1024 * 1024
SUPPORTED_VERSIONS = (0, 2)
# Offset bit counts measured on all four references; others are unvalidated.
SUPPORTED_OFFSET_BITS = (10, 11, 12, 13)
WORD_MASK = 0xFFFFFFFF


class UlzFormatError(ValueError):
    """Malformed Ulz data or a variant this decoder does not validate."""


@dataclass(frozen=True)
class UlzHeader:
    decompressed_size: int
    version: int
    literal_offset: int
    offset_bits: int
    pair_offset: int

    @property
    def length_bits(self) -> int:
        return 16 - self.offset_bits


@dataclass(frozen=True)
class UlzResult:
    header: UlzHeader
    payload: bytes
    flag_words_used: int
    flag_words_unused: int
    unused_flag_words_zero: bool
    final_word_unused_flag_bits: int
    literal_bytes_used: int
    literal_bytes_unused: int
    pair_bytes_used: int
    pair_bytes_unused: int
    literal_count: int
    match_count: int

    def to_report(self) -> dict:
        report = {key: value for key, value in asdict(self).items() if key != 'payload'}
        report['header']['length_bits'] = self.header.length_bits
        return report


def is_ulz(data: bytes) -> bool:
    """True when the data starts with the Ulz signature; no further validation."""
    return data[:4] == MAGIC


def parse_ulz_header(data: bytes, *, max_output: int = MAX_OUTPUT_BYTES) -> UlzHeader:
    """Validate the 16-byte header and stream layout without decompressing."""
    if len(data) < HEADER.size:
        raise UlzFormatError('Truncated Ulz header: expected at least 16 bytes')
    magic, size_word, literal_word, pair_offset = HEADER.unpack_from(data)
    if magic != MAGIC:
        raise UlzFormatError('Invalid Ulz magic')
    version = size_word >> 24
    decompressed_size = size_word & 0xFFFFFF
    offset_bits = literal_word >> 24
    literal_offset = literal_word & 0xFFFFFF
    if version not in SUPPORTED_VERSIONS:
        raise UlzFormatError(f'Unsupported Ulz version {version}; supported: 0 and 2')
    if offset_bits not in SUPPORTED_OFFSET_BITS:
        raise UlzFormatError(
            f'Ulz offset bit count {offset_bits} is unvalidated; measured counts are 10-13')
    if decompressed_size > max_output:
        raise UlzFormatError(
            f'Ulz decompressed size {decompressed_size} exceeds the {max_output} byte limit')
    if literal_offset < HEADER.size or (literal_offset - HEADER.size) % 4:
        raise UlzFormatError(
            f'Ulz literal offset {literal_offset} must follow whole 32-bit flag words')
    if pair_offset < literal_offset or pair_offset > len(data):
        raise UlzFormatError(
            f'Ulz stream offsets {literal_offset}/{pair_offset} are not ordered within {len(data)} bytes')
    return UlzHeader(decompressed_size, version, literal_offset, offset_bits, pair_offset)


def decompress_ulz(data: bytes, *, max_output: int = MAX_OUTPUT_BYTES) -> UlzResult:
    """Decode one Ulz blob and report exact stream consumption.

    Flags are 32-bit little-endian words consumed from bit 31 downward: a one
    bit copies one literal byte, a zero bit reads a 16-bit little-endian pair
    whose low ``offset_bits`` bits hold distance minus one and whose high bits
    hold length minus three. Version 2 uses all 32 bits of each word. Version 0
    reserves the lowest set bit of each word as a sentinel; bits below it are
    unused and a zero word has no flags. Output must reach the declared size
    exactly; any exhausted stream, early reference, or overrun is an error.
    Version 0 must also have a zero flag word immediately after the last word
    used; a missing or nonzero terminator is an error.
    """
    header = parse_ulz_header(data, max_output=max_output)
    flags = data[HEADER.size:header.literal_offset]
    literals = data[header.literal_offset:header.pair_offset]
    pairs = data[header.pair_offset:]
    flag_word_count = len(flags) // 4
    offset_mask = (1 << header.offset_bits) - 1
    target = header.decompressed_size

    out = bytearray()
    word = 0
    bits_left = 0
    flag_index = literal_index = pair_index = 0
    literal_count = match_count = 0
    while len(out) < target:
        if bits_left == 0:
            if flag_index >= flag_word_count:
                raise UlzFormatError(
                    f'Ulz flag stream exhausted after {len(out)} of {target} bytes')
            word = struct.unpack_from('<I', flags, flag_index * 4)[0]
            flag_index += 1
            if header.version == 0:
                if word == 0:
                    raise UlzFormatError(
                        f'Ulz version 0 zero flag word after {len(out)} of {target} bytes')
                bits_left = 31 - ((word & -word).bit_length() - 1)
                if bits_left == 0:
                    # 0x80000000 never occurs in the references; its meaning is unknown.
                    raise UlzFormatError(
                        f'Ulz version 0 sentinel-only flag word {flag_index - 1} is '
                        f'unvalidated (after {len(out)} of {target} bytes)')
            else:
                bits_left = 32
        bit = word >> 31
        word = (word << 1) & WORD_MASK
        bits_left -= 1
        if bit:
            if literal_index >= len(literals):
                raise UlzFormatError(
                    f'Ulz literal stream exhausted after {len(out)} of {target} bytes')
            out.append(literals[literal_index])
            literal_index += 1
            literal_count += 1
            continue
        if pair_index + 2 > len(pairs):
            raise UlzFormatError(
                f'Ulz pair stream exhausted after {len(out)} of {target} bytes')
        value = pairs[pair_index] | (pairs[pair_index + 1] << 8)
        pair_index += 2
        distance = (value & offset_mask) + 1
        length = (value >> header.offset_bits) + 3
        if distance > len(out):
            raise UlzFormatError(
                f'Ulz back-reference distance {distance} precedes output start at {len(out)}')
        if len(out) + length > target:
            raise UlzFormatError(
                f'Ulz match of {length} bytes overruns declared size {target} at {len(out)}')
        match_count += 1
        if distance >= length:
            start = len(out) - distance
            out += out[start:start + length]
        else:
            for _ in range(length):
                out.append(out[-distance])
    if header.version == 0:
        # Flags left above the sentinel after the declared size are accepted only
        # when zero: every measured case (English references only) is zero.
        if bits_left and word >> (32 - bits_left):
            raise UlzFormatError(
                f'Ulz version 0 final flag word has nonzero unused flags '
                f'0b{word >> (32 - bits_left):0{bits_left}b} after the declared size')
        # Version 0 ends on a zero flag word; reaching the size alone is not enough.
        terminator = flags[flag_index * 4:flag_index * 4 + 4]
        if terminator != b'\0\0\0\0':
            state = 'missing' if len(terminator) < 4 else f'0x{int.from_bytes(terminator, "little"):08x}'
            raise UlzFormatError(
                f'Ulz version 0 terminator after flag word {flag_index} is {state}, not zero')
    unused_flags = flags[flag_index * 4:]
    return UlzResult(
        header=header,
        payload=bytes(out),
        flag_words_used=flag_index,
        flag_words_unused=flag_word_count - flag_index,
        unused_flag_words_zero=not any(unused_flags),
        final_word_unused_flag_bits=bits_left,
        literal_bytes_used=literal_index,
        literal_bytes_unused=len(literals) - literal_index,
        pair_bytes_used=pair_index,
        pair_bytes_unused=len(pairs) - pair_index,
        literal_count=literal_count,
        match_count=match_count,
    )
