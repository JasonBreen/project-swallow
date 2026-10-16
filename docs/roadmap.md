# Project Swallow roadmap

## 0. Reproducible input baseline — implemented

Inventory four local images; verify the Japanese pair against published fingerprints; retain separate English-labeled references; compare supported file contents; preserve original inputs; run synthetic tests. Exact results and limits are in `disc-inventory.md` and `validation.md`.

## 1. Resource toolchain — in progress

The read-only `ACE.BPH` index parser is implemented in tooling v0.3.0 and validated against all four references. It resolves entry ranges within `ACE.BPB`, preserves uncertain fields, and rejects malformed normal layouts. See [the format contract](bph-format.md) and [validation results](bph-validation.md).

Tooling v0.4.0 adds the read-only nested BPB container walker and a validated Namco Ulz (versions 0 and 2) decompressor, measured against every entry of all four references. See [the container contract](bpb-format.md) and [validation results](bpb-validation.md).

Tooling v0.5.0 validates PlayStation TIM image leaves to metadata on all four references and records the `00002311` leaves as a measured hypothesis. See [the TIM contract](tim-format.md).

Next, classify the remaining opaque leaves that the walker exposes (the `00002311` leaves, the four text-like top-level entries, and the other decoded leaf signatures) from measured examples, and implement a bounded resource interface for understood formats. Use local-only extraction outputs and reproducible metadata manifests. The project goal is a fully playable open-source AC3 reconstruction, and these tools build its resource foundation.

Separately investigate `ACE.SPH` plus the Form 2 media in `ACE.SPB`. Preserve file/channel identifiers, timing, interleave, and payload boundaries. Do not route streaming data through the Form 1-only file helper.

## 2. Recover original behavior and inspect assets

Build bounded tools for assets actually understood. Investigate the original executable and data/overlay relationships, using emulation as a reference. Preserve the original flight model rather than substituting unrelated behavior. Decide between executable reuse, translation/recompilation, selective emulation, and recovered native components only after measuring feasibility; no approach is validated by this starter.

## 3. One end-to-end mission

One aircraft, a verified level, original-behavior reference captures, controls, camera, objectives, weapons, results, and representative dialogue. Add the artist's first enhanced asset and team-recorded voice samples. Track unresolved mismatches; a screenshot or moving aircraft is not a completed mission.

## 4. Campaign and presentation

Validate mission transitions, campaign branching, disc-specific data, saves, subtitle timing, human voice casting/recording, UI, original/enhanced presentation options, and accessibility. Preserve clean voice masters separately from generated radio effects. Do not record the entire dub against an unversioned script.

## 5. Platform and release validation

Windows and Linux are target platforms. Controller/TV presentation and SteamOS testing follow a stable playable build. Steam Frame/VR integration is a later experiment, not an initial dependency. Publish reproducible tooling builds and test evidence with clear implemented-versus-planned status.
