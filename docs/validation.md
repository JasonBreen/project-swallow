# Validation record — Project Swallow v0.2.0

Date: 2026-10-12. Local environment: Python 3.13.5 on Linux x86_64.

## Executed locally

- Read all four archives and staged their BIN/CUE members using system libarchive.
- Ran the packaged inspector with `--hash --file-hashes --json` on all four images.
- Measured MD5/SHA1/SHA256 for each complete BIN; matched the Japanese MD5s against the Load Word Team reference.
- Ran three packaged file comparisons: Japanese Disc 1 versus Disc 2, plus Japanese versus English-labeled for each disc.
- Hashed six supported logical Form 1 files per image. Form 2 `ACE.SPB` was explicitly left unverified by the per-file reader, while remaining covered by each whole-image hash.
- Detected both original Japanese CUE filename mismatches and verified two newly generated `.fixed.cue` descriptors with `--strict-cue`.
- Ran `PYTHONPATH=src python -m unittest discover -s tests -v`: **28 tests passed**. Test data is generated synthetically at runtime.

The tests cover profile identity/size validation, content hashing across sector boundaries, unsupported Form 2/multi-extent handling, corrupt-sector and extent errors, moved-file comparison, missing files, CUE path checks, and CLI return codes.

## Not executed / not established

- GitHub Actions has not run; its Windows/Linux and Python-version matrix is configuration only.
- No remote GitHub repository was created or changed by this package preparation.
- No emulator boot, gameplay, save/disc-swap, controller, Windows, SteamOS, Steam Frame, or TV-machine test was performed here.
- No complete raw-sector EDC/ECC validation, generalized ISO compatibility claim, XA decode, archive-entry extraction, renderer, or native game execution.
- No inference that matching executable hashes prove the complete gameplay implementation is understood.
- English patch author/version identity is not independently verified beyond the upload labels.

## Reference sources checked

- Revision fingerprints: https://loadwordteam.com/projects/ace-combat-3-electrosphere-a-complete-fan-translation/
- Sector layouts: https://psx-spx.consoledev.net/cdromformat/
- GitHub workflow guidance: https://docs.github.com/actions/guides/building-and-testing-python
- Action versions used by CI: https://github.com/actions/checkout and https://github.com/actions/setup-python (their current README examples use v7).
