# ACE.BPB nested container and Ulz contract

Implemented in [`swallow_tools.bpb`](../src/swallow_tools/bpb.py) and
[`swallow_tools.ulz`](../src/swallow_tools/ulz.py), tooling v0.4.0, with TIM
leaf validation ([`swallow_tools.tim`](../src/swallow_tools/tim.py),
[contract](tim-format.md)) added in v0.5.0. Validated on 2026-10-15 and
2026-10-16 against all four supplied images; see
[measurements and reproduction](bpb-validation.md). This builds on the
[BPH index contract](bph-format.md): every range below is a normal BPH entry's
payload inside the logical Form 1 contents of `ACE.BPB`.

## Entry payload classification

The walker classifies a byte range by structure, in this order:

1. **Ulz leaf** — the range starts with ASCII `Ulz` followed by `0x1a`.
2. **Offset-table container** — the range satisfies every rule in the next
   section. This is the shape psx-spx calls `DOT1` (four-byte aligned, no end
   entry). Its items are classified recursively.
3. **TIM image leaf** — the range starts with the TIM id word `0x00000010`
   and passes every rule in the [TIM contract](tim-format.md). Up to three
   zero bytes after the pixel block are accepted only in a non-final item,
   whose next aligned table offset proves they are padding.
   The report carries mode, dimensions, CLUT size, and VRAM placement only.
   A range that starts with the id word but fails validation stays opaque
   and records the rejection reason.
4. **Opaque leaf** — anything else. The report records only its size and the
   hex of its first four bytes as a `signature`; no semantics are claimed.

A zero-length item of a container is reported as `empty`. Classification is
purely structural: a leaf whose first words happen to satisfy the table rules
is indistinguishable from a container, so the kinds describe how the bytes
parse, not what the game does with them.

## Offset-table container

All integers are little-endian. A container holds a 32-bit `count`, then
`count` 32-bit offsets relative to the container's first byte, then the items.

| Rule | Reader behaviour |
|---|---|
| `count >= 1` | Zero is rejected; the range is then an opaque leaf |
| `offsets[0] == 4 + 4 * count` | The table is immediately followed by the first item |
| Every offset is a multiple of 4 | Measured on every table in all four images |
| Offsets are nondecreasing | Equal consecutive offsets produce `empty` items |
| `offsets[-1] <= len(range)` | The last item extends to the end of the range |

There is no end-offset entry and no item-size field: an item's size is the
distance to the next offset or to the end of the enclosing range. Top-level
entries therefore already carry the zero-filled alignment padding measured in
the BPH validation; nested items do not.

Stored (undecoded) nesting reaches depth 3 in these images. The walker stops
at depth 8 and at 100,000 nodes per entry. Every node is charged before it is
built: a table reserves all of its declared children, empty ones included,
before descending, and a table whose count exceeds the remaining budget is
rejected before its offsets are unpacked, and rejects entries over 16 MiB;
these are tool limits, not format facts.

## Ulz compressed leaf

A Ulz blob is a 16-byte header followed by three streams.

| Offset | Width | Field |
|---|---:|---|
| `0x00` | 4 | ASCII `Ulz` + `0x1a` |
| `0x04` | 3 | Decompressed size in bytes |
| `0x07` | 1 | Version: `0` or `2` are supported; others are rejected |
| `0x08` | 3 | Offset of the literal stream, relative to the blob start |
| `0x0b` | 1 | Offset bit count `N`: only the measured 10–13 are accepted; length gets `16 - N` bits |
| `0x0c` | 4 | Offset of the pair stream, relative to the blob start |
| `0x10` | … | Flag words up to the literal offset, then literals, then pairs |

The reader requires `16 <= literal_offset <= pair_offset <= len(blob)`, a
flag region that is a whole number of 32-bit words, `N` in 10–13 (other counts are unvalidated and rejected), and a
decompressed size within the 16 MiB in-memory limit.

Decoding consumes flag words as little-endian 32-bit values from bit 31
downward. A one bit copies the next literal byte to the output. A zero bit
reads the next little-endian 16-bit pair: the low `N` bits plus one are the
back-reference distance and the remaining high bits plus three are the copy
length. Overlapping copies are permitted. Decoding ends exactly when the
output reaches the declared size.

Version 2 uses all 32 bits of every flag word. Version 0 treats the lowest set
bit of each word as a sentinel: only the bits above it are flags, and a word
that is zero carries no flags and is an error if output is still incomplete.
Once output reaches the declared size, version 0 also requires the next flag
word to be zero; a missing or nonzero terminator is an error. Version 2 ends
on size alone and may omit the trailing word.

Two further version 0 rules are deliberately strict because they are
unvalidated:

- A sentinel-only word (`0x80000000`, no flags) is rejected. It occurs in none
  of the four references. The game's routine reads it as a word with no flags
  and moves to the next word ([Ulz routine](ulz-routine.md)); the decoder
  still rejects it until that is validated on data.
- Flag bits left above the sentinel in the final word after the declared size
  must be zero. Japanese blobs never leave any. 423 (English Disc 1) and 361
  (English Disc 2) English-patch blobs leave 1–30 bits, always zero. Their
  spare pair bytes could not service those bits as references, so a decoder
  that ignored the declared size would overrun. The game's routine does
  ignore the size for version 0 and stops only at the zero word, so it is
  predicted to overrun on those blobs; this has not been run on the English
  images ([Ulz routine](ulz-routine.md)). Nonzero leftovers are rejected, and
  the zero-bit count is reported per blob as `final_word_unused_flag_bits`.
The psx-spx notes describe the same distinction as "31 flag bits with an
implied stop bit" for version 0 and "all 32 bits with a stop bit shifted in"
for version 2. Both readings were confirmed locally: the sentinel sits at bit 0
on every mid-stream word, and on the final word it sits either at bit 0
(English references) or directly below the last used flag (Japanese
references).

The decoder rejects an exhausted flag, literal, or pair stream, a distance
that reaches before the output start, and a copy that would exceed the
declared size. It does not truncate silently. The result reports exact
consumption: unused flag words (zero or one in every measured blob, and always
zero-valued), unused literal bytes (0–3, the padding to a four-byte boundary;
not always zero bytes), and unused pair bytes (0 or 2).

## Decoded payloads

With `--decode` the walker decompresses each Ulz leaf in memory, records the
SHA256 of the result, and classifies the decoded bytes with the same rules,
marking those nodes `origin: decoded`. Many decoded payloads are themselves
offset-table containers. No decoded payload is written to disk by the CLI or
the validation script; the reports contain sizes, hashes, offsets, and stream
accounting only.

## Known unknowns

- Four top-level entries (indices 1–4 on every image) are opaque to the
  walker: each starts with a small word followed by strings or tables, not an
  offset table. They are identical across all four images. **Decoded from
  code:** they are code overlays for `ACE3.BIN`. Its module loader reads
  entry *k* whole to `0x800CBFE8`, the end of `ACE3.BIN`'s BSS, and calls
  handlers inside it; see [overlays](overlays.md). Their leading words, 4, 5,
  6, and 7 on Japanese Disc 1, are not decoded. The walker still reports them
  as opaque, because it classifies data formats, not code.
- Four opaque leaves on Japanese Disc 1 contain the Ulz signature at a nonzero
  offset: `508.0.1`, `525.0.0`, `536.0.1`, and `543.0.0`. Their own header
  shapes are unknown, so the embedded data is not walked or decoded.
- Opaque leaves other than validated TIM images are not decoded. The most
  frequent remaining signature, `00002311`, is measured as an open hypothesis
  in the [TIM contract's unknowns](tim-format.md#the-00002311-leaves-open-hypothesis).
  No model, text, or mission format is claimed.
- The psx-spx notes mention a third `ULZ` variant with an uppercase ID, a
  different header, and a `+2` length bias (Time Crisis). It does not occur in
  these images and is not implemented.
- The game's routine answers both version 0 questions: it skips a
  `0x80000000` word, and it stops on the zero word without reading the size.
  On every version 0 blob on Japanese Disc 1 the zero word comes exactly at
  the declared size, so the decoder's rule of requiring both agrees with the
  routine there ([Ulz routine](ulz-routine.md)). The routine also has a
  version 1 path in 16-bit units, which no measured blob uses.
- Padding bytes in the literal stream are sometimes nonzero. Their origin is
  unknown and they are reported, not interpreted.

## Scope and provenance

The CLI `python -m swallow_tools.bpb IMAGE [--entry N] [--decode] [--json|--tree]`
reads BPH/BPB through ISO extents, rejecting overlapping extents exactly as the
BPH reader does, and never writes game bytes. The public
[psx-spx Ulz description](https://psx-spx.consoledev.net/cdromfileformats/)
was consulted after the bit layout had been derived from local samples, and
the two agree. [Krishty's archive research](https://www.zfx.info/viewtopic.php?p=53328)
describes nested containers in this archive. This module is original Python
project code; no third-party decompressor was incorporated.
