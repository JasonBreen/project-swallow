# BPB container and Ulz validation — 2026-10-15

Local host: Windows, Python 3.12. Checkout and all input, temporary, and
verification data are on `G:`. Original images were read without modification.
No decoded payload was written to disk; the checked-in reports hold metadata.

## Synthetic tests

The complete suite passes **86 tests**, including 33 new container and Ulz
tests. They cover hand-built flag/literal/pair vectors for both Ulz versions,
the version 0 sentinel positions and zero-word error, round trips through a
local greedy test encoder for both versions and 10–13 offset bits, every
header rejection, each stream-exhaustion case, early back-references, match
overrun, the size limit, offset-table rules and rejections, nested and decoded
classification, recorded (not raised) decode errors, depth and node limits,
summary counts, synthetic BPH/BPB image discovery, entry filtering, and CLI
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

For every reference the logical SHA256 of `ACE.BPH` and `ACE.BPB` matched the
[baseline](disc-inventory.md) before walking, every normal BPH entry was
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
- Unused flag words: exactly one zero word follows the used flags in every
  Japanese blob. On the English references 40 (Disc 1) and 28 (Disc 2) blobs
  have no trailing word; every unused word that exists is zero.
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

## Reproduce with local images

Metadata for one entry, decoded in memory:

```powershell
$env:PYTHONPATH = "src"
python -m swallow_tools.bpb "G:\ProjectSwallow-data\japanese-disc1\Ace Combat 3 - Electrosphere (Japan) (Disc 1).bin" --entry 5 --decode --tree
```

The stronger full-walk check verifies BPH/BPB hashes against the baselines,
walks every entry with decoding, writes complete trees to an ignored local
directory, and refreshes the compact summaries:

```powershell
python scripts/validate_bpb_references.py --output-dir local-reports --summary-dir docs/reports `
  --reference jp1 "G:\ProjectSwallow-data\japanese-disc1\Ace Combat 3 - Electrosphere (Japan) (Disc 1).bin" `
  --reference jp2 "G:\ProjectSwallow-data\japanese-disc2\Ace Combat 3 - Electrosphere (Japan) (Disc 2).bin"
```

Add `--reference en1 ...` and `--reference en2 ...` for the English pair. The
full-tree outputs are about 12 MB per image and stay in `local-reports/`.

## Practical limits

This establishes the nested container structure and a validated decoder for
the two Ulz versions present. Opaque leaves, the four text-like top-level
entries, TIM candidates, and every decoded container's contents remain
uninterpreted. SPH/SPB and XA media are separate work. No emulator boot,
playable mission, flight behavior, renderer, campaign transition, or English
dub has been validated by this change. See the
[container contract](bpb-format.md) for the exact rules and unknowns.
