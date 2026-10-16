# BPB container, Ulz, and TIM validation — 2026-10-15 / 2026-10-16

Local host: Windows, Python 3.12, both dates. Checkout and all input, temporary, and
verification data are on `G:`. Original images were read without modification.
No decoded payload was written to disk; the checked-in reports hold metadata.

## Synthetic tests

The complete suite passes **110 tests**: 95 from v0.4.0 plus 15 TIM
validator and walker tests added in v0.5.0 (header, flag, CLUT, pixel-block,
VRAM, length, and trailing-byte rules; walker classification of valid,
rejected, padded, and decoded TIM leaves; summary and CLI output). The
v0.4.0 tests cover hand-built flag/literal/pair vectors for both Ulz versions,
the version 0 sentinel positions, zero-word error, required terminator,
sentinel-only word rejection, and final-word leftover flags, round trips through a
local greedy test encoder for both versions and 10–13 offset bits, every
header rejection, each stream-exhaustion case, early back-references, match
overrun, the size limit, offset-table rules and rejections, nested and decoded
classification, recorded (not raised) decode errors, depth and node limits,
summary counts, empty items charged to the node limit, rejection of
overlapping BPH/BPB extents, synthetic BPH/BPB image discovery, entry filtering, and CLI
success/error paths.

```powershell
$env:PYTHONPATH = "src"
$env:TEMP = "G:\ProjectSwallow-data\tmp"
$env:TMP = $env:TEMP
python -m unittest discover -s tests -v
```

A local result is not a hosted CI result; the GitHub Actions matrix reports
separately for the commit that introduces this change.

## Four-disc validation

For every reference the whole-image MD5/SHA1/SHA256 and the logical SHA256 of
`ACE.BPH` and `ACE.BPB` matched the [baseline](disc-inventory.md) before
walking, every normal BPH entry was
walked with decoding, and no Ulz blob produced an error.

| Reference | Top-level containers | Opaque top-level | Stored nodes | Ulz blobs (v0 / v2) | Compressed bytes | Decompressed bytes | Report |
|---|---:|---:|---:|---:|---:|---:|---|
| Japanese Disc 1 | 570 | 4 | 5,540 | 2,256 (1,167 / 1,089) | 35,147,148 | 72,108,154 | [JSON](reports/bpb-jp1.json) |
| Japanese Disc 2 | 570 | 4 | 5,540 | 2,256 (1,167 / 1,089) | 35,147,148 | 72,108,154 | [JSON](reports/bpb-jp2.json) |
| English-labeled Disc 1 | 570 | 4 | 5,540 | 2,250 (1,155 / 1,095) | 34,021,844 | 71,900,466 | [JSON](reports/bpb-en1.json) |
| English-labeled Disc 2 | 570 | 4 | 5,540 | 2,256 (1,166 / 1,090) | 34,370,088 | 72,097,650 | [JSON](reports/bpb-en2.json) |

Measured on all four images:

- The four opaque top-level entries are indices 1–4 on every image. Every
  other entry is an offset-table container; all table offsets are four-byte
  aligned and ordered. Stored nesting reaches depth 3; with decoded payloads
  the deepest node is at depth 5.
- Offset bit counts are 10, 11, 12, or 13 only. Lengths therefore use 6, 5, 4,
  or 3 bits, giving maximum copy lengths of 66, 34, 18, and 10 bytes.
- Every blob reached its declared size exactly; no stream was exhausted and no
  copy overran. Across the Japanese pair the decoder processed 17,578,292
  literals and 7,197,959 matches per disc.
- No version 0 blob contains a sentinel-only `0x80000000` flag word. Final
  words leave unused zero flag bits in 0 Japanese, 423 English Disc 1, and 361
  English Disc 2 version 0 blobs; none leaves a nonzero bit.
- Unused flag words: exactly one zero word follows the used flags in every
  Japanese blob and in every version 0 blob on all four images, which the
  decoder now requires. On the English references 40 (Disc 1) and 28 (Disc 2)
  version 2 blobs have no trailing word; every unused word that exists is zero.
- Unused literal bytes are 0–3 (padding to four bytes) and unused pair bytes
  are 0 or 2 in every blob. Literal padding is not always zero-valued.
- Of the decoded payloads, 1,069 per image are themselves offset-table
  containers and the rest are opaque. The most frequent opaque signatures in
  decoded data are `00002311` (19,814 leaves) and the PlayStation TIM header
  word `10000000` (about 18,140 leaves). These are candidates for the next
  classification step, not decoded formats.
- The Japanese discs produce byte-identical reports, as their BPB contents
  are identical. The English references change a small number of Ulz blobs
  and leaf sizes while keeping the same container structure and entry count.

## TIM leaf validation — 2026-10-16

Tooling v0.5.0 re-ran the full walk with the TIM validator on all four
images. Whole-image and BPH/BPB hashes matched the baselines, every Ulz blob
decoded with zero errors, and the container and Ulz counts above were
unchanged. Rules and field distributions are in the
[TIM contract](tim-format.md).

| Reference | TIM candidates | Validated `tim` leaves | Stored / decoded | Padded | Rejected | TIM bytes |
|---|---:|---:|---:|---:|---:|---:|
| Japanese Disc 1 | 18,282 | 18,267 | 138 / 18,129 | 131 | 15 | 44,104,550 |
| Japanese Disc 2 | 18,282 | 18,267 | 138 / 18,129 | 131 | 15 | 44,104,550 |
| English-labeled Disc 1 | 18,280 | 18,265 | 144 / 18,121 | 131 | 15 | 44,094,054 |
| English-labeled Disc 2 | 18,280 | 18,265 | 138 / 18,127 | 131 | 15 | 44,094,054 |

- Modes per Japanese image: 17,956 4bpp, 303 8bpp, 8 24bpp; the English
  references have 301 8bpp images and are otherwise identical in every
  distribution. No 16bpp or mixed-mode image exists.
- The 15 rejected candidates per image are the same on every image: 11
  pixel-block length words short of `12 + 2wh`, 2 pixel blocks with zero
  width and height after a 1,024-entry CLUT, and 2 CLUT blocks at `x = 960`
  that would extend past the 1,024-halfword VRAM width. All are decoded
  leaves; no stored candidate is rejected.
- The 131 padded images are followed by exactly two zero bytes on every
  image. 14 CLUT blocks per image sit at `x = 511`.
- With TIM classified, the remaining opaque leaves per Japanese image are
  1,810 stored and 23,672 decoded. The most frequent decoded signatures are
  now `00002311` (19,814), `00000109` (700), and `00000000` (167); the most
  frequent stored signatures are `00000000` (799) and `01000000` (101).

## The `00002311` leaves — measured hypothesis

The validation script makes a second in-memory pass over every leaf whose
first four bytes are `00 00 23 11` and tests the header-word rules recorded
in the [TIM contract's unknowns](tim-format.md#the-00002311-leaves-open-hypothesis).
On all four images every one of the 19,814 leaves satisfies every rule:
word 1 is 24, word 2 is 0, word 3 is `68 + 20n`, word 3 is below word 4,
word 4 is a multiple of 4 below the leaf size, the size minus word 4 is
`4 + 60m`, the size is a multiple of 4, and the last word is zero. Sizes run
from 124 to 2,524 bytes; `n` is 0 in 4,607 leaves and `m` is 0 in 5,545
leaves. All 19,814 leaves are decoded, spread over 57 containers that hold
nothing else. No semantics are claimed; the counts are in the
`signature_00002311` block of each summary report.

## Reproduce with local images

Metadata for one entry, decoded in memory:

```powershell
$env:PYTHONPATH = "src"
python -m swallow_tools.bpb "G:\ProjectSwallow-data\japanese-disc1\Ace Combat 3 - Electrosphere (Japan) (Disc 1).bin" --entry 5 --decode --tree
```

The stronger full-walk check verifies whole-image and BPH/BPB hashes against
the baselines, so a disc supplied under the wrong key is rejected, walks every
entry with decoding, fails if any Ulz blob is rejected, writes complete trees to an ignored local
directory, and refreshes the compact summaries:

```powershell
python scripts/validate_bpb_references.py --output-dir local-reports --summary-dir docs/reports `
  --reference jp1 "G:\ProjectSwallow-data\japanese-disc1\Ace Combat 3 - Electrosphere (Japan) (Disc 1).bin" `
  --reference jp2 "G:\ProjectSwallow-data\japanese-disc2\Ace Combat 3 - Electrosphere (Japan) (Disc 2).bin"
```

Add `--reference en1 ...` and `--reference en2 ...` for the English pair. The
full-tree outputs are about 24 MB per image with TIM metadata and stay in
`local-reports/`.

## Practical limits

This establishes the nested container structure, a validated decoder for
the two Ulz versions present, and metadata-only validation of TIM image
leaves. The `00002311` leaves, the other opaque leaves, the four text-like
top-level entries, and the 15 rejected TIM candidates per image remain
uninterpreted; no pixel is decoded or exported. SPH/SPB and XA media are
separate work. No emulator boot, playable mission, flight behavior,
renderer, campaign transition, or English dub has been validated by this
change. See the
[container contract](bpb-format.md) for the exact rules and unknowns.
