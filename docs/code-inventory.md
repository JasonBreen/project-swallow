# Code inventory for the recompiler

Implemented in [`swallow_tools.inventory`](../src/swallow_tools/inventory.py),
tooling v0.11.0; report schema 2 and the division, GTE-encoding, and timing
sections since v0.12.0. Measured on 2026-11-03 and 2026-11-05 against
Japanese Disc 1. This is part
of milestone M1 in the [reconstruction plan](reconstruction-plan.md). It
lists what a static recompiler must know before it can translate the game's
code: where control goes indirectly, what the code registers as callbacks,
what hardware it touches, and which instructions the
[interpreter](mips-interpreter.md) cannot run yet. It reads splat's output,
not the disc, and the report holds names, addresses, decoded fields, and
counts only, never instruction words.

## Method

`swallow-inventory` reads a [`swallow-split`](disassembly.md) work directory:
its `split-report.json`, each unit's text, and its rodata and data. A unit
is the boot program, `ACE3.BIN`, or one of the four [overlays](overlays.md).

- **Library or game code.** A function is library code when it starts inside
  a block of placed PsyQ objects from [`psyq-jp1.json`](reports/psyq-jp1.json).
  Objects at most 64 KiB apart form one block, so short library code that no
  signature placed still counts as library code. Each executable is matched
  by SHA256. The overlays have not been scanned for signatures, so all of
  their code counts as game code. On Japanese Disc 1 the blocks this forms
  are exactly the documented library ranges: `0x80025724–0x800257e8` (crt0)
  and `0x8009b910–0x800afcf8` in `ACE3.BIN`, and `0x801255a4–0x80136618` in
  the boot program.
- **Computed jumps.** A `jr` on any register other than `$ra` is resolved
  when one of splat's `jtbl_` tables targets labels in the same function.
  BIOS stubs are recognized and listed separately; each one is
  `addiu $t2, $zero, 0xA0`, `0xB0`, or `0xC0`, then `jr $t2`, with the call
  number put in `$t1` in the delay slot.
- **Address-taken functions.** A function's address is taken when an
  instruction's `%hi`, `%lo`, or `%gp_rel` operand names it, or when a data
  word does. Any of them can be an indirect-call target.
- **Callbacks.** Each call to a PsyQ function whose name ends in `Callback` is
  checked. `ResetCallback`, `StopCallback`, `RestartCallback`, and
  `CheckCallback` are skipped, since they take no function. The callback
  argument is the last `%lo` or zero written to `$a0` within 8 instructions
  before the call, or in its delay slot. `DMACallback`, `InterruptCallback`,
  and `VSyncCallbacks` take it in `$a1`.
- **Hardware addresses.** These are `0x1F80xxxx` addresses in operands, as
  spimdisasm prints paired `lui` forms, and `D_1F80xxxx` symbols. They are
  classed as scratchpad (`0x1F800000–0x1F8003FF`), I/O
  (`0x1F801000–0x1F802FFF`), or other. A `lui` of `0x1F80` with no paired
  address is counted as unresolved.
- **Interpreter gaps.** Every mnemonic outside
  `swallow_tools.mips.SUPPORTED_MNEMONICS` is counted, after mapping
  spimdisasm's pseudo-instructions (`nop`, `beqz`, `bnez`, `b`, `negu`,
  `neg`, `not`, `move`) to the instructions they stand for. A GTE command
  also counts as a gap when its word does not decode, such as `MVMVA` with
  an unmodelled selector.
- **GTE encodings.** Each game-code GTE command is counted by its decoded
  fields: command, `sf`, `lm`, and for `MVMVA` the matrix, vector, and
  translation selectors.
- **Division guards.** A game-code `break 7` is a zero-divisor guard when
  the three instructions before it are `div`/`divu $zero, a, b`,
  `bnez b`, and `nop`. A `break 6` is an overflow guard when the five
  before it are `addiu $at, $zero, -1`, `bne b, $at`, `lui $at, 0x8000`,
  `bne a, $at`, and `nop`, and the division is nine instructions back. A
  `div`/`divu` without a zero-divisor guard three instructions later is
  listed as unguarded.
- **Timing cases.** Straight-line sequences the interpreter refuses (see
  [the interpreter](mips-interpreter.md#refused-timing-cases)) are counted.

## Results

Game code only, except where a column says otherwise.

| Unit | Game functions | Game instructions | `jalr` | Computed `jr` without a jump table | Address-taken functions (all code) | Function-pointer tables |
|---|---:|---:|---:|---|---:|---:|
| Boot | 23 | 2,159 | 8 | none | 59 | 5 |
| `ACE3.BIN` | 1,286 | 121,176 | 26 | `ulz_decompress` | 222 | 28 |
| `ovl1` | 271 | 16,714 | 10 | none | 125 | 43 |
| `ovl2` | 645 | 61,434 | 33 | none | 295 | 70 |
| `ovl3` | 20 | 3,403 | 0 | none | 3 | 1 |
| `ovl4` | 20 | 1,757 | 0 | none | 2 | 1 |
| **All** | **2,265** | **206,643** | **77** | **1** | | |

The 1,286 `ACE3.BIN` functions are the 1,284 in the
[game code range](psyq-libraries.md#results) plus `ulz_decompress` and the
16-byte `func_800AFCF8`. The boot count of 23 also matches that document.

**Computed jumps.** In game code, every computed `jr` is covered by one of
splat's jump tables except one: `ulz_decompress`'s jump through
`ulz_version_table`, which lives inside the text and has its three targets
documented in [the Ulz routine](ulz-routine.md#what-the-routine-does). There
are 50 jump tables in `ACE3.BIN` (1,098 entries), 8 in the boot program, and
25, 3, 3, and 3 in the overlays. Four computed jumps without a jump table
remain in `ACE3.BIN`'s library code, in `EnablePAD`, `DisablePAD`, and two
unnamed neighbours.

**BIOS calls.** `ACE3.BIN` has 33 BIOS stubs and the boot program 20, all in
library code. Game code calls three of them directly: `A(0x70)` from
`ACE3.BIN`, and `A(0x43)` and `A(0x44)` from the boot program. The rest are
called by the libraries. Their full list, with vectors and numbers, is in
the report.

**Callbacks.** Game code registers these functions. Every other registration
in game code passes zero, which clears the callback:

| Unit | Setter | Callback |
|---|---|---|
| `ACE3.BIN` | `CdReadyCallback` | `func_80029A44`, `func_80029D70` |
| `ACE3.BIN` | `CdSyncCallback` | `func_8002A200` |
| `ACE3.BIN` | `DecDCToutCallback` | `func_80057170` |
| `ovl2` | `DecDCToutCallback` | `func_800FCB44` |

The first three are in the game's own CD sector driver
(`0x80029A44–0x8002AE0C`, see [overlays](overlays.md#other-findings)), and the
`DecDCToutCallback` ones run when the MDEC finishes a block. These are the
interrupt-time entry points the runtime's scheduler must call. Library code
registers its own DMA, interrupt, and CD callbacks too; they are in the
report.

**Hardware.** No game-code operand names an I/O register. Game code names
only scratchpad addresses, in 931 operands: 698 in `ACE3.BIN`, 227 in
`ovl2`, 5 in `ovl4`, and 1 in `ovl1`. No `lui 0x1F80` is left unpaired.
I/O addresses appear only in library code: the root counter 2 registers
`0x1F801120`, `0x1F801124`, and `0x1F801128` in both executables, and SPU
registers `0x1F801C00` and `0x1F801D80` in the boot program. This scan
covers addresses formed from immediates; an address computed at run time,
or loaded from data, would not show up.

**Instructions the interpreter cannot run**, in game code. Tooling v0.11.0
listed these classes; v0.12.0 added all but the traps to the
[interpreter](mips-interpreter.md):

| Class | Boot | `ACE3.BIN` | `ovl1` | `ovl2` | `ovl3` | `ovl4` | All | Since v0.12.0 |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| GTE (`cop2`, its moves, loads, and stores) | 0 | 3,485 | 0 | 463 | 0 | 0 | 3,948 | Runs |
| Multiply and divide (`mult`, `div`, `mfhi`, `mflo`, …) | 16 | 1,420 | 137 | 711 | 28 | 12 | 2,324 | Runs |
| Unaligned access (`lwl`, `lwr`, `swl`, `swr`) | 40 | 534 | 112 | 236 | 80 | 0 | 1,002 | Runs |
| Traps (`break`, `syscall`) | 2 | 318 | 32 | 108 | 10 | 0 | 470 | Raise `MipsTrap` |
| Coprocessor 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | Library only |

Coprocessor 0 instructions appear only in library code, 5 in `ACE3.BIN`,
as do the GTE commands `OP` (4) and `DPCL` (1). With v0.12.0, `break` is the only game-code
instruction the interpreter does not run to completion.

### Division guards

**Measured:** every one of the 470 game-code `break`s is a GCC division
guard of the shapes above. There are 236 zero-divisor guards (`break 7`) and
234 overflow guards (`break 6`): 160 and 158 in `ACE3.BIN`, 1 and 1 in the
boot program, 16 and 16 in `ovl1`, 54 and 54 in `ovl2`, and 5 and 5 in
`ovl3`. `break 0, 1` and `syscall` occur only in library code. This replaces
v0.11.0's hypothesis that most of them were.

Twelve game-code divisions have no guard. All are hand-written
`div $zero, $v0, $a2` instructions. In all twelve, the next HI/LO
instruction in program order is an `mflo`, 20 to 54 instructions later, so
game code uses the quotient:

| Function | Addresses |
|---|---|
| `func_80099894` | `0x80099C14`, `0x80099D50`, `0x80099E64`, `0x8009A068`, `0x8009A1A4`, `0x8009A2B8` |
| `func_8009A944` | `0x8009AC90`, `0x8009ADCC`, `0x8009AEE0`, `0x8009B0E4`, `0x8009B220`, `0x8009B334` |

At the first site, the path is taken only for a negative divisor, so a zero
divisor cannot reach it but `0x80000000 / -1` can. The other eleven were not
traced. These are where the R3000's documented division results become
observable, so they are what the recompiler's checked mode and the capture
rig target.

### GTE encodings and timing

Game code uses 21 distinct GTE command encodings, listed with counts in
[the interpreter document](mips-interpreter.md#encodings-game-code-uses).
`MVMVA` uses only the RT and LLM matrices and the TR or no translation;
the documented defective selectors never occur. None of the four refused
timing cases occurs in game code.

## What this means for the plan

- The recompiler's indirect-call dispatch has to cover the address-taken
  functions, the module handler tables, and the five callbacks above. Only
  one computed jump in game code, `ulz_decompress`'s, needs a hand-written
  target list.
- Game code reaches hardware only through the libraries and the scratchpad.
  That supports replacing the hardware at the PsyQ API, as the plan
  proposes, rather than emulating registers for game code.
- Since v0.12.0 the interpreter runs every game-code instruction except
  `break`, so game functions can run as an oracle once they are given
  their `$gp`, stack, and GTE state; see
  [the calling contract](mips-interpreter.md#the-calling-contract-for-compiled-code).

## Reproduce

```powershell
$env:PYTHONPATH = "src"
python -m swallow_tools.inventory G:\ProjectSwallow-data\splat\jp1
python -m swallow_tools.inventory G:\ProjectSwallow-data\splat\jp1 --json > G:\ProjectSwallow-data\reports\code-inventory-jp1.json
```

`--psyq` selects the PsyQ report; the default is
`docs/reports/psyq-jp1.json`. The tool exits with `0` on success and `2` when
the work directory or report cannot be read. The checked-in report is
[`reports/code-inventory-jp1.json`](reports/code-inventory-jp1.json).

## Limits

- Japanese Disc 1 only. The code is identical on all four references (see
  [disassembly](disassembly.md) and [overlays](overlays.md#on-all-four-references)),
  but English-labeled Disc 1's `ACE3.BIN` has a different SHA256, and the
  PsyQ report lists only the Japanese hash, so the library split would be
  unavailable there.
- The overlays were not scanned for PsyQ signatures.
- Callback arguments are resolved only from a `%lo` or zero within 8
  instructions. Anything else is reported as `unresolved`.
- Address-taken functions are found from relocations and data words only. A
  function reached through an address computed some other way would be
  missed.
