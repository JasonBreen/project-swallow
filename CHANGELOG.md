# Changelog

## Unreleased

- Added the Ace Combat Zero (SLUS_213.46, PS2) reference from PR #8: three Git LFS 7z volumes of one 4,240,605,184-byte ISO, with its supplementary manifest, inventory report, and `docs/supplementary-discs.md`. The volumes were fetched again and match their LFS OIDs. The restored ISO matches the recorded size, MD5, SHA1, and SHA256. A full LFS checkout is now 5,192,688,883 bytes.
- Brought in PR #8's Ace Combat (1995) evidence: the inventory and 63-track validation reports, and `scripts/prepare_acecombat_reference.py`. PR #8's `ps1 games.tar.bz2` is not tracked: its members are byte-identical to `game.BIN` and `game.CUE` in the stored `discs/acecombat/game.tar.bz2`. The supplementary manifest now points at that archive and records the other one's hash.
- The correction script now builds its output in a hidden staging directory and renames it into place only when every file is written and verified. A failed run leaves nothing behind, so it no longer blocks a retry; this fixes the open review finding on PR #8. Added 3 synthetic tests; the cleanup test fails against the previous script.
- Stored the supplied ACECOMBAT image (`game.BIN` and `game.CUE`) through Git LFS. It is not one of the four Electrosphere baselines. The CUE names `ACECOMBAT.BIN`; the archived member is `game.BIN`.
- Added `docs/reconstruction-plan.md`. It plans how the recovered code becomes the C engine: a static recompiler, a runtime that replaces the PsyQ library API, and readable C proven equal to the translated code. It lists decisions D1–D9, all still open for the owner, and milestones M1–M8. It records the owner's direction to reconstruct Ace Combat Zero, Ace Combat (1995), and Ace Combat 2 after AC3; Ace Combat 04 depends on Sal's work. Nothing in the plan is implemented.
- Measured for the plan, on Japanese Disc 1: game code and BPB entries 1–4 call 159 distinct PsyQ functions and use 3,485 inline GTE instructions across 15 commands, with 26 indirect calls and 42 computed jumps. A survey of the stored Ace Combat (1995) image found no `SYSTEM.CNF`, a `PSX.EXE` that boots seven module executables loaded at `0x80023000`, no Ulz or BPB data, and 62 CD-DA tracks.

## 0.14.0 — 2026-11-09

- Added the GTE to the C runtime core: `runtime/ps1/gte/`, a line-for-line port of `swallow_tools.gte` with the same registers, read and write conversions, FLAG rules, and fifteen commands. It works in 64-bit integers and floors signed shifts as Python does, so no result depends on implementation-defined C. The CPU state carries the GTE, and `swallow_gte_get`, `swallow_gte_set`, and `swallow_gte_execute` expose it to ctypes. This is the C half of M3's GTE core.
- The core gains `swallow_lwl`, `swallow_lwr`, `swallow_swl`, and `swallow_swr`, with the interpreter's byte rules.
- `swallow-recompile` now translates every instruction in the game's code:
  - the unaligned loads and stores;
  - the COP2 moves, loads, and stores, and every modelled GTE command.
  - A branch or `j` into another translated function's entry is a tail call.
  - The core traps when more than 4,096 translated functions are active at once, so a tail-call cycle or runaway recursion stops instead of overflowing the host stack. A new self-test case runs such a cycle. Found by review on PR #14.
  - A tail call traps when `$ra` no longer holds the caller's return address, and the load-delay and timing checks follow a tail call into the callee's first instructions. A new self-test case changes `$ra` before a tail call. Neither case occurs in game code. Found by review on PR #14.
  - A computed `jr` resolves through any jump table whose words land in its function, so the compiler's rodata tables work as `ulz_version_table` does.
  - A static check refuses the interpreter's timing cases on every path inside a function: HI/LO writes too soon after `mfhi`/`mflo`, GTE commands too soon after a GTE register write and the reverse, and COP2 instructions in delay slots.
- `swallow-recompile --split-dir WORK_DIR` translates every game function of `ACE3.BIN` from a `swallow-split` work directory. Library code is left out by the PsyQ report's object ranges, and each function is analysed on its own, so a refused function is listed with its reason instead of stopping the run.
- Measured on Japanese Disc 1:
  - All 1,286 game functions translate, 121,172 instructions, with none refused and no instruction left as an "unsupported" trap. 727 `jal` sites call library code and trap until the runtime implements it.
  - The 9.7 MB module compiles without warnings under `-Wall -Wextra` with GCC 13.3 and Clang 18.1. Its `ulz_decompress` matches the project decoder on all 2,256 Ulz blobs.
  - The summary is `docs/reports/recompile-jp1.json`.
- Added `swallow_tools.recompcheck` and the `swallow-recompcheck` CLI. It calls every translated function natively and in the interpreter from the same state, with shaped random arguments, a seeded heap, and a random GTE state, and compares registers, HI/LO, GTE registers, all writable memory, and write extents.
  - On Japanese Disc 1, 32 trials per function (41,152 in all): 9,024 matching returns, 28,501 agreeing stops, 3,570 that reached library code, 57 that hit the interpreter's step limit, and no mismatch. 469 functions returned at least once. Recorded in `docs/reports/recompcheck-jp1.json`.
  - A build with a broken arithmetic shift gave 66 mismatches in 4 trials per function.
- The native self-test has two more programs (unaligned accesses at every misalignment, and every kind of COP2 access with a run of commands) and five more cases, 23 in all. Each case now also compares the GTE registers. It also compares the C GTE with the Python model on 200 command words over 200 random states, on 20,000 points of the perspective divider, and on which words each refuses. A wrong rounding constant in the divider passed the random states and failed the sweep.
- `inventory.parse_text` records each alternate entry's address.
- Added 13 synthetic Python tests (315 total).

## 0.13.0 — 2026-11-06

- Added `swallow_tools.recompile` and the `swallow-recompile` CLI, the recompiler prototype. It translates original MIPS functions to C that runs against a guest register file and guest memory. It writes the C, a CMake project, and a report without code to a new directory outside the repository, since the C is derived from the game. How it works:
  - Code is found by following control flow from the entry points, so data inside the text, such as a jump table, is never translated.
  - A delay slot runs after the branch condition is evaluated and before the transfer. A slot that is also a branch target is emitted a second time under its own label.
  - A static check proves that no instruction uses a register in the delay slot of its load, on every path, before loads are allowed to take effect at once.
  - Overflowing `add`/`addi`/`sub`, `break`, `syscall`, unknown jump targets, a spent branch budget, and instructions not yet translated all trap at run time.
- Added the C runtime core in `runtime/`: CMake 3.21 or later, C11, built with warnings as errors on GCC, Clang, and MSVC.
  - Guest memory has the interpreter's region contract.
  - A trap returns to `swallow_call` with a message, and the CPU stays usable afterwards.
  - Division helpers return the R3000's documented results where C's `/` is undefined.
- Added `swallow_tools.native`, which drives a built module through ctypes with every argument and result type declared.
- `swallow-ulzcheck --native LIBRARY` runs the translated routine on every blob, with the same regions and the same five checks. It refuses a library translated from a different executable. The report moves to schema 2 with an `oracle` field, and instruction counts are `null` in native mode.
- Measured on all four references:
  - `ulz_decompress` translates to 262 instructions, with nothing left untranslated.
  - Built natively, it matches the project decoder on all 2,256 blobs on each Japanese disc.
  - On English-labeled Discs 1 and 2 it reproduces the interpreter's 423 and 361 overruns. The counts are identical, and so are the listed sites.
  - On Japanese Disc 1 the native check takes 24 seconds, against about 8.5 minutes in the interpreter.
  - The generated C compiles without warnings under `-Wall -Wextra` with GCC 13.3 and Clang 18.1.
  - The reports are `docs/reports/ulz-routine-native-*.json`. The interpreter reports were regenerated in schema 2.
- Added `swallow_tools.selftest`: ten synthetic programs, 18 cases, translated at build time and compared with the interpreter. The CTest targets run them natively (`selftest`) and through ctypes (`native_selftest`), next to the core unit tests (`core`). A deliberately broken translation, with the slot run before the branch condition, failed two of the self-test's checks.
- CI gains a `runtime` job that builds `runtime/` and runs CTest with GCC and Clang on Ubuntu and with MSVC on Windows.
- `docs/reconstruction-plan.md` records the owner's decisions of 2026-11-04 on D1 (recompile, then rewrite), D5 (CMake and C11), and D6 (evaluate PCSX-Redux and DuckStation first). `docs/recompiler.md` documents the translation, the runtime, and the results.
- `.gitignore` covers native build outputs.
- Added 20 synthetic Python tests (302 total).

## 0.12.0 — 2026-11-05

- `swallow_tools.mips` now runs every instruction in the game's code except `break`. It adds `mult`, `multu`, `div`, `divu`, `mfhi`, `mflo`, `mthi`, `mtlo`, the unaligned `lwl`, `lwr`, `swl`, `swr`, and the coprocessor 2 moves and loads. An `lwr` right after an `lwl` into the same register from the same base merges into the in-flight value, as the hardware does. `call` takes and returns HI, LO, and GTE state; `RunResult` keeps its two-field construction.
- `break`, `syscall`, and `add`/`addi`/`sub` overflow now raise `MipsTrap`, a subclass of `MipsError` that carries the address, mnemonic, reason, and code. A trap is a result of the original code, distinct from an interpreter limitation.
- The interpreter refuses four timing cases instead of modelling them: HI/LO writes within two instructions of `mfhi`/`mflo`, a GTE command within two instructions of a GTE register write, a GTE write right after a command, and any COP2 instruction in a branch delay slot. Measured on Japanese Disc 1, game code contains none of them.
- Added `swallow_tools.gte`, a model of the GTE registers and the 15 commands game code issues, with 44-bit MAC checks at every addition, FLAG and its bit-31 mask, the SXY, SZ, and RGB FIFOs, and the perspective division. The reciprocal table is generated from its published formula. Every rule is documentation-derived until a capture checks it; `docs/mips-interpreter.md` says where each comes from.
- Added `swallow_tools.oracle`, the calling contract for compiled game code. It stages an executable by content hash (moved from `swallow-ulzcheck`), reads code ranges and `gp_value` from a splat config, finds crt0's `$gp`, and maps code, data, RAM, and scratchpad. On Japanese Disc 1, crt0's `$gp` equals the configs' value for both executables. `ACE3.BIN`'s `func_80036130`, run under the oracle for all 256 counter values, matched a direct Python calculation 256 times.
- Moved the test assembler to `swallow_tools.mipsasm`, extended with the new instructions and GTE command words.
- `swallow-inventory` report schema 2 adds the GTE commands by decoded fields, division guards, unguarded divisions, and refused timing cases. Re-measured on Japanese Disc 1: `break` is the only game-code instruction the interpreter does not run. All 470 game-code `break`s are GCC division guards, 236 zero-divisor and 234 overflow guards, which closes v0.11.0's hypothesis. Twelve hand-written divisions in `func_80099894` and `func_8009A944` have no guard, and their results are used. Game code uses 21 GTE command encodings.
- `swallow-ulzcheck` still matches all 2,256 Ulz blobs on Japanese Disc 1.
- Added 60 synthetic tests (282 total).

## 0.11.0 — 2026-11-03

- Added `swallow_tools.inventory` and the `swallow-inventory` CLI. It reads a `swallow-split` work directory, never the disc, and reports per unit:
  - indirect calls, computed jumps, and the jump tables that resolve them
  - address-taken functions and function-pointer tables
  - callbacks registered with the PsyQ libraries
  - BIOS stubs and their call numbers
  - `0x1F80xxxx` addresses by class
  - instructions outside the interpreter's set

  It tells library from game code by blocks of placed PsyQ objects; on Japanese Disc 1 those blocks are exactly the documented library ranges.
- Measured on Japanese Disc 1, in `docs/reports/code-inventory-jp1.json` and `docs/code-inventory.md`. Game code holds 2,265 functions and 206,643 instructions across the boot program, `ACE3.BIN`, and the overlays, with 77 `jalr`. Every computed `jr` in game code has a jump table except `ulz_decompress`'s version dispatch. Game code registers five callbacks: the CD driver's ready and sync handlers and two MDEC handlers. It names only scratchpad addresses; I/O registers appear only in library code. It holds 3,948 GTE, 2,324 multiply/divide, 1,002 unaligned-access, and 470 trap instructions that the interpreter does not run yet.
- `swallow_tools.mips` now exports `SUPPORTED_MNEMONICS`, checked by a test against what its decoder accepts.
- Added 10 synthetic tests (222 total).

## 0.10.0 — 2026-11-03

- Traced how `ACE3.BIN` loads its overlays. `archive_open` (`0x8002AE0C`) finds `ACE.BPH`, `ACE.SPH`, `ACE.BPB`, and `ACE.SPB`, reads both indexes, checks `AC3E` and BPH word `+8` against `0x427`, and adds each data file's start sector to its index records. `archive_read_entry` queues reads of entry *id*, and an id with bit `0x800` selects `ACE.SPH`. `ace3_module_step` (`0x80026350`) runs a nine-record module table at `0x800B0134`, each record holding a handler table, overlay id, base, and end. It loads a module's overlay before calling its handlers. The functions and tables are named in `symbol_addrs.ace3.txt`.
- BPH entries 1–4 are code overlays, all loaded whole at `0x800CBFE8`, the end of `ACE3.BIN`'s BSS. Four checks agree: the module records; each entry's own `jal` targets (748 of 768 and 1,841 of 1,877 land on function entries in entries 1 and 2); each handler table pointing into its own overlay; and each overlay's code ending exactly at its handler table. Documented in `docs/overlays.md`. This replaces the overlay hypothesis in `docs/bpb-format.md`.
- `swallow-split` now also disassembles the overlays. `splat/manifest.json` is schema 2 and lists each overlay with its size and SHA256. The tool reads the BPH index through the ISO directory, hashes only entries of a listed size, and needs exactly one match per overlay config. Configs `ovl1.yaml`–`ovl4.yaml` load at `0x800CBFE8` and also use `ACE3.BIN`'s symbols. The split report is schema 2, with an `overlays` list.
- Split all four references with these configs: 271, 645, 20, and 20 overlay functions, 956 in all, with no undecodable words, and `ACE3.BIN` still at 1,715 functions. The overlays contain no `$gp`-relative instructions. Entries 1, 2, and 4 are byte-identical on all four references. Entry 3 differs between the Japanese and English-labeled discs in 117,943 bytes, all before its code, so `ovl3.yaml` accepts both hashes. This corrects the earlier record that entries 1–4 were identical everywhere.
- Ran the Ulz routine check on the other three references. On Japanese Disc 2 all 2,256 blobs match, as on Disc 1. On the English-labeled discs the failures are exactly the version 0 blobs that leave spare zero flag bits after the declared size, 423 and 361 of them; every other blob matches. The routine reads those bits as copies and writes past the declared size, but the bytes up to that size still equal `ulz.py`'s output. Recorded in `docs/reports/ulz-routine-{jp2,en1,en2}.json` and `docs/ulz-routine.md`.
- `swallow-psyq --update-symbols` and `swallow-ulzcheck` now look only at executable configs in the splat manifest.
- BPH `unknown_08` (`0x427` on all four references) is checked by the game; the field keeps its neutral name. `archive_find_files` uses `lwl`/`lwr`/`swl`/`swr`, which the MIPS interpreter does not implement yet.
- Added 5 synthetic tests (212 total).

## 0.9.0 — 2026-11-01

- Located the game's own Ulz decompressor, `ulz_decompress` at `0x800AFD08` in `ACE3.BIN`. It is hand-written assembly after the PsyQ libraries, outside the v0.8.0 game code range, and never compares the `Ulz` magic, so it was found by its decoder shape. Named it, its version jump table, its version 0, 1 and 2 paths, and two unreferenced stream entry points in the hand-written section of `splat/symbol_addrs.ace3.txt`; splat's function counts are unchanged.
- Added `swallow_tools.mips`, a strict interpreter for the MIPS I integer instructions with R3000 branch and load delay slots. It stops on anything it does not model: unknown instructions, unmapped or misaligned access, writes to read-only memory, overflow traps, branches in delay slots, and load-delay hazards.
- Added `swallow_tools.ulzcheck` and the `swallow-ulzcheck` CLI. It runs the routine from the disc's `ACE3.BIN`, found through the splat manifest and symbol file, on every Ulz blob the BPB walker reaches. It maps only the text, the blob, and a guarded destination, and compares the output bytes, end pointer, write extent, and flag, literal, and pair consumption with `ulz.py`. It exits `0` when all match, `1` on any disagreement, and `2` on input errors.
- Measured on Japanese Disc 1: all 2,256 Ulz blobs (1,167 version 0, 1,089 version 2) match, with no mismatches or errors on either side, in 643,716,825 interpreted instructions. Recorded in `docs/reports/ulz-routine-jp1.json`. The other three references were not run.
- The routine settles open Ulz questions. Version 0 stops at the zero flag word and never reads the declared size. A `0x80000000` word carries no flags and is skipped. Version 2 reads literals as aligned words and finishes a copy that crosses the size. A version 1 path in 16-bit units exists, and no blob uses it. `ulz.py` is unchanged. The English-labeled version 0 blobs with spare flag bits are predicted to overrun; this is not yet measured.
- BPB entries 1–4 hold 280, 981, 47, and 50 `jal` words that land on `ACE3.BIN` function starts, against 16 in all other stored entries, and entries 1 and 2 call `ulz_decompress` 13 times after comparing the magic. This is the first direct evidence that they contain code linked against `ACE3.BIN`; no load address or layout is decoded.
- Added 37 synthetic tests (199 total), using only project-written MIPS programs. Documented the routine, the check, and the results in `docs/ulz-routine.md`.

## 0.8.0 — 2026-10-27

- Added `swallow_tools.psyq` and the `swallow-psyq` CLI. It finds Sony PsyQ library objects in each disc executable by signature, using a separate, uncommitted copy of the signature data from `lab313ru/psx_psyq_signatures` (pinned commit `e9e46e7`). A signature counts only when it pins at least 32 bytes and matches once, word-aligned, counting overlapping matches. The report holds names, addresses, and sizes, never game bytes.
- Measured on all four references: 88 library objects (69,284 bytes, 88% of the text) in the boot executable and 118 (82,348 bytes) in `ACE3.BIN`. `ACE3.BIN`'s own game code is `0x800257e8–0x8009b910` (483,624 bytes, 1,284 functions). The boot libraries all match PsyQ 4.3. The `ACE3.BIN` libraries match 4.5, except LIBPRESS, which matches 4.6 and 4.7. Recorded in `docs/reports/psyq-jp1.json`. The other three discs give the same result.
- `--update-symbols` writes the library names into a generated section of the splat symbol files, below the hand-written entries. With them, splat names 237 of 364 boot functions and 286 of 1,715 `ACE3.BIN` functions.
- The three `Ps` words that splat cannot decode are the first bytes of Sony objects (LIBAPI `C114.OBJ` and LIBGTE `MSC00.OBJ`), so they are library code. Removed the `ace3_ps_marker_800AF2F8` data symbol, since that word is now known to open `C114.OBJ`, ahead of `_96_remove`.
- Added 8 synthetic tests (162 total). Documented the method and results in `docs/psyq-libraries.md`.

## 0.7.0 — 2026-10-21

- Adopted splat (owner choice, 2026-10-21) as the MIPS disassembler, pinned with spimdisasm 1.42.4 and rabbitizer 1.16.2 in `splat/requirements.txt` as an optional tooling dependency. Added splat configs and symbol files for the boot executable and `ACE3.BIN`, with rodata, text, and data boundaries measured from the instructions.
- Added `swallow_tools.split` and the `swallow-split` CLI. It matches executables to configs by SHA256, extracts them to a work directory outside the repository, runs splat, and writes a count report. Generated assembly is never committed.
- `swallow-split` fails, exiting 2, before it writes anything if any of these are true: an executable has no config, a config has no executable, a manifest name is not a portable bare file name, two names differ only by case, or the work directory is inside the repository or not empty. A relative `--python` path is resolved from the current directory.
- Measured on all four references: 363 functions in the boot text and 1,713 in `ACE3.BIN`, with every undecodable word accounted for. Both entry points follow the PsyQ crt0 pattern; the symbol files name them, their BSS ranges, and their main functions (`0x801233e8`, `0x800261a8`). English-labeled Disc 1's `ACE3.BIN` differs by one data byte, and the code is the same on every disc.
- Added 21 synthetic tests (154 total), the splat ones using a fake splat, so CI still needs no third-party packages. Documented the pipeline in `docs/disassembly.md`.

## 0.6.0 — 2026-10-19

- Added a VS Code and Antigravity editor setup: debug configurations for the disc, BPH, BPB, compare, and validation tools; a committed test env file so Test Explorer works on a fresh clone; a shared `[tool.pyright]` configuration for Pylance and basedpyright; LF and final-newline file settings; `TEMP`/`TMP` on `G:` for debug, test, and Windows terminal runs; and a `tests/__init__.py` so root-level (`-t .`) discovery works. See `docs/editor-setup.md`.
- Added a read-only PS-X EXE header reader and `swallow-exe` CLI (`swallow_tools.exe`). It resolves the boot file from `SYSTEM.CNF`, finds every other executable by its magic rather than its name, validates the header against the file size and the 2 MiB RAM map, and reports hashes, text ranges, uninterpreted fields, and `\NAME;n` path strings that name files on the same disc. Form 2 files are listed and skipped.
- Found that `ACE3.BIN` is a second PS-X EXE (entry `0x8002572c`, text `0x80010000–0x800bd000`) on all four references, which withdraws the earlier overlay-header reading of its first words. The boot executable contains `\ACE3.BIN;1`, and `ACE3.BIN` names all four archive files. Both are recorded as evidence, not as proof of load order.
- Added `scripts/validate_exe_references.py`, which checks whole-image and per-executable hashes against the baselines. Added 21 synthetic tests and documented the contract in `docs/exe-format.md`.
- Owner direction (recorded 2026-10-13): the C language is permitted and the game engine is C; Python stays limited to offline tooling. The `C:` drive must not be used; all work and data stay on `G:`. AGENTS.md and the README replace the earlier "no C" wording, which had been misread as a language ban.
- Expanded the Team NEMO (Project NEMO) acknowledgment in CREDITS.md and the README, sourced from Romhacking.net: the full crew with credited roles, and esperknight's decompression work noted as prior art. The provenance of the English-labeled discs is still unverified.
- Planned the executable assembly track ([next task](docs/next-task-executable.md)): recover flight and gameplay behavior 1:1 from the original code, starting by matching the game's own Ulz routine.
- The Ulz decoder now rejects offset bit counts outside the measured 10–13, and BPB reports list bit-31-clear BPH records with their raw words instead of omitting them. All 9,018 reference blobs still decode; synthetic tests grow from 111 to 112.
- Recorded two leads for that track in `docs/bpb-format.md`: entries 1–4 hold hundreds of words in the PlayStation RAM range and may be overlays, and four opaque leaves contain an embedded Ulz signature. Both are unverified hypotheses.

## 0.5.0 — 2026-10-16

- Added a read-only PlayStation TIM validator (`swallow_tools.tim`) that checks the id word, flags, CLUT block, pixel block, block lengths, and VRAM placement of one byte range and returns metadata only; pixels are never copied or written.
- The BPB walker now reports an opaque leaf that validates as TIM with kind `tim` and its metadata. Candidates that start with the TIM id word but fail validation stay `opaque` with the recorded rejection reason. Up to three zero bytes after the pixel block are accepted, and reported, only in a non-final container item whose next aligned offset proves they are padding. The full `swallow-bpb` report is now schema version 2.
- Measured on all four references with matching baseline hashes: 18,264 validated TIM leaves per Japanese image and 18,262 per English-labeled image (4bpp, 8bpp, and eight 24bpp images; 128 with two bytes of alignment padding), 18 rejected candidates per image recorded as counterexamples, and zero Ulz errors.
- `scripts/validate_bpb_references.py` reports TIM counts, modes, rejection reasons, and a measured header-word profile of the still-unexplained `00002311` leaves; summary schema version 2.
- Expanded synthetic tests from 95 to 111; documented the TIM contract, counterexamples, and the open `00002311` hypothesis in `docs/tim-format.md`.

## 0.4.0 — 2026-10-15

- Added a read-only nested `ACE.BPB` container walker and `swallow-bpb` CLI that classifies each BPH entry payload as an offset-table container, a Ulz leaf, or an opaque leaf, recursively, with depth/node/size limits; every node, empty items included, is reserved before it is built, so nested tables cannot exceed the cap. BPH and the walker share one archive lookup that rejects overlapping BPH/BPB extents.
- Added a validated Namco Ulz decompressor for versions 0 and 2 with exact flag, literal, and pair stream accounting; every exhausted stream, early back-reference, overrun, missing version 0 terminator, unvalidated sentinel-only word, or nonzero leftover final-word flag is an error.
- Walked all 574 entries on each of four references with decoding: 570 containers and 4 opaque entries per image, 9,018 Ulz blobs decoded with zero errors, and compact summaries recorded under `docs/reports/`.
- Added `scripts/validate_bpb_references.py` for the hash-checked full walk; it verifies whole-image identity and fails on any Ulz decode error. Expanded synthetic tests from 53 to 95.
- Documented the container and Ulz contracts, the measured unknowns, and the next classification targets.

## 0.3.0 — 2026-10-12

- Added a typed, read-only `ACE.BPH` index parser and `swallow-bph` CLI, using ISO-discovered extents and explicit payload sizes.
- Added bounded logical Form 1 reads, normal-range validation, lossless unknown fields, and explicit uninterpreted special records.
- Validated all 574 entries on each of four supplied references; recorded complete index reconstruction, baseline hashes, and measured zero-filled alignment gaps.
- Expanded synthetic tests from 28 to 53 and documented reproducible local-disc validation.
- Added both full Japanese source archives through Git LFS with verified archive/member hashes.
- Reworked the README with original Electrosphere-style artwork and the complete playable open-source AC3 goal, while clearly identifying the current pre-alpha toolchain stage.
- Recorded the owner's no-C language requirement and G: workspace/data constraint.

## 0.2.0 — 2026-10-12

- Added verified Japanese Disc 2 and fingerprinted both English-labeled local references.
- Added content-addressed profile recognition, supported logical file hashes, and `swallow-compare`.
- Added explicit unsupported Form 2/multi-extent results and strict CUE-reference validation.
- Recorded four complete image inventories and three per-file comparisons.
- Added local-agent instructions, a bounded archive-parser task, and a VS Code workspace.
- Expanded synthetic tests from 3 to 28; defined Windows/Linux CI with current documented checkout/setup-python major versions.
- Supplied separately validated CUE filename fixes; left original BIN/CUE files unchanged.

## 0.1.0

Initial read-only directory inspector, Disc 1 inventory, MIT license, three tests, and project roadmap.
