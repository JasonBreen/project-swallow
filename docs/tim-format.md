# TIM image leaf contract and the open `00002311` hypothesis

Implemented in [`swallow_tools.tim`](../src/swallow_tools/tim.py) and wired
into the [BPB walker](bpb-format.md), tooling v0.5.0. Validated on 2026-10-16
against all four supplied images; see [measurements](bpb-validation.md).
The validator returns metadata only: it never converts, copies, or writes
pixel or palette data, and nothing in this change exports a game asset.

## Why TIM

The v0.4.0 walk left the PlayStation TIM id word `10000000` as the second
most frequent opaque-leaf signature in decoded data. TIM is the standard
Sony texture-image container for the PlayStation, documented publicly in the
psx-spx section "CDROM File Video Texture Image TIM/PXL/CLT". The rules
below were written from that public layout and then checked against every
candidate leaf in all four images; every rule has a measured count.

## Layout

All integers are little-endian. A TIM is an 8-byte file header, an optional
CLUT block, and one pixel block. Both blocks share one 12-byte block header.

| Offset | Width | Field | Validator rule |
|---|---:|---|---|
| `0x00` | 4 | Id word | Exactly `0x00000010`: id `0x10`, version `0`, reserved `0` |
| `0x04` | 4 | Flags | Bits 0–2 `pmode`, bit 3 CLUT present; any higher bit set is rejected |
| `0x08` | … | CLUT block | Present exactly when bit 3 is set; only with `pmode` 0 or 1 |
| … | … | Pixel block | Always present; follows the CLUT block |

`pmode` 0 is 4-bit indexed, 1 is 8-bit indexed, 2 is 16-bit direct, 3 is
24-bit direct. `pmode` 4 ("mixed") is rejected as unvalidated; it does not
occur in these images and neither does 16-bit direct colour.

Block header, for the CLUT block and the pixel block alike:

| Offset | Width | Field | Validator rule |
|---|---:|---|---|
| `+0x00` | 4 | Block length `bnum` | Must equal `12 + 2 * w * h` and fit inside the data |
| `+0x04` | 2 | VRAM `x` (halfwords) | `x + w <= 1024` |
| `+0x06` | 2 | VRAM `y` (lines) | `y + h <= 512` |
| `+0x08` | 2 | `w` (halfwords) | `w >= 1` |
| `+0x0a` | 2 | `h` (lines) | `h >= 1` |
| `+0x0c` | `2*w*h` | Entries or pixels | Not read |

For the CLUT block `w * h` is the number of 16-bit palette entries. For the
pixel block `w` counts 16-bit VRAM halfwords, so the reported pixel width is
`4w` for 4bpp, `2w` for 8bpp, `w` for 16bpp, and `2w / 3` for 24bpp (`2w`
must then be divisible by 3).

The blocks must end exactly at the end of the range. The walker alone passes
`max_trailing_bytes=3`: BPB containers align every item except the last to
four bytes, so an image whose size is 2 mod 4 is followed by two zero bytes
inside its range. Those bytes must be zero and are reported as
`trailing_bytes`. Every other caller gets the strict rule.

## Report fields

A `tim` node carries `size`, `flags`, `pmode`, `mode`, `bits_per_pixel`,
`pixel_width`, `pixel_height`, `trailing_bytes`, a `pixels` block
(`offset`, `length`, `x`, `y`, `width`, `height`), and, when present, a
`clut` block of the same shape plus `clut_entries`. Offsets are relative to
the leaf. No pixel, palette, hash, or sample of the data is included.

## Measured on the four references

Candidate means a leaf that starts with the id word. Counts are per image.

| Reference | Candidates | Validated `tim` | Of which padded | Rejected |
|---|---:|---:|---:|---:|
| Japanese Disc 1 | 18,282 | 18,267 (138 stored, 18,129 decoded) | 131 | 15 |
| Japanese Disc 2 | 18,282 | 18,267 (138 stored, 18,129 decoded) | 131 | 15 |
| English-labeled Disc 1 | 18,280 | 18,265 (144 stored, 18,121 decoded) | 131 | 15 |
| English-labeled Disc 2 | 18,280 | 18,265 (138 stored, 18,127 decoded) | 131 | 15 |

Field distributions, identical in shape on all four images (Japanese counts;
the English references differ by two 8bpp images):

- Flags are `8` (4bpp with CLUT, 17,934 including the 131 padded images),
  `9` (8bpp with CLUT, 303), `0` (4bpp without CLUT, 22), and `3` (24bpp
  direct, 8). No other flag value occurs, so bits above bit 3 are never set
  and no image uses `pmode` 2 or 4. Validated TIM data totals 44,104,550
  bytes per Japanese image.
- CLUT blocks are `16x1` in 16,954 images, with multi-row 16-wide palettes
  (`16x2` … `16x16`), `256x1` for every 8bpp image, and five other shapes
  (`32x1`, `48x1`, `64x1`, `64x4`, `16x12`), one image each.
- 14 CLUT blocks sit at VRAM `x = 511`, which is not a multiple of 16. The
  Sony convention places palettes on 16-halfword boundaries; the validator
  does not enforce it and reports the count as `clut_x_not_16_aligned`.
- Pixel blocks are placed across the full 1024x512 halfword VRAM: 13,414
  start at `x >= 640` and 3,321 at `y >= 256` (Japanese; 13,412 and 3,319
  on the English references).
- The eight 24bpp images are 256 pixels wide (384 halfwords) at `x = 576`,
  `y = 0`, 60 to 400 lines tall (46,100 to 307,220 bytes), and are stored
  (uncompressed) first items of eight different top-level containers.
- The 131 padded images are 4bpp with a 16-entry CLUT and a pixel block
  whose halfword count `w * h` is odd (for example 3x17 or 1x5), so the
  image ends 2 mod 4 and the container follows it with two zero bytes.
- Stored (uncompressed) TIM leaves sit at depth 1 or 2; all other images
  are leaves of decoded Ulz payloads.

## Rejected candidates (counterexamples)

Fifteen candidates per image fail validation. They stay `opaque` with the
reason recorded on the node, and nothing is claimed about them:

| Count | Shape | What fails |
|---:|---|---|
| 11 | 4bpp, 16-entry CLUT; leaf sizes 192, 372, 576 | The pixel block length word is 4, 6, or 8 bytes short of `12 + 2wh` while the leaf size still equals `8 + 44 + 12 + 2wh` |
| 2 | 4bpp, 2,080 bytes | A `16x64` CLUT (1,024 entries) followed by a pixel block header with `w = h = 0` and length 12 |
| 2 | 8bpp, 5,248 bytes | A `256x1` CLUT at `x = 960`, which would extend to halfword 1216, past the 1024-halfword VRAM width |

Hypotheses, not decoded: the first group may be hand-edited length words the
game ignores in favour of `w * h`; the second may be palette-only images;
the third may rely on GPU coordinate wrapping. Each would need a runtime
observation before the validator accepts it.

## The `00002311` leaves (open hypothesis)

The most frequent opaque signature in decoded data remains `00002311`
(19,814 leaves per image, all of origin `decoded`, none stored). The
validation script measures their header words without interpreting them;
the counts for each image are in the [validation record](bpb-validation.md)
and the `signature_00002311` block of each summary report.

Measured on every one of the 19,814 leaves per image:

- The first four bytes are `00 00 23 11` (as a little-endian word
  `0x11230000`; as two halfwords `0x0000`, `0x1123`).
- Word 1 is always `24` and word 2 is always `0`.
- Word 3 is always `68 + 20n` for some `n >= 0`, and is always less than
  word 4.
- Word 4 is always a multiple of 4 and less than the leaf size; the leaf
  size minus word 4 is always `4 + 60m` for some `m >= 0`.
- Leaf sizes are multiples of 4, from 124 to the per-image maximum recorded
  in the report; the most common sizes are 124 (2,327 leaves) and 336
  (1,779). The final word of every leaf is zero.
- Every such leaf lives in one of 57 decoded containers per image, and each
  of those containers holds only `00002311` leaves.

These facts are consistent with a 24-byte header followed by three regions
delimited by words 3 and 4, with 20-byte and 60-byte strides, but no
region's contents, units, or purpose have been validated. The signature is
not a model, mesh, animation, or text format until a rule about its
contents has been checked against the images and against runtime behaviour.

## Scope and provenance

`python -m swallow_tools.bpb IMAGE --decode --tree` prints each `tim` leaf's
mode, dimensions, and CLUT entry count. The public psx-spx TIM description
was used for the field layout; every rule was then measured on the images.
This module is original Python project code and incorporates no third-party
image library or converter. No pixel data is written by any command.
