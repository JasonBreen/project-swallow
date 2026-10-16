# Project Swallow roadmap

## 0. Reproducible input baseline — implemented

Inventory four local images; verify the Japanese pair against published fingerprints; retain separate English-labeled references; compare supported file contents; preserve original inputs; run synthetic tests. Exact results and limits are in `disc-inventory.md` and `validation.md`.

## 1. Resource toolchain — in progress

The read-only `ACE.BPH` index parser is implemented in tooling v0.3.0 and validated against all four references. It resolves entry ranges within `ACE.BPB`, preserves uncertain fields, and rejects malformed normal layouts. See [the format contract](bph-format.md) and [validation results](bph-validation.md).

Tooling v0.4.0 adds the read-only nested BPB container walker and a validated Namco Ulz (versions 0 and 2) decompressor, measured against every entry of all four references. See [the container contract](bpb-format.md) and [validation results](bpb-validation.md).

Next, classify the opaque leaves that the walker exposes (TIM image candidates, the four top-level entries that may be overlays, and the decoded containers) from measured examples, and implement a bounded resource interface for understood formats. Use local-only extraction outputs and reproducible metadata manifests. The project goal is a fully playable open-source AC3 reconstruction, and these tools build its resource foundation. The game's own Ulz routine has not yet been matched; that is the first goal of the executable track below.

Separately investigate `ACE.SPH` plus the Form 2 media in `ACE.SPB`. Preserve file/channel identifiers, timing, interleave, and payload boundaries. Do not route streaming data through the Form 1-only file helper.

## 2. Recover original behavior and inspect assets

This is the executable assembly track: [next task](next-task-executable.md). It covers disassembling the boot EXE and `ACE3.BIN`, identifying PsyQ library code, and documenting game routines in annotated assembly, starting with the Ulz decompressor and the BPB loading path. The flight model and gameplay are recovered **1:1 from the original code**, never rebuilt or approximated. The engine is written in C. The form of the recovered code (annotated-asm reference or matching decompilation) is an owner decision recorded in that task. Emulator captures are the behavioral reference.

## 3. One end-to-end mission

One aircraft, a verified level, original-behavior reference captures, controls, camera, objectives, weapons, results, and representative dialogue. Add the artist's first enhanced asset and team-recorded voice samples. Track unresolved mismatches; a screenshot or moving aircraft is not a completed mission.

## 4. Campaign and presentation

Validate mission transitions, campaign branching, disc-specific data, saves, subtitle timing, human voice casting/recording, UI, original/enhanced presentation options, and accessibility. Preserve clean voice masters separately from generated radio effects. Do not record the entire dub against an unversioned script.

## 5. Platform and release validation

Windows and Linux are target platforms. Controller/TV presentation and SteamOS testing follow a stable playable build. Steam Frame/VR integration is a later experiment, not an initial dependency. Publish reproducible tooling builds and test evidence with clear implemented-versus-planned status.
