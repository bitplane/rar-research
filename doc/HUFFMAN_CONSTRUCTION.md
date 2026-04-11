# Huffman Construction (Encoder Side)

All RAR versions from 2.0 onward use canonical Huffman coding for LZ
literal/length/distance symbols. The existing format specs
(`RAR15_40_FORMAT_SPECIFICATION.md` §16.4 and §18.2,
`RAR5_FORMAT_SPECIFICATION.md` §11.3) describe how to **read** code-length
tables back out of a compressed stream, but say nothing about how an encoder
turns symbol frequencies into those tables. This document fills that gap.

The algorithm described here is the one used by 7-Zip's `HuffEnc.c`
(`_refs/7zip/C/HuffEnc.c`, ~330 lines, Igor Pavlov, public domain). It is not
RAR-specific — the same algorithm works for RAR 2.x, 3.x, 4.x, and 5.0 because
canonical Huffman is fully determined once you fix (a) the length-limit rule
and (b) the canonical-code assignment order.

---

## 1. Pipeline overview

On the encoder side, turning a symbol stream into wire-format Huffman data is a
five-step pipeline:

```
  raw symbol stream
        │
        ▼
  (1) count frequencies  freqs[0 .. num-1]
        │
        ▼
  (2) build length-limited canonical tree → lens[0 .. num-1]
        │
        ▼
  (3) assign canonical codes              → codes[0 .. num-1]
        │
        ▼
  (4) RLE-pack lens[] into wire format    (per-version, see §5)
        │
        ▼
  (5) emit packed lengths, then emit symbol codes
```

Steps 2 and 3 are version-independent. Step 1 is trivial (an array increment
per symbol). Step 4 is the only part that differs across RAR versions, and
it's just a specific RLE scheme over `lens[]` — the length *values* themselves
are identical regardless of how they end up packed.

---

## 2. Constraints

7-Zip's `Huffman_Generate` (in `HuffEnc.c`) assumes the following, which are
all compatible with every RAR Huffman table:

| Constraint | Value | Reason |
|---|---|---|
| `2 ≤ num ≤ 1024` | symbol count | RAR tables top out at 306 (Main) |
| `sum(freqs) < 2^22` | 4M total | Bit-packing in the tree array |
| `1 ≤ maxLen ≤ 16` | max code length | RAR uses 15 |
| Sentinel | `lens[i] = 0` if `freqs[i] = 0` | Symbols never used get length 0 |

Symbols with frequency 0 are **not** given a code. They appear in the wire
format as length 0 and the decoder will treat them as invalid if they occur
(which they can't, by construction).

---

## 3. Length-limited canonical tree (Huffman_Generate)

This is the core algorithm. It produces `lens[0 .. num-1]` from
`freqs[0 .. num-1]`, guaranteeing no code is longer than `maxLen` bits. It is
**not** the theoretically optimal length-limited Huffman (package-merge
achieves that), but it is deterministic, fast, and within a small fraction of a
bit per symbol of optimal. DEFLATE, gzip, zlib, 7-Zip, and (we assume) RAR all
use some variant of it.

### 3.1 Sort symbols by frequency

Build an array `p[0 .. num-1]` where each entry packs `symbol | (freq << 10)`.
The low 10 bits hold the symbol index, the high 22 bits hold the frequency.
Sorting `p` by value sorts by frequency (ties broken by symbol index).

7-Zip uses a bucket-sort fast path for the very-low-frequency cases (freq < 95,
which is most symbols in any block of reasonable size) and falls back to heap
sort for the tail. An implementer can use any stable sort; the bit-packing
trick is just an optimization.

**Edge cases for `num ≤ 2`:** the tree-construction loop assumes ≥ 2
leaves and cannot be entered otherwise. 7-Zip's `Huffman_Generate`
short-circuits these cases (`HuffEnc.c:133-157`) by assigning code
length 1 to two positions and returning. The degenerate cases matter
because a RAR decoder can never accept a zero-length code — every
used symbol must produce a decode step.

`num` here is the count of symbols with non-zero frequency (the
sorted `p[]` array holds only those). Let `minCode` and `maxCode` be
the *symbol indices* (not frequency ranks) assigned length 1:

| `num` | `minCode` | `maxCode` | Notes |
|-------|-----------|-----------|-------|
| 0     | 0         | 1         | No symbols were used. Assign a dummy canonical table for `{0, 1}` so the wire format is well-formed. Rare but legal. |
| 1, sym ≠ 0 | 0    | sym       | Real symbol gets `maxCode`; index 0 is a dummy. |
| 1, sym = 0 | 0    | 1         | `maxCode` is bumped from 0 to 1 so the two assigned indices differ. |
| 2     | min(sym₀, sym₁) | max(sym₀, sym₁) | Sort by **symbol index** for canonical assignment, not by frequency. |

Assign `lens[minCode] = lens[maxCode] = 1`, assign codes `0` and `1`,
and return. The `num = 0` and `num = 1` cases synthesize a phantom
second symbol to force a two-leaf tree. The decoder will never
actually decode the phantom — it won't appear in the compressed
stream — but the Huffman table must be structurally valid, and a
single 1-bit code with no sibling violates the canonical-code
invariants.

Implementations that only handle `num ≥ 2` and fall through to the
main tree-construction loop will either assign a zero-length code
(unreadable by the decoder) or underflow the sorted-array indexing.
RAR blocks with a single literal byte repeated, or a single
distance value reused throughout, hit this case.

### 3.2 In-place tree construction

7-Zip reuses the sorted `p[]` array as scratch space for the tree. The
layout during construction (conceptually — see `HuffEnc.c` for the bit-packing
details):

```
  p[0 .. b)   processed internal nodes (low bits = parent index)
  p[b .. e)  pending internal nodes (waiting for a parent)
  p[e .. pi) processed leaves (parent assigned)
  p[pi .. n) pending leaves (original sorted freqs)
```

The algorithm walks left-to-right, pairing the two smallest unassigned items
(either leaf or pending internal) at each step, storing the sum as a new
pending internal node, and recording each child's parent pointer.

After `num - 1` pairings, `p[num - 2]` holds the root. A second pass walks
back from the root, converting parent-index pointers into tree levels, and
increments `lenCounters[level]` for each leaf encountered.

### 3.3 Length-limit fixup

If a leaf would land at level `len ≥ maxLen`, it's moved. The fixup picks the
nearest allowed leaf at `len < maxLen`, demotes that leaf one level
(`lenCounters[len]--`, `lenCounters[len + 1] += 2`), and places the problem
leaf at the vacated slot. This is equivalent to the classic "move a shallow
leaf down" operation and preserves the Kraft inequality:
`Σ 2^(maxLen - lens[i]) ≤ 2^maxLen`.

Because the fixup is post-hoc rather than part of the main tree-building loop,
the resulting codes are slightly suboptimal (typically by < 0.1% total bits)
compared to true length-limited Huffman via package-merge. For RAR purposes
this is fine — RAR's format only requires that the codes be *valid* canonical
Huffman codes; it does not require optimality.

### 3.4 Read out code lengths

Walk `lenCounters[maxLen .. 1]` from longest to shortest. For each level
`len`, the next `lenCounters[len]` entries of the sorted `p[]` get their
symbol's `lens[]` entry set to `len`. Because `p[]` is still sorted by the
*original* frequency, this assigns the longest codes to the least-frequent
symbols, as required.

After this pass, `lens[0 .. num-1]` is the final per-symbol code length, with
`lens[i] = 0` exactly when `freqs[i] = 0`.

---

## 4. Canonical code assignment

Once `lens[]` is finalized, canonical codes are assigned by the DEFLATE rule
(RAR uses the same rule):

```
next_code = 0
for len = 1 to maxLen:
    next_code = (next_code + lenCounters[len - 1]) << 1
    base_code[len] = next_code

for i = 0 to num - 1:
    if lens[i] != 0:
        codes[i] = base_code[lens[i]]
        base_code[lens[i]] += 1
```

Equivalent invariants:
- Within a length class, codes are assigned in increasing symbol-index order.
- A code at length `L` is numerically greater than every code at length `< L`.
- The first code at length `L` is `(last_code_at_L-1 + 1) << 1`.

These two rules (length + order) uniquely determine every code, so the encoder
never needs to transmit code values — only lengths. This is why the wire
format only contains `lens[]` and not `codes[]`.

A sanity check every implementation should run:

```
assert (next_code + lenCounters[maxLen] == 1 << maxLen)
```

(After the loop, all leaves must exactly fill the level-`maxLen` code space.)

---

## 5. Packing `lens[]` into the wire format

Only this step differs across RAR versions. The length *values* are the same;
only the RLE container around them changes.

### 5.1 RAR 2.0 (Unpack20)

See `RAR15_40_FORMAT_SPECIFICATION.md` §16.4. A 19-symbol level table is
pre-coded with 4-bit fixed lengths, then the level table is itself a
canonical Huffman used to decode the main-table lengths with run-length
escape codes for repeated values and runs of zero.

Encoder flow:
1. Build `lens[]` for the main tables via §3.
2. Compute the edit-distance–style encoding (each length is encoded as the
   *delta* from the previous length, modulo 16).
3. Emit runs of deltas, using the level-table alphabet symbols 0–15 (literal
   deltas), 16 (repeat previous), 17–18 (run of zeros).
4. Count level-symbol frequencies, build a Huffman over the 19 level symbols
   via §3 at `maxLen = 15`, but emit the level-table lengths as raw 4-bit
   fields (no recursive level table).

### 5.2 RAR 2.9 / 3.x / 4.x (Unpack29)

See `RAR15_40_FORMAT_SPECIFICATION.md` §18.2. A 20-symbol level table, same
scheme as RAR 5.0, encoding the concatenated lengths of four main tables
(Main 299, Distance 60, Align 17, Length 28 → 404 code lengths total).

Encoder flow identical to §5.1 but with the 20-symbol level table and the
delta-modulo-16 encoding of the main-table lengths.

### 5.3 RAR 5.0 (Unpack50/70)

See `RAR5_FORMAT_SPECIFICATION.md` §11.3. Same 20-symbol level table as
Unpack29. Four main tables: Main 306, Distance 64 (v6) / 80 (v7), Align 16,
Length 44 → 430–446 code lengths concatenated.

Encoder flow:
1. Build `lens[]` for all four tables via §3 with `maxLen = 15`.
2. Concatenate them in the order Main, Distance, Align, Length.
3. RLE-encode with level symbols:
   - `0–15` — literal length value.
   - `16` — repeat previous length for `3 + read_bits(3)` entries
     (`3..10`).
   - `17` — repeat previous length for `11 + read_bits(7)` entries
     (`11..138`).
   - `18` — emit zeros for `3 + read_bits(3)` entries (`3..10`).
   - `19` — emit zeros for `11 + read_bits(7)` entries (`11..138`).
   Symbols `16` and `17` require at least one previously decoded length.
4. Count level-symbol frequencies, build the 20-symbol level Huffman via §3
   at `maxLen = 15`, and emit its 20 lengths as 4-bit fields followed by a
   special 4-bit `15, N` escape for runs of zero level-lengths. That escape
   applies only while writing the 20 level-table lengths, not while writing the
   concatenated Main/Distance/Align/Length table lengths.
5. Emit the RLE-packed main-table lengths.

---

## 6. Reference

- `_refs/7zip/C/HuffEnc.c` — `Huffman_Generate` function, 330 lines.
  The entire algorithm in §3 is ~200 lines of dense but straightforward C.
- `_refs/7zip/CPP/7zip/Compress/HuffmanDecoder.h` — the matching decoder
  template, useful for cross-checking that generated codes decode correctly.
- RAR decoders; for the encoder to round-trip correctly the generated code
  lengths must decode back to the same symbol frequencies, so decoder
  compatibility is the test oracle.

---

## 7. Self-test recipe

A good encoder test harness:

1. Generate a random symbol stream with non-uniform frequencies.
2. Run the pipeline §1 to produce `lens[]`, `codes[]`, and the bit-packed
   output.
3. Run the RAR decoder (or 7-Zip's `HuffmanDecoder`) over the packed data.
4. Assert `decoded == original`.
5. Assert `max(lens[]) ≤ 15`.
6. Assert the Kraft equality `Σ 2^(15 - lens[i]) = 2^15` over non-zero
   `lens[i]` entries.

If all four hold for every RAR version's table layout, the encoder is
format-conformant.
