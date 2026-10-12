# Project Swallow roadmap

## 0. Reproducible input baseline — implemented

Inventory four local images; verify the Japanese pair against published fingerprints; retain separate English-labeled references; compare supported file contents; preserve original inputs; run synthetic tests. Exact results and limits are in `disc-inventory.md` and `validation.md`.

## 1. Archive investigation — next

Implement a validated reader for `ACE.BPH` and its associated `ACE.BPB`. Document field widths, byte order, units, entry counts, special values, and bounds based on evidence. Identify compression instead of assuming every entry is a known format. Use local-only extraction outputs and reproducible metadata manifests.

Separately investigate `ACE.SPH` plus the Form 2 media in `ACE.SPB`. Preserve file/channel identifiers, timing, interleave, and payload boundaries. Do not route streaming data through the Form 1-only file helper.

## 2. Recover original behavior and inspect assets

Build bounded tools for assets actually understood. Investigate the original executable and data/overlay relationships, using emulation as a reference. Preserve the original flight model rather than substituting unrelated behavior. Decide between executable reuse, translation/recompilation, selective emulation, and recovered native components only after measuring feasibility; no approach is validated by this starter.

## 3. One end-to-end mission

One aircraft, a verified level, original-behavior reference captures, controls, camera, objectives, weapons, results, and representative dialogue. Add the artist's first enhanced asset and team-recorded voice samples. Track unresolved mismatches; a screenshot or moving aircraft is not a completed mission.

## 4. Campaign and presentation

Validate mission transitions, campaign branching, disc-specific data, saves, subtitle timing, human voice casting/recording, UI, original/enhanced presentation options, and accessibility. Preserve clean voice masters separately from generated radio effects. Do not record the entire dub against an unversioned script.

## 5. Platform and release validation

Windows and Linux are target platforms. Controller/TV presentation and SteamOS testing follow a stable playable build. Steam Frame/VR integration is a later experiment, not an initial dependency. Publish reproducible tooling builds and test evidence with clear implemented-versus-planned status.
