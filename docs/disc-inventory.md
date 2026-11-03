# Project Swallow: four-image baseline

Verified locally on 2026-10-12. This report contains metadata and hashes, not game data.

Two additional owner-supplied references were inventoried on 2026-10-21:
the original PlayStation Ace Combat and a PlayStation 2 Ace Combat Zero ISO.
See [supplementary references](supplementary-discs.md) and
their [separate manifest](../discs/supplementary-manifest.json).
The four AC3 references below retain their existing baseline identities.

## Evidence and scope

All four supplied archives were read through system libarchive and their regular BIN/CUE members staged locally. The readme HTML members in the Japanese archives were not executed or included in the source package. Whole-image hashes below were calculated from the extracted BIN bytes, not the compressed archives. All four raw images were inspected using the packaged CLI.

Both Japanese BIN MD5 values match the Load Word Team's published **Revision 1** entries. This uses that team's revision terminology, not a fresh Redump database certification. Its page also lists the same Disc 2 MD5 for the Asian release, so Disc 2's hash alone does not uniquely establish retail packaging/region.

The English images are labeled **v0.9b3** by their uploaded filenames. Their bytes are now fingerprinted; the translation author and exact release identity have not been independently authenticated. These uploads must not be conflated with a different translation release.

Reference: https://loadwordteam.com/projects/ace-combat-3-electrosphere-a-complete-fan-translation/

## Whole-image fingerprints

| Baseline | BIN bytes | Raw sectors | Volume | MD5 |
|---|---:|---:|---|---|
| Japanese Disc 1 | 716,287,488 | 304,544 | `ACE3ES1` | `30f7dce98b6901290cb26c9baf27268f` |
| Japanese Disc 2 | 723,813,888 | 307,744 | `ACE3ES2` | `50760963ae8a26ea0188b165f05b33a1` |
| English-labeled Disc 1 | 715,238,496 | 304,098 | `ACE3ES1` | `deca6b0cf45d4358a3ec1a13817df04a` |
| English-labeled Disc 2 | 722,936,592 | 307,371 | `ACE3ES2` | `0193416e6167c50b2b59f816703cb9ac` |

SHA1 and SHA256 are included in each [machine-readable report](reports/).

## Japanese Disc 2 directory

| File | ISO-recorded bytes | Start LBA | Logical-file hashing |
|---|---:|---:|---|
| `ACE.BPB` | 94,976,000 | 611 | `verified_form1` |
| `ACE.BPH` | 4,612 | 606 | `verified_form1` |
| `ACE.SPB` | 533,725,184 | 46,986 | `unsupported_form2` |
| `ACE.SPH` | 3,916 | 609 | `verified_form1` |
| `ACE3.BIN` | 710,656 | 259 | `verified_form1` |
| `SLPS_020.21` | 481,280 | 24 | `verified_form1` |
| `SYSTEM.CNF` | 67 | 23 | `verified_form1` |

The ISO size for `ACE.SPB` is a directory-record measurement, not a claim about decoded audio/video byte counts.

## Verified comparisons

Across the two Japanese originals, `ACE.BPB`, `ACE.BPH`, and `ACE3.BIN` have matching SHA256 hashes of their logical Form 1 contents. The boot executables (`SLPS_020.20` and `SLPS_020.21`) also match despite their different filenames; their SHA256 is:

`daddca3ac3299372370a8cf7a046eb066537a39cf53216904009c46f2924b0e5`

`ACE.SPH` and `SYSTEM.CNF` differ between the Japanese discs. `ACE.SPB` has a different ISO-recorded size and requires a separate XA-aware reader; its full logical media content was not compared.

This supports investigating one shared BPB/BPH resource pipeline with disc-specific streaming data. It does **not** prove that gameplay reconstruction, branching, or disc switching is already solved.

For Japanese versus English-labeled versions:

| Observation | Disc 1 | Disc 2 |
|---|---|---|
| `ACE.BPB` contents | Different | Different |
| `ACE.BPH` contents | Different | Different |
| `ACE3.BIN` contents | Different | Identical |
| Boot executable contents | Identical | Identical |
| `ACE.SPH` contents | Identical | Identical |
| `SYSTEM.CNF` contents | Identical | Identical |
| `ACE.SPB` content identity | Unverified | Unverified |
| `ACE.SPB` start LBA | 46,986 -> 46,540 | 46,986 -> 46,613 |

Identical above means matching SHA256 of a complete supported logical file, not merely matching size. The comparison tool matches by path; the different boot-executable names across Japanese discs were correlated separately using their reported hashes.

## Why the media reader is separate

`ACE.SPB` starts with MODE2 Form 2 sectors in all four images. The inspector detects that condition and reports `unsupported_form2`; it does not discard payload bytes and claim a valid file hash. Form 1 and Form 2 sector layouts are documented by [psx-spx](https://psx-spx.consoledev.net/cdromformat/).

The whole-disc MD5/SHA1/SHA256 still cover every BIN byte, including the streaming archive. Only the per-file Form 1 hash is skipped for `ACE.SPB`.

## CUE filename mismatch

Both original Japanese descriptors refer to old `... (J) (v1.1) ... [SLPS-xxxxx].bin` names, while their archives contain `... (Japan) (Disc n).bin` names. Both English descriptors refer to existing files correctly.

The separately supplied CUE-fixes ZIP contains new `.fixed.cue` descriptors. Copy them beside the corresponding Japanese BINs and open the new descriptor. Existing BINs and original CUEs were left unchanged. The replacement descriptors passed the tool's strict filename-reference check; no emulator boot test was performed.

## Reproduce

```powershell
$env:PYTHONPATH = "src"
python -m swallow_tools "D:\AC3\Japanese Disc 2.bin" --hash --file-hashes --json
python -m swallow_tools.compare "D:\AC3\Japanese Disc 2.bin" "D:\AC3\English Disc 2.bin" --json
```

Replace the illustrative paths with actual local paths. Reading BINs directly works even when the original CUE contains a mismatched filename.
