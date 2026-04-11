# LZ Match Finding (Encoder Side)

Every RAR compression algorithm from 1.3 onward is built on LZ77: the encoder
replaces the upcoming bytes with a *(length, distance)* pair pointing at an
earlier occurrence of the same byte sequence. How the encoder **finds** that
earlier occurrence is an implementation detail not mandated by the format —
the decoder only cares that the distance is within the dictionary and the
referenced bytes match. This document describes the two standard match-finder
data structures (hash chain and binary tree), the three parsing strategies
(greedy, lazy, optimal), and the RAR-specific constraints that bound them.

Primary reference: `_refs/7zip/C/LzFind.c` (1,746 lines, Igor Pavlov, public
domain). The match finder in LzFind.c is the one LZMA uses, and the algorithms
transfer directly to RAR — only the dictionary size, minimum/maximum match
length, and cost function differ per version.

---

## 1. Problem statement

At each byte position `pos` in the input stream, the encoder wants the answer
to:

> Among all positions `q` such that `pos - dict_size ≤ q < pos`, which gives
> the **longest** common-prefix with the bytes starting at `pos`?

The answer is a list of candidates sorted by length — the encoder picks one
per its parsing strategy (§5). Both data structures below compute it
incrementally: `GetMatches(pos)` returns candidates at `pos` and updates the
structure so that a future query at `pos + k` sees `pos` as a valid
backreference target.

---

## 2. Window and cyclic buffer

The encoder maintains a window covering `dict_size + lookahead` bytes of
input. The window is a linear byte buffer (7-Zip calls it `bufBase`) plus a
cyclic index buffer of size `cyclic = dict_size + 1`. All stored positions are
absolute `UInt32` counters; they index into the cyclic buffer via
`pos % cyclic`.

**Normalization.** When `pos` approaches `UInt32` max, subtract a large
constant (typically `pos - cyclic` or `kNormalizeStep`) from every value
stored in `hash[]` and `son[]`, saturating to 0 for positions that fall out of
the window. 7-Zip does this in `MatchFinder_Normalize3` with a SIMD saturating
subtract (`LzFind_SaturSub_128/256`). A naive implementation can scalar-loop.
Normalization is correctness-critical: without it, positions near `UInt32` max
alias to small values and the match finder returns bogus matches.

---

## 3. Hash construction

The hash indexes candidates by a short prefix of the current bytes (2, 3, or 4
bytes). Hash tables sit at fixed sizes:

```
kHash2Size = 1 << 10    // 1024 entries
kHash3Size = 1 << 16    // 65 536 entries
kHash4Size = typically 1 << 20 or larger, derived from dict_size
```

7-Zip's hash (`LzHash.h`) combines up to three CRC-based sub-hashes:

```
h2 = crc[buf[0]] ^ buf[1]
h3 = h2 ^ (crc[buf[2]] << 5)
h4 = h3 ^ (crc[buf[3]] << 10)
```

Where `crc[]` is the CRC-32 byte table (256 × 4 bytes, see
`CRC32_SPECIFICATION.md`). The shifts spread the hash across output bits; 5
and 10 are empirical choices — smaller shifts collide more, larger shifts need
bigger tables. 7-Zip stores all three in a single combined `hash[]` array with
`h2` at offset 0, `h3` at offset `kHash2Size`, and `h4` at offset
`kHash2Size + kHash3Size`. Only `h4` is used by the main match search; `h2`
and `h3` exist to let the encoder short-circuit to very short matches
(length 2–3) that the main `h4` search would miss.

**Format impact:** zero. The decoder never sees the hash. Any hash function
with good distribution works. A RAR-compatible encoder is free to use FNV,
xxHash, or a CRC-based hash as long as it's deterministic for a given window
position.

---

## 4. Match-finder data structures

### 4.1 Hash chain (HC4)

Memory: 1 × `UInt32` per window position. Layout:

```
hash[h4(cur)]  → most recent position whose h4 equals h4(cur)
son[p % cyclic] → previous position in the chain (older than p)
```

**GetMatches(pos):** start at `curMatch = hash[h4(pos)]`, walk back through
`son[]` up to `cutValue` steps. For each candidate position `q`, compute the
actual match length by byte comparison (with the classic `buf[q+maxLen] ==
buf[pos+maxLen]` pre-check to reject short matches without touching lower
bytes). Record candidates whose length strictly exceeds the best-so-far.

**Update:** before returning, store `son[pos % cyclic] = hash[h4(pos)]` and
`hash[h4(pos)] = pos`, so the next query sees `pos` at the head of the chain.

**Pros:** small (1 word / position), simple, fast when matches are shallow.
**Cons:** worst-case `O(cutValue × matchMaxLen)` per position, and the chain
can degenerate on pathological inputs. `cutValue` is the primary
time/compression knob — LZMA defaults range from 8 (fast) to 1000+ (best).

Reference: `Hc_GetMatchesSpec` at `_refs/7zip/C/LzFind.c:879`.

### 4.2 Binary tree (BT4)

Memory: 2 × `UInt32` per window position. Layout:

```
hash[h4(cur)]  → root of BST keyed by suffix
son[p % cyclic × 2 + 0] → left  child (suffix < cur)
son[p % cyclic × 2 + 1] → right child (suffix > cur)
```

The tree is keyed by byte-wise lexicographic order of the suffix starting at
each window position. Insertion at `pos` walks down from the root (the most
recent position with matching `h4`), comparing bytes, and:

1. At each node, computes the common-prefix length `len` with the current
   suffix. If `len > maxLen` so far, emit a match `(len, delta)`.
2. If `buf[node + len] < buf[cur + len]`, descend right (larger suffixes); else
   descend left.
3. Maintains `len0` and `len1` across the walk — the tree property guarantees
   every later node shares at least `min(len0, len1)` bytes, so the inner
   byte-comparison loop can skip past them.
4. At the end of the walk, splices `pos` into the tree at the insertion point.
   The subtree containing positions older than `pos - cyclic` is discarded
   (effectively pruning out-of-window nodes).

**Pros:** every step of the tree walk contributes useful information, so the
effective branching is logarithmic in the number of matching prefixes. Better
compression ratios than HC for the same time budget.
**Cons:** 2× memory. More complex code. Pathological inputs (e.g. all-zero
windows) can still degrade to `O(cutValue × matchMaxLen)`, which is why
`cutValue` bounds it.

Reference: `GetMatchesSpec1` at `_refs/7zip/C/LzFind.c:961`.

### 4.3 Skip

Both structures have a `Skip(num)` entry point. It updates the structure for
`num` positions without returning match lists — used when the parser has
already committed to a long match and needs to advance past its interior
positions while keeping the chain/tree consistent.

Reference: `SkipMatchesSpec` at `_refs/7zip/C/LzFind.c:1032` (BT skip);
`Hc_GetMatchesSpec`'s discarding branch handles the HC case.

---

## 5. Parsing strategies

Given a match finder, three strategies turn the stream of candidate matches
into a sequence of `(literal | match)` tokens.

### 5.1 Greedy

```
while pos < end:
    matches = GetMatches(pos)
    if best_match(matches).len >= min_match:
        emit match
        Skip(match.len - 1)
        pos += match.len
    else:
        emit literal buf[pos]
        pos += 1
```

Cheapest possible. Misses the common case where a short match at `pos` blocks
a longer match at `pos + 1`.

### 5.2 Lazy (1-lookahead)

```
while pos < end:
    m1 = best of GetMatches(pos)
    if m1.len >= min_match:
        m2 = best of GetMatches(pos + 1)
        if m2.len > m1.len + 1:
            emit literal buf[pos]
            pos += 1
            use m2 as m1 on next iteration
            continue
        emit match m1
        Skip(m1.len - 1)
        pos += m1.len
    else:
        emit literal; pos += 1
```

The `> len1 + 1` test is the classical lazy-match heuristic — a longer match
one byte later must beat the current match by at least 2 bytes to justify the
extra literal. Deflate uses a variant with `> len1` (no slack) for `level 4+`,
`> len1 + 1` for higher levels. RAR encoder defaults are unknown but any of
these produces a valid archive.

### 5.3 Optimal parsing

Model the block as a DAG: vertex = input position, edges = either "emit
literal, advance 1" or "emit match of length `L`, advance `L`". Edge weights
are bits-to-encode under the current Huffman (or arithmetic) model. Solve for
the minimum-weight path from 0 to `block_end` via dynamic programming:

```
cost[end] = 0
for pos in reverse(range(block_end)):
    cost[pos] = cost[pos+1] + cost_of_literal(buf[pos])
    for (len, dist) in GetMatches(pos):
        c = cost_of_match(len, dist) + cost[pos + len]
        if c < cost[pos]:
            cost[pos] = c
            best[pos] = (len, dist)
```

This requires a **cost function** that assigns a bit cost to each literal,
match length, and match distance. In RAR, the cost function comes from the
Huffman tables in `RAR5_FORMAT_SPECIFICATION.md` §11 and `RAR15_40` §18. The
circularity is resolved by a two-pass approach:

1. **Pass 1 (coarse):** parse greedily, count symbol frequencies, build a
   preliminary Huffman via `HUFFMAN_CONSTRUCTION.md` §3. This gives a cost
   function.
2. **Pass 2 (refine):** re-parse the same block optimally using pass-1 costs,
   count the new frequencies, build a final Huffman, emit.

Some encoders iterate more than twice; the gain after two passes is usually
< 0.5%. LZMA's "normal" mode uses optimal parsing with a range-coder–based
cost model that's continuously updated, so it's effectively one-pass.

### 5.4 Strategy × mode tradeoff

| Mode | Match finder | Parser | Used by |
|---|---|---|---|
| Fast | HC4, low `cutValue` | Greedy | deflate `-1`, store-like |
| Normal | HC4 or BT4 | Lazy | deflate `-6`, RAR `-m2..m3` |
| Best | BT4, high `cutValue` | Optimal (2-pass) | LZMA `-mx9`, RAR `-m5` |

The actual RAR `-m0..-m5` match-finder parameters (cut value, chain size, mode
switches) are **not public** — they are WinRAR parity-only items tracked in
`IMPLEMENTATION_GAPS.md`. Any encoder shipping today picks its own defaults.

---

## 6. RAR-specific constraints

### 6.1 Dictionary sizes

| Version | Dict size | Source |
|---|---|---|
| RAR 1.3 | 4 KB | historical |
| RAR 1.5 | 64 KB (max) | `RAR15_40` §16 |
| RAR 2.0 | 1 MB (fixed, regardless of header value) | `RAR15_40` §16.1 |
| RAR 2.9 / 3.x / 4.x | up to 4 MB | `RAR15_40` §18 |
| RAR 5.0 v0 | `128 KB × 2^N`, `N ∈ [0, 23]` (128 KB to 1 TB theoretical) | `RAR5` §8 |
| RAR 5.0 v1 | `(frac + 32) << (power + 12)`, allows 31 intermediate sizes per power of 2 | `RAR5` §8 |

The match finder's `cyclicBufferSize` must equal `dict_size + 1`.

### 6.2 Minimum and maximum match length

| Version | Min match | Max match |
|---|---|---|
| RAR 2.0 (Unpack20) | 2 | 255 + offset from length table |
| RAR 2.9+ (Unpack29) | 2 | 257 + extra bits (up to ~258 typical) |
| RAR 5.0 (Unpack50/70) | 2 | symbol 262+ slot encodes up to `0x1001FF` ≈ 1 MB |

`lenLimit` passed to `GetMatchesSpec1` / `Hc_GetMatchesSpec` must be clamped
to the per-version maximum.

### 6.3 Distance constraints

- RAR 5.0 rotates a 4-slot rep-distance buffer (`rep0..rep3`). The encoder
  should treat rep matches specially: they have their own cheaper cost in the
  Length table (§11.3), so short matches at a rep distance can be preferred
  even when a longer non-rep match exists.
- Very small distances (1–63 for RAR 3.x, 1–?? for RAR 5.0) use short-match
  fast paths with different Huffman alphabets. The match finder doesn't care;
  the cost function in optimal parsing must price them correctly.

### 6.4 Short-match fast paths

RAR 2.9+ has "short match" symbols (`RAR15_40` §18.3, symbols 263–270) that
encode length-2 matches with a very small distance directly into the Main
table. An optimal parser should consider these alongside regular matches —
the match finder itself returns `(2, delta)` pairs for `delta < 63` and lets
the cost function decide.

### 6.5 Match cost function (the hard part)

This is the single biggest gap *not* filled by this document. Building an
accurate cost function for RAR requires:

- Current Huffman code lengths for the Main, Distance, Length, Align tables
  (from §11 / §18).
- Distance-slot extra-bit counts.
- Length-slot extra-bit counts.
- Rep-distance discount.
- Filter-trigger penalty (filters cost extra symbols in the Main table).

None of that is in the match finder itself. It belongs in the parser. The
match finder just enumerates candidates; the parser multiplies each candidate
by `bits(len, dist)` via the Huffman cost model.

---

## 7. Implementation notes

1. **Don't roll your own hash.** The CRC-based 3-way hash in `LzHash.h` has
   been tuned across decades of LZMA development. Use it.
2. **Cut value is your knob.** Everything else being equal, going from
   `cutValue = 16` to `cutValue = 256` gains 1–3% compression at 10–20× the
   match-finder cost. There is no setting that is always right.
3. **BT4 is worth the 2× memory** for any "good" compression mode. HC4 is for
   fast modes only. Both are implemented in `LzFind.c`; pick at CreateVTable
   time.
4. **Normalization is not optional.** Without it, compression silently breaks
   on inputs > 4 GB.
5. **Skip() is not an optimization, it's correctness.** After emitting a
   match of length `L`, the match finder must see the `L - 1` interior
   positions before the next `GetMatches()` call — otherwise the chain/tree
   loses those positions as future reference points.
6. **Test oracle:** any byte stream that round-trips through a RAR decoder and
   byte-matches the original is a correct match-finder output, regardless of
   what matches were chosen. Compare compressed sizes against the official
   `rar` binary to gauge parser quality.

---

## 8. Reference file map

| File | What's in it |
|---|---|
| `_refs/7zip/C/LzFind.h` | Public interface, `CMatchFinder` struct, vtable |
| `_refs/7zip/C/LzFind.c` | HC4, BT4, Bt3/Bt5/Hc5 variants, normalization, skip |
| `_refs/7zip/C/LzFindMt.c` | Multi-threaded match finder (optional, same API) |
| `_refs/7zip/C/LzFindOpt.c` | SIMD normalization helpers |
| `_refs/7zip/C/LzHash.h` | Hash constants and shift values |
| a public RAR reader | Decoder — the test oracle |
