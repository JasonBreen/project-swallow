# The MIPS interpreter and GTE model (the oracle)

Implemented in [`swallow_tools.mips`](../src/swallow_tools/mips.py),
[`swallow_tools.gte`](../src/swallow_tools/gte.py), and
[`swallow_tools.oracle`](../src/swallow_tools/oracle.py), tooling v0.12.0.
This is milestone M1's oracle in the [reconstruction plan](reconstruction-plan.md):
it runs original game functions on chosen inputs, so a project decoder or
generated C can be compared with what the game's own code does. It is
offline tooling, not an emulator. There are no interrupts, caches, cycle
timing, or hardware registers.

## What it runs

Every instruction that Ace Combat 3's game code contains, measured on
Japanese Disc 1 by the [code inventory](code-inventory.md), except `break`:

- the MIPS I integer instructions, with branch delay slots as on the R3000;
- `mult`, `multu`, `div`, `divu`, `mfhi`, `mflo`, `mthi`, `mtlo`;
- the unaligned loads and stores `lwl`, `lwr`, `swl`, `swr`;
- the coprocessor 2 moves `mfc2`, `cfc2`, `mtc2`, `ctc2`, `lwc2`, `swc2`;
- the fifteen GTE commands game code issues: `RTPS`, `RTPT`, `NCLIP`,
  `MVMVA`, `DPCS`, `DPCT`, `INTPL`, `SQR`, `GPF`, `GPL`, `AVSZ3`, `AVSZ4`,
  `NCS`, `NCT`, and `NCCS`.

`break` and `syscall` decode, and running one raises `MipsTrap`, as does an
`add`/`addi`/`sub` overflow. A trap is what the original code would do
there, a result to compare. It is distinct from `MipsError`, which means the
interpreter cannot model a case faithfully and refuses to guess. `MipsTrap`
is a subclass of `MipsError`, so code that stops on either keeps working.
For `break`, `MipsTrap.code` is the operand spimdisasm prints, bits 16–25
(`break 7` is `0x0007000D`), and `raw_code` is the 20-bit field in bits 6–25.

`call(memory, entry, arguments, max_steps=..., hi=0, lo=0, gte=None)` takes
the starting HI, LO, and GTE registers and returns them with the general
registers. A `GteState` is an immutable snapshot of the 32 data and 32
control registers.

## Load delays and the one merging pair

A load result, and an `mfc2` or `cfc2` result, becomes visible only after
the following instruction, and that instruction may not read or write the
loaded register. That keeps every run independent of interlock details.

The exception is the unaligned-copy idiom. An `lwr` right after an `lwl`
into the same register from the same base merges into the `lwl` result while
it is still in flight, as the hardware does. The merged word lands after the
`lwr`'s own delay slot. `lwr` first, a different base, a base equal to the
target, or any other use of the register in the slot is still refused. A
lone `lwl` merges with the register's old value. Measured on Japanese
Disc 1: all 219 game-code `lwr`s follow an `lwl` into the same register from
the same base, and no instruction after an `lwr` uses its target.

`lwl`/`swl` touch exactly the bytes from the aligned word's first byte to
the address, and `lwr`/`swr` from the address to the aligned word's last
byte, little-endian. A byte outside a mapped region stops the run, as any
other access does.

## Refused timing cases

These are undefined on MIPS I or documented to misbehave on the GTE. The
interpreter refuses them rather than modelling them:

| Case | Game code, Japanese Disc 1 |
|---|---:|
| `mult`, `div`, `mthi`, or `mtlo` within two instructions after `mfhi`/`mflo` | 0 |
| A GTE command within two instructions after `mtc2`, `ctc2`, or `lwc2` | 0 |
| `mtc2`, `ctc2`, or `lwc2` right after a GTE command | 0 |
| Any COP2 instruction in a branch delay slot | 0 |

The counts come from `swallow-inventory`, which checks straight-line code;
the interpreter checks the dynamic order as well. Because none occurs, GTE
register writes are modelled as immediate and a command completes before
the next instruction. That is the PsyQ convention: two `nop`s after a write,
and a read straight after a command, which relies on the hardware interlock.
121 game-code commands are followed at once by `mfc2`, `cfc2`, or `swc2`.

## Where each rule comes from

None of these rules has been checked against this project's own capture of
hardware or an emulator yet; Track D of the plan does that. Until then:

| Rule | Source | Status |
|---|---|---|
| Integer instructions, delay slots, load delay | MIPS I architecture | Documentation-derived |
| `div` by zero: HI = dividend; LO = −1 if the dividend is non-negative, else +1 | Public PlayStation hardware documentation | Documentation-derived |
| `divu` by zero: HI = dividend, LO = `0xFFFFFFFF` | Same | Documentation-derived |
| `0x80000000 / -1`: HI = 0, LO = `0x80000000` | Same | Documentation-derived |
| `lwl`/`lwr` merge in flight | Same | Documentation-derived |
| GTE register conversions, FLAG bits, FIFOs, command formulas | Public hardware documentation and the published behaviour of open-source emulators | Documentation-derived |
| Reciprocal table and perspective division | Generated from the published formula | Documentation-derived |
| Timing cases above never occur in game code | `swallow-inventory` on Japanese Disc 1 | Measured |

The division results matter because of the twelve unguarded divisions the
[code inventory](code-inventory.md#division-guards) lists, whose LO game
code consumes. The tests check the four cases (positive by zero, negative by
zero, `divu` by zero, `0x80000000 / -1`) against these values, which tests
the implementation, not the hardware.

## The GTE model

### Registers

Registers are stored as the hardware keeps them; reads convert them.

- `VZ0–2` and `IR0–3` hold 16 bits and read sign-extended. `OTZ` and
  `SZ0–3` hold 16 bits and read zero-extended.
- `SXYP` (data 15): a write pushes the screen-coordinate FIFO; a read
  returns `SXY2`.
- `IRGB` (data 28): a write sets IR1–3 to each 5-bit component times
  `0x80`. Reading `IRGB` or `ORGB` returns IR1–3 divided by `0x80` and
  clamped to 0–31. `ORGB` and `LZCR` ignore writes; `LZCR` counts the
  leading bits of `LZCS` equal to its sign bit, 1–32.
- Control registers 4, 12, 20, 26 (`H`), 27 (`DQA`), 29 (`ZSF3`), and
  30 (`ZSF4`) hold 16 bits and read back sign-extended, `H` included,
  although commands use `H` unsigned.
- FLAG (control 31): bits 0–11 read as zero; bit 31 is the OR of the bits
  under `0x7F87E000` (30–23 and 18–13). IR3 saturation (bit 22), the colour
  bits 19–21, and IR0 saturation (bit 12) do not set it.

### Commands

- FLAG is cleared at the start of every command.
- `MAC1–3` sums are checked against 44 bits after every addition, setting
  bits 30–25, and wrapped as the hardware does. An overflow that only an
  intermediate sum reaches still sets its flag. The result is shifted right
  by 12 when `sf` = 1 and kept as 32 bits.
- `IR1–3` saturate to −0x8000..0x7FFF, or 0..0x7FFF when `lm` = 1, setting
  bits 24–22. Inside `DPCS`, `DPCT`, and `INTPL`, the first step's
  `FC − colour` result saturates as if `lm` were 0.
- `MAC0` is checked at 32 bits (bits 16 and 15) and truncated, not
  saturated. This is the `NCLIP` and `AVSZ3`/`AVSZ4` path.
- `RTPS`/`RTPT`: `SZ3` is `MAC3 >> 12` clamped to 0..0xFFFF (bit 18). When
  `sf` = 0, IR3's flag follows `MAC3 >> 12` while its value follows `MAC3`.
  The quotient `H / SZ3` uses the reciprocal table and one Newton–Raphson
  step, clamped to `0x1FFFF`; when `H ≥ 2·SZ3` it is `0x1FFFF` with bit 17.
  `SX2`/`SY2` are `(quotient · IR + OFX/OFY) >> 16` clamped to −0x400..0x3FF
  (bits 14, 13). `MAC0` and `IR0` (0..0x1000, bit 12) come from
  `DQA·quotient + DQB`, for `RTPT` from its last vector only.
- The colour commands push `RGB0–2` with each component `MAC >> 4` clamped
  to 0..0xFF (bits 21–19) and the CODE byte copied from `RGBC`. `DPCT`
  reads `RGB0` on each of its three passes, so each pass takes the next
  entry.
- `MVMVA`'s defective selectors, matrix 3 and far-colour translation
  (`cv` = 2), are rejected at decode time. Game code never uses them.
- Bits 20–24 of a command word, where PsyQ stores its macro number, and bits
  6–9 and 11–12 are ignored, as on the hardware.

Two points differ between sources, and game code never reaches either:

- One source says `RTPS`/`RTPT` saturate IR1–3 to −0x8000 whatever `lm` is;
  emulator implementations apply `lm`. The model applies `lm`. Every
  game-code `RTPS`/`RTPT` has `lm` = 0.
- When `quotient · IR + OFX` exceeds 32 bits, `SX2` here is clamped from the
  full sum shifted right by 16, as emulators do. Truncating to 32 bits first
  would give a different value. Bit 16 is set either way.

### Reciprocal table

`UNR_TABLE[i] = max(0, (0x40000 // (i + 0x100) + 1) // 2 - 0x101)` for `i`
0–256, the generating formula published beside the table in the Nocash
PlayStation specifications. It is built when the module loads; no table
bytes are in the repository.

### Encodings game code uses

Measured on Japanese Disc 1 by `swallow-inventory`'s `gte_commands_game`
table, as decoded fields with counts. Every row has a test vector in
[`tests/test_gte.py`](../tests/test_gte.py):

| Command | `sf` | `lm` | `MVMVA` matrix, vector, translation | Count |
|---|---:|---:|---|---:|
| `MVMVA` | 1 | 0 | RT, V0, TR | 10 |
| `MVMVA` | 1 | 0 | RT, V0, none | 75 |
| `MVMVA` | 1 | 0 | RT, V1, none | 1 |
| `MVMVA` | 1 | 0 | RT, V2, none | 1 |
| `MVMVA` | 1 | 0 | RT, IR, none | 50 |
| `MVMVA` | 1 | 1 | LLM, V0, none | 12 |
| `NCLIP` | 0 | 0 | | 82 |
| `DPCS` | 1 | 0 | | 53 |
| `RTPS` | 1 | 0 | | 47 |
| `RTPT` | 1 | 0 | | 43 |
| `GPF` | 0 | 0 | | 22 |
| `GPF` | 1 | 0 | | 11 |
| `INTPL` | 1 | 0 | | 23 |
| `DPCT` | 1 | 0 | | 8 |
| `SQR` | 0 | 1 | | 6 |
| `GPL` | 1 | 0 | | 6 |
| `AVSZ3` | 1 | 0 | | 4 |
| `NCT` | 1 | 1 | | 2 |
| `NCCS` | 1 | 1 | | 2 |
| `AVSZ4` | 1 | 0 | | 1 |
| `NCS` | 1 | 1 | | 1 |

Counts cover `ACE3.BIN` and `ovl2`; the boot program and the other
overlays issue no GTE commands. `NCLIP` ignores `sf`.

## What a run of a GTE function must be given

The libraries set most GTE control registers once (`InitGeom`,
`SetGeomOffset`, `SetGeomScreen`, and the matrix and colour setters), and
game code relies on them. Game code never writes `ZSF3` or `ZSF4`, and
writes the screen offset and `H` only a handful of times. An oracle run of a
function that uses a command must therefore be given these through `gte=`;
zeroed registers give wrong answers, not errors:

| Command | Control registers it reads |
|---|---|
| `RTPS`, `RTPT` | RT (0–4), TR (5–7), OFX, OFY (24–25), H (26), DQA, DQB (27–28) |
| `MVMVA` | The selected matrix (RT 0–4, LLM 8–12, LCM 16–20) and translation (TR 5–7, BK 13–15) |
| `DPCS`, `DPCT`, `INTPL` | FC (21–23) |
| `NCS`, `NCT`, `NCCS` | LLM (8–12), BK (13–15), LCM (16–20) |
| `AVSZ3`, `AVSZ4` | ZSF3 (29), ZSF4 (30) |
| `NCLIP`, `SQR`, `GPF`, `GPL` | None |

The values come from the library setup calls that run before the function,
which an emulator capture (Track D) or a run of those calls under the
oracle can supply.

## The calling contract for compiled code

Compiled PsyQ code reaches small data through `$gp` and keeps frames on
`$sp`. `ACE3.BIN`'s header has `initial_gp` = 0; crt0 computes `$gp` with a
`lui`/`addiu` pair. A function started with zeroed registers stops at its
first `%gp_rel` access or frame push. `swallow_tools.oracle` supplies that
state:

- `find_executable` stages an executable from a disc image by content hash,
  as `swallow-ulzcheck` does.
- `read_layout` reads a [splat config](../splat/) for its code ranges and
  `gp_value`.
- `crt0_gp` finds crt0's `$gp` from the entry point. Measured: it gives
  `0x800BCB74` for `ACE3.BIN` and `0x80194F40` for the boot program on
  Japanese Disc 1, the same values the splat configs were written with.
- `game_memory` maps code read-only and executable, the rest of the image
  and all RAM after it up to 2 MiB as writable data, and the 1 KiB
  scratchpad. RAM below the image, the BIOS area, stays unmapped.
- `game_call` sets `$gp` and an aligned `$sp` and calls the function.

**Checked on real code.** `func_80036130` in `ACE3.BIN` reads a structure
pointer through `$gp`, increments a byte counter in it, wraps the counter
modulo 37 with the compiler's `multu`/`mfhi` reciprocal, and copies two
bytes from a table into the structure. Run under the oracle for all 256
counter values, its results matched a direct Python calculation 256 times
out of 256.

## Reproduce

```powershell
$env:PYTHONPATH = "src"
python -m unittest tests.test_mips tests.test_gte tests.test_oracle -v
python -m swallow_tools.inventory G:\ProjectSwallow-data\splat\jp1 --json > G:\ProjectSwallow-data\reports\code-inventory-jp1.json
python -m swallow_tools.ulzcheck "G:\path\to\Ace Combat 3 - Electrosphere (Japan) (Disc 1).bin"
```

## Limits

- Only the instructions and GTE commands game code uses are modelled.
  Library-only instructions (`mfc0`, `mtc0`, `rfe`, GTE `OP`, `DPCL`) and
  other commands stop a run.
- Every hardware rule is documentation-derived until a capture checks it.
- There is no timing: a routine that polls hardware or depends on cycle
  counts cannot run.
- The `$gp` scan reads crt0's first `lui`/`addiu` or `lui`/`ori` pair on
  `$gp` within 64 instructions of the entry point; an executable that sets
  `$gp` another way returns none.
