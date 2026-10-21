# Supplementary disc references

Inventoried locally on **2026-10-21**. These are two additional owner-supplied
references. Project Swallow's four AC3 baselines and gameplay-recovery scope
remain as documented in [the AC3 inventory](disc-inventory.md).

## Inputs and identity evidence

| Input | Platform / layout | Image bytes | Observed identifier |
|---|---|---:|---|
| `ps1 games.tar.bz2`: `ACECOMBAT.BIN` / `ACECOMBAT.CUE` | PS1, mixed-mode raw 2352-byte sectors | 609,182,112 | `BISLPS-00061ACECOMB` in `X/TITTLE.EXE`; volume `ACECOMBAT` |
| `Ace Combat Zero.iso` | PS2, ISO 9660 with 2048-byte sectors | 4,240,605,184 | `BOOT2 = cdrom0:\SLUS_213.46;1`, `VER = 1.00`, `VMODE = NTSC` in `SYSTEM.CNF` |

The PS1 executable also contains `Sony Computer Entertainment Inc. for Japan
area`. These measured strings support identifying it as the original
Japanese PlayStation **Ace Combat**; the serial is listed by
[PSX Data Center](https://psxdatacenter.com/games/J/A/SLPS-00061.html).
The PS2 boot identifier corresponds to the North American **Ace Combat Zero:
The Belkan War**, as listed by [PCSX2](https://wiki.pcsx2.net/Ace_Combat_Zero:_The_Belkan_War).
Title/region identification does not certify a clean retail dump or a specific
pressing. Neither new image matches an existing AC3 image fingerprint.

## Preservation and verification

The [supplementary manifest](../discs/supplementary-manifest.json) records each
stored archive/volume hash and its source-image hashes. Its `inputs` entries
describe storage explicitly; it is separate from the existing four-archive
AC3 `manifest.json`.

- The complete original PS1 tar.bz2 is preserved byte for byte. Both members
  were streamed to a new directory under `G:\ProjectSwallow-data\imports\`.
  Each member's MD5, SHA1, and SHA256 was calculated. The extracted BIN was
  hashed again and matched the BIN already beside the supplied archive.
- The supplied PS2 ISO was read without modification and packaged into a new
  lossless 7z archive, split into volumes of at most **1,073,741,824 bytes**.
  The volumes preserve one complete ISO; they are not independently usable
  disc images. Decompression was streamed and the restored byte count, MD5,
  SHA1, and SHA256 were compared with the supplied ISO.
- Both additions use Git LFS. Raw extracts and scratch scripts stay on `G:`
  outside Git. The supplied images were not modified, mounted, or booted.
  The separate derived PS1 reference described below has a verified audio-layout
  correction; it does not replace the original archive.

The new stored data totals **3,523,624,085 bytes**: 429,739,506 bytes for the
original PS1 archive and 3,093,884,579 bytes across three PS2 volumes. Together
with the four existing AC3 archives, a complete checkout contains
**5,191,614,553 bytes** of compressed reference data.

Whole-image SHA256:

```text
ACECOMBAT.BIN
96a667f18a6f070c47f988347352adc87479a8ecc258c19ab1f342f3cf3bd7b3

Ace Combat Zero.iso
4d9f41ff87690fe8a4cb1a20599d055e66107f9990a823af47fb1c8a58745d3d
```

## PS1 inspection limits

The [PS1 report](reports/supplementary-ps1-acecombat.json) contains **324 files
and 39 directories**. Of those files, 259 were hashed through verified Form 1
access, 3 were explicitly reported as unsupported Form 2, and 62 ISO extents
reaching the CUE-declared CD-DA area were skipped. Whole-image hashes cover
every BIN byte, including all skipped media.

The original CUE names the existing `ACECOMBAT.BIN` correctly and declares
**63 tracks: one MODE2/2352 track and 62 AUDIO tracks**. All declared index
positions are inside the image and track INDEX 01 positions increase.
The descriptor was preserved exactly. MAME's `acecombt` preservation entry
also records **63 tracks**, with per-track sizes, SHA1, and CRC32, and links to
Redump disc 1691. The earlier PSX Data Center count of 16 is contradicted by
this track-level evidence; changing this image to 16 tracks would be incorrect.
The investigation used the
[pinned MAME metadata](https://github.com/mamedev/mame/blob/006f4ade8d461bcbd8895a613f27b3b76dc62f24/hash/psx.xml),
not a fresh physical-disc dump or a directly retrieved Redump record.

### Verified audio-layout correction

The [per-track investigation](reports/supplementary-ps1-track-validation.json)
establishes the difference between the supplied files and that reference:

- The source data track matches the recorded size, SHA1, and CRC32 exactly.
- The source CUE synthesizes the first audio track's 150-sector pregap, so
  those 352,800 bytes are absent from the source BIN.
- Audio has a consistent **+32-byte read-offset correction**, equivalent to
  eight stereo sample frames. The original BIN includes a silent final track.
- Materializing that pregap and advancing the audio by 32 bytes makes
  **all 63 track sizes, SHA1 hashes, and CRC32 values match** the pinned entry.
  The data track remains byte-identical. This also corroborates the track
  boundaries; physical subchannel data was not available.

[`prepare_acecombat_reference.py`](../scripts/prepare_acecombat_reference.py)
checks both source fingerprints, creates a new derived BIN/CUE, verifies every
resulting track, and refuses an existing output directory. It copies every
original data/audio byte, writes 352,768 zero bytes before audio and 32 after
it, and converts the virtual pregap to explicit CUE indexes. No game payload
is downloaded. It is restricted to this fingerprinted source and reference.

```powershell
python scripts/prepare_acecombat_reference.py 'G:\ProjectSwallow-data\imports\2026-10-21-ps1-games\ACECOMBAT.BIN' 'G:\ProjectSwallow-data\imports\2026-10-21-ps1-games\ACECOMBAT.CUE' 'G:\ProjectSwallow-data\imports\2026-10-21-acecombat-corrected'
```

The generated image is **609,534,912 bytes**, SHA256
`178ce1ddd01f8e024a32b6ba2d8ee3f1d963295740e8b26337fab7a34bfe3244`.
The generated `ACECOMBAT.reference.cue` opens the corresponding derived BIN.
Both generated files stay outside Git; the original archive plus the script
reproduce them. The checked-in report records the full verification result.
Audio playback and emulator boot have not been tested.

Use the existing CLI for image metadata, whole-image hashes, and CUE filename
checks:

```powershell
$env:PYTHONPATH = 'src'
python -m swallow_tools 'G:\ProjectSwallow-data\imports\2026-10-21-ps1-games\ACECOMBAT.BIN' --cue 'G:\ProjectSwallow-data\imports\2026-10-21-ps1-games\ACECOMBAT.CUE' --strict-cue --hash --json
```

Do not apply the CLI's blanket `--file-hashes` option to this mixed-mode disc:
some ISO entries refer into CD-DA, which the Form 1 reader cannot hash as files.
The supplementary report limits file hashing to extents before the first
CUE-declared audio track. The existing AC3 profile registry continues to report
this image as unknown; BPH/BPB validation is still specific to the AC3 inputs.

## PS2 inspection limits

The [PS2 report](reports/supplementary-ps2-ace-combat-zero.json) records **22
files and 4 directories**, their ISO extents, and SHA256 for every listed file.
Inspection used bounded direct ISO 9660 reads and streaming hashes. Logical
block size and both-endian directory extent/length fields were checked.

The directory contains `BIN/DATA.PAC`, `BIN/DATA.TBL`, movie and briefing PACs,
audio/radio PACs, IRX modules, the boot executable, and `SYSTEM.CNF`. These
filenames are observations; their archive contents and gameplay behavior
have not been decoded. The existing `swallow-disc` reader remains a raw PS1
MODE2/2352 reader. This intake does not implement PS2 support in that tool.

## Restore the stored inputs

After pulling the LFS files, extract the PS1 archive into a new local folder:

```powershell
New-Item -ItemType Directory 'G:\ProjectSwallow-data\restored-acecombat'
tar -xjf 'discs/supplementary/ps1/ps1 games.tar.bz2' -C 'G:\ProjectSwallow-data\restored-acecombat'
```

For the PS2 ISO, keep **all** numbered 7z volumes together and open `.001`:

```powershell
& 'C:\Program Files\7-Zip\7z.exe' x 'discs\supplementary\ps2\Ace Combat Zero.7z.001' '-oG:\ProjectSwallow-data\restored-ace-combat-zero'
Get-FileHash -Algorithm SHA256 -LiteralPath 'G:\ProjectSwallow-data\restored-ace-combat-zero\Ace Combat Zero.iso'
```

Compare the result with the SHA256 above and the manifest. `7z l` can inspect
the restored ISO directory without mounting or executing it. `git lfs fsck`
checks local stored LFS object integrity. None of these checks proves an
emulator boot, audio timing, or playable reconstruction.
