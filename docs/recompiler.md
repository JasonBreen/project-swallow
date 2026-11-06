# The recompiler prototype

Implemented in [`swallow_tools.recompile`](../src/swallow_tools/recompile.py),
the C runtime core in [`runtime/`](../runtime/), and
[`swallow_tools.native`](../src/swallow_tools/native.py), tooling v0.13.0.
This is the first part of milestone M4 in the
[reconstruction plan](reconstruction-plan.md). It follows the owner's
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
- Every taken backward branch spends one unit of a budget, and the run traps
  when it is spent. This is the native counterpart of the interpreter's
  instruction limit.
- `break` and `syscall` trap.
- Instructions the translator does not handle yet trap with an
  "unsupported" message when reached. These are the GTE and COP2
  instructions and the unaligned loads and stores. The report lists them,
  and none occurs in `ulz_decompress`.

## The runtime core

`runtime/` is a CMake project (CMake 3.21 or later, C11):

- `ps1/core/cpu.h` and `cpu.c` hold the register file, traps, and the
  arithmetic helpers.
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
  assembles ten synthetic programs with `mipsasm`. They cover each shape
  above, including a slot that is also a target, a jump table in the text,
  calls, faults, traps, and an endless loop. The programs are translated at
  build time, and the 18 cases run natively and are compared with the
  interpreter's registers, HI/LO, memory, and write extents.
- **`native_selftest`**: the same module built as a shared library and run
  through ctypes, as `swallow-ulzcheck --native` runs the game's routine.

The self-test caught a deliberately broken translation: running the delay
slot before evaluating the branch condition failed two of its checks.

## Reproduce

```bash
PYTHONPATH=src python -m swallow_tools.recompile "DISC1.bin" ../recompiled-jp1
cmake -S ../recompiled-jp1 -B ../recompiled-jp1/build -DCMAKE_BUILD_TYPE=Release
cmake --build ../recompiled-jp1/build --config Release
PYTHONPATH=src python -m swallow_tools.ulzcheck "DISC1.bin" --native ../recompiled-jp1/build/libswallow_recompiled.so
```

On Windows the library is `build\Release\swallow_recompiled.dll`. The
output directory must be new or empty and outside the repository, because
the generated C is derived from the game. The runtime's own tests:

```bash
cmake -S runtime -B build-runtime
cmake --build build-runtime --config Release
ctest --test-dir build-runtime -C Release --output-on-failure
```

## Limits

- One routine. It is hand-written assembly that calls nothing, so the
  translation of calls, the GTE, unaligned accesses, and the PsyQ library
  boundary has not met game code yet.
- The two stream entry points are translated and checked for hazards, but
  nothing runs them: no caller of them is known.
- The budget counts taken backward branches, not instructions, so a
  runaway routine stops later natively than under the interpreter.
- MSVC builds run only in CI. The local results above are from GCC and
  Clang on Linux.
