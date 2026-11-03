# Reconstruction plan: from recovered code to a native engine

**Status: plan, drafted 2026-11-01. Nothing below is implemented unless it
says so.** Items marked **D1–D9** are decisions for the owner. AGENTS.md
requires measured evidence and project approval before an engine stack is
chosen, so this plan recommends; it does not decide. The near-term steps are
in the [roadmap](roadmap.md) and the [executable track](next-task-executable.md).

## Owner direction

- 2026-10-13: the game engine is written in C, and Python stays offline tooling.
- 2026-11-01: turn the recovered AC3 code into an executable game engine.
  Later, do the same for Ace Combat Zero, Ace Combat (1995), and Ace Combat 2.
  Ace Combat 04 may not be needed if Sal's work on it is completed; its
  status is open.

## What AC3 needs, measured

Measured on Japanese Disc 1 from the splat output, the
[PsyQ symbols](psyq-libraries.md), and BPB entries 1–4. Function counts for
the entries are `jr $ra` counts, a rough estimate until the entries are split.

| Area | Measurement | Consequence |
|---|---|---|
| Main game code | `ACE3.BIN` `0x800257e8–0x8009b910`: 120,906 instructions in 1,284 functions, plus the [Ulz routine](ulz-routine.md) | The core of the work |
| Boot program | 23 functions of its own; links LIBSND, which `ACE3.BIN` does not | Small; also shows the load path |
| Overlay code | BPB entries 1–4: 289, 707, 20, and 22 `jr $ra`; hundreds of calls onto `ACE3.BIN` functions | About 1,000 more functions, which need load addresses first |
| Library boundary | 159 distinct PsyQ functions called from game code and entries 1–4: LIBGTE 31, LIBGPU 25, LIBCD 19, LIBSPU 17, LIBPAD 11, LIBC2 10, LIBPRESS 8, LIBAPI 5, LIBCARD 5, LIBETC 5, LIBSN 1, and 22 short unnamed stubs, mostly BIOS calls | The interface the runtime replaces |
| Geometry coprocessor (GTE) | 3,485 inline GTE instructions in game code, using 15 commands: MVMVA 127, NCLIP 81, DPCS 52, RTPS 39, RTPT 33, GPF 32, INTPL 21, DPCT 8, SQR 6, GPL 6, AVSZ3 4, NCT 2, NCCS 2, NCS 1, AVSZ4 1 | A bit-exact GTE is required; the flight and camera maths run through it |
| Indirect control flow | 26 `jalr`, 42 computed `jr`, 587 jump-table labels | Every target must be listed before translation |
| Hardware access | 121 instructions form `0x1F80xxxx` addresses; all 27 that splat resolves are scratchpad, none an I/O register | Scratchpad must exist; the other sites still need classifying |
| Sound | `ACE3.BIN` uses LIBSPU without LIBSND | Any music sequencing in it is game code, recovered like the rest |
| Compression | The game's Ulz routine matches `ulz.py` on all 2,256 blobs | The first routine with an executable proof |

## Strategy: recompile first, then decompile

There are three ways to get from MIPS to C:

| | Matching decompilation | Static recompilation | Hand-written C checked by tests |
|---|---|---|---|
| What it is | C that the original compiler turns back into identical bytes | A tool translates each instruction into C mechanically | C written per function, compared with the original on recorded inputs |
| First native build | After most functions are matched | Early: everything is translated at once | After most functions are rewritten |
| Proof per function | Identical object code | Inherited from the translator, which is tested once | Only as good as the test inputs |
| Readability | High | Low | High |
| Main risk | Finding and licensing a period compiler setup; thousands of functions to match | A complete list of indirect targets and overlays; callback timing | Bugs that the inputs miss |

**Recommendation (D1):** combine the second and third approaches, and keep
the first available.

1. **Translate everything.** An offline Python recompiler turns every
   recovered function, overlays included, into C that runs against a guest
   RAM image. It uses the same instruction semantics as
   [`swallow_tools.mips`](../src/swallow_tools/mips.py), which becomes its
   test oracle.
2. **Replace the libraries, not the hardware.** A C runtime implements the
   159 PsyQ functions at their API, backed by a bit-exact GTE and models of
   GPU primitives, SPU voices, CD and XA, MDEC, pads, and the memory card.
   Library identification is what makes this boundary visible.
3. **Rewrite progressively.** Readable C replaces translated functions one at
   a time, starting with the flight model. Each replacement must equal the
   translated original on recorded inputs. Matching compilation is an extra
   proof where the owner approves a compiler setup.

This gives a native build early, keeps behaviour identical to the original
by construction, and gives every rewrite an oracle. The flight model is
never approximated: it runs as translated code until a rewrite proves equal.

The risks are concrete. The recompiler needs every indirect-call target,
jump table, and overlay load address. Callbacks such as `VSyncCallback` and
CD-ready callbacks need a deterministic schedule. Translated code is derived
from the game's code (see D3).

## Proposed architecture

```text
tools/               Python, offline: disc, BPB, EXE, splat, PsyQ (exist);
                     recompiler, capture and asset tools (planned)
runtime/ps1/         C, shared by the PlayStation titles
  core/              guest RAM and scratchpad, CPU state, scheduler (VSync,
                     root counters, callbacks), indirect-call dispatch
  gte/               bit-exact geometry coprocessor, including FLAG
  gpu/               primitive and ordering-table processing; renderers
  spu/  cd/  mdec/   voices and reverb; disc image, XA, CD-DA; movies
  pad/  card/        controllers; memory card files
  psyq/  bios/       the library and BIOS functions the games call
games/ac3/           generated code, rewritten C, and AC3-specific hooks
platform/            window, input, audio output, graphics API (D4)
```

- **Memory (D2).** Game state lives in a guest RAM image, and pointers stay
  32-bit guest addresses. That keeps original data layouts. It also lets
  native runs be compared with emulator RAM byte for byte. Native structures
  come later, only where the presentation layer needs them.
- **Time.** The simulation advances exactly as the original frame loop does.
  Higher refresh rates may interpolate presentation, but they never change
  the simulation rate.
- **Assets.** The runtime reads the user's own disc image. Enhanced textures
  replace originals keyed by the hash of the TIM or VRAM upload. Voices and
  music replace XA or VAB data keyed by file, channel, and sector range.
  Every original asset stays available as an option.
- **Dub and text.** Clean voice masters stay separate from the radio effect,
  which is applied at runtime. Text goes through a script table keyed by the
  original resource, with the Japanese source kept beside each translation.

## Validation

Each rewrite must agree with an oracle:

1. **The original code** in `swallow_tools.mips`, for isolated functions.
   The [Ulz check](ulz-routine.md) is the pattern. It needs a GTE model in
   the interpreter before most game functions can run.
2. **A reference emulator** (D6), for whole-game captures: deterministic
   input replay and per-frame RAM snapshots.
3. **Hardware**, later, if a capture setup becomes available.

The planned tests:

- **Function tests:** record inputs (registers and the memory a function
  reads), then run the original and the replacement and compare every
  register and write.
- **Game-state parity:** replay the same inputs natively and in the
  emulator, and compare game-state RAM each frame. Simulation state must
  match exactly; any excluded region, such as a CD buffer, is listed with a
  reason.
- **Flight-model parity:** recorded flights for each aircraft, compared
  every tick.
- **GTE vectors:** bit-exact results and flags for all 15 commands the game
  uses, from captures.
- **CI:** the C build and unit tests run on Windows and Linux without discs,
  like the Python tests. Disc-based parity runs locally. A passing local or
  CI run is not an emulator or hardware validation.

## Milestones

| | Milestone | Done when |
|---|---|---|
| M1 | Code inventory | Every byte of the boot program, `ACE3.BIN`, and the overlays is assigned to code or data. Overlay load addresses and all indirect-call and jump-table targets are recorded. |
| M2 | Capture rig | The chosen emulator replays recorded input deterministically twice and dumps RAM each frame. |
| M3 | Runtime skeleton | C build on Windows and Linux in CI; guest memory, scheduler, and platform layer; the GTE passes its vectors. |
| M4 | Recompiler v1 | `ulz_decompress`, translated and compiled natively, passes the same 2,256-blob check; then every function translates. |
| M5 | Native boot | The PsyQ functions are implemented, and the native build reaches the title screen with game-state parity against the emulator. |
| M6 | One mission | Roadmap stage 3: one mission plays natively, and flight-model parity holds over recorded flights. |
| M7 | Readable core | The flight model and mission logic are rewritten in C and proven equal; texture and voice replacement hooks work. |
| M8 | Full game | Both discs, branching, saves, presentation options, and the dub. |

## Decisions needed

| | Decision | Options | Recommendation | What settles it |
|---|---|---|---|---|
| D1 | Form of recovered code | Matching decompilation; recompilation plus rewrites; rewrites only | Recompilation plus tested rewrites, with matching where approved | A recompiler prototype on `ulz_decompress` (M4), and a measured identification of the game's compiler from its code patterns |
| D2 | Memory model | Guest RAM image; native pointers | Guest RAM first | Whether loaded data and saves embed 32-bit pointers, found by the loader trace |
| D3 | What is published | Tools only, with code generated from the user's disc at build time; or committed generated and rewritten code | Tools only for generated code; get legal review before any public release of code derived from the game | Owner choice, with legal advice; this plan is not legal advice |
| D4 | Platform libraries | SDL3 with its GPU API, SDL with OpenGL, or Vulkan directly | Evaluate SDL3, which is zlib-licensed and compatible with MIT, in a renderer spike | Spike results on Windows, Linux, and Steam Deck class hardware |
| D5 | Build and language | CMake and C11 with GCC, Clang, and MSVC (or clang-cl); alternatives | CMake and C11 | The first C commit |
| D6 | Reference emulator | Candidates with scripting, RAM dumps, and deterministic replay | Evaluate the candidates; use one only as a tool and incorporate none of its code | The M2 replay test |
| D7 | Frame-rate policy | Original rate only; interpolated presentation | Original simulation rate, with interpolation as an option | M6 parity with interpolation on |
| D8 | Title order | See below | AC3 to M6 first, then AC2 and AC1, then the PS2 titles | Owner priority and Sal's AC04 status |
| D9 | Repository layout | One repository; a shared tooling and runtime repository plus one per title | Split the shared parts out when a second title starts | Size: the PS2 reference alone is 3.1 GB compressed |

## Other titles

Measured facts are marked; everything else is expectation.

| Title | Platform | Reference | What is known | Main new work |
|---|---|---|---|---|
| Ace Combat (1995), SLPS-00061 | PS1 | On `main` (`discs/acecombat`) | **Measured 2026-11-01:** no `SYSTEM.CNF`, so the BIOS boots `PSX.EXE` (38,912 bytes, text `0x80012000`). Seven module executables in `X/` all load at `0x80023000`: GAME, GAMEVS, LOADER, MISSEL, MOVIE, STOP, TITTLE. All carry the PsyQ `$Id: sys.c` string. There is no Ulz or BPB: missions are plain files (`M00`–`M21`, `.TD` and `.MD`), with VAB banks, STR movies, and 62 CD-DA tracks. | Booting without `SYSTEM.CNF` in the tools; switching between whole executables at runtime; CD-DA music; its own formats |
| Ace Combat 2 | PS1 | Not supplied | | Expected to reuse the PS1 runtime |
| Ace Combat 3 | PS1 | Four references | This project | |
| Ace Combat 04 | PS2 | Not supplied | Open pending Sal's work; need its status, scope, and license | Shares the PS2 runtime with Zero |
| Ace Combat Zero, SLUS_213.46 (NTSC-U) | PS2 | `discs/supplementary/ps2`, three 7z volumes of one ISO | From its [inventory](supplementary-discs.md): `BIN/DATA.PAC` with `DATA.TBL`, IRX modules (LIBSD, MCMAN, SIO2MAN, NUSOUND, NUSNDSTR), `SMV` movies, and `STREAM` BGM and radio archives | A separate PS2 runtime: the R5900 CPU, VU0 and VU1 microcode, the GS, and the IOP; far larger code |

What carries across titles:

- the disc, executable, splat, and signature tooling
- the recompile-then-rewrite method
- the validation approach
- asset replacement and the dub pipeline
- for AC1 and AC2, the whole PS1 runtime

What does not carry is each game's own code. Shared Namco code between
titles is a hypothesis to measure, by comparing normalized function bytes
across executables, before anything is shared.

Survey a title before starting it. A survey costs little and can happen at
any time: inventory, executables, libraries, and archive formats, as AC3 had
in `disc-inventory.md`. Runtime work on another title should wait until AC3
reaches M6, so the PS1 runtime has been proven once.

## Next bounded tasks

1. Trace the BPB loader and the overlay load addresses (track step 5), and
   add splat configs for entries 1–4.
2. Finish the code inventory (M1): classify the remaining `0x1F80` sites, and
   list indirect-call targets and registered callbacks.
3. Owner decisions D1–D5, which can run alongside the above.
4. Emulator spike for D6: deterministic replay and RAM dumps (M2).
5. Recompiler prototype on `ulz_decompress` (M4), once D5 allows the first C
   code.
6. GTE core and vectors (M3), first in the Python interpreter so more game
   functions can run under the oracle.
