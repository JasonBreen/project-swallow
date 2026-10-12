# First bounded coding assignment: BPH archive-index research

Read `AGENTS.md`, `docs/disc-inventory.md`, and the reports before changing code.

**Goal:** implement and document a read-only BPH index parser with falsifiable validation rules, not a generic binary-to-JSON guesser.

**Known local evidence:** both Japanese `ACE.BPH` files are 4,612 bytes and their complete logical hashes match. Both associated `ACE.BPB` files are 94,976,000 bytes and match. English-labeled BPH files have the same size but different contents and associated BPB sizes. These are useful positive and changed-data cases; no header fields or entry counts have been decoded by the current toolkit.

**Input handling:** discover file extents with the ISO inspector. Read Form 1 data without disc-sector headers. Keep original images unchanged. Any staged source bytes stay in an ignored local directory. Do not hard-code source-image LBAs into the parser.

**Deliverables:** a small typed parser API, a metadata-only CLI report, documented evidence for field layout and addressing units, and synthetic tests. Validate offsets and sizes against the relevant BPB; handle reserved/sentinel entries explicitly. Any uncertain field must remain labeled uncertain.

**Acceptance:** run the full unit suite without disc dependencies; demonstrate parsing locally against both Japanese references and both English-labeled references; account for every index byte or explicitly document why it remains unknown. Return a meaningful nonzero error on malformed input. Do not claim asset semantics based only on file sizes or repeated byte patterns.

**Out of scope:** writing a full engine, replacing flight physics, extracting the entire soundtrack, parallel cloud-agent sessions, or building the English dub. Those need separate tasks after this parser is grounded.
