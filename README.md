<div align="center">

![Project Swallow — Ace Combat 3: Electrosphere, rebuilt for a new generation](docs/assets/project-swallow-banner.svg)

**A complete, playable, open-source reconstruction of ACE COMBAT 3: ELECTROSPHERE.**

*The full Japanese campaign. The original flight model, recovered 1:1 from the game's own code. A new way into the Electrosphere.*

![Development: pre-alpha](https://img.shields.io/badge/development-pre--alpha-65e5f2?style=flat-square&labelColor=101b2d) [![Code license: MIT](https://img.shields.io/badge/original_code-MIT-b9a6ff?style=flat-square&labelColor=101b2d)](LICENSE) · [Windows / Linux CI](https://github.com/JasonBreen/project-swallow/actions/workflows/tests.yml)

[The vision](#enter-the-electrosphere) · [Current build](#development-status) · [Get started](#run-the-toolchain) · [Roadmap](docs/roadmap.md) · [Contribute](CONTRIBUTING.md)

</div>

## Enter the Electrosphere

Project Swallow is building **Ace Combat 3: Electrosphere as a fully playable open-source game for modern PCs**. It preserves the complete Japanese two-disc experience: the branching campaign, characters, missions, and original flight behavior.

AC3's world of corporate power, fractured loyalties, and digital identity deserves to be experienced as a complete game. Swallow aims to deliver that on Windows and Linux, with enhanced artwork, modern presentation, and an English dub performed by the project team.

**Original behavior is the reference, not an inspiration.** The flight model and gameplay will be recovered one-to-one from AC3's own code through reverse engineering of the original executable; that work is planned and has not started. They will not be rebuilt, approximated, or replaced with generic flight physics.

## How it is built

- **Engine: C.** The game engine is written in C.
- **Reverse engineering: MIPS assembly.** The original PlayStation executable and overlays are disassembled and documented. The recovered routines are the ground truth for the engine.
- **Tooling: Python, kept minimal.** Disc, archive, and validation tools are standard-library Python. Python is not used for the engine itself.
- **Evidence first.** No format field or behavior is marked as decoded until bytes, code, or emulator captures confirm it. Hypotheses and counterexamples are written down.

## Development status

**Pre-alpha · toolchain v0.5.0 · no playable build yet.**

There is currently no engine, renderer, playable mission, or recovered gameplay code. The work so far builds reliable access to the game's data, so the reverse engineering can begin from solid ground.

| Area | Working now | Destination |
|---|---|---|
| Source discs | Four fingerprinted references; complete archives stored with Git LFS in this private repository | Reproducible inputs for the complete Japanese campaign |
| Disc access | ISO directory inspection, raw-image hashes, bounded Form 1 reads, logical-file comparison | Reliable access to game resources |
| `ACE.BPH` index | Typed parser with explicit payload sizes and range checks; validated on all four references | Resource loading for the engine |
| `ACE.BPB` contents | Nested DOT1 offset tables walked, Namco **Ulz** (v0/v2) decoded in memory with exact stream accounting, and PlayStation TIM image leaves validated to metadata on all four references; other leaf formats still uninterpreted | Decoded models, textures, and data for the engine |
| Executable | PS-X EXE and `ACE3.BIN` identified; assembly track [planned](docs/next-task-executable.md) | Annotated original routines: Ulz, loaders, flight model, missions |
| Streaming media | Form 2 detected and kept outside the Form 1 reader | Correct XA audio/video, timing, and interleave |
| Gameplay and campaign | Not started; to be recovered from the original code | Playable missions, branching routes, transitions, and saves |
| English voices | Human-performed dub planned | A complete, credited team recording |

## Run the toolchain

You need Python **3.11 or newer**. On the owner's Windows machine, **everything stays on `G:`**: the checkout, extracted discs, temporary files, and reports. Do not use the `C:` drive for this project.

The repository is private. It contains all four owner-supplied reference archives through Git LFS, about **1.67 GB compressed**. For a source-only checkout:

```powershell
Set-Location G:\
$env:GIT_LFS_SKIP_SMUDGE = "1"
git clone https://github.com/JasonBreen/project-swallow.git Project-Swallow
Remove-Item Env:\GIT_LFS_SKIP_SMUDGE
Set-Location G:\Project-Swallow
```

To retrieve the archives, run `git lfs install` and then `git lfs pull`. See the [reference-disc inventory](discs/README.md) for hashes and extraction commands. The tools read extracted raw MODE2/2352 BIN images and never modify them.

From the checkout, inspect a Japanese disc, decode its BPH index, and walk one BPB entry:

```powershell
$env:PYTHONPATH = "src"
$env:TEMP = "G:\ProjectSwallow-data\tmp"
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force $env:TEMP | Out-Null

$disc = "G:\ProjectSwallow-data\japanese-disc1\Ace Combat 3 - Electrosphere (Japan) (Disc 1).bin"
python -m swallow_tools $disc --hash --file-hashes --json   # disc inventory and hashes
python -m swallow_tools.bph $disc --json                    # ACE.BPH index
python -m swallow_tools.bpb $disc --entry 5 --decode --tree # one ACE.BPB entry, Ulz decoded in memory
python -m unittest discover -s tests -v
```

On Linux, use the same modules with `PYTHONPATH=src`. `python -m swallow_tools.compare A.bin B.bin --json` compares two images file by file. An optional `python -m pip install -e .` (in a virtual environment on your project drive) installs the `swallow-disc`, `swallow-bph`, `swallow-bpb`, and `swallow-compare` commands.

The tests use synthetic data only. They need no discs, network, emulator, paid model, or GPU.

All reports contain metadata and hashes only; no tool writes extracted assets. Exit codes are `0` for success, `2` for input or format errors, and `3` for failed strict CUE-reference checks. Exact guarantees are in the format contracts: [BPH](docs/bph-format.md) and [BPB/Ulz](docs/bpb-format.md).

## From archives to a playable game

1. **Disc baseline: complete.** Identify the four supplied images, preserve the originals, and compare their contents.
2. **Archive access: in progress.** The BPH index, the BPB structure with Ulz decompression, and TIM image validation are done. Next are the remaining leaf formats, including the `00002311` leaves, and the separate XA streaming reader.
3. **Executable reverse engineering: next.** Disassemble the boot EXE and `ACE3.BIN` and separate out PsyQ library code. Document game routines in annotated assembly, starting with the Ulz decompressor and the BPB loader, then work toward the flight model.
4. **One complete mission in the C engine.** Aircraft, level, the recovered flight model, controls, weapons, objectives, dialogue, and results working together, checked against emulator captures.
5. **The full Electrosphere.** The branching campaign, disc-specific content, saves, enhanced presentation, human English dialogue, and platform validation.

The [roadmap](docs/roadmap.md) tracks these stages. Passing tests establishes tool behavior; a playable mission needs its own runtime evidence.

## Evidence, people, and provenance

The [four-image baseline](docs/disc-inventory.md), [BPH validation](docs/bph-validation.md), [BPB validation](docs/bpb-validation.md), and [machine-readable reports](docs/reports/) record what has been measured. The supplied Japanese BINs match the earlier reference hashes. The English **v0.9b3** names are upload labels, not an independent release certification.

Swallow builds on years of community preservation, translation, and format research. Special thanks go to **[Team NEMO (Project NEMO)](https://www.romhacking.net/community/1189/)**, whose English fan translation and AC3 hacking work, including esperknight's decompression research, have made the full Japanese game accessible to English-speaking players. Their full crew is credited in [CREDITS.md](CREDITS.md), along with everyone else whose work this project relies on. The banner is original project artwork inspired by AC3's interface language and contains no extracted game artwork.

Code, verified format findings, original artwork, and human-performed dialogue are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) and the [local development contract](AGENTS.md). Open the checkout in VS Code or Antigravity for the supplied tests, debug configurations, and Python settings; see the [editor setup](docs/editor-setup.md).

## License

Project Swallow's original source code, tests, documentation, and banner are **MIT-licensed**. Third-party game data and the owner-supplied disc archives keep their existing rights and are excluded from that license. Project Swallow is independent of Bandai Namco.
