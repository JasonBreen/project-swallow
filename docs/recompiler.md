# The recompiler

Implemented in [`swallow_tools.recompile`](../src/swallow_tools/recompile.py),
the C runtime core in [`runtime/`](../runtime/),
[`swallow_tools.native`](../src/swallow_tools/native.py), and
[`swallow_tools.recompcheck`](../src/swallow_tools/recompcheck.py). The
prototype on the Ulz routine is tooling v0.13.0, the first part of milestone
M4 in the [reconstruction plan](reconstruction-plan.md). Tooling v0.14.0
translates every game function in `ACE3.BIN` and adds the GTE to the C core;
see [Every game function](#every-game-function). It follows the owner's
decisions of 2026-11-04: recompile first and rewrite later (D1), in C11 built
with CMake on GCC, Clang, and MSVC (D5).

`swallow-recompile` translates original MIPS functions into C that runs
against a guest register file and guest memory. The first target is the
game's [Ulz routine](ulz-routine.md). Built as a shared library, the
translation runs the same check the interpreter runs, on every Ulz blob on
the disc. Nothing here is a playable build.

## Results

Measured on 2026-11-06:

| Disc | Blobs | Matched | Routine errors | Mismatches | Same counts and listed sites as the interpreter |
|---|---:|---:|---:|---:|---|
| Japanese Disc 1 | 2,256 | 2,256 | 0 | 0 | Yes |
| Japanese Disc 2 | 2,256 | 2,256 | 0 | 0 | Yes |
| English-labeled Disc 1 | 2,250 | 1,827 | 419 | 4 | Yes |
| English-labeled Disc 2 | 2,256 | 1,895 | 354 | 7 | Yes |

- The English-labeled discs' errors and mismatches are the 423 and 361
  overruns the interpreter found, at the same blobs; see
  [the Ulz routine](ulz-routine.md#results). Each report lists up to 50 of
  them, and the listed paths are identical in both runs.
- `ACE3.BIN` is identical on the two Japanese discs and English-labeled
  Disc 2, so one library serves all three. English-labeled Disc 1's differs
  by one data byte, so it has its own translation, and the check refuses a
  library translated from another executable.
- `ulz_decompress` translates to 262 instructions with 3 entry points and
  nothing left untranslated.
- The static load-delay check passes on every path. That includes the
  version 1 path and the two stream entry points, which no blob on the disc
  runs.
- The generated C compiles without warnings under `-Wall -Wextra` with
  GCC 13.3 and Clang 18.1.
- On Japanese Disc 1 the native check takes 24 seconds, against about
  8.5 minutes in the interpreter, in the same container.

The reports are [`ulz-routine-native-jp1.json`](reports/ulz-routine-native-jp1.json)
and its `jp2`, `en1`, and `en2` companions. Like the interpreter's reports,
they hold counts, paths, and addresses, never game bytes.

## Every game function

Measured on 2026-11-09 on Japanese Disc 1, with tooling v0.14.0.

`swallow-recompile --split-dir` takes function bounds, alternate entry
points, and the compiler's `jtbl_` jump tables from a `swallow-split` work
directory, and leaves out library code by the object ranges in the
[PsyQ report](psyq-libraries.md). The runtime is meant to replace the
library at its API, so calls into it stay untranslated. Each function is
analysed on its own, and one the translator refuses is left out of the
module with its reason, so a single refusal does not stop the rest.

| Measure | `ACE3.BIN`, Japanese Disc 1 |
|---|---:|
| Game functions | 1,286 |
| Translated | 1,286 |
| Refused | 0 |
| Instructions translated | 121,172 |
| Instructions left as "unsupported" traps | 0 |
| Library functions left out | 429 |
| Jump tables | 51 (50 `jtbl_` tables and `ulz_version_table`) |
| `jal` sites to translated functions | 2,838 |
| `jal` sites to library code, trapping for now | 727 |
| `jalr` sites, through the module's dispatch | 26 |

The 1,286 are the 1,284 functions in the game code range, `ulz_decompress`,
and the 16-byte `func_800AFCF8`. Every one passes the static load-delay and
timing checks. The module is one 9.7 MB C file. It compiles without warnings
under `-Wall -Wextra` with GCC 13.3 (38 s at `-O1`) and Clang 18.1 (20 s).
Built in this form, `ulz_decompress` still matches the project decoder on
all 2,256 Ulz blobs on the disc. The summary, without per-function detail,
is [`recompile-jp1.json`](reports/recompile-jp1.json).

### Checked against the interpreter

[`swallow-recompcheck`](../src/swallow_tools/recompcheck.py) calls every
translated function natively and in the [interpreter](mips-interpreter.md)
from the same start, and compares the outcome. Memory is mapped as the
oracle maps it after crt0: code read-only, data, bss, heap, and stack
writable, and the scratchpad. `$gp` is crt0's value and `$sp` is near the
top of RAM. The four argument registers are random but shaped: small
integers, negative ones, and pointers into the heap, the image's data, or
the scratchpad. The heap starts full of seeded words, 40% of them heap
pointers, so pointer chains go somewhere. The GTE starts in a random
scene-shaped state.

Each trial ends one of five ways:

| Outcome | Meaning | Trials |
|---|---|---:|
| Match | Both returned with equal general registers, HI, LO, GTE registers, every writable byte, and write extents | 9,024 |
| Both stopped | Both stopped, for the same kind of reason at the same address | 28,501 |
| Boundary | The native run reached code the module does not translate, almost always a PsyQ call | 3,570 |
| Step limit | The interpreter spent its 200,000 steps | 57 |
| Mismatch | Anything else | **0** |

That is 32 trials for each of the 1,286 functions, 41,152 in all, with seed
`0x5A11`. 1,213 functions had at least one comparable trial and 469 had a
matching call that returned. The report is
[`recompcheck-jp1.json`](reports/recompcheck-jp1.json).

A stop compares by its kind and the first address it names. A computed jump
or a return through a clobbered `$ra` to an address that is not code is an
instruction-fetch stop in the interpreter and a trap natively; both name the
target, so they count as the same stop. Random pointers often overwrite a
saved `$ra` on the stack, which is how the first runs found this case.

As a negative control, a build whose `swallow_sra` dropped the sign bits
gave 66 mismatches in 4 trials per function. Without the check against the
interpreter that broken shift would have looked like a clean build.

## How a function is translated

**Inputs.** The executable is staged from the disc image by content hash,
through the same helper the oracle uses. Functions come from the splat
symbol file: a sized `type:func` symbol is a function, and any `type:func`
symbol inside it is another entry point. Jump tables inside the text are
named symbols (`ulz_version_table` by default); each runs to the next symbol
in the file.

**Finding the code.** Instructions are found by following control flow from
the entry points, never by a linear sweep. Data inside the text, such as the
four words of `ulz_version_table`, is never translated. Control that would
leave the function is refused when the translation is made.

**One C function per original function.** Its body starts with a `switch`
on the entry address, so a shared tail, such as the exit block at
`0x800B0110`, is emitted once. Registers are C locals, loaded from the
register file on entry and written back on return.

**Delay slots.** A branch's condition, or a jump register's target, is
evaluated first. Then the delay-slot instruction runs, then the transfer.
That keeps a slot that changes the branch's own register correct, such as
`bgtz $t8` with `addi $t8, $t8, -1` in its slot. When a delay-slot address
is itself a branch target, as `0x800AFEDC` and `0x800B004C` are, the slot
instruction is emitted a second time under its own label. The branch's
not-taken path then jumps over it. A branch that is always taken, such as
`beq $zero, $zero` (assemblers write it `b`), has no not-taken path.

**Loads.** A translated load takes effect at once, while on the R3000 its
result lands one instruction later. That is faithful only if no instruction
uses a register in the delay slot of the load that sets it. A static check
proves this for every path before anything is written, or the translation is
refused. The one exception is an `lwr` right after an `lwl` into the same
register from the same base, which merges as the hardware does.

**Other instructions.**

- `add`, `addi`, and `sub` trap on signed overflow, as the R3000 does.
- `div` and `divu` go through helpers that return the R3000's documented
  results for a zero divisor and for `0x80000000 / -1`, where C's `/` is
  undefined. Those results are documentation-derived; see
  [the interpreter](mips-interpreter.md#where-each-rule-comes-from).
- `jal` calls a translated function directly, and `jalr` goes through the
  module's dispatch table. Registers are written back before the call and
  reloaded after it.
- A computed `jr` becomes a `switch` over its function's jump-table
  targets. Any other value traps, including the zero word in
  `ulz_version_table`.
- `jr $ra` returns, and traps if `$ra` no longer holds the caller's return
  address.
- Every translated function is a host C frame, so the core counts the ones
  active at once, calls and tail calls together, and traps past 4,096. A
  guest recursion or a cycle of tail calls, which no backward branch inside
  either function would count, then stops instead of overflowing the host
  stack.
- Every taken backward branch spends one unit of a budget, and the run traps
  when it is spent. This is the native counterpart of the interpreter's
  instruction limit.
- `break` and `syscall` trap.
- `lwl`, `lwr`, `swl`, and `swr` touch exactly the bytes of their aligned
  word that the interpreter's rules give. An `lwr` right after an `lwl` into
  the same register reads the `lwl` result, which is the in-flight merge.
- `mfc2`, `cfc2`, `mtc2`, `ctc2`, `lwc2`, and `swc2` go through the C GTE's
  register accessors, with the same read and write conversions as the
  Python model. A GTE command calls `swallow_gte_command` with its word.
- A branch or `j` to another translated function's entry is a tail call:
  registers are written back, the callee runs, and the function returns
  with the callee's result. A target that is not an entry traps.
- A computed `jr` resolves through every jump table whose words land inside
  its function, whether the table sits inside the function, as
  `ulz_version_table` does, or in rodata, as the compiler's tables do.
- Words the interpreter cannot decode trap with an "unsupported" message
  when reached. The report lists them, and none occurs in game code.

**Timing.** The translation runs each instruction to completion in order.
That is faithful only where none of the interpreter's refused timing cases
occurs, so a static check follows every path inside the function and
refuses: a HI/LO write within two instructions after `mfhi`/`mflo`; a GTE
command within two after `mtc2`, `ctc2`, or `lwc2`; one of those writes
right after a command; and any COP2 instruction in a branch delay slot.
Paths that leave the function through a call or return are not followed.

## The runtime core

`runtime/` is a CMake project (CMake 3.21 or later, C11):

- `ps1/core/cpu.h` and `cpu.c` hold the register file, traps, and the
  arithmetic helpers.
- `ps1/gte/gte.h` and `gte.c` are the GTE: a line-for-line C port of
  [`swallow_tools.gte`](../src/swallow_tools/gte.py), with every rule the
  [interpreter document](mips-interpreter.md#the-gte-model) lists. It works
  in 64-bit integers and floors signed shifts the way Python's `>>` does,
  since C leaves `>>` of a negative value implementation-defined and `<<`
  of one undefined.
- `ps1/core/guest.h` and `guest.c` hold guest memory: a table of mapped
  regions with the interpreter's `Memory` contract. Regions sit at fixed
  addresses and may not overlap. A misaligned or unmapped access, or a write
  to a read-only region, traps. Each region records one past the highest
  byte written. The caller owns the buffers.
- A trap returns control to the innermost `swallow_call` with a message,
  through `setjmp`/`longjmp`. The CPU stays usable for the next call, so a
  trapping blob becomes a routine error, not a crash.
- The runtime's own sources build with warnings as errors on GCC, Clang,
  and MSVC. Generated code builds without warnings-as-errors.

A translated module is a shared library. It exports `swallow_run`, and
`swallow_module_sha256`, the SHA256 of the executable it was translated
from. It also exports the core's CPU and guest-memory functions, so ctypes
can drive it. Every other symbol is hidden.
[`swallow_tools.native`](../src/swallow_tools/native.py) declares the
argument and result types of every call, so 32-bit guest addresses never
pass through a signed C `int`. It shares region buffers with the library
instead of copying them.

## Self-tests

The runtime's CTest targets, which CI runs with GCC, Clang, and MSVC:

- **`core`**: unit tests for the region rules, accesses, trap recovery, and
  the arithmetic helpers.
- **`selftest`**: [`swallow_tools.selftest`](../src/swallow_tools/selftest.py)
  assembles thirteen synthetic programs with `mipsasm`. They cover each shape
  above, including a slot that is also a target, a jump table in the text,
  calls, faults, traps, an endless loop, the unaligned loads and stores at
  every misalignment, and every kind of COP2 access with a run of GTE
  commands. The programs are translated at build time, and the 23 cases run
  natively and are compared with the interpreter's registers, HI/LO, GTE
  registers, memory, and write extents.
- **`native_selftest`**: the same module built as a shared library and run
  through ctypes, as `swallow-ulzcheck --native` runs the game's routine.
  It also compares the C GTE with the Python model directly: all 200
  modelled command words (each command with both `sf` and `lm`, and every
  MVMVA selector), with random ignored bits set, on 200 random states, half
  of them scene-shaped; a sweep of 20,000 (H, SZ3) pairs through the
  perspective divider; and 2,000 words, to check that both refuse the same
  ones.

Random states alone rarely reach every rounding case of the divider: a
deliberately wrong rounding constant passed 200 random states. The sweep
catches it, and also a wrong `0x80` constant in its Newton-Raphson step.

The self-test caught a deliberately broken translation: running the delay
slot before evaluating the branch condition failed two of its checks.

## Reproduce

```bash
PYTHONPATH=src python -m swallow_tools.recompile "DISC1.bin" ../recompiled-jp1
cmake -S ../recompiled-jp1 -B ../recompiled-jp1/build -DCMAKE_BUILD_TYPE=Release
cmake --build ../recompiled-jp1/build --config Release
PYTHONPATH=src python -m swallow_tools.ulzcheck "DISC1.bin" --native ../recompiled-jp1/build/libswallow_recompiled.so
```

Every game function, then the check against the interpreter:

```bash
PYTHONPATH=src python -m swallow_tools.split "DISC1.bin" --work-dir ../split-jp1
PYTHONPATH=src python -m swallow_tools.recompile "DISC1.bin" ../recompiled-all-jp1 --split-dir ../split-jp1
cmake -S ../recompiled-all-jp1 -B ../recompiled-all-jp1/build -DCMAKE_BUILD_TYPE=Release
cmake --build ../recompiled-all-jp1/build --config Release
PYTHONPATH=src python -m swallow_tools.recompcheck "DISC1.bin" ../recompiled-all-jp1/build/libswallow_recompiled.so ../recompiled-all-jp1/recompile-report.json --trials 32 --json
```

The check took 1 minute 48 seconds in this container. It exits with `0`
when nothing mismatches, `1` on any mismatch, and `2` on input errors.

On Windows the library is `build\Release\swallow_recompiled.dll`. The
output directory must be new or empty and outside the repository, because
the generated C is derived from the game. The runtime's own tests:

```bash
cmake -S runtime -B build-runtime
cmake --build build-runtime --config Release
ctest --test-dir build-runtime -C Release --output-on-failure
```

## Limits

- The library boundary is open: 727 call sites into PsyQ code trap, and so
  do the 3,570 trials that reached one. Implementing the 159 library
  functions in the runtime is the rest of M4 and M5.
- Only `ACE3.BIN` is translated. The boot program and the four overlays
  are not yet, and the overlays load at the same address, so they need a
  module per overlay or a loader-aware dispatch.
- The random calls are shaped, not recorded. Most trials stop at a wild
  pointer, and 469 of 1,286 functions returned at least once; agreement on
  real game inputs needs the emulator captures of M2.
- A native call returns to its call site, so a return through a `$ra` that
  the function changed traps, where the console would jump to that address.
  Game code was not found to do this, but a clobbered `$ra` makes the
  difference visible in the random check.
- The timing check stops at calls and returns, so a case split across a
  function boundary is not refused. `swallow-inventory` found none in
  straight-line code.
- The two Ulz stream entry points are translated and checked for hazards,
  but nothing runs them: no caller of them is known.
- The budget counts taken backward branches, not instructions, so a
  runaway routine stops later natively than under the interpreter.
- The C GTE agrees with the Python model, which is documentation-derived.
  Neither has been compared with hardware or an emulator capture yet.
- MSVC builds run only in CI. The local results above are from GCC and
  Clang on Linux.
