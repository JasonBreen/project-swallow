# BPB / Ulz validation — 2026-10-13

Local host: Windows, Python 3.12. The checkout, the extracted references, and
all temporary and report data are on `G:`. Original images were read without
modification.

## Synthetic tests

The complete suite passes **76 tests**, 23 more than before. The new Ulz tests
use hand-built blocks with known expected output, with no test-side encoder:

- **Decoding:** literals, overlapping and non-overlapping references, and the version 0 stop bit / zero-word end.
- **Version 0 trailing reference flags:** counted, not executed.
- **Flag-word boundaries:** each measured displacement width (10–13).
- **Rejections:** 15 malformed variants, plus the output limit.

The new BPB tests cover:

- **DOT1 checks:** exact acceptance, and rejection of the measured counterexample shapes and malformed tables.
- **Walking:** nested tables, empty slots, Ulz children, compressed tables walked after decoding, reported nonzero padding, the nesting limit, and the CLI's success and error paths on a synthetic disc.

```powershell
$env:PYTHONPATH = "src"
$env:TEMP = "G:\ProjectSwallow-data\tmp"
$env:TMP = $env:TEMP
python -m unittest discover -s tests -v
```

This is a local result. GitHub Actions results are separate evidence.

## Four-disc validation

`scripts/validate_bpb_references.py` checks each reference image as follows:

1. Recomputes the whole-image MD5/SHA1/SHA256 and matches the [baseline](disc-inventory.md).
2. Walks all 574 BPH entries in process and through the CLI (`--no-trees`), and checks that every per-entry tree digest agrees.
3. Requires every Ulz block to consume all three streams up to alignment padding only: a flag pad of 0 or 4 bytes, and literal and reference pads of at most 3.
4. Requires every Ulz block that appears on more than one reference to decode to the same bytes everywhere.

| Reference | Ulz blocks (v0 / v2) | Decoded bytes | DOT1 tables | Empty slots | Unclassified leaves | Report |
|---|---:|---:|---:|---:|---:|---|
| Japanese Disc 1 | 2,256 (1,167 / 1,089) | 72,108,154 | 2,413 | 7,511 | 43,749 | [JSON](reports/bpb-jp1.json) |
| Japanese Disc 2 | 2,256 (1,167 / 1,089) | 72,108,154 | 2,413 | 7,511 | 43,749 | [JSON](reports/bpb-jp2.json) |
| English-labeled Disc 1 | 2,250 (1,155 / 1,095) | 71,900,466 | 2,413 | 7,511 | 43,747 | [JSON](reports/bpb-en1.json) |
| English-labeled Disc 2 | 2,256 (1,166 / 1,090) | 72,097,650 | 2,413 | 7,511 | 43,747 | [JSON](reports/bpb-en2.json) |

All four references pass every check. The maximum nesting depth is 5.

- **Content comparison:** the Japanese Disc 1 and Disc 2 walks are identical for all 574 entries. Japanese vs English: Disc 1 matches on 334/574 entry trees and Disc 2 on 391/574. English Disc 1 vs English Disc 2: 325/574.
- **Decode consistency:** 2,329 distinct Ulz blocks occur across the four references. Each decodes identically wherever it appears.

## Encoder differences on the English-labeled references

The English images contain Ulz blocks that were clearly produced by a different encoder:

| Observation | JP1 / JP2 | EN1 | EN2 |
|---|---:|---:|---:|
| v0 blocks with clear flags left over after the declared size is reached | 0 | 423 | 361 |
| Total unused reference flags | 0 | 6,328 | 5,285 |
| v2 blocks without the trailing all-zero flag word | 0 | 40 | 28 |

The leftover v0 flags have no matching reference words; the literal and
reference streams are fully consumed. Following psx-spx's version 0 description
literally, a decoder would read past the reference stream and past the output
size, as happened in the first English run of this validation. The project
decoder counts these flags (`unused_reference_flags`) and does not execute them.
**Whether the game's routine stops at the declared size, or tolerates a
harmless overrun, is unverified.** It is an explicit check for the
[executable track](next-task-executable.md).

## Reproduce

Extract the four archives into separate directories on `G:` (see [discs/README.md](../discs/README.md); with GNU tar, pass `--force-local` or POSIX `/g/...` paths so `G:` is not read as a remote host). Then run:

```powershell
$env:PYTHONPATH = "src"
$env:TEMP = "G:\ProjectSwallow-data\tmp"
$env:TMP = $env:TEMP
$d = "G:\ProjectSwallow-data"
python scripts/validate_bpb_references.py --output-dir local-reports `
  --reference jp1 "$d\japanese-disc1\Ace Combat 3 - Electrosphere (Japan) (Disc 1).bin" `
  --reference jp2 "$d\japanese-disc2\Ace Combat 3 - Electrosphere (Japan) (Disc 2).bin" `
  --reference en1 "$d\english-disc1\Ace Combat 3 Electrosphere (English v0.9b3)[Disc 1].bin" `
  --reference en2 "$d\english-disc2\Ace Combat 3 Electrosphere (English v0.9b3)[Disc 2].bin"
```

A run takes under a minute per disc, including whole-image hashing. The output is metadata only.

## Practical limits

These checks prove internal consistency and complete stream accounting, not
agreement with the game's own decompression routine. DOT1 acceptance is
structural. Leaf formats, possible overlays, the four embedded Ulz blocks in
unknown containers, SPH/SPB/XA media, and every gameplay behavior remain
undecoded. No emulator boot was performed.
