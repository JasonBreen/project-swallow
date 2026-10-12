# Changelog

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
