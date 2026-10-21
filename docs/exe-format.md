# PS-X EXE header contract

Implemented in [`swallow_tools.exe`](../src/swallow_tools/exe.py), tooling
v0.6.0. Validated on 2026-10-19 against all four supplied images. This is
step 1 of the executable track: it reads headers, hashes, and path strings.
It does not disassemble, load, or run any code, and it writes no game bytes.

## Finding: `ACE3.BIN` is a second PS-X EXE

Earlier notes described the first words of `ACE3.BIN` (`2c570280 00000000
00000180 00d00a00`) as a possible overlay header. Those words are at file
offset `0x10`. The file starts with the `PS-X EXE` magic, and its header
passes every rule below: entry `0x8002572c`, text loaded at `0x80010000`,
text size `0xad000`, and file size exactly `0x800 + 0xad000`. The overlay
hypothesis for `ACE3.BIN` is withdrawn. The BPB entries 1–4 overlay
hypothesis in [bpb-format.md](bpb-format.md) is a separate question and is
unchanged.

The boot executable occupies `0x80120000–0x80195000` and `ACE3.BIN`
occupies `0x80010000–0x800bd000`, so their text ranges do not overlap. The
boot executable contains the string `\ACE3.BIN;1` at `0x8012000c`, and
`ACE3.BIN` contains `\ACE.SPB;1`, `\ACE.BPB;1`, `\ACE.SPH;1`, and
`\ACE.BPH;1` at `0x800103a8–0x800103cc`. That fits a boot loader that stays
resident and loads `ACE3.BIN` as the main program. It is a hypothesis until
the disassembly or an emulator trace shows the load.

## Layout

All integers are little-endian. The layout follows the public psx-spx
description of the PS-X EXE header.

| Offset | Width | Field | Reader rule |
|---|---:|---|---|
| `0x00` | 8 | Magic | Exactly `PS-X EXE` |
| `0x08` | 8 | Reserved | Kept as hex; nonzero is a warning |
| `0x10` | 4 | Initial PC | Word aligned; outside the text image, compared as physical RAM, is a warning |
| `0x14` | 4 | Initial GP | Reported only |
| `0x18` | 4 | Text address | Word aligned, inside main RAM through KUSEG, KSEG0 or KSEG1 and any of the four 2 MiB bus mirrors in each, clear of the first 64 KiB |
| `0x1c` | 4 | Text size | A multiple of 2048; header plus text equals the ISO file size exactly; the text does not run past the end of its 2 MiB mirror |
| `0x20` | 8 | Data address and size | Reported; either word nonzero is a warning, and no memory effect is assumed |
| `0x28` | 8 | BSS address and size | Same as data |
| `0x30` | 8 | Stack base and offset | Reported only |
| `0x38` | 20 | Reserved | Kept as hex; nonzero is a warning |
| `0x4c` | to NUL | Region marker | Printable ASCII; nonzero bytes after it are a warning |

## Discovery

`SYSTEM.CNF` must appear once at the root. Its `BOOT` line must be a
`cdrom:` path to exactly one ISO file, and that file must be a valid PS-X
EXE. Its `;n` version, when given, chooses between ISO versions of the same
name. A repeated key keeps its first value, as the PlayStation OS
documentation specifies, and the ignored lines are listed in the report. Text after
whitespace in `BOOT` is kept as `boot_arguments`, apart from the path. Every other file is checked by content: a file whose first eight
logical bytes are the magic is parsed as an executable, whatever its name.
A file whose first sector is MODE2 Form 2 is listed under `form2_skipped`
and never read as Form 1 data.

Path references are `\NAME;n` strings naming files in the same ISO. A match
must begin at a path boundary, so `\DIR\GAME.BIN;1` counts only as
`DIR/GAME.BIN`, never as a root `GAME.BIN`, and it must end after the
version digits. Each
records its file offset and, for text bytes, its load address. Overlap checks
compare physical addresses, so the same RAM seen through two mirrors still
counts as overlap.

## Measurements

| Reference | Boot file | Boot text range | `ACE3.BIN` text range | `ACE3.BIN` matches Japanese Disc 1 |
|---|---|---|---|---|
| Japanese Disc 1 | `SLPS_020.20` | `0x80120000–0x80195000` | `0x80010000–0x800bd000` | — |
| Japanese Disc 2 | `SLPS_020.21` | `0x80120000–0x80195000` | `0x80010000–0x800bd000` | Yes |
| English-labeled Disc 1 | `SLPS_020.20` | `0x80120000–0x80195000` | `0x80010000–0x800bd000` | No; same header, different text bytes |
| English-labeled Disc 2 | `SLPS_020.21` | `0x80120000–0x80195000` | `0x80010000–0x800bd000` | Yes |

The boot executable has the same SHA256 on all four references
(`daddca3a…`), as does `ACE3.BIN` everywhere except English-labeled Disc 1,
matching the earlier [inventory](disc-inventory.md). `ACE.SPB` starts with a
Form 2 sector on every reference and is skipped. Every executable on every
reference has zero warnings: reserved bytes, data,
and BSS fields are all zero, and both entry points lie inside their text.
Each header has stack base `0x801ffff0` with offset 0, while `SYSTEM.CNF`
sets `STACK = 801FFF00`. Which value the BIOS uses is not decided here.
`SYSTEM.CNF` also sets `TCB = 4` and `EVENT = 16`.

## Reproduce

```powershell
$env:PYTHONPATH = "src"
python -m swallow_tools.exe "G:\ProjectSwallow-data\japanese-disc1\Ace Combat 3 - Electrosphere (Japan) (Disc 1).bin"
python scripts\validate_exe_references.py --reference jp1 <BIN> --output-dir G:\ProjectSwallow-data\reports
```

The four reports are in `docs/reports/exe-{jp1,jp2,en1,en2}.json`.
The validation script checks each image's whole-file hashes and each
executable's hash against the baseline reports before it writes anything.
When `docs/reports/exe-KEY.json` exists, the discovered executables must
match it exactly, so a report that misses an executable fails. Both the
reader and the script exit with `0` for success and `2` for input, format,
or validation errors.

## Next

The disassembly pipeline is implemented; see [disassembly](disassembly.md).
Next come PsyQ library identification, then the Ulz decompressor and the BPB
loader inside `ACE3.BIN`.
