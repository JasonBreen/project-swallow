# Next bounded task: executable assembly track

**Status: header reader (v0.6.0), splat disassembly (v0.7.0), PsyQ library identification (v0.8.0), the game's Ulz routine (v0.9.0), and the overlay loader (v0.10.0) implemented; the rest of the BPB loading path is next.**
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
- **BPB entries 1–4** contain strings, words like `0x800d1890`, and hundreds of `jal` words that land exactly on `ACE3.BIN` function starts, including 13 calls to `ulz_decompress` in entries 1 and 2. They are code overlays: `ACE3.BIN`'s module loader reads entry *k* to `0x800CBFE8`, the end of its BSS. See [overlays](overlays.md).
- **Ulz decoding** matches the game's own routine on every Ulz blob on both Japanese discs. The routine stops version 0 on the zero flag word and never reads the declared size, so on the English-labeled discs it overruns on exactly the 423 and 361 version 0 blocks that leave flags after the declared size; up to that size its output still equals `ulz.py`'s. See [the Ulz routine](ulz-routine.md#results).

## Deliverables, in order

1. **Header reader — implemented** (Python tooling): `swallow_tools.exe` resolves `BOOT` from `SYSTEM.CNF`, discovers executables by magic, and reports validated headers, hashes, RAM text ranges, and path strings. It preserves uninterpreted fields and has synthetic regression coverage. This completes the header step for both PS-X EXEs; it does not establish how they are loaded at runtime.
2. **Disassembly pipeline — implemented** with splat, which the owner chose on 2026-10-21 (MIT licensed, pinned in `splat/requirements.txt`). `swallow_tools.split` produces annotated assembly with function boundaries, labels, and cross-references for both executables, outside the repository. See [disassembly](disassembly.md).
3. **PsyQ library identification — implemented.** `swallow_tools.psyq` places 88 library objects in the boot executable and 118 in `ACE3.BIN` by signature, and names their functions in the splat symbol files. `ACE3.BIN`'s game code is `0x800257e8–0x8009b910`, plus the Ulz routine at `0x800afd08–0x800b0130` after the libraries, which step 4 found. See [library identification](psyq-libraries.md).
4. **First game routine: the Ulz decompressor — implemented.** `ulz_decompress` is at `0x800AFD08`, hand-written assembly after the libraries, not in the range above. It never compares the magic, so it was found by its decoder shape. Its version paths are documented and named in the symbol file. `swallow_tools.ulzcheck` runs it, unmodified, in a strict MIPS interpreter on every Ulz blob, and it matches [`ulz.py`](../src/swallow_tools/ulz.py) byte for byte and in stream consumption on all 2,256 blobs on Japanese Disc 1. No emulator RAM capture has been compared yet. See [the Ulz routine](ulz-routine.md).
5. **BPB loading path.** Trace how the code indexes BPH, reads BPB entries, and walks DOT1 slots. Confirm or refute the DOT1 and overlay hypotheses from the code. **The overlay part is implemented:** the archive open, index, and read functions and the module loader are traced and named, BPH entries 1–4 load at `0x800CBFE8`, and `swallow-split` disassembles them by content hash: 271, 645, 20, and 20 functions. See [overlays](overlays.md). Still to do: what the module handlers load from the other entries, how DOT1 slots are walked, and `func_800273FC`.
6. **Oracle interpreter — implemented.** `swallow_tools.mips` runs every instruction in the game's code except the compiler's division traps, which raise `MipsTrap`. `swallow_tools.gte` models the 15 GTE commands game code issues, and `swallow_tools.oracle` gives compiled functions their `$gp`, stack, and memory. All 470 game-code `break`s are division guards, and twelve hand-written divisions have none. The GTE and division rules are documentation-derived until a capture checks them. See [the interpreter](mips-interpreter.md).
7. **Recompiler prototype — implemented.** `swallow-recompile` translates `ulz_decompress` to C, and the C runtime core in `runtime/` runs it as a shared library. `swallow-ulzcheck --native` matches all 2,256 Ulz blobs on each Japanese disc, and reproduces the interpreter's overruns on the English-labeled discs. See [the recompiler](recompiler.md).

## Decided: the form of recovered code

The owner chose static recompilation first, then readable C proven equal to
it, one function at a time (decision D1 in the
[reconstruction plan](reconstruction-plan.md#decisions)). Matching
decompilation stays optional and needs a reviewed compiler setup before any
adoption.

Either way, the engine's behavior is judged against the original code and
reference captures, not against intuition.

## Out of scope for this task

Writing the engine runtime, renderer, audio/XA, mission logic, the English dub,
or any flight-model code beyond locating and documenting where it lives.
