# ACE.BPB entry contract: DOT1 tables and Ulz blocks

Implemented in [`swallow_tools.ulz`](../src/swallow_tools/ulz.py) and
[`swallow_tools.bpb`](../src/swallow_tools/bpb.py), tooling v0.4.0.
Validated on 2026-10-13; see [measurements](bpb-validation.md).

Each normal [BPH](bph-format.md) record gives one entry range in the logical
Form 1 contents of `ACE.BPB`. This document covers what sits inside those
ranges. All integers are little-endian.

## Status of each claim

| Claim | Status |
|---|---|
| Ulz block layout and decoding | **Validated structurally.** All 2,256 blocks per Japanese disc decode to their declared size and consume every stream up to alignment padding. Byte-exact agreement with the game's own routine is **not yet proven** (see [next task](next-task-executable.md)). |
| DOT1 offset tables | **Structural acceptance only.** A table is accepted when every check below passes. What the game does with each slot is unknown. |
| `tim_header_candidate` / `tmd_header_candidate` | **Hints only.** First-word patterns, not decoded formats. |
| `unclassified` | **Unknown.** No rule matched. No meaning is assumed. |

## Ulz blocks

This matches the AC3/Klonoa "Ulz" variant described by
[psx-spx](https://psx-spx.consoledev.net/cdromfileformats/) ("CDROM File
Compression Ulz/ULZ (Namco)"). The decoder is original project code.

| Offset | Width | Field |
|---|---:|---|
| `0x00` | 4 | Magic `55 6c 7a 1a` (`Ulz` + `0x1a`) |
| `0x04` | 3 | Decompressed size |
| `0x07` | 1 | Version: 0 or 2 (others rejected) |
| `0x08` | 3 | Literal-stream offset (block-relative) |
| `0x0b` | 1 | Displacement bits N (measured 10–13; 1–15 accepted) |
| `0x0c` | 4 | Reference-stream offset (block-relative) |
| `0x10` | … | 32-bit flag words, then literal bytes, then 16-bit references |

Decoding:
- Flags are consumed from bit 31 down.
- A set flag copies one literal byte.
- A clear flag reads one 16-bit reference: length = `(value >> N) + 3`, distance = `(value & (2^N - 1)) + 1`. The bytes are copied one at a time, so overlapping references repeat recent output.
- **Version 2** uses all 32 bits of each flag word and stops when the declared size is reached.
- **Version 0** treats the lowest set bit of each word as a stop bit and ends at an all-zero flag word. Its output must then equal the declared size.

The decoder rejects any of the following:
- Bad magic, an unsupported version or N, or literal offset 0. psx-spx says version 0 may store 0 there, but no measured example exists.
- Stream offsets out of order (`16 <= literal <= reference <= block end`).
- A read past any stream.
- A distance before the output start.
- Output above or below the declared size, or above the 16 MiB limit.

### Measured stream accounting (both Japanese discs)

| Observation | Count per disc |
|---|---:|
| Version 0 blocks; flag stream ends exactly at the literal offset | 1,167 |
| Version 2 blocks; exactly one trailing all-zero flag word (4 bytes) | 1,089 |
| Literal stream ends 0 / 1 / 2 / 3 bytes before the reference offset | 627 / 496 / 543 / 590 |
| Reference stream ends 0 / 2 bytes before the block end | 1,035 / 1,221 |
| Blocks whose alignment padding contains nonzero bytes | 778 |

The padding is consistent with 4-byte alignment of each stream. It is **not**
always zero-filled, and its content is preserved but uninterpreted. The trailing
zero word in version 2 is measured, not required by the decoder.

The English-labeled references use a different encoder:

- Some v2 blocks lack the trailing zero word.
- Some v0 blocks leave clear flags after the declared size is reached, with no matching reference words.

The decoder counts those flags as `unused_reference_flags` rather than reading
past the stream. A set flag after the declared size is still rejected. Counts
and the open question about the game's own behavior are in the
[validation](bpb-validation.md).

The walker decodes a block only where a node *starts* with the magic. Four
`Ulz` signatures per Japanese disc sit at nonzero offsets inside unclassified
leaves (BPH entries 508, 525, 536, and 543). Those leaves have unknown header
shapes, for example `01000000 01010001 14000000 …`. They are not walked.

## DOT1 offset tables

psx-spx describes DOT1 as a count followed by increasing offsets. Its AC3 notes
say BPB "contains DOT1 child archives, and Ulz compressed files". The walker
accepts a table only if **all** of these hold:

1. `1 <= count <= 65536`, and the table fits in the data.
2. The first offset is **exactly** `4 + 4*count`: no size word and no padding.
3. Offsets are 4-byte aligned, nondecreasing, and within the data.

Offsets are relative to the table's own start. Child `i` spans
`[offset[i], offset[i+1])`, and the last child ends where the parent ends.
Equal offsets produce `empty` slots. These are common in fixed 16- and 128-slot
tables: 7,511 per Japanese disc. Tables are walked recursively, including inside
decoded Ulz payloads, up to depth 12. The measured maximum is 5.

### Counterexamples (rejected, kept unclassified)

| BPH entry | First bytes | Why it is rejected |
|---|---|---|
| 1 | `04000000 "LOG OUT\0easy\0…normal…hard…" 90180d80 …` | Text where the offsets would be |
| 2 | `05000000 "CPU  %3d%% %3d\n\0GPU …MEM  %8X…"` | Debug format strings where the offsets would be |
| 3 | `06000000 00000000 …` | All-zero offsets |
| 4 | `07000000 "BGOK02\0\0BGOK01\0\0BGTT01…"` | Label text where the offsets would be |

These four top-level entries plus 43,745 nested leaves stay `unclassified`.

**Hypothesis, unverified:** entries 1–4 may be code or data overlays. Each
has a small leading count, then strings and words such as `0x800d1890` that
look like PS1 RAM addresses. The debug strings in entry 2 point the same
way. This is the first place to check during the executable work. Neither
the count's meaning nor any load address has been decoded.
A single-slot table (`01000000 08000000 …`) is structurally valid but weak
evidence. psx-spx warns that it can be confused with other data. The walker
accepts it, so a nested single-slot table remains a hypothesis until code or
emulator evidence confirms it.

## Report

`python -m swallow_tools.bpb IMAGE --json` emits one tree per BPH entry with
these node fields: `kind`, `offset`, `size`, `sha256`, Ulz stream metadata,
hints, and children. Offsets are relative to the parent's bytes; a Ulz node's
single child is its decoded payload at offset 0. Each tree is rooted at offset
0, and the entry's BPB position is reported separately as `offset_bytes`, so
`tree_sha256` compares content across discs whose layouts differ. `--no-trees` keeps per-entry `tree_sha256` digests and
counts. No asset bytes are written. Totals such as `unclassified_bytes` include
bytes inside decoded payloads, so they can exceed the BPB size.
