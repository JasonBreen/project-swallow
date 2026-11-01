# The game's Ulz routine

Located and checked in tooling v0.9.0, measured on 2026-11-01 against
Japanese Disc 1. This is step 4 of the
[executable track](next-task-executable.md). It names the routine
`ACE3.BIN` uses to decompress Ulz data, documents what it does, and runs it,
unmodified, on every Ulz blob on the disc to compare it with
[`ulz.py`](../src/swallow_tools/ulz.py). It does not turn the routine into C,
and nothing here writes game bytes.

## Location

| Symbol | Address | Evidence |
|---|---|---|
| `ulz_decompress` | `0x800AFD08` | Entry; 14 `jal` sites in `ACE3.BIN` |
| `ulz_version_table` | `0x800AFD30` | Three words indexed by the version byte, then a zero word |
| `ulz_decode_v0` | `0x800AFD40` | Target of table word 0 |
| `ulz_decode_v1` | `0x800AFDE4` | Target of table word 1 |
| `ulz_decode_v2` | `0x800AFE90` | Target of table word 2 |
| `ulz_stream_init` | `0x800AFF74` | Second entry point; no caller found |
| `ulz_stream_step` | `0x800AFFF0` | Third entry point; no caller found |
| `ulz_stream_state` | `0x800BCB44` | `0x30`-byte state the two stream entry points share |

The names are in the hand-written section of
[`splat/symbol_addrs.ace3.txt`](../splat/symbol_addrs.ace3.txt). The
routine spans `0x800AFD08–0x800B0130`, the last `0x428` bytes of the text
segment. splat keeps it as one function, so the function counts in
[disassembly](disassembly.md) are unchanged.

The routine sits after the PsyQ library span, which ends at `0x800AFCF8`,
and not in the `0x800257e8–0x8009b910` range that
[library identification](psyq-libraries.md) gives for the game's code, so
the game's code also includes `0x800AFD08–0x800B0130`. The 16-byte
`func_800AFCF8` between them is below the 32-byte signature minimum and
stays unclassified.

The routine is hand-written assembly. It uses `add`, `addi`, and `sub` 61
times. No function in the game code range uses them; elsewhere in
`ACE3.BIN` they occur only inside the library span, for example in
`DecDCTvlc2` and `RotMatrix_gte`. Its entry points also share exit code,
and its jump table sits inside the text.

### How it was found

The magic `Ulz\x1a` occurs nowhere in either executable, as bytes or as the
word `0x1A7A6C55`, so the routine never compares it. A scan ranked every
`ACE3.BIN` function by decoder features: 24-bit field masks, shifts by a
variable bit count, `+1` and `+3` adjustments, and byte loads and stores.
`func_800AFD08` ranked first, and its body matches the format contract in
[bpb-format.md](bpb-format.md).

## What the routine does

**Call.** `a0` holds the blob and `a1` the destination. The routine returns
in `v0` the address one past the last byte it wrote. It uses no stack and
only caller-saved registers (`v1`, `a0`, `a2`, `a3`, `t0`–`t9`).

**Dispatch.** It reads the version byte at `+0x7` and the offset bit count
`N` at `+0xB`, then jumps through `ulz_version_table`. There is no range
check: version 3 would jump through the zero word to address 0, and higher
versions would take code words as targets. The literal and pair offsets at
`+0x8` and `+0xC` are both masked to 24 bits. `ulz.py` reads the word at
`+0xC` whole; its top byte is zero in every blob measured.

| | Version 0 | Version 1 | Version 2 |
|---|---|---|---|
| Flag word | Bits above the lowest set bit, from bit 31 down; the lowest set bit is a stop bit | As version 0 | All 32 bits; the next word is loaded only when another bit is needed |
| Literal (flag 1) | One byte | One halfword | One byte, read from 32-bit words with aligned `lw` |
| Pair (flag 0) | Distance `low N bits + 1`, length `high bits + 3`, in bytes | Offset field `N - 1` bits; distance `low + 1`, length `high + 2`, in halfwords | As version 0 |
| Ends | At a zero flag word | At a zero flag word | When the bytes written reach the declared size, checked after each literal or whole copy |
| Declared size | Not read | Not read | Read from `+0x4`, 24 bits |
| Executed by the check | Yes | No; no version 1 blob exists | Yes |

Copies run forward one unit at a time, so a distance shorter than the length
repeats data.

### What this settles

These answer open questions in [bpb-format.md](bpb-format.md):

- **Version 0 stops on the zero word, not on the size.** Every version 0
  blob on Japanese Disc 1 places that word exactly where the declared size
  is reached, so the two rules agree there. `ulz.py` requires both.
- **A version 0 word of `0x80000000` holds only the stop bit.** The routine
  takes no flag from it and loads the next word. `ulz.py` rejects that word
  as unvalidated. It occurs in no reference, so the decoder is unchanged.
- **English-labeled version 0 blobs** leave zero flag bits above the stop
  bit after the declared size. **Prediction, not measured:** the routine
  would read each as a copy and write past the declared size. The English
  images were not run here.
- **Version 2 needs a word-aligned literal stream**, since it reads
  literals with `lw`. `ulz.py` already requires the literal offset to be a
  multiple of 4. A version 2 copy that crosses the declared size is
  completed before the routine stops, as the negative control under
  [Results](#results) shows. `ulz.py` rejects such a copy, and none occurs
  on the disc.
- **Version 1 exists in the code** and works in 16-bit units. No blob on
  the measured disc uses it, so `ulz.py` still rejects it.

### The stream entry points

`ulz_stream_init` and `ulz_stream_step` were read, not executed. By that
reading, `ulz_stream_init(blob, destination, budget)` stores a blob's stream
pointers, declared size, `N`, offset mask, and a per-call byte budget in
`ulz_stream_state`. Each `ulz_stream_step()` call then decodes up to the
budget with version 2 flag rules and bytewise literals, returns 0 while
bytes remain, and returns the end pointer on the call that finishes. Neither
checks the version byte. No `jal`, pointer word, or `%hi`/`%lo` pair in
`ACE3.BIN`, and no `jal` word in any BPB leaf, refers to either one.

## Callers

`ACE3.BIN` calls `ulz_decompress` from 14 sites in three functions. None
compares the magic first.

| Function | Calls | Use |
|---|---:|---|
| `func_800272F0` | 1 | Decompresses one blob, then passes the destination to `func_80027338` |
| `func_80035A38` | 11 | Chains decodes: each call's returned end pointer is the next destination, and each start is stored in a table |
| `func_80035F98` | 2 | Chains two decodes the same way, then passes the first output to `func_80027338` |

BPB entries 1–4 hold `jal` words into `ACE3.BIN`. Words with the `jal`
opcode were counted on each stored entry, and a hit is a target that is
exactly one of splat's 1,715 `ACE3.BIN` function starts:

| BPB entry | Size | `jal` words | Hit a function start | Call `ulz_decompress` | Load `0x1A7A` with `lui` |
|---:|---:|---:|---:|---:|---:|
| 1 | 72,340 | 1,073 | 280 | 5 | 5 |
| 2 | 290,380 | 2,881 | 981 | 8 | 5 |
| 3 | 338,656 | 959 | 47 | 0 | 0 |
| 4 | 21,860 | 102 | 50 | 0 | 0 |
| All other stored entries | | 602,781 | 16 | 0 | |

Disassembled, the bytes around the decoder calls in entry 1 are coherent
code. They load `0x1A7A6C55` (`Ulz\x1a`), compare it with a buffer's first
word, branch past the call when it differs, and then call `ulz_decompress`
with `a0` as the blob and `a1` as the destination. Entry 1 also jumps to
`0x800D8BF8`, above the end of `ACE3.BIN`'s BSS at `0x800CBFE8`. This is the
first direct evidence for the [overlay hypothesis](bpb-format.md#known-unknowns):
entries 1–4 contain MIPS code that is linked against `ACE3.BIN`. Their load
addresses, their count words, and how they are loaded are still not
decoded.

## Running the routine

[`swallow_tools.mips`](../src/swallow_tools/mips.py) is a small
interpreter for the MIPS I integer instructions: shifts, ALU operations,
immediates, branches, jumps, and byte, halfword, and word loads and stores.
It runs branch delay slots as the R3000 does, and makes a load visible only
after the next instruction. It is offline tooling, not an emulator. It
stops, rather than guessing, on any instruction outside that set, any
unmapped or misaligned access, any write to read-only memory, any
`add`/`addi`/`sub` overflow (a trap on the R3000), any branch in a delay
slot, and any instruction that uses a register in the delay slot of the
load that sets it.

[`swallow_tools.ulzcheck`](../src/swallow_tools/ulzcheck.py) finds the
executable whose SHA256 the [splat manifest](../splat/manifest.json) lists
with a symbol file that names `ulz_decompress`, and reads the address from
that file. It maps three regions and nothing else:

| Region | Address | Access |
|---|---|---|
| `ACE3.BIN` text | `0x80010000`, from the header | Read and execute |
| Blob | `0x81000000` | Read |
| Destination | `0x82000000`, declared size plus 64 KiB | Read and write |

The blob and destination addresses are interpreter choices, not RAM
addresses; the routine only follows the pointers it is given. Since no RAM,
BSS, scratchpad, or hardware register is mapped, any access to them would
stop the run.

Each Ulz blob the [BPB walker](bpb-format.md) reaches, stored or inside a
decoded payload, is run with `a0` = blob and `a1` = destination. It counts
as a match only when all of these hold:

1. The returned end pointer is the declared size.
2. Nothing is written past the declared size.
3. The bytes written equal `ulz.py`'s payload.
4. The stream pointers the routine leaves in `t6` (flags), `t5`
   (literals), and `a3` (pairs) equal `ulz.py`'s accounting. Version 0 must
   have read the zero word after its flags, and version 2 must have read
   whole literal words.

## Results

| Measure | Japanese Disc 1 |
|---|---:|
| `ACE3.BIN` SHA256 | `2c3f2ce4…` (the manifest's Japanese hash) |
| BPH entries checked | 574 |
| Ulz blobs run | 2,256: 1,167 version 0, 1,089 version 2 |
| Matched | 2,256 |
| Mismatches / decoder errors / routine errors | 0 / 0 / 0 |
| Pair-offset words with a nonzero top byte | 0 |
| Instructions executed | 643,716,825 |
| Most in one blob | 4,472,864 |

The blob count and version split equal the walker's figures for the same
image in [`bpb-jp1.json`](reports/bpb-jp1.json). So on every Ulz blob on
Japanese Disc 1, `ulz.py` and the game's own routine agree on four things:

- the output bytes
- the end pointer
- writing nothing past the declared size
- how many flag words, literal bytes, and pair bytes each one consumes

Every run stayed inside the three mapped regions. None hit a load-delay
hazard or an overflow trap. The full report is
[`reports/ulz-routine-jp1.json`](reports/ulz-routine-jp1.json).

**Negative control (a scratch run, not part of the tool).** Altering real
blobs makes the check fail as it should:

- Lowering the declared size by one makes `ulz.py` reject the blob, while
  the routine writes past it. A version 0 blob declared as 303 bytes still
  got 304, because version 0 ignores the size. A version 2 blob declared as
  115 ended at 116, because the routine finishes a copy that crosses the
  size.
- Flipping one flag bit makes the routine read before the start of the
  blob, which stops the run.

## Reproduce

```powershell
$env:PYTHONPATH = "src"
python -m swallow_tools.ulzcheck <BIN> --json > G:\ProjectSwallow-data\reports\ulz-routine-jp1.json
python -m swallow_tools.ulzcheck <BIN> --entry 5 --entry 6
```

The full check runs about 1.7 million interpreted instructions per second
in CPython; Japanese Disc 1 took 6 minutes 24 seconds. `--entry` limits it to
chosen BPH entries. It exits with `0` when every blob matches, `1` when any
blob mismatches or either side rejects one, and `2` for input, format,
manifest, or symbol errors. The report holds counts, paths, and addresses,
never game bytes.

## Limits

- One image was run. Japanese Disc 2 has the same `ACE3.BIN` and
  byte-identical BPB reports, so the same result is expected but was not
  run. The English-labeled images were not run.
- The version 1 path and both stream entry points were read, not run.
- The interpreter is checked only against synthetic, project-written
  programs. Its agreement with the R3000 rests on the documented
  instruction set; no emulator or hardware trace has been compared yet.
- A match shows that the routine and `ulz.py` agree on these blobs. It
  says nothing about input that does not occur on the disc.

## Next

- Run the check on the English-labeled images to test the version 0
  overrun prediction.
- Decide whether `ulz.py` should accept the `0x80000000` version 0 word,
  now that the routine's handling of it is known.
- Trace the BPB loading path: the callers above, how entries 1–4 are
  loaded, and where.
