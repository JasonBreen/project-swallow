# Next bounded task: executable assembly track

**Status: planned, not started.** Recorded on 2026-10-13 after the
[BPB/Ulz walker](bpb-format.md) landed. Read `AGENTS.md` first.

## Owner direction

- The flight model and gameplay are **recovered 1:1 from the game's own code**.
  Nothing is rebuilt, approximated, or replaced with generic physics.
- Expect substantial MIPS assembly work. Both discs' code may be used as references.
- The game engine is written in **C**. Python stays limited to offline tooling.
- All work and data stay on `G:`. Never use the `C:` drive.

## Known evidence

- **`SLPS_020.20`/`SLPS_020.21`:** a PS-X EXE with entry point `0x801255ac`, load address `0x80120000`, and text size `0x75000`. It links PsyQ libraries (`$Id: sys.c … 1998/01/12`, `bios.c`, `intr.c`). Its content is byte-identical on all four discs.
- **`ACE3.BIN`:** 710,656 bytes. Its first words (`2c570280 00000000 00000180 00d00a00`) look like an overlay header. This is unverified, and no field has been decoded. It is identical across the Japanese discs and the same on English Disc 2, but differs on English Disc 1.
- **BPB entries 1–4** contain strings and words like `0x800d1890` and may be overlays (hypothesis in [bpb-format.md](bpb-format.md)).
- **Ulz decoding** is structurally validated against all measured blocks, but has not been compared with the game's own routine. Open question: English-labeled v0 blocks leave reference flags after the declared size. Does the game stop at the size, or overrun? See [bpb-validation.md](bpb-validation.md).

## Deliverables, in order

1. **Header parsers** (Python tooling): the PS-X EXE header, and an `ACE3.BIN` header *hypothesis*. The load address and entry point must come from evidence, such as code that references the overlay or emulator RAM. Use typed results, raw fields, and synthetic tests, as the BPH/BPB tools do.
2. **Disassembly pipeline** for MIPS R3000. Compare a minimal stdlib disassembler against existing tooling (spimdisasm/splat), and record license and provenance. **Ask the owner before adopting third-party tools.** The output is annotated assembly with function boundaries, labels, and cross-references.
3. **PsyQ library identification** by signature, so library code is separated from game code.
4. **First game routine: the Ulz decompressor.**
   - Locate it, for example by finding the `Ulz\x1a` magic comparison and the `>> N` / `+ 3` / `+ 1` pattern.
   - Document it in annotated asm.
   - Prove that [`ulz.py`](../src/swallow_tools/ulz.py) matches it byte-for-byte. Cross-check with emulator RAM captures where possible.
5. **BPB loading path.** Trace how the code indexes BPH, reads BPB entries, and walks DOT1 slots. Confirm or refute the DOT1 and overlay hypotheses from the code.

## Decision needed before step 2

C is now permitted, so the recovered-code form needs an owner choice:

- **Annotated assembly as the reference**, with engine C written to match measured behavior.
- **Matching decompilation**: C that recompiles to byte-identical instructions. This needs a period-appropriate compiler setup. Its licensing and provenance need review before any adoption.

Either way, the engine's behavior is judged against the original code and
reference captures, not against intuition.

## Out of scope for this task

Writing the engine runtime, renderer, audio/XA, mission logic, the English dub,
or any flight-model code beyond locating and documenting where it lives.
