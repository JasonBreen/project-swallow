# Project Swallow

**AI-assisted AC3 research tooling, a modern presentation target, and a human English voice cast.**

An independent project investigating the complete Japanese two-disc *Ace Combat 3: Electrosphere* experience. The goal is to preserve original gameplay while developing an open toolchain, enhanced presentation, and an English dub performed by the team.

## What exists today — tooling v0.2.0

- Read-only raw MODE2/2352 BIN directory inspection, CUE filename checks, and whole-image MD5/SHA1/SHA256.
- Content-addressed recognition for two verified Japanese reference images and two locally fingerprinted English-labeled uploads.
- Streaming SHA256 of supported logical Form 1 files, plus a file-comparison CLI that ignores sector relocation when comparing content.
- Explicit unsupported results for Form 2 media: no misleading 'identical' verdict based on an absent hash.
- Four metadata inventories, three comparison reports, 28 synthetic tests, a Windows/Linux CI definition, and a VS Code workspace.
- An `AGENTS.md` work contract and a bounded next coding assignment.

**Not implemented yet:** BPB/BPH entry extraction, ULZ decoding, XA stream decoding, model conversion, recovered gameplay execution, a native renderer, a playable mission, or the English dub. No emulator boot/playthrough testing has been performed for this package.

## Run without installing dependencies (Python 3.11+)

PowerShell, from the extracted `project-swallow` directory:

```powershell
$env:PYTHONPATH = "src"
python -m swallow_tools "D:\AC3\disc2.bin" --hash --file-hashes --json
python -m swallow_tools.compare "D:\AC3\jp-disc2.bin" "D:\AC3\english-disc2.bin" --json
python -m unittest discover -s tests -v
```

Linux/macOS:

```bash
PYTHONPATH=src python -m swallow_tools /path/to/disc2.bin --hash --file-hashes --json
PYTHONPATH=src python -m swallow_tools.compare /path/to/jp-disc2.bin /path/to/english-disc2.bin --json
PYTHONPATH=src python -m unittest discover -s tests -v
```

Optional editable install in your own virtual environment: `python -m pip install -e .`. This exposes `swallow-disc` and `swallow-compare`; runtime dependencies are standard-library only. Packaging uses setuptools.

Add `--cue /path/to/game.cue --strict-cue` to the inspector to fail on missing/empty FILE references. Exit 0 means the requested operation completed, 2 means an input/format error, and 3 means strict CUE-reference validation failed. CUE checks do **not** validate multi-track timing or the BIN's association with every CUE track. Comparison exit 0 means a completed comparison, not identical discs.

The tool reads extracted BINs; it does not directly open 7z/tar archives. File-hash reports explicitly distinguish checked and unsupported content.

## Open in VS Code

Open `project-swallow.code-workspace`, select your Python interpreter, and run **Swallow: unit tests** from the task list. Recommended Python/Pylance extensions are included. For the Python Test Explorer, copy `.env.example` to `.env` so it can import the `src` layout. No paid AI service or agent plugin is required to run the tools or tests.

## Sources and baseline

All four uploads have local inventories. Japanese Disc 1 and Disc 2 match the Load Word Team's Revision 1 MD5 fingerprints. The English v0.9b3 label is recorded as upload metadata, not independent release certification.

Read the [measured baseline](docs/disc-inventory.md), [validation record](docs/validation.md), and [machine-readable reports](docs/reports/). Both Japanese source CUEs have a filename mismatch; corrected descriptors are supplied separately without modifying any BIN.

## Next development task

[Decode and validate the BPH index before choosing a rendering stack](docs/first-agent-task.md). Investigate the matching Japanese BPB/BPH pair once, while keeping disc-specific streaming data distinct. See the [roadmap](docs/roadmap.md).

## License and contributions

This repository's original source code, tests, and documentation are MIT-licensed. External game data and third-party work retain their own rights and are not included. The tools consume local input images. Project Swallow is independent of Bandai Namco.

See [CONTRIBUTING.md](CONTRIBUTING.md), [AGENTS.md](AGENTS.md), and [CREDITS.md](CREDITS.md).
