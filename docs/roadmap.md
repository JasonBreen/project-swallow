# Project Swallow roadmap

## 0. Reproducible input baseline — implemented

Inventory four local images; verify the Japanese pair against published fingerprints; retain separate English-labeled references; compare supported file contents; preserve original inputs; run synthetic tests. Exact results and limits are in `disc-inventory.md` and `validation.md`.

## 1. Resource toolchain — in progress

The read-only `ACE.BPH` index parser is implemented in tooling v0.3.0 and validated against all four references. It resolves entry ranges within `ACE.BPB`, preserves uncertain fields, and rejects malformed normal layouts. See [the format contract](bph-format.md) and [validation results](bph-validation.md).

Tooling v0.4.0 adds the read-only nested BPB container walker and a validated Namco Ulz (versions 0 and 2) decompressor, measured against every entry of all four references. See [the container contract](bpb-format.md) and [validation results](bpb-validation.md).

Tooling v0.5.0 validates PlayStation TIM image leaves to metadata on all four references and records the `00002311` leaves as a measured hypothesis. See [the TIM contract](tim-format.md).

Tooling v0.6.0 starts the executable track with a PS-X EXE header reader validated on all four references. It finds executables by content and shows that `ACE3.BIN` is a second PS-X EXE with a header text address of `0x80010000`; runtime load order remains unverified. See [the EXE contract](exe-format.md). Tooling v0.7.0 disassembles both executables with splat on all four references; see [disassembly](disassembly.md). Tooling v0.8.0 identifies the PsyQ library code in both and leaves `ACE3.BIN`'s game code at `0x800257e8–0x8009b910`; see [library identification](psyq-libraries.md). Tooling v0.9.0 locates the game's own Ulz routine at `0x800AFD08` and runs it in a strict MIPS interpreter: it matches the project decoder on every Ulz blob on both Japanese discs, and overruns only on English-patch blobs that leave spare flag bits; see [the Ulz routine](ulz-routine.md). Tooling v0.10.0 traces the module loader: BPH entries 1–4 are code overlays loaded at `0x800CBFE8`, which `swallow-split` now disassembles; see [overlays](overlays.md). Tooling v0.11.0 inventories the game code's indirect control flow, callbacks, hardware addresses, and interpreter gaps; see [the code inventory](code-inventory.md). Tooling v0.12.0 completes the interpreter as an oracle: it runs every game-code instruction except the compiler's division traps, with a GTE model, and the inventory shows that all 470 traps are division guards; see [the interpreter](mips-interpreter.md). Tooling v0.13.0 adds the recompiler prototype and the C runtime core: the Ulz routine, translated to C and built natively, matches the project decoder on every Ulz blob on both Japanese discs and reproduces the interpreter's results on the English-labeled ones; see [the recompiler](recompiler.md). Tooling v0.14.0 ports the GTE to the C core and translates all 1,286 of `ACE3.BIN`'s game functions, which agree with the interpreter on 41,152 random calls; calls into the PsyQ libraries still trap until the runtime implements them.

The disc baseline, BPH parser, BPB container walker, Ulz v0/v2 decoder, TIM metadata validator, and PS-X EXE header reader are the implemented read-only tooling baseline. Next, classify the remaining opaque leaves that the walker exposes (including the `00002311` leaves and the overlay entries 1–4, which now show code linked against `ACE3.BIN`) from measured examples, and implement a bounded resource interface for understood formats. Use local-only extraction outputs and reproducible metadata manifests. These tools build the resource foundation; they do not constitute a game runtime. The game's own Ulz routine is checked on all four references: it matches the decoder on both Japanese discs, and on the English-labeled discs it overruns only where the patch's data leaves spare flag bits.

Separately investigate `ACE.SPH` plus the Form 2 media in `ACE.SPB`. Preserve file/channel identifiers, timing, interleave, and payload boundaries. Do not route streaming data through the Form 1-only file helper.

## 2. Recover original behavior and inspect assets

The executable/MIPS recovery track has started: [next task](next-task-executable.md). The boot EXE and `ACE3.BIN` are disassembled with splat, their library code is identified, and the game's Ulz routine is located and checked by execution; next, document the original loaders and gameplay routines, using emulation as a reference. The flight model and gameplay will be recovered from the original code, never rebuilt or approximated with generic physics. The planned game runtime will be written in C; no engine or playable mission exists yet. The form of recovered code and the runtime boundary remain decisions to make from measured evidence.

The [reconstruction plan](reconstruction-plan.md) proposes how the recovered code becomes the C engine: a static recompiler, a runtime that replaces the 159 PsyQ library functions the game calls, and readable C that replaces translated functions once tests prove it equal. The owner approved its decisions D1–D9; D4 and D6 are approved as evaluations, not final picks.

## 3. One end-to-end mission

One aircraft, a verified level, original-behavior reference captures, controls, camera, objectives, weapons, results, and representative dialogue. Add the artist's first enhanced asset and team-recorded voice samples. Track unresolved mismatches; a screenshot or moving aircraft is not a completed mission.

## 4. Campaign and presentation

Validate mission transitions, campaign branching, disc-specific data, saves, subtitle timing, human voice casting/recording, UI, original/enhanced presentation options, and accessibility. Preserve clean voice masters separately from generated radio effects. Do not record the entire dub against an unversioned script.

## 5. Platform and release validation

Windows and Linux are target platforms. Controller/TV presentation and SteamOS testing follow a stable playable build. Steam Frame/VR integration is a later experiment, not an initial dependency. Publish reproducible tooling builds and test evidence with clear implemented-versus-planned status.

## Later: other Ace Combat titles

Owner direction (2026-11-01): after AC3, reconstruct Ace Combat Zero, Ace Combat (1995), and Ace Combat 2 the same way. Ace Combat 04 may not be needed if Sal's work on it is completed. A survey of each title can happen at any time; runtime work waits until AC3 has one native mission. See [other titles](reconstruction-plan.md#other-titles).
