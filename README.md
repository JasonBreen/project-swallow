<div align="center">

![Project Swallow — Ace Combat 3: Electrosphere, rebuilt for a new generation](docs/assets/project-swallow-banner.svg)

**A complete, playable, open-source reconstruction of ACE COMBAT 3: ELECTROSPHERE.**

*The full Japanese campaign. The original flight experience. A new way into the Electrosphere.*

![Development: pre-alpha](https://img.shields.io/badge/development-pre--alpha-65e5f2?style=flat-square&labelColor=101b2d) [![Code license: MIT](https://img.shields.io/badge/original_code-MIT-b9a6ff?style=flat-square&labelColor=101b2d)](LICENSE) · [Windows / Linux CI](https://github.com/JasonBreen/project-swallow/actions/workflows/tests.yml)

[The vision](#enter-the-electrosphere) · [Current build](#development-status) · [Get started](#run-the-toolchain) · [Roadmap](docs/roadmap.md) · [Contribute](CONTRIBUTING.md)

</div>

## Enter the Electrosphere

Project Swallow is building **Ace Combat 3: Electrosphere as a fully playable open-source game for modern PCs**, preserving the complete Japanese two-disc experience: its branching campaign, characters, missions, and original flight behavior.

AC3's world of corporate power, fractured loyalties, and digital identity deserves to be experienced as a complete game. Swallow's destination is that experience on Windows and Linux, with enhanced artwork, modern presentation, and an English dub performed by the project team. Original behavior is the reference throughout development.

The repository begins with the disc and archive tools needed to build that game faithfully. Those tools are the foundation of Swallow; the playable reconstruction is the project.

## Development status

**Pre-alpha · toolchain v0.4.0 · no playable build available yet.**

The game is in development. There is currently no native renderer, playable mission, recovered gameplay runtime, or completed English dub. Windows and Linux are target game platforms; current automated coverage runs the Python tools on both.

| Area | Working now | Destination |
|---|---|---|
| Source discs | Four fingerprinted references; complete archives stored with Git LFS in this private repository | Reproducible inputs for the complete Japanese campaign |
| Disc access | ISO directory inspection, raw-image hashes, bounded Form 1 reads, logical-file comparison | Reliable access to game resources |
| `ACE.BPH` | Typed index parser, explicit payload sizes, range checks, metadata reports; validated against all four references | Resource loading for the game |
| `ACE.BPB` assets | Nested offset-table containers walked and Namco Ulz (v0/v2) decoded in memory with exact stream accounting; leaf formats still uninterpreted | Original assets and enhanced presentation |
| Streaming media | Form 2 detected and kept outside the Form 1 reader | Correct XA audio/video, timing, and interleave |
| Gameplay and campaign | Original behavior established as the reference; runtime work remains ahead | Playable missions, branching routes, transitions, and saves |
| English voices | Human-performed dub planned | A complete, credited team recording |

The toolchain is written in **Python**, with standard-library runtime dependencies. The game's runtime architecture remains an open engineering decision, within the project's **no C** language constraint.

## Run the toolchain

Python **3.11 or newer** is required. On the owner's Windows machine, keep the checkout, extracted discs, and temporary data on **`G:`**.

The repository is currently private and contains all four owner-supplied reference archives through Git LFS. A full clone downloads approximately **1.67 GB of compressed discs** in addition to the source. For a source-only checkout:

```powershell
Set-Location G:\
$env:GIT_LFS_SKIP_SMUDGE = "1"
git clone https://github.com/JasonBreen/project-swallow.git Project-Swallow
Remove-Item Env:\GIT_LFS_SKIP_SMUDGE
Set-Location G:\Project-Swallow
```

To retrieve the full archives, install Git LFS and run:

```powershell
git lfs install
git lfs pull
```

See the [reference-disc inventory](discs/README.md) for archive hashes and extraction commands. The tools read extracted raw MODE2/2352 BIN images. Original archives and images remain unchanged.

From the checkout, inspect a Japanese disc, decode its BPH index, and walk one BPB entry:

```powershell
$env:PYTHONPATH = "src"
$env:TEMP = "G:\ProjectSwallow-data\tmp"
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force $env:TEMP | Out-Null

$disc = "G:\ProjectSwallow-data\japanese-disc1\Ace Combat 3 - Electrosphere (Japan) (Disc 1).bin"
python -m swallow_tools $disc --hash --file-hashes --json
python -m swallow_tools.bph $disc --json
python -m swallow_tools.bpb $disc --entry 5 --decode --tree
python -m unittest discover -s tests -v
```

On Linux:

```bash
PYTHONPATH=src python -m swallow_tools /path/to/disc.bin --hash --file-hashes --json
PYTHONPATH=src python -m swallow_tools.bph /path/to/disc.bin --json
PYTHONPATH=src python -m swallow_tools.bpb /path/to/disc.bin --decode
PYTHONPATH=src python -m swallow_tools.compare /path/to/japanese.bin /path/to/english.bin --json
PYTHONPATH=src python -m unittest discover -s tests -v
```

An optional editable install, `python -m pip install -e .`, exposes `swallow-disc`, `swallow-bph`, `swallow-bpb`, and `swallow-compare`. Use a virtual environment on your project drive. Tests use synthetic data and require no discs, network, emulator, paid model, or GPU.

The BPH report contains metadata and validates normal entry ranges against the ISO-recorded BPB size. It does not extract assets or execute the game. Uninterpreted special records remain explicit. Read the [format contract](docs/bph-format.md) and [validation results](docs/bph-validation.md) for the exact guarantees.

The BPB report classifies each entry as a nested offset-table container, a Ulz compressed leaf, or an opaque leaf, and with `--decode` decompresses Ulz data in memory, reporting sizes, hashes, and exact stream consumption. It never writes game bytes. See the [container contract](docs/bpb-format.md) and [validation results](docs/bpb-validation.md).

For disc inspection, `--cue /path/to/game.cue --strict-cue` checks that referenced filenames exist; it does not validate track timing. Exit codes are `0` for a completed operation, `2` for input/format errors, and `3` for failed strict CUE-reference checks. A completed comparison can report different files.

## From archives to a playable game

1. **Disc baseline — complete.** Identify the four supplied images, preserve originals, and compare supported logical contents.
2. **BPH index — implemented.** Decode and validate the archive's entry ranges; preserve uncertain fields and publish reproducible metadata.
3. **BPB containers and compression — implemented.** Walk nested offset tables and decode Ulz versions 0 and 2 with validated stream accounting on all four references.
4. **Resources and original behavior — next.** Classify the exposed leaf formats; develop a separate XA-aware media reader; establish executable/data behavior with reference captures.
5. **One complete mission.** Aircraft, level, original flight behavior, controls, weapons, objectives, dialogue, and results working together.
6. **The full Electrosphere.** Branching campaign, disc-specific content, saves, enhanced presentation, human English dialogue, and platform validation.

The [roadmap](docs/roadmap.md) tracks these stages. Passing parser tests establishes tool behavior; a playable mission and campaign require their own runtime evidence.

## Evidence, people, and provenance

The [four-image baseline](docs/disc-inventory.md), [BPH validation](docs/bph-validation.md), [BPB/Ulz validation](docs/bpb-validation.md), and [machine-readable reports](docs/reports/) record what has actually been measured. The Japanese BINs supplied for this repository match the earlier reference hashes. The English **v0.9b3** names are recorded as upload labels rather than independent release certification.

Swallow builds on years of community preservation and translation research, acknowledged in [CREDITS.md](CREDITS.md). The banner is original project artwork inspired by AC3's futuristic interface language; it contains no extracted game artwork.

Code, verified format findings, original artwork, and human-performed dialogue are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) and the [local development contract](AGENTS.md). Open the checkout in VS Code or Antigravity for the supplied tests, debug configurations, and Python settings; see the [editor setup](docs/editor-setup.md).

## License

Project Swallow's original source code, tests, documentation, and banner are **MIT-licensed**. Third-party game data and the owner-supplied disc archives retain their existing rights and are excluded from that license. Project Swallow is independent of Bandai Namco.
