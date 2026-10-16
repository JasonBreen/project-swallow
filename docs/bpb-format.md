# ACE.BPB nested container and Ulz contract

Implemented in [`swallow_tools.bpb`](../src/swallow_tools/bpb.py) and
[`swallow_tools.ulz`](../src/swallow_tools/ulz.py), tooling v0.4.0. Validated
on 2026-10-15 against all four supplied images; see
[measurements and reproduction](bpb-validation.md). This builds on the
[BPH index contract](bph-format.md): every range below is a normal BPH entry's
payload inside the logical Form 1 contents of `ACE.BPB`.

## Entry payload classification

The walker classifies a byte range by structure, in this order:

1. **Ulz leaf** — the range starts with ASCII `Ulz` followed by `0x1a`.
2. **Offset-table container** — the range satisfies every rule in the next
   section. This is the shape psx-spx calls `DOT1` (four-byte aligned, no end
   entry). Its items are classified recursively.
3. **Opaque leaf** — anything else. The report records only its size and the
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
at depth 8 and at 100,000 nodes per entry and rejects entries over 16 MiB;
these are tool limits, not format facts.

## Ulz compressed leaf

A Ulz blob is a 16-byte header followed by three streams.

| Offset | Width | Field |
|---|---:|---|
| `0x00` | 4 | ASCII `Ulz` + `0x1a` |
| `0x04` | 3 | Decompressed size in bytes |
| `0x07` | 1 | Version: `0` or `2` are supported; others are rejected |
| `0x08` | 3 | Offset of the literal stream, relative to the blob start |
| `0x0b` | 1 | Offset bit count `N` (observed 10–13); length gets `16 - N` bits |
| `0x0c` | 4 | Offset of the pair stream, relative to the blob start |
| `0x10` | … | Flag words up to the literal offset, then literals, then pairs |

The reader requires `16 <= literal_offset <= pair_offset <= len(blob)`, a
flag region that is a whole number of 32-bit words, `1 <= N <= 15`, and a
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

- Four top-level entries (indices 1–4 on every image) are opaque: each starts
  with a small count word followed by text-like fixed records, not an offset
  table. They are identical across all four images and remain uninterpreted.
- Opaque leaves are not decoded. Frequent signatures (for example the
  PlayStation TIM header word `0x00000010`) are recorded as candidates only.
  No image, model, text, or mission format is claimed by this change.
- The psx-spx notes mention a third `ULZ` variant with an uppercase ID, a
  different header, and a `+2` length bias (Time Crisis). It does not occur in
  these images and is not implemented.
- Whether the game treats a `0x80000000` version 0 word (sentinel only) as
  legal is unknown; the decoder accepts it as contributing no flags.
- Padding bytes in the literal stream are sometimes nonzero. Their origin is
  unknown and they are reported, not interpreted.

## Scope and provenance

The CLI `python -m swallow_tools.bpb IMAGE [--entry N] [--decode] [--json|--tree]`
reads BPH/BPB through ISO extents and never writes game bytes. The public
[psx-spx Ulz description](https://psx-spx.consoledev.net/cdromfileformats/)
was consulted after the bit layout had been derived from local samples, and
the two agree. [Krishty's archive research](https://www.zfx.info/viewtopic.php?p=53328)
describes nested containers in this archive. This module is original Python
project code; no third-party decompressor was incorporated.
