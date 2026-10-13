# Changelog

## 0.4.0 — 2026-10-13

- Added a read-only Namco "Ulz" decoder (`swallow_tools.ulz`) for versions 0 and 2. It enforces strict per-stream bounds and reports where each stream stopped.
- Added a structural BPB walker and the `swallow-bpb` CLI. The walker accepts DOT1 offset tables only when they pass exact checks, decodes Ulz blocks recursively, and leaves everything else explicitly `unclassified`. Reports contain metadata and hashes only.
- Validated against all four references: every Ulz block decodes to its declared size and consumes its streams up to alignment padding. The two Japanese discs produce identical trees for all 574 entries. Agreement with the game's own decompression routine is not yet proven.
- Expanded synthetic tests from 53 to 76, using hand-built Ulz vectors and the measured counterexample shapes.
- Recorded encoder differences on the English-labeled references: v0 flags left over after the declared size are counted as `unused_reference_flags` and not executed; what the game does with them is unverified.
- Owner direction: the C language is permitted and the engine is C; Python is for offline tooling. The `C:` drive must not be used; all work and data stay on `G:`. AGENTS.md and the README were updated, replacing the earlier "no C" wording.
- Expanded the Team NEMO (Project NEMO) acknowledgment in CREDITS.md and the README, sourced from Romhacking.net: the full crew with credited roles, and esperknight's decompression work noted as prior art. The provenance of the English-labeled discs is still unverified.
- Planned the executable assembly track ([next task](docs/next-task-executable.md)): recover flight and gameplay behavior 1:1 from the original code.

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
