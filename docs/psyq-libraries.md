# PsyQ library identification

Implemented in [`swallow_tools.psyq`](../src/swallow_tools/psyq.py), tooling
v0.8.0. Measured on 2026-10-27 against all four supplied images. This is step
3 of the [executable track](next-task-executable.md). It separates Sony's
PsyQ SDK library code from the game's own code and names the library
functions in the splat output. It does not name or explain any game function.

## Method

Each PsyQ library object (`.OBJ` inside a `.LIB`) is compiled once by Sony and
linked byte for byte into every game that uses it, except for relocated
addresses. A signature is that object's text with the relocated bytes
replaced by wildcards. The tool:

1. Reads each PS-X EXE's text from the disc, matched by content as in
   [the EXE contract](exe-format.md).
2. Tries every signature. A signature counts only when it pins at least 32
   bytes (eight instructions) and matches exactly once, at a word-aligned
   offset.
3. Merges an object that matches identically in several SDK versions into one
   entry that lists all of them.
4. Resolves overlapping candidates by keeping the one with the most fixed
   bytes.
5. Reports each placed object and its global labels. Generated local names
   (`loc_*`, `text_*`) are left out.

## Signature data

The signatures come from
[lab313ru/psx_psyq_signatures](https://github.com/lab313ru/psx_psyq_signatures),
commit `e9e46e7e133ef275a79bfce650924f98edb086bc` (2022-01-09). They cover
PsyQ 2.6 to 4.7. The repository has no license, and the data describes
Sony's object code, so it is **never committed here**. Clone it outside the
repository and pass its path. The committed outputs are names, addresses,
and sizes only.

## What is committed

- [`docs/reports/psyq-jp1.json`](reports/psyq-jp1.json): the full report for
  Japanese Disc 1, listing every placed object with its versions, and every
  named label.
- The generated section at the end of each
  [splat symbol file](../splat), below the
  `// --- PsyQ library labels ...` marker. Entries written by hand above the
  marker keep their names, so `boot_entry`, `InitHeap`, and both `main`
  functions are unchanged.

## Results

The other three references give the same objects and labels for every
executable, including the English Disc 1 `ACE3.BIN`, whose one differing
byte is in data.

| Executable | Library objects | Library bytes | Named labels | Library span |
|---|---:|---:|---:|---|
| Boot (`SLPS_020.2x`) | 88 | 69,284 | 238 | `0x801255a4–0x80136618` |
| `ACE3.BIN` | 118 | 82,348 | 287 | `0x8009b910–0x800afcf8`, plus crt0 at `0x80025724` |

The boot executable's text runs from `0x801233e8` to `0x80136618`
(78,384 bytes), so 88% of it is library code. Its own code is the
8,636 bytes from `0x801233e8` (`boot_main`) to the crt0 at `0x801255a4`,
which hold 23 of splat's 364 functions.

`ACE3.BIN`'s text runs from `0x80025724` to `0x800b0130` (567,820 bytes).
The game's own code is the 483,624 bytes from `0x800257e8`, after crt0, to
`0x8009b910`, where the libraries start. That range holds `ace3_main`
(`0x800261a8`) and 1,284 of splat's 1,715 functions. It is the target
for the rest of the track.

After a re-run of `swallow-split` with the generated symbols, splat names
237 of 364 boot functions and 286 of 1,715 `ACE3.BIN` functions. The
remaining library-range functions are objects that no signature placed, or
entry points that only have generated names.

### Libraries linked

| Library | Boot | `ACE3.BIN` | Role |
|---|---|---|---|
| crt0 (`2MBYTE.OBJ`) | yes | yes | Startup; the RAM size table and `__SN_ENTRY_POINT` |
| LIBAPI | yes | yes | BIOS calls, root counters, pads |
| LIBC / LIBC2 | yes | yes | `printf`, `memcpy`, `strcmp`, `setjmp` |
| LIBCD | yes | yes | CD-ROM control, ISO 9660, `CdRead` |
| LIBETC | yes | yes | `VSync`, interrupts, video mode |
| LIBGPU | yes | yes | GPU setup, display lists, TMD |
| LIBPAD | yes | yes | Controllers |
| LIBSPU | yes | yes | Sound hardware |
| LIBSND | yes | no | Sequenced sound (`SsInit`, VAB, MIDI playback) |
| LIBGTE | no | yes | Geometry transforms, `InitGeom`, matrices |
| LIBPRESS | no | yes | MDEC decoding (movies) |
| LIBCARD | no | yes | Memory card |
| LIBSN | no | yes | 64-bit divide helpers |

LIBSND appears only in the boot executable, which also holds the VAB sound
bank at the start of its data. Only `ACE3.BIN` links LIBGTE, LIBPRESS, and
LIBCARD. **Hypothesis:** the boot executable handles startup audio and video
setup before it loads `ACE3.BIN`, and the 3D game, movies, and saves live in
`ACE3.BIN`. That fits the libraries but does not yet show the load order.

### SDK version

Every boot library matches PsyQ 4.3 signatures, so the boot executable is
consistent with a single PsyQ 4.3 link. In `ACE3.BIN`, every library has a
common 4.5 match except LIBPRESS, which matches only 4.6 and 4.7. The
signature set may label versions imperfectly, or the libraries may come
from more than one SDK release. Both executables carry the same `$Id:`
strings (`sys.c,v 1.140 1998/01/12`, `bios.c,v 1.86`, `intr.c,v 1.75`).

### The "Ps" words

The [disassembly](disassembly.md) found three 8-byte words that begin with
`Ps` (`0x7350`) and splat cannot decode. Two of them, at `0x8012d008` (boot)
and `0x800af2f8` (`ACE3.BIN`), are the first 8 bytes of LIBAPI `C114.OBJ`,
which then holds the `_96_remove` BIOS stub. The third, at `0x800a6268`, is
the first 8 bytes of LIBGTE `MSC00.OBJ`, before `InitGeom`. The signatures
contain these bytes as fixed, so they belong to Sony's objects and not to
the game. The earlier module-stamp hypothesis is half confirmed: these words
open library objects. What the remaining bytes encode is still unknown. The
words differ between the two executables
(`50 73 00 25 9b 52 43 00` vs `50 73 00 00 00 00 45 00`).

## Reproduce

```powershell
git clone https://github.com/lab313ru/psx_psyq_signatures G:\ProjectSwallow-data\psyq-sigs
git -C G:\ProjectSwallow-data\psyq-sigs checkout e9e46e7e133ef275a79bfce650924f98edb086bc
$env:PYTHONPATH = "src"
python -m swallow_tools.psyq <BIN> --signatures G:\ProjectSwallow-data\psyq-sigs --json > report.json
python -m swallow_tools.psyq <BIN> --signatures G:\ProjectSwallow-data\psyq-sigs --update-symbols
```

A full run over all 16 SDK versions takes under a minute per image.
`--versions 430 450` limits the search. `--update-symbols` rewrites only the
generated section of the symbol files of the configs whose SHA256 matches,
and fails if an executable has no config. The tool exits with `0` for
success and `2` for input, format, or signature errors. It writes no game
bytes.

## Limits

- An object that no signature places stays unnamed, even inside the library
  span. Very short objects, such as single BIOS call stubs (`InitHeap`), are
  below the 32-byte minimum on purpose.
- A unique, aligned match of at least 32 fixed bytes is strong evidence, not
  proof. Each placed object lies inside the measured library spans, and none
  was placed in the game code range.
- Library data and BSS are not matched. Only text is.

## Next

- Locate the Ulz decompressor in `ACE3.BIN`'s game code range
  (`0x800257e8–0x8009b910`), now that library calls in it carry names.
