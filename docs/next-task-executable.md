# Next bounded task: executable assembly track

**Status: header reader (v0.6.0), splat disassembly (v0.7.0), and PsyQ library identification (v0.8.0) implemented; the Ulz decompressor is next.**
Recorded on 2026-10-13 after the [BPB/Ulz walker](bpb-format.md) landed;
header evidence updated after the PS-X EXE reader. Read `AGENTS.md` first.

## Owner direction

- The flight model and gameplay are **recovered 1:1 from the game's own code**.
  Nothing is rebuilt, approximated, or replaced with generic physics.
- Expect substantial MIPS assembly work. Both discs' code may be used as references.
- The game engine is written in **C**. Python stays limited to offline tooling.
- All work and data stay on `G:`. Never use the `C:` drive.

## Known evidence

- **`SLPS_020.20`/`SLPS_020.21`:** a PS-X EXE with entry point `0x801255ac`, load address `0x80120000`, and text size `0x75000`. It links PsyQ libraries (`$Id: sys.c … 1998/01/12`, `bios.c`, `intr.c`). Its content is byte-identical on all four discs.
- **`ACE3.BIN`:** 710,656 bytes, identified by `PS-X EXE` magic as a second executable. Its header specifies entry `0x8002572c`, text address `0x80010000`, and text size `0xad000`. The earlier possible-overlay words (`2c570280 00000000 00000180 00d00a00`) are header fields at file offset `0x10`; that hypothesis is withdrawn. It is identical across the Japanese discs and the same on English Disc 2, but differs on English Disc 1. The boot executable contains `\ACE3.BIN;1`; runtime load order remains unverified. See the [EXE contract and measurements](exe-format.md).
- **BPB entries 1–4** contain strings and words like `0x800d1890` and may be overlays (hypothesis in [bpb-format.md](bpb-format.md)).
- **Ulz decoding** is structurally validated against all measured blocks, but has not been compared with the game's own routine. Open question: English-labeled v0 blocks leave reference flags after the declared size. Does the game stop at the size, or overrun? See [bpb-validation.md](bpb-validation.md).

## Deliverables, in order

1. **Header reader — implemented** (Python tooling): `swallow_tools.exe` resolves `BOOT` from `SYSTEM.CNF`, discovers executables by magic, and reports validated headers, hashes, RAM text ranges, and path strings. It preserves uninterpreted fields and has synthetic regression coverage. This completes the header step for both PS-X EXEs; it does not establish how they are loaded at runtime.
2. **Disassembly pipeline — implemented** with splat, which the owner chose on 2026-10-21 (MIT licensed, pinned in `splat/requirements.txt`). `swallow_tools.split` produces annotated assembly with function boundaries, labels, and cross-references for both executables, outside the repository. See [disassembly](disassembly.md).
3. **PsyQ library identification — implemented.** `swallow_tools.psyq` places 88 library objects in the boot executable and 118 in `ACE3.BIN` by signature, and names their functions in the splat symbol files. `ACE3.BIN`'s game code is `0x800257e8–0x8009b910`. See [library identification](psyq-libraries.md).
4. **First game routine: the Ulz decompressor.**
   - Locate it in `ACE3.BIN`'s game code range, for example by finding the `Ulz\x1a` magic comparison and the `>> N` / `+ 3` / `+ 1` pattern.
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
