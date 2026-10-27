# Changelog

## 0.8.0 — 2026-10-27

- Added `swallow_tools.psyq` and the `swallow-psyq` CLI. It finds Sony PsyQ library objects in each disc executable by signature, using a separate, uncommitted copy of the signature data from `lab313ru/psx_psyq_signatures` (pinned commit `e9e46e7`). A signature counts only when it pins at least 32 bytes and matches once, word-aligned. The report holds names, addresses, and sizes, never game bytes.
- Measured on all four references: 88 library objects (69,284 bytes, 88% of the text) in the boot executable and 118 (82,348 bytes) in `ACE3.BIN`. `ACE3.BIN`'s own game code is `0x800257e8–0x8009b910` (483,624 bytes, 1,284 functions). The boot libraries all match PsyQ 4.3. The `ACE3.BIN` libraries match 4.5, except LIBPRESS, which matches 4.6 and 4.7. Recorded in `docs/reports/psyq-jp1.json`. The other three discs give the same result.
- `--update-symbols` writes the library names into a generated section of the splat symbol files, below the hand-written entries. With them, splat names 237 of 364 boot functions and 286 of 1,715 `ACE3.BIN` functions.
- The three `Ps` words that splat cannot decode are the first bytes of Sony objects (LIBAPI `C114.OBJ` and LIBGTE `MSC00.OBJ`), so they are library code. Removed the `ace3_ps_marker_800AF2F8` data symbol, since that word is now known to open `C114.OBJ`, ahead of `_96_remove`.
- Added 7 synthetic tests (161 total). Documented the method and results in `docs/psyq-libraries.md`.

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
