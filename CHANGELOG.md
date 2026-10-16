# Changelog

## Unreleased

- Owner direction (recorded 2026-10-13): the C language is permitted and the game engine is C; Python stays limited to offline tooling. The `C:` drive must not be used; all work and data stay on `G:`. AGENTS.md and the README replace the earlier "no C" wording, which had been misread as a language ban.
- Expanded the Team NEMO (Project NEMO) acknowledgment in CREDITS.md and the README, sourced from Romhacking.net: the full crew with credited roles, and esperknight's decompression work noted as prior art. The provenance of the English-labeled discs is still unverified.
- Planned the executable assembly track ([next task](docs/next-task-executable.md)): recover flight and gameplay behavior 1:1 from the original code, starting by matching the game's own Ulz routine.
- Recorded two leads for that track in `docs/bpb-format.md`: entries 1–4 hold hundreds of words in the PlayStation RAM range and may be overlays, and four opaque leaves contain an embedded Ulz signature. Both are unverified hypotheses.

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
