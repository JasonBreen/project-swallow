# BPH validation — 2026-10-12

Local host: Windows, Python 3.12.10. Checkout and all input, temporary, and
verification data are on `G:`. Original images were read without modification.

## Synthetic tests

The complete suite passes **53 tests**, including 25 new BPH and logical-read
tests. Tests cover explicit sizes versus allocated spans, byte reconstruction,
unknown flags/fields, special records, zero lengths, malformed/truncated input,
unsupported flags, reversed/overlapping/out-of-bounds entries, relocated ISO
extents, multi-sector indices, duplicate/missing archive files, invalid sector
modes, Form 2 rejection, bounded cross-sector reads, and CLI success/errors.

```powershell
$env:PYTHONPATH = "src"
$env:TEMP = "G:\ProjectSwallow-data\tmp"
$env:TMP = $env:TEMP
python -m unittest discover -s tests -v
```

CI runs synthetic tests on Windows and Linux, Python 3.11–3.14, without fetching
LFS objects. A local result and a hosted CI result are separate evidence.

## Four-disc validation

| Reference | Records | BPH bytes | BPB bytes | Normal payload bytes | Gap bytes | Report |
|---|---:|---:|---:|---:|---:|---|
| Japanese Disc 1 | 574 | 4,612 | 94,976,000 | 94,441,168 | 534,832 | [JSON](reports/bph-jp1.json) |
| Japanese Disc 2 | 574 | 4,612 | 94,976,000 | 94,441,168 | 534,832 | [JSON](reports/bph-jp2.json) |
| English-labeled Disc 1 | 574 | 4,612 | 94,062,592 | 93,513,048 | 549,544 | [JSON](reports/bph-en1.json) |
| English-labeled Disc 2 | 574 | 4,612 | 94,212,096 | 93,664,108 | 547,988 | [JSON](reports/bph-en2.json) |

For every reference:

- Whole-image MD5, SHA1, and SHA256 were recomputed and match the corresponding
  [baseline](disc-inventory.md). The new Japanese archives reproduce the earlier
  Japanese BIN baseline.
- The actual JSON CLI completed successfully. Every index byte can be rebuilt
  from the parsed header and raw record words; reconstruction equals the input.
- All 574 records are normal, nonempty, ordered, nonoverlapping, and within the
  associated BPB. No uninterpreted special records or parser warnings occur.
- All 4,612 index bytes are accounted for: `20 + 574 * 8`.
- The entire BPB was read as Form 1 and its logical SHA256 matches the baseline.
- Leading gaps are zero; entry gaps range from zero to 2,044 bytes. All positive
  gaps contain only zero bytes: 572 on each Japanese disc and 573 on each English
  reference. Payload sizes plus gaps exactly account for each BPB.

Japanese BPH records are identical across both discs. The English references
contain changed payload sizes/offsets and are handled by the same parser.
Disc-specific LBAs, entry counts, and archive lengths are not parser constants.

## Reproduce with local images

Run the metadata CLI with `python -m swallow_tools.bph IMAGE --json`.
To repeat the stronger full-image/BPB checks and write metadata to an ignored
local directory, supply the baseline key and the corresponding BIN:

```powershell
$env:PYTHONPATH = "src"
$env:TEMP = "G:\ProjectSwallow-data\tmp"
$env:TMP = $env:TEMP
python scripts/validate_bph_references.py --output-dir local-reports `
  --reference jp1 "G:\ProjectSwallow-data\japanese-disc1\Ace Combat 3 - Electrosphere (Japan) (Disc 1).bin" `
  --reference jp2 "G:\ProjectSwallow-data\japanese-disc2\Ace Combat 3 - Electrosphere (Japan) (Disc 2).bin"
```

Add `--reference en1 "G:\path\to\english-disc1.bin"` and the corresponding
`en2` argument for the English pair. Keys identify expected baseline hashes;
filenames alone do not establish identity. The script stops on a hash or
reconstruction mismatch and writes no extracted assets.

## Practical limits

This establishes a validated BPH index and known BPB entry boundaries. Nested
resource semantics and decompression remain ahead. SPH/SPB and XA media are
separate work. No emulator boot, playable mission, flight behavior, renderer,
campaign transition, or English dub has been validated by this change. See the
[format contract](bph-format.md) for unknown fields and special-record policy.
