# Disassembly with splat

Implemented in [`swallow_tools.split`](../src/swallow_tools/split.py), tooling
v0.7.0. Measured on 2026-10-21 against all four supplied images. This is step
2 of the [executable track](next-task-executable.md). It produces annotated
MIPS assembly for both PS-X EXEs. It does not identify PsyQ library code,
reassemble the output, or run anything.

## Tool choice

The owner chose [splat](https://github.com/ethteck/splat) on 2026-10-21 over
a project-written disassembler. splat drives
[spimdisasm](https://github.com/Decompollaborate/spimdisasm), which decodes
through [rabbitizer](https://github.com/Decompollaborate/rabbitizer). All
three are MIT licensed. The version is pinned in
[`splat/requirements.txt`](../splat/requirements.txt) (`splat64[mips]==0.50.1`,
with spimdisasm 1.42.4 and rabbitizer 1.16.2). It is an optional
tooling dependency: the package itself still has no dependencies, and the
synthetic tests run without splat.

Install it into a virtual environment. On some system Pythons, a plain
`pip install` fails to build the `intervaltree` and `pylibyaml` wheels until
setuptools is upgraded, which a fresh venv does.

## What is committed and what is not

The [`splat/`](../splat) directory holds one config per executable, a
symbol file per executable, and `manifest.json`, which maps each config to
the SHA256 values it accepts. The extracted executables and everything
splat generates are derived from the game, so the tool refuses a work
directory inside the repository. Its output stays local, on `G:`.

## Measured boundaries

Each code segment is split into rodata, text, and data. Splat's own
`create_config` estimate put the text start at the wrong place on both
executables, so the boundaries below were measured from the instructions.

| Executable | Rodata | Text | Data | Text evidence |
|---|---|---|---|---|
| Boot (`SLPS_020.2x`) | `0x80120000` | `0x801233e8` | `0x80136618` | The first function is the lowest in-range `jal` target and has a stack prologue; the last `jr $ra` is at `0x80136610`; data starts with a VAB (`pBAV`) sound bank header |
| `ACE3.BIN` | `0x80010000` | `0x80025724` | `0x800b0130` | The first function is a `jr $ra` stub that is the lowest in-range `jal` target; the last `jr $ra` is at `0x800b0128`; pointer tables follow |

The four executables reduce to three distinct files, and the code is the same
in all of them. `ACE3.BIN` on English-labeled Disc 1 differs from the
others by one byte at file offset `0xa29d8` (`0x800b21d8`, data: `0x31`
becomes `0x3c`). The same configs therefore cover all four references. The
generated assembly is identical apart from that one data word.

## Entry points

The header entry point is not a `jal` target, so splat reads it as data
unless it is named. Both entry points hold the PsyQ crt0 pattern:

1. Clear BSS: `0x80194f88–0x801cee00` for the boot executable, and
   `0x800bce40–0x800cbfe8` for `ACE3.BIN`.
2. Load the stack from a four-word RAM size table (`0x00200000` each).
3. Call the BIOS `A(0x39)` stub (InitHeap, per psx-spx).
4. Call main: `0x801233e8` for the boot executable, `0x800261a8` for `ACE3.BIN`.

These addresses, and the RAM size tables, are in the symbol files with their
evidence. The header BSS fields are zero, so these ranges come from the code
alone. The `ACE3.BIN` BSS starts at `0x800bce40`, which is inside the last
`0x1c0` bytes of its text image. That is recorded and not yet explained.

## Results

| Text file | Functions | Data labels in text | Undecodable words |
|---|---:|---:|---:|
| `boot_text.s` | 363 | 2 | 5 |
| `ace3_text.s` | 1,713 | 3 | 8 |

The counts are the same on all four references. Every undecodable word is
accounted for: the four RAM size table words in each executable, plus 8-byte
markers that begin with `Ps` (`0x7350`) at `0x8012d008`, `0x800a6268`, and
`0x800af2f8`. Each marker sits between library-style code. **Hypothesis:** these
are PsyQ module stamps that can anchor library identification. That is not
yet verified.

## Reproduce

```powershell
python -m venv G:\ProjectSwallow-data\venv-splat
G:\ProjectSwallow-data\venv-splat\Scripts\python -m pip install -r splat\requirements.txt
$env:PYTHONPATH = "src"
python -m swallow_tools.split <BIN> --work-dir G:\ProjectSwallow-data\splat\jp1 --python G:\ProjectSwallow-data\venv-splat\Scripts\python.exe
```

The work directory must be empty or absent. The tool matches executables to
configs by SHA256 and fails on any executable without a config. It writes
`asm/`, the linker scripts, one `*.splat.log` per config, and
`split-report.json` with the counts above. It exits with `0` for success and
`2` for input, format, or splat errors.

## Next

- Identify PsyQ library functions by signature, starting from the `Ps` markers
  and the BIOS call stubs, so that library code is separated from game code.
- Reassemble the output with a MIPS assembler and compare it byte for byte
  with the input. That needs a toolchain choice, which is not made here.
- Locate the Ulz decompressor in `ace3_text.s`.
