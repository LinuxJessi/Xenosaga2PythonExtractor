# The scene scripts: how Episode II's script machine works

Episode I scripted its scenes in Java. Episode II threw that out and wrote a
small stack machine of its own, and every scene, every field map's events
and every enemy group's battle thinking runs on it. This page is the
machine, read from the game's own code, and how to read the kit's
disassembly of it.

```sh
python cli.py sb --iso "Disc 1.iso" --path 'cf\sc1010.sb'     # one script, dialogue inline
python cli.py browse --out OUT --kinds scripts                  # all 465, as OUT/browse/scripts/**/*.asm
python sb.py --self-test
```

## Where the scripts are

| Files | Count | Source name in the file | What they are |
|---|---|---|---|
| `cf\scNNNN.sb` + `cf\en\scNNNN.sbl` | 352 | `nakahara\sc1010`, `koji\…`, `fuji\…`, `yonesaka\…`, `kuramoto\…`, `event\…` | scenes and field events; the `.sbl` beside each holds its lines |
| `model\map\E2_*.bin` + `model\map\en\E2_*.sbl` | 113 | `think008`, `think032`, … | enemy thinking: entry points such as `EnemyNBoostCall`, `NBoostRenkei`, `uso_001_think` |

The source name at the end of each file is the folder of the person who
wrote it: 209 scenes are Nakahara's, 55 Koji's, 43 Fuji's, 23 Yonesaka's,
12 Kuramoto's, 10 "event". All 465 parse and disassemble with no structural
problem: 2,609,696 instructions, every jump, switch case, code address and
entry point landing on an instruction, every global slot in range.

## The file

```
+0x00 "SB  "   +0x04 u32 1   +0x08 u32 ?   +0x0C u32 entries   +0x10 u32 imports
+0x14 code     +0x18 globals  +0x1C (the .sbl's address at run time)
+0x20 entry table             +0x24 import table             +0x28 source name
entry table : entries x {u32 name offset, u32 code word}, then the names
import table: imports x {u16 function offset, u16 class offset}, then a string pool
```

Code is a stream of 16-bit words; an operand is the 32-bit value in the two
words after the opcode. Code addresses count 16-bit words.

## The machine (OV01)

The interpreter is in the battle/field overlay `OV01.OVL`. One handler per
opcode, in a 46-entry table at `0xab5fc0`; instruction lengths in a 46-byte
table at `0xab5f90` (`switch` is the one variable-length instruction,
`4n + 5` words). The context the handlers share:

| Offset | Field |
|---|---|
| +0x00 | pc, in 16-bit words |
| +0x04 | stack depth |
| +0x08 | frame pointer (locals are stack slots above it) |
| +0x14 | the loaded script image |
| +0x1C | stack base |
| +0x20 | stack size |

**Globals live inside the script image.** `ldg n` reads 32-bit slot `n`
of the globals section (header +0x18): the file's bytes are the variables'
starting values and, once loaded, their storage. The 465 scripts carry
395,935 global slots, 296,153 of them non-zero: they are data tables
(positions, parameters, lists of records) as much as variables. Locals and
arguments are stack slots relative to the frame pointer.

## The instructions

| Op | Name | Length | Does |
|---|---|---|---|
| 00 | nop | 1 | |
| 01 | op | 3 | arithmetic/logic on the stack; operand picks add sub mul div mod or and shr shl neg eq gt lt ge le ne land lor |
| 03 | pushaddr | 3 | push a variable's slot number (see below) |
| 04 | push | 3 | push an integer |
| 06 | pushtext | 3 | push a text reference |
| 07 | pushcode | 3 | push a code address |
| 08 / 09 | ldg / ldl | 3 | push global / local slot n |
| 0a / 0b | stg / stl | 1 | pop slot number, pop value, store |
| 0c / 0d | stg.keep / stl.keep | 1 | the same, value left on the stack |
| 0e / 0f / 10 | jmp / jz / jnz | 3 | |
| 11 | enter | 1 | push a 3-word frame header (return slot, caller's frame pointer, spare) |
| 12 | call | 3 | fill in the return address, jump |
| 13 / 14 | ret / retv | 1 | return / return with the top of the stack |
| 15 | native | 3 | call a game function (below) |
| 16 / 17 | waitfor / waitfor2 | 1 | block until a thread or event completes |
| 18 / 19 | dup / drop | 1 | |
| 1a / 1b | ldg.ind / ldl.ind | 1 | pop a slot number, push that slot |
| 1c / 1d / 1e | yield / end / sleep | 1 | |
| 1f / 20 | send / recv | 1 | 16-byte messages between threads |
| 21-24 | incg incl decg decl | 1 | pop a slot number, add or subtract 1 there |
| 25 | switch | 4n+5 | n, default, n x (key, target) |
| 26 / 27 | push0 | 1 | push 0 (two encodings, below) |
| 28-2d | push1 … push6 | 1 | push the constant |
| 02 / 05 | line / mark | 3 | never used on the disc |

### Four pushes, one handler

Opcodes 03, 04, 06 and 07 have byte-identical handlers in OV01: each pushes
its 32-bit operand. The compiler chose the opcode by the kind of literal,
and the scripts show the split without exception:

| Op | Operands, all 465 scripts | Used by |
|---|---|---|
| 03 pushaddr | 60,384, all small; the 60,189 consumed by a global op are inside the globals | `stg`, `ldg.ind`, `incg`, `decg` (and `stl`, `ldl.ind` for locals) |
| 04 push | 788,573 integers; `-1` is common | everything |
| 06 pushtext | 13,818, all `0x20 << 16 \| n` | always followed by the type 3 below |
| 07 pushcode | 8,053, every one an instruction start, 7,587 of them entry points | `waitfor`, `recv`, `call`, native callbacks |

The listing prints them as `pushaddr g12`, `push -1`, `pushtext text#4`
(with the line from the `.sbl`), and `pushcode L_7875` (a label).

### Calling the game

`native` takes `class << 16 | index`; the script's import table names it
(`camera.SetPers`, `talk.msgDtl`, `runtime.mapChange`). The game registers
its functions by class with `FUN_00a9cdb0(name, table)` from `{name,
handler}` tables in the boot executable ("runtime" at 0x3e6e40, "talk" at
0x3ec868, …) and resolves a script's imports when it loads
(`FUN_00a9d148`). The scripts call 227 distinct natives in 15 classes;
by calls: camera 80,938, cfe 50,780, runtime 22,849, bat 16,101, objx
11,226, talk 9,356, math 4,540, enepc 3,912, uwamono 1,891, g2path 511,
id 465, menu 153, seg 112, ladder 84, samp 31.

Arguments go on the stack as (value, type) pairs, then the count:

```
  06eb  push      4
  06ee  push1     ; type int
  06ef  pushtext  text#4    ; [Canaan]What is this thing?
  06f2  push3     ; type text
  06f3  push      2
  06f6  native    talk.msgDtl
  06f9  drop
```

`native` pops `2n + 1` words and pushes the result, which the script drops
when it has no use for it. The type is a small constant the game function
reads (`FUN_00a9f140`):

| Type | Pushed after | Meaning |
|---|---|---|
| 0 | `push0`, or `ldg` of an unset variable | **omitted**: `FUN_00a9f1c0` treats it as "use the default" |
| 1 | `push`, `ldl`, arithmetic | integer |
| 3 | `pushtext` | text: resolved through the scene's `.sbl` (`FUN_00a9f238`) |
| 4 | `pushcode` | code address (a callback or thread) |
| 5 | `ldg` (1,523 times, every value a slot inside the globals) | a table in the globals |

Types 2 and 6 never appear. The two `push0` encodings run the same handler;
the compiler uses 0x27 only right after 0x26 (48,363 times), as the type
of an omitted argument, so `push0 push0` reads as "no argument here".

### Text references

`FUN_00a9f238` takes a type-3 value, checks `(v & 0xF00000) == 0x200000`,
and reads the string from a structure at the address stored in script
+0x1C: count at +0x08, offset table at +0x14, strings at +0x18, the `.sbl`
header exactly. In the file that word only equals the entry-table offset;
the loader puts the bank's address there. This is why a translation never
touches the scripts: a line is a number into the `.sbl`, and the `.sbl` is
rebuilt on its own (`text-export` / `text-import`).

### Calls, threads, messages

`enter` pushes a frame header and `call` fills in its return slot, so a
call site is `enter`, the arguments, `call L_xxxx`; `ret`/`retv` restore
the caller's frame. Code addresses pushed with `pushcode` start threads
and handlers; `waitfor` blocks until the thread or event named by the popped
value is done (`FUN_00a9d2a8`), `send` copies a 16-byte message to a target
and `recv` collects one if any is waiting, pushing 1 or 0.

## Reading a listing

```
; source nakahara\sc1010
; 4 entries, 35 imports, 1528 instructions, 16 global slots
;   import   0 = runtime.mapChange
...
locater:
  05b8  ldg       g1
  05bb  pushcode  locater
  05be  recv
```

Labels are entry names where the file has them and `L_xxxx` (word address)
elsewhere; `gN` is a global slot, `lN` a local; `; type …` marks the type
half of an argument pair.

### The older form: the demo on *Xenosaga Freaks*

*Xenosaga Freaks* (SLPS_253.53, 2004) carries a Japanese Episode II demo
whose chain sits in `EXP\` (3,612 entries). Its 135 scripts have the same
header version, opcodes and tables, with two differences (2026-11-05):

* **No `.sbl`.** A `pushtext` operand is the byte offset of a NUL-terminated
  EUC-JP string in the script's own globals (5,053 of 5,053 land on a string
  start). Dialogue, `deb.put` debug prints and effect names all live in the
  `.sb`. `sb` shows them as `str@0x5ac ; "…"`; `Script.text_kind` is
  `inline` (retail: `sbl`).
* **Standalone battle AI.** The 109 `yamamoto\think\thinkNNN.sb` are the
  enemy-thinking scripts that retail folds into `model\map\E2_*.bin` (source
  names `think000`… as in retail). Retail OV01 still formats
  `yamamoto\think\think%03d.sb` and `yamamoto\think\jp\think%03d.sbl`, but
  neither retail disc lists such files. They call 80 natives (`bat`, `camera`,
  `math`, and `deb.put`, which no retail table registers) and print their
  state in Japanese ("勝利者ゆにっと！！！", "★現在の技").

Four demo headers leave the source-name word at 0.

## Native signatures

`cli.py natives` reads the (value, type) pairs before every `native` call and
lists each native with its call count and the type signatures seen, e.g.
`bat.unitCreate (int, int, int, code, code)`, `bat.msgPut (int, int, text,
int, int, int)`. Retail, both discs: 227 natives, 202,949 calls; the demo:
159 natives. With `--out` it also writes a listing per script and, for the
demo, `strings.tsv`.

## Open

* The meaning of each native's arguments: the names are free (the game's
  own tables) and the types are listed (`natives`), but what each argument
  means is not yet written down.
* The u32 at header +0x08 (0 in 439 scripts, 132 or 735 in the rest).
* An assembler, if scripts are ever to be edited rather than read.
