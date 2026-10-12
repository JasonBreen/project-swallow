# Project Swallow: coding-agent instructions

## Goal and current state
Build a complete, playable, open-source reconstruction of Ace Combat 3: Electrosphere for modern PCs. Preserve the Japanese gameplay and branching campaign, with enhanced artwork and a human-performed English dub. The current implementation is the read-only disc and archive toolchain; a playable game build does not exist yet. Describe the game as the project goal and tooling as its present development stage.

## Work contract
- Work in the current local workspace. Do not spawn cloud sessions or parallel agents without an explicit request.
- On this machine, the owner requires Project Swallow work on `G:`. Use `G:\Project-Swallow` for the checkout and keep temporary disc, LFS, and verification data on `G:`; do not stage project data on `C:`.
- Do not use C for this project. The current toolchain is Python; assess future runtime choices within the owner's language constraint.
- Keep each task bounded: inspect evidence, implement one interface, add synthetic tests, run them, and report exact results and unresolved questions.
- Start from `docs/disc-inventory.md` and the checked-in metadata reports. Disc identity is content-addressed; never infer it from the filename alone.
- Read ISO file extents. Do not hard-code LBAs, file sizes, or disc-specific asset numbers in parsers.
- Shared byte-identical archive/executable content does not prove all game behavior is shared or that a native port is already solved.
- Do not silently read MODE2 Form 2 media as 2048-byte Form 1 data. The current file-hashing API explicitly skips it; XA support is a separate task.
- Preserve original flight behavior as the reference. Do not replace it with generic flight-simulator physics or select an entire engine stack without measured evidence and project approval.
- Never mark an unknown format field as decoded without validation. Record hypotheses and counterexamples.
- The owner explicitly requested full supplied disc archives in this private repository using Git LFS. Track approved archives under `discs/` through LFS, record their hashes, and read source images without modifying them. Keep raw extracts, credentials, and unfinished recordings outside commits. See `discs/README.md` for the current inputs and storage workflow.
- Voices are performed by the project team; voice cloning is not part of the current scope.

## Check before finishing
```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```
On PowerShell set `$env:PYTHONPATH = "src"` first. Tests must run without discs, emulators, network access, paid models, or GPU hardware.

Report implemented features separately from planned features. A passing local test suite is not a GitHub Actions run and not emulator boot validation.
