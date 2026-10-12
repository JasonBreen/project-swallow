# Repository setup provenance

Date: 2026-10-12.

The private repository is `https://github.com/JasonBreen/project-swallow`.
The working checkout is `G:\Project-Swallow`, per the owner's instruction to
keep this project off `C:`. Setup materials are in `G:\ProjectSwallow-setup`.

## Recovered work

The setup continued the earlier chats **Make AC3 English Available** and
**Plan Project Milestone**. The existing
`project-swallow-private-github-setup.zip` was downloaded from the original
chat, rather than recreating the toolkit from conversation summaries.
The package's publisher was inspected and run in `--check-only` mode:
all 34 starter files matched its SHA256 manifest, and all 28 synthetic tests
passed on Windows with Python 3.12.10.

The first commit preserves those 34 original starter files byte for byte.
The following setup commit adds Git LFS and the supplied English disc pair,
and updates the storage guidance to follow the owner's explicit instruction
to include the full discs in the private repository. MIT licensing remains
limited to project-owned work. The original one-time publisher only supported
the unchanged, disc-free starter, so the updated repository is published with
Git and Git LFS directly.

## Scope

**Current update:** the project is a complete, playable open-source AC3
reconstruction in development. Tooling v0.3.0 implements the BPH index reader;
both supplied Japanese archives are now included through Git LFS and their
BINs match the recovered baseline. See [BPH validation](bph-validation.md).
There is no playable build yet. The paragraphs below record the earlier
repository setup stage and are retained as history.

The project remains a research toolkit with a planned human-performed English
dub and enhanced visuals. Its next bounded coding task is the validated
`ACE.BPH` index reader described in `first-agent-task.md`.
This setup does not establish archive decoding, a playable native runtime,
emulator boot, gameplay, or save/disc-switch correctness.

The older `validation.md` describes the original package preparation on
Linux. Current Windows verification and GitHub Actions should be assessed
separately from that historical record. The Japanese reports are recovered
evidence; this setup initially includes only the two newly supplied English
archives, pending the owner's Japanese files.
