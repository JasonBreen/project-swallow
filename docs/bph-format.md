# ACE.BPH index contract

Implemented in [`swallow_tools.bph`](../src/swallow_tools/bph.py), tooling v0.3.0.
Validated on 2026-10-12 against all four supplied images; see
[measurements and reproduction](bph-validation.md).

## Addressing and layout

`ACE.BPH` describes entries within the logical Form 1 contents of `ACE.BPB`.
The ISO directory determines each file's disc LBA and logical length. An entry's
sector word is relative to the beginning of BPB, not an absolute raw-disc LBA.
Raw 2,352-byte CD sectors and logical 2,048-byte Form 1 sectors are distinct.

All integers below are little-endian. The header occupies 20 bytes:

| Offset | Width | Field | Interpretation |
|---|---:|---|---|
| `0x00` | 4 | Magic | ASCII `AC3E` |
| `0x04` | 4 | Archive type | `3` for the supported BPH format |
| `0x08` | 2 | `unknown_08` | Preserved; observed `0x0427` on all four inputs |
| `0x0a` | 2 | `unknown_0a` | Preserved; observed zero on all four inputs |
| `0x0c` | 4 | `unknown_0c` | Preserved; observed `0x1484` on all four inputs |
| `0x10` | 4 | Entry count | Number of eight-byte records following the header |

The reader requires exactly `20 + count * 8` bytes. The observed count of 574
is evidence about these inputs, not a parser constant.

Each record preserves two raw 32-bit words: `descriptor` and `sector_word`.

| Descriptor bits | Supported interpretation |
|---|---|
| `0..18` | Normal payload length in four-byte units |
| `19..28` | Must be zero in this BPH reader; other layouts are unsupported |
| `29` | Required to be one for a supported normal record |
| `30` | Unknown flag; either value accepted and preserved |
| `31` | One marks a normal record; zero is an uninterpreted special record |

For normal records:

```text
offset_bytes = sector_word * 2048
size_bytes   = (descriptor & 0x7ffff) * 4
range        = [offset_bytes, offset_bytes + size_bytes)
```

All 2,296 records measured across the four images have high descriptor bits
`0xe0000000`. No special or zero-length records occur in those images.
Synthetic tests exercise both unknown-bit values, zero lengths, and special
records without establishing their game semantics.

## Payload length and alignment

The distance to the next offset is an allocated span, not the payload size.
English Disc 1's first descriptor is `0xe001977b`, its sector word is zero,
and the next record starts at sector 204. Its payload is 417,260 bytes;
the next offset is 417,792 bytes. The intervening 532 bytes are zero-filled.

For an entirely normal index, `gap_after_bytes` measures space to the next
entry or BPB EOF, and `leading_gap_bytes` measures space before the first.
Local validation reads every positive gap and confirms zero-filled padding in
these four inputs. The general parser does not require zero-filled gaps or
assume all future gaps are padding.

## Validation and uncertain records

`parse_bph(data, bpb_size)` returns frozen typed header, entry, and index records.
`inspect_bph(path)` discovers the root ISO files and produces the report used by
`python -m swallow_tools.bph IMAGE --json` or installed `swallow-bph`.

The reader rejects:

- A truncated header, incorrect magic/type, or count/length mismatch.
- An index larger than the explicit 1 MiB resource limit.
- Negative/noninteger BPB sizes, unsupported normal flags, out-of-bounds ranges,
  reversed normal offsets, or overlapping normal payloads.
- Missing or duplicate root archive files, unsupported multi-extent files,
  extents outside the image, or overlapping BPH/BPB ISO extents.
- Malformed raw sectors, a different sector mode, or Form 2 sectors encountered
  while reading the index. Form 2 requires a separate media reader.

Bit-31-clear records retain both raw words as `special_uninterpreted`; offset
and size remain null. They are not guessed to be empty assets or sentinels.
Such a report has `bounds_status: partial_special_records`, a warning, and null
gap values. Only normal ranges have been validated. Zero-length normal records
remain normal and produce a warning without a guessed sentinel meaning.

Header fields `unknown_08`, `unknown_0a`, `unknown_0c`, and descriptor bit 30
remain unknown in the API. Published date-like hypotheses are not promoted to
decoded semantics without executable/reference evidence. Raw values allow
byte-for-byte reconstruction of the entire index, including uncertain data.

## Scope and provenance

The ordinary CLI reads BPH and checks BPB's ISO extent and logical size; it does
not read every BPB payload sector. The optional reference-validation script
also hashes the entire BPB and checks its gaps. Neither operation verifies
CD EDC/ECC, nested asset formats, compression, rendering, gameplay, campaign
behavior, or emulator booting.

The public [psx-spx AC3 archive specification](https://psx-spx.consoledev.net/cdromfileformats/)
documents the header, four-byte BPH length units, 2,048-byte addressing, and
normal/special distinction. These format facts were checked against local bytes
and bounds. [Krishty's archive research](https://www.zfx.info/viewtopic.php?p=53328)
provides prior work on AC3's archive offsets and nested containers. This is
original Python project code; no third-party unpacker implementation was
incorporated. The [credits](../CREDITS.md) record those sources.
