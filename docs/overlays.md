# Overlays and the module loader

Traced in tooling v0.10.0, measured on 2026-11-03 against Japanese Disc 1;
all four references were split with the same configs.
This is step 5 of the [executable track](next-task-executable.md) and the
first task of milestone M1 in the [reconstruction plan](reconstruction-plan.md).
[`swallow_tools.split`](../src/swallow_tools/split.py) now also disassembles
the overlays. Nothing here writes game bytes into the repository.

## Result

BPH entries 1–4 of `ACE.BPB` are code overlays for `ACE3.BIN`. Its module
loader reads entry *k* whole to `0x800CBFE8`, the end of `ACE3.BIN`'s BSS,
and calls handler functions inside it. All four share that address, so only
one is resident at a time. This replaces the [overlay hypothesis](bpb-format.md#known-unknowns)
with code evidence. `ACE3.BIN` never refers to an overlay address directly;
control reaches an overlay only through the module table.

## How an overlay is loaded

Each step was read in the disassembly. The names are in the hand-written
section of [`symbol_addrs.ace3.txt`](../splat/symbol_addrs.ace3.txt).

| Step | Function | What it does |
|---|---|---|
| Open the archives | `archive_open` `0x8002AE0C` | Finds the four files in `archive_file_names` (`0x800B0380`): `\ACE.BPH;1`, `\ACE.SPH;1`, `\ACE.BPB;1`, `\ACE.SPB;1`, in that order, with `archive_find_files` (`CdSearchFile`). Reads 4 sectors (8 KiB) each of `ACE.BPH` and `ACE.SPH` into adjacent buffers. Both must start with `AC3E`, and BPH word `+8` is compared with `0x427`; a mismatch sets an error status and prints a message. Points `+0x18` of its state at the BPH records (buffer `+0x14`) and `+0x1C` at the SPH records. Adds the start sector of `ACE.BPB` or `ACE.SPB` to every record's sector word. |
| Find a record | `archive_index_record` `0x8002995C` | Index `id & 0x7FF` into 8-byte records: BPH, or SPH when bit `0x800` is set |
| Size an entry | `archive_entry_size` `0x8002C5E0` | BPH: `(word & 0x7FFFF) * 4`, as [`bph.py`](../src/swallow_tools/bph.py) reads it. SPH: `(word & 0x7FFFF) << ((word >> 24) & 7)`, read but not checked against data |
| Queue the read | `archive_read_entry` `0x8002C8B8` → `archive_queue_read` `0x8002C7AC` | Puts a type 2 request (id, destination, callback) in a 16-slot ring of 20-byte requests and records the destination and its end |
| Run the module | `ace3_module_step` `0x80026350` | Calls the current module's handler for its sub-state. If the module's overlay is not the one loaded, it runs `ace3_overlay_load_steps` instead: `ace3_overlay_load_start` (`0x8009B6B8`) queues entry *id* to the module's base, and `ace3_overlay_load_poll` (`0x8009B7CC`) waits for it. |

A handler's return value steers the module system: `0x20000000` selects the
module in the low byte, `0x08000000` the next module, `0x10000000` the
sub-state in the low byte, and `0x04000000` the next sub-state. A loader
step returns `0x02000000` when the overlay is in. `0x40000000` calls
`func_800273FC` with the current module's end address.
**Hypothesis:** that sets up memory after the module's code; it has not been
traced.

### The module table

`ace3_module_table` at `0x800B0134` holds nine 16-byte records: handler
table, overlay id, load base, and end.

| Module | Handler table | Overlay id | Base | End | Code pointers at the table |
|---:|---|---:|---|---|---:|
| 0, 1 | `0x800B0130` | −1 | `0x80010000` | `0x800CBFB8` | 1 |
| 2 | `0x800B0A10` | −1 | `0x80010000` | `0x800CBFB8` | 18, including `func_80035A38` and `func_80035F98`, the [Ulz callers](ulz-routine.md#callers) |
| 3 | `0x801097A8` | 2 | `0x800CBFE8` | `0x80112E34` | 27 |
| 4 | `0x800DCB00` | 1 | `0x800CBFE8` | `0x800DDA80` | 4 |
| 5 | `0x800B2BE0` | −1 | `0x80010000` | `0x800CBFB8` | 6 |
| 6 | `0x800B2BF0` | −1 | `0x80010000` | `0x800CBFB8` | 2, the last two of module 5's |
| 7 | `0x8011E854` | 3 | `0x800CBFE8` | `0x8011EAC8` | 2 |
| 8 | `0x800CF2A8` | 4 | `0x800CBFE8` | `0x800D1550` | 2 |

Id −1 runs code in `ACE3.BIN`. The pointer counts are runs of words that
point into code; where one table ends is not decoded. The resident end,
`0x800CBFB8`, is `0x30` below the overlay base. That is not explained.

## Overlay layout

Each overlay holds, in order: a leading word, strings and tables, code,
then data that starts with its module's handler table. The code ends with
the last `jr $ra` and its delay slot, exactly where the handler table begins.
Offsets are from the entry start, which loads at `0x800CBFE8`.

| Overlay | BPH entry | Size | SHA256 | Code | Data | End − base − size | splat functions |
|---|---:|---:|---|---|---|---:|---:|
| `ovl1` | 1 | 72,340 | `f9c4ae47…` | `0x5F0` | `0x10B18` | 4 | 271 |
| `ovl2` | 2 | 290,380 | `beb4377c…` | `0x17D8` | `0x3D7C0` | 0 | 645 |
| `ovl3` | 3 | 338,656 | `5f049da9…` (Japanese), `707deead…` (English-labeled) | `0x4F340` | `0x5286C` | 0 | 20 |
| `ovl4` | 4 | 21,860 | `e5f73492…` | `0x174C` | `0x32C0` | 4 | 20 |

The code starts after the last string or pointer table. In `ovl1` and
`ovl4` that is a stack-frame prologue; in `ovl2` and `ovl3` it is a leaf
function. No overlay has an undecodable word. The 956 overlay functions add
to `ACE3.BIN`'s 1,284 game functions.

The leading words are 4, 5, 6, and 7, not the ids 1–4; their meaning is not
decoded. The 4 bytes past entries 1 and 4 may be BSS; they are not
measured. Overlay code has no `$gp`-relative instructions, while splat's
`ACE3.BIN` output has 3,897. **Hypothesis:** the overlays were compiled with
a different small-data setting.

## Why the base is right

Four independent checks agree:

1. Every overlay record in the module table gives `0x800CBFE8`, and its end
   minus base is the entry's BPH size, or 4 more.
2. Of each entry's own `jal` targets, these land on a function entry at that
   base: 748 of 768 in entry 1, 1,841 of 1,877 in entry 2, 25 of 51 in
   entry 4, and 20 of 910 in entry 3, which is mostly data. The next-best
   bases score 254, 506, 8, and 4.
3. Each handler table, read at that base, points to function starts in the
   same overlay. splat resolves `ovl1_handler_table` to four of `ovl1`'s
   functions.
4. Each overlay's code ends exactly where the module table says its handler
   table is.

## Other findings

- **BPH header word `+8` is `0x427`** (1,063, documented as `unknown_08`) on
  every reference. The game compares it with `0x427`; on a mismatch it sets
  error status 5 and prints a message. What it does after that is not traced.
- **BPH sector words are relative to `ACE.BPB`.** The game adds the file's
  start sector, which matches how `bph.py` turns them into offsets.
- **`ACE.SPH` is a second `AC3E` index** for `ACE.SPB`, addressed with ids
  that have bit `0x800`. Its size rule includes a shift field in bits 24–26.
  This is read from code only; SPH parsing is a separate task.
- **The game has its own CD sector driver**, `0x80029A44–0x8002AE0C`, built
  on LIBCD's low-level calls: `CdControlF`, `CdGetSector`, and the callbacks.
- **`archive_find_files` uses `lwl`, `lwr`, `swl`, and `swr`.**
  [`swallow_tools.mips`](../src/swallow_tools/mips.py) does not implement
  them yet, which matters for the planned recompiler.

## Disassembling the overlays

[`splat/manifest.json`](../splat/manifest.json) is now schema 2. It lists
each overlay with its config, target, symbol file, size, and SHA256.
`swallow-split` reads the BPH index through the ISO directory. It hashes only
entries of a listed size, and it needs exactly one match for every overlay
config, as it does for executables. Entries are never chosen by number.
Overlays run after the executables, because each overlay config also loads
`symbol_addrs.ace3.txt`; that names its calls into `ACE3.BIN`, such as
`jal ulz_decompress` and `jal LoadImage2`.

Each overlay's splat log contains a generic line, "Unable to determine a
segment for the following user-declared symbols". It refers to
`ACE3.BIN`'s symbols, which lie outside the overlay's segment. It names no
symbol, the references still resolve, and splat exits 0.

```powershell
$env:PYTHONPATH = "src"
python -m swallow_tools.split <BIN> --work-dir G:\ProjectSwallow-data\splat\jp1 --python G:\ProjectSwallow-data\venv-splat\Scripts\python.exe
```

The report, `split-report.json`, is schema 2. It adds an `overlays` list
with each entry's BPH index, size, SHA256, config, and text files.

## On all four references

All four references were split with these configs, with the same function
and undecodable-word counts: 1,715 and 8 for `ACE3.BIN`, 364 and 5 for the
boot program, and 271, 645, 20, and 20 functions with no undecodable words
for the overlays.

Entries 1, 2, and 4 are byte-identical on all four. Entry 3 is the same on
both Japanese discs, and the same on both English-labeled discs, but differs
between the two. That corrects the earlier record in
[bpb-format.md](bpb-format.md) that all four entries were identical.

- 117,943 bytes differ, all before entry 3's code; the last is at
  `+0x4DE2C`, and the code starts at `+0x4F340`.
- The code and data regions are byte-identical, and splat's `ovl3` code
  output is the same for both. So `ovl3.yaml` accepts both SHA256 values,
  as `ace3.yaml` does for `ACE3.BIN`.
- **Hypothesis:** the changed bytes are 4-bit image data, such as replaced
  graphics or glyphs. The nibble patterns suggest it; nothing has decoded
  them.

## Limits

- The request ring, the CD driver, and `func_800273FC` were read only as far
  as described. Nothing in this trace was executed.
- Which screens the modules drive is not claimed. Entry 1 holds menu strings
  such as `LOG OUT` and `Mission Simulater`, and entry 2 holds CPU, GPU, and
  memory load strings and `SC0xx` names; those are hints, not evidence.

## Next

- Trace `func_800273FC` and how modules allocate memory after their code.
- Follow module 2's resource loaders into the other BPB entries.
- Finish the M1 code inventory: indirect-call targets, including these
  handler tables, the `0x1F80` sites, and registered callbacks.
