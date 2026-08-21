# PPMd Variant H Algorithm Specification

**Independent documentation derived from publicly available sources:**
- Original algorithm by Dmitry Shkarin (2001), released as public domain
- Igor Pavlov's 7-Zip implementation `Ppmd7.c` (public domain)
- Shkarin, "PPM: One Step to Practicality", DCC 2002
- Shkarin, "Improving the Efficiency of the PPM Algorithm", Problems of
  Information Transmission, Vol. 37, 2001

**All reference source code is public domain.**

---

## Table of Contents

1. [Overview](#1-overview)
2. [Constants](#2-constants)
3. [Data Structures](#3-data-structures)
4. [Memory Management](#4-memory-management)
5. [Model Initialization](#5-model-initialization)
6. [Symbol Decoding](#6-symbol-decoding)
7. [Symbol Encoding](#7-symbol-encoding)
8. [Model Update](#8-model-update)
9. [Secondary Escape Estimation (SEE)](#9-secondary-escape-estimation-see)
10. [Binary Context Optimization](#10-binary-context-optimization)
11. [Rescaling](#11-rescaling)
12. [Range Coder](#12-range-coder)
13. [RAR-Specific Integration](#13-rar-specific-integration)

---

## 1. Overview

PPMd variant H is a context-mixing compression algorithm based on Prediction by
Partial Matching (PPM). It maintains a suffix-linked trie of byte contexts up to
a configurable maximum order, and uses adaptive frequency statistics to predict
the next byte. When a symbol is not found in the current context, an "escape"
event moves to a shorter (lower-order) context.

Key innovations over classical PPM:
- **Secondary Escape Estimation (SEE)**: Adaptive escape probabilities based on
  context features, replacing fixed formulas.
- **Information Inheritance**: When adding a symbol to ancestor contexts after
  escape, its initial frequency is inherited proportionally from the context
  where it was found.
- **Binary context optimization**: Single-symbol contexts use a fast binary
  probability table instead of the full arithmetic coder.

The algorithm is used by RAR 3.x/4.x as an alternative to LZ compression
(selectable per-block).

---

## 2. Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `MAX_FREQ` | 124 | Maximum symbol frequency before rescaling. |
| `UNIT_SIZE` | 12 | Bytes per allocation unit. |
| `PPMD_INT_BITS` | 7 | Binary probability adaptation rate exponent. |
| `PPMD_PERIOD_BITS` | 7 | SEE adaptation period exponent. |
| `PPMD_BIN_SCALE` | 16384 (`1 << 14`) | Binary probability denominator. |
| `PPMD_NUM_INDEXES` | 38 | Number of free-list size buckets. |
| `PPMD7_MIN_ORDER` | 2 | Minimum model order. |
| `PPMD7_MAX_ORDER` | 64 | Maximum model order. |
| `kTopValue` | `1 << 24` | Range coder normalization threshold. |
| `PPMD_N1` | 4 | Free-list bucket group 1 count. |
| `PPMD_N2` | 4 | Free-list bucket group 2 count. |
| `PPMD_N3` | 4 | Free-list bucket group 3 count. |
| `PPMD_N4` | 26 | Free-list bucket group 4 count. |

### Initialization Tables

**kExpEscape** — maps binary probability ranges to initial escape estimates:
```
kExpEscape[16] = {25, 14, 9, 7, 5, 5, 4, 4, 4, 3, 3, 3, 2, 2, 2, 2}
```

**kInitBinEsc** — binary summary initialization values:
```
kInitBinEsc[8] = {0x3CDD, 0x1F3F, 0x59BF, 0x48F3, 0x64A1, 0x5ABC, 0x6632, 0x6051}
```

---

## 3. Data Structures

### 3.1 Symbol State (`CPpmd_State`, 6 bytes packed)

```
struct State {
    symbol:         u8      // byte value
    freq:           u8      // frequency count (max MAX_FREQ = 124)
    successor_low:  u16     // successor pointer, low 16 bits
    successor_high: u16     // successor pointer, high 16 bits
}
```

The successor is a 32-bit offset: `successor_low | (successor_high << 16)`.
It points to either a child context node or a position in the text buffer
(a "virtual" successor that hasn't been expanded into a real context yet).

### 3.2 Context Node (`CPpmd7_Context`, 12 bytes)

```
struct Context {
    num_stats:  u16         // number of symbol states
    summ_freq:  u16         // sum of all symbol frequencies
    stats:      Ref         // pointer to array of States
    suffix:     Ref         // pointer to parent (shorter) context
}
```

When `num_stats == 1`, the single state is stored inline overlapping the
`summ_freq` and `stats` fields (the `OneState` optimization). Access via:
```
one_state(ctx) = reinterpret(State, &ctx.summ_freq)
```

### 3.3 SEE Context (`CPpmd_See`, 4 bytes)

```
struct See {
    summ:   u16     // accumulated escape frequency estimate
    shift:  u8      // adaptation speed (lower = faster)
    count:  u8      // count until next shift increment
}
```

### 3.4 Model State (`CPpmd7`)

```
struct Model {
    // Context pointers
    min_context:    &Context    // context where symbol was found
    max_context:    &Context    // highest-order context attempted
    found_state:    &State      // the state that was matched

    // Order tracking
    order_fall:     uint        // orders descended during escape
    max_order:      uint        // configured maximum order
    init_esc:       uint        // initial escape estimate (from binary)
    prev_success:   uint        // 1 if previous symbol was predicted correctly
    hi_bits_flag:   uint        // high-bit flag for current context

    // Run tracking
    run_length:     i32         // sustained correct prediction count
    init_rl:        i32         // initial run_length value

    // Memory management
    base:           &[u8]       // base of allocated memory
    lo_unit:        &u8         // low allocation pointer (grows up)
    hi_unit:        &u8         // high allocation pointer (grows down)
    text:           &u8         // text buffer pointer
    units_start:    &u8         // boundary between text and units
    size:           u32         // total allocated size
    glue_count:     u32         // countdown to free-block coalescing
    free_list:      [Ref; 38]   // free lists by size bucket

    // Lookup tables
    indx2units:     [u8; 38]    // bucket index → unit count
    units2indx:     [u8; 128]   // unit count → bucket index
    ns2indx:        [u8; 256]   // num_stats → logarithmic index
    ns2bs_indx:     [u8; 256]   // num_stats → binary summary index
    hb2flag:        [u8; 256]   // symbol → high-bit flag

    // Probability tables
    bin_summ:       [[u16; 64]; 128]    // binary context probabilities
    see:            [[See; 16]; 25]     // SEE contexts
    dummy_see:      See                 // SEE for order-(-1)
}
```

---

## 4. Memory Management

All model data is allocated from a single contiguous memory block of
configurable size (1 MB to 256 MB for RAR).

### 4.1 Memory Layout

```
[AlignOffset padding] [Text →  ...  ← UnitsStart ... LoUnit → ... ← HiUnit]
                       ^text grows→                    ^units grow both ways^
```

- **Text area**: grows upward from `base + align_offset`. Stores raw input bytes
  for deferred context creation.
- **Units area**: between `units_start` and the top. `lo_unit` grows up,
  `hi_unit` grows down. Contexts are allocated from `hi_unit`, symbol state
  arrays from `lo_unit`.

### 4.2 Free Lists

38 free lists for blocks of different unit counts. The mapping from unit count
to bucket index:

```
for i = 0, k = 0; i < 38; i++:
    step = if i >= 12 then 4 else (i / 4) + 1
    repeat step times:
        units2indx[k++] = i
    indx2units[i] = k
```

Run that and the 38 bucket sizes come out as:

| Buckets | Step | Unit sizes |
|---|---|---|
| 0–3 | 1 | 1, 2, 3, 4 |
| 4–7 | 2 | 6, 8, 10, 12 |
| 8–11 | 3 | 15, 18, 21, 24 |
| 12–37 | 4 | 28, 32, 36, … 128 |

Note the steps run 1, 2, 3, 4 rather than 1, 2, 4: the third group climbs
by three, so it is 15, 18, 21, 24 and not 12, 16, 20. The largest bucket
is 128 units.

### 4.3 Allocation flow (AllocUnits / AllocUnitsRare / SplitBlock)

`AllocUnits(indx)` is the fast path. It pops the head of `free_list[indx]`
if non-empty; otherwise it tries to bump-allocate from the `[lo_unit,
hi_unit)` region (`lo_unit` advances upward by `bucket_units * UNIT_SIZE`).
If the bump fails, control falls into the rare path.

`AllocUnitsRare(indx)` is the slow path. It:

1. If `glue_count == 0`, runs `GlueFreeBlocks()` (which sets
   `glue_count = 255`), then retries `free_list[indx]`. If a node is now
   available, return it.
2. Walks upward through bucket indices `i = indx+1, indx+2, ...` searching
   for the first non-empty `free_list[i]`.
3. If a larger free block is found, it is removed via `RemoveNode` and
   `SplitBlock(block, i, indx)` cuts it down: the requested-size prefix is
   returned to the caller; the remainder (one to two leftover pieces,
   chosen via `units2indx` / `indx2units` to land on valid bucket sizes)
   is pushed back onto the corresponding free lists via `InsertNode`.
4. If no larger bucket has free blocks, the path runs through the bump-
   allocator one more time and decrements `glue_count` by 1. The bump
   succeeds iff `(units_start − text) > requested_bytes`. When it succeeds,
   the allocator carves the block off the *bottom* of `units_start` (so
   `units_start` shrinks downward) rather than advancing `lo_unit` upward.
   When it fails it returns NULL.

Notes:

- `glue_count` is **decremented only on the bump-fallback path inside
  `AllocUnitsRare`**, not on every alloc call. It is reset to 255 each time
  `GlueFreeBlocks` runs.
- The split step uses `units2indx` to find the target bucket for the
  remainder. If the leftover unit count does not match any bucket exactly,
  the implementation pushes the largest fitting piece first and then the
  trailing residue, so all pieces land on valid bucket sizes.
- Decoder and encoder share this exact allocator. Restart timing is
  driven by the same alloc-failure conditions on both sides, so the
  byte stream stays in sync only if implementations match this flow.

### 4.4 GlueFreeBlocks

GlueFreeBlocks coalesces adjacent free blocks at the *byte-address* level
inside the units region. It does **not** sort offsets; it walks memory
directly using a record-type stamp at offset 0 of each 12-byte unit:

- `CPpmd_State`: `Symbol; Freq` — `Freq != 0` for live state arrays
- `CPpmd7_Context`: `UInt16 NumStats` — `NumStats != 0` for live contexts
- `CPpmd7_Node`: `UInt16 Stamp` — `Stamp == 0` marks a free record (head
  records and the guard at `lo_unit` use `Stamp == 1`)

**The stamp polarity here is inverted from the C reference.** That code stamps
free records `1` and writes `0` into the guard at `LoUnit`; this document uses
`0` for free and `1` for the guard. Both are self-consistent, the values never
leave memory, and neither choice touches the archive. Compare a single line
across the two and it will look like an off-by-one; compare the guard against
the test that reads it (`if node2.Stamp != 0`) and it is the same algorithm.
Pick one polarity and use it in both places.

Algorithm:

```
procedure GlueFreeBlocks():
    glue_count = 255

    // Set a guard node at lo_unit so the merge walk has a stop sentinel.
    if lo_unit != hi_unit:
        ((Node *)lo_unit).Stamp = 1

    // Walk every free_list, threading the freed records into a singly-
    // linked list `head` sorted by allocation history (not by address).
    // Each record gets its node header re-stamped (Stamp = 0, NU = bucket
    // size in units).
    head = 0
    for i in 0..38:
        nu = indx2units[i]
        for node in free_list[i]:        // pops in list order
            node.Stamp = 0
            node.NU = nu
            node.Next = head
            head = node
        clear free_list[i]

    // Glue pass: walk `head`. For each node, attempt to extend it by
    // adding the NU of the *physically next* unit in memory (i.e.,
    // node + node.NU). If that next unit's Stamp == 0 and the combined
    // NU stays below 0x10000, merge by absorbing the trailing block.
    prev = &head
    for n in head-list:
        if n.NU == 0:
            // Already absorbed earlier in this loop, drop it.
            *prev = n.Next
            continue
        prev = &n.Next
        loop:
            node2 = n + n.NU            // pointer arithmetic in units
            new_nu = n.NU + node2.NU
            if node2.Stamp != 0 or new_nu >= 0x10000: break
            n.NU = new_nu
            node2.NU = 0                // mark absorbed; skipped above

    // Fill pass: walk `head` again and re-bucket each surviving block.
    // Runs larger than 128 units are emitted as repeated 128-unit
    // blocks; the trailing residue is split into the largest fitting
    // bucket plus (optionally) a remainder.
    // n is a byte address; nu, k and 128 are unit counts. Every step
    // along n is therefore scaled by UNIT_SIZE.
    for n in head-list:
        nu = n.NU
        while nu > 128:
            InsertNode(n, 37)                     // bucket 37 = 128 units
            n += 128 * UNIT_SIZE; nu -= 128
        i = units2indx[nu - 1]
        if indx2units[i] != nu:
            k = indx2units[i - 1]
            InsertNode(n + k * UNIT_SIZE, nu - k - 1)  // residue, smaller bucket
            i = i - 1                                   // prefix into i-1
        InsertNode(n, i)
```

The scaling is easy to lose because the C reference walks a typed pointer,
where `n += 128` already means 128 units. In a flat byte array it does not,
and the blocks this pass hands back to the free lists will overlap each other
by eleven twelfths of their length.

If `AllocUnitsRare` runs `GlueFreeBlocks` and still cannot satisfy the
allocation, the eventual NULL return propagates up to `UpdateModel` or
`CreateSuccessors`, which call `RestartModel()` per §11.1.

---

## 5. Model Initialization

### 5.1 One-Time Construction

Build the lookup tables (called once, before any Init):

```
// NS2BSIndx: maps num_stats to binary summary group
ns2bs_indx[0] = 0
ns2bs_indx[1] = 2
ns2bs_indx[2..10] = 4
ns2bs_indx[11..255] = 6

// NS2Indx: maps num_stats to logarithmic index (for SEE table row)
ns2indx[0] = 0, ns2indx[1] = 1, ns2indx[2] = 2
for i = 3, m = 3, k = 1; i < 256; i++:
    ns2indx[i] = m
    if --k == 0: k = ++m - 2

// HB2Flag: high-bit flag for symbol values, used as a column adder in
// SEE / BinSumm lookups. Two variants are used by the model:
//
//   hb2flag_3(sym): 0 if sym < 0x40, else 8     (used in MakeEscFreq,
//                                                SEE col, GetBinSumm
//                                                "found_state" term)
//   hb2flag_4(sym): 0 if sym < 0x40, else 16    (used in GetBinSumm
//                                                "one_state" term)
//
// The reference implements these as `(((unsigned)sym + 0xC0) >> (8-n))
// & (1 << n)` for n in {3, 4}; the equivalent table form is shown above.
// Many spec readers see only the n=3 form and miss the n=4 variant,
// which is needed to compute the binary-context BinSumm column.
```

### 5.2 RestartModel (called at Init and on memory exhaustion)

```
procedure RestartModel():
    clear all free_lists to 0
    text = base + align_offset
    hi_unit = text + size
    // Every division here is integer, and the truncation is load-bearing.
    text_units = size / 8 / UNIT_SIZE       // one eighth of the pool, in units
    lo_unit = units_start = hi_unit - text_units * 7 * UNIT_SIZE
    glue_count = 0
    order_fall = max_order
    run_length = init_rl = -(min(max_order, 12)) - 1
    prev_success = 0

    // Create order-0 context with all 256 symbols
    hi_unit -= UNIT_SIZE
    min_context = max_context = Context at hi_unit
    min_context.suffix = 0
    min_context.num_stats = 256
    min_context.summ_freq = 257

    // Allocate states for all 256 symbols
    found_state = State array at lo_unit
    lo_unit += 256 / 2 * UNIT_SIZE    // 128 units = 1536 bytes
    min_context.stats = found_state
    for i = 0 to 255:
        found_state[i].symbol = i
        found_state[i].freq = 1
        found_state[i].successor = 0

    // Initialize binary summary table
    for i = 0 to 127:
        for k = 0 to 7:
            val = PPMD_BIN_SCALE - kInitBinEsc[k] / (i + 2)
            for m = 0 to 63, step 8:
                bin_summ[i][k + m] = val

    // Initialize SEE table
    for i = 0 to 24:
        for k = 0 to 15:
            see[i][k].summ = (5 * i + 10) << (PPMD_PERIOD_BITS - 4)
            see[i][k].shift = PPMD_PERIOD_BITS - 4    // = 3
            see[i][k].count = 4
```

The units region is exactly `7 * text_units` units taken off the top, and
everything the divisions throw away stays in the text region: `size % 96` from
the eighth, plus `size % UNIT_SIZE` from the pool itself. Give that slack to
the units region instead and it holds a few more units than the reference does.

That is not cosmetic. The allocator's job is to run out at the same moment on
both sides, because running out triggers `RestartModel`, and a restart the
encoder made and the decoder did not is a stream that decodes to garbage from
that point. A few units of headroom moves the moment.


### 5.3 Ppmd7_Init

```
procedure Init(max_order):
    self.max_order = max_order
    RestartModel()
    dummy_see.shift = PPMD_PERIOD_BITS  // = 7
    dummy_see.summ = 0
    dummy_see.count = 64
```

---

## 6. Symbol Decoding

The decoder reads symbols one at a time from the range-coded bitstream.

```
function DecodeSymbol(rc: RangeDecoder) -> int:
    char_mask = [0xFF; 256]     // all bits set = not masked

    if min_context.num_stats != 1:
        // Multi-symbol context
        s = stats(min_context)
        count = rc.GetThreshold(min_context.summ_freq)
        hi_cnt = s.freq

        if count < hi_cnt:
            // Found at first position
            rc.Decode(0, s.freq)
            found_state = s
            symbol = s.symbol
            Update1_0()
            return symbol

        prev_success = 0
        for i = 1 to min_context.num_stats - 1:
            s = s.next
            hi_cnt += s.freq
            if hi_cnt > count:
                rc.Decode(hi_cnt - s.freq, s.freq)
                found_state = s
                symbol = s.symbol
                Update1()
                return symbol

        // Escape from this context
        hi_bits_flag = hb2flag[found_state.symbol]
        rc.Decode(hi_cnt, min_context.summ_freq - hi_cnt)
        // Mask all symbols in this context
        for each state in min_context:
            char_mask[state.symbol] = 0
    else:
        // Binary (single-symbol) context. The coder has no DecodeBit: this
        // is GetThreshold(PPMD_BIN_SCALE) against *prob, then Decode(0, *prob)
        // or Decode(*prob, PPMD_BIN_SCALE - *prob). See §12.2.
        prob = GetBinSumm()
        if rc.GetThreshold(PPMD_BIN_SCALE) < *prob:
            rc.Decode(0, *prob)
            *prob = UPDATE_PROB_0(*prob)
            found_state = one_state(min_context)
            symbol = found_state.symbol
            UpdateBin()
            return symbol

        rc.Decode(*prob, PPMD_BIN_SCALE - *prob)
        *prob = UPDATE_PROB_1(*prob)
        init_esc = kExpEscape[*prob >> 10]
        char_mask[one_state(min_context).symbol] = 0
        prev_success = 0

    // Escape loop: descend through suffix chain
    loop:
        num_masked = min_context.num_stats
        loop:
            order_fall += 1
            if min_context.suffix == 0:
                return -1               // end of stream
            min_context = min_context.suffix
        until min_context.num_stats != num_masked

        // Collect non-masked symbols and their frequencies
        s = stats(min_context)
        non_masked_states = []
        hi_cnt = 0
        for each state in min_context:
            if char_mask[state.symbol] != 0:    // not masked
                hi_cnt += state.freq
                non_masked_states.push(state)

        see = MakeEscFreq(num_masked, &esc_freq)
        freq_sum = hi_cnt + esc_freq
        count = rc.GetThreshold(freq_sum)

        if count < hi_cnt:
            // Found symbol after escape
            acc = 0
            for each ns in non_masked_states:
                acc += ns.freq
                if acc > count:
                    rc.Decode(acc - ns.freq, ns.freq)
                    See_Update(see)
                    found_state = ns
                    symbol = ns.symbol
                    Update2()
                    return symbol

        // Escape again
        rc.Decode(hi_cnt, esc_freq)
        see.summ += freq_sum
        for each ns in non_masked_states:
            char_mask[ns.symbol] = 0
```

---

## 7. Symbol Encoding

The encoder mirrors the decoder exactly, using `RangeEnc_Encode` instead of
`GetThreshold` + `Decode`. The model update calls are identical.

```
function EncodeSymbol(rc: RangeEncoder, symbol: int):
    char_mask = [0xFF; 256]

    if min_context.num_stats != 1:
        s = stats(min_context)
        if s.symbol == symbol:
            rc.Encode(0, s.freq, min_context.summ_freq)
            found_state = s
            Update1_0()
            return

        prev_success = 0
        sum = s.freq
        for i = 1 to min_context.num_stats - 1:
            s = s.next
            if s.symbol == symbol:
                rc.Encode(sum, s.freq, min_context.summ_freq)
                found_state = s
                Update1()
                return
            sum += s.freq

        // Escape
        hi_bits_flag = hb2flag[found_state.symbol]
        mask all symbols, encode escape
        rc.Encode(sum, min_context.summ_freq - sum, min_context.summ_freq)
    else:
        prob = GetBinSumm()
        s = one_state(min_context)
        if s.symbol == symbol:
            rc.EncodeBit0(*prob)
            *prob = UPDATE_PROB_0(*prob)
            found_state = s
            UpdateBin()
            return
        else:
            rc.EncodeBit1(*prob)
            *prob = UPDATE_PROB_1(*prob)
            init_esc = kExpEscape[*prob >> 10]
            char_mask[s.symbol] = 0
            prev_success = 0

    // Escape loop (same structure as decoder)
    loop:
        descend to suffix context with more symbols than masked
        compute non-masked frequencies + escape via MakeEscFreq

        for each non-masked state:
            if state.symbol == symbol:
                encode cumulative range, update, return

        encode escape, update see, mask symbols
```

---

## 8. Model Update

After finding a symbol, the model is updated to improve future predictions.

### 8.1 Frequency Updates

Four update variants depending on where the symbol was found:

**Update1_0** — found at first position in multi-symbol context:
```
prev_success = (2 * found_state.freq > min_context.summ_freq)
run_length += prev_success
found_state.freq += 4
min_context.summ_freq += 4
if found_state.freq > MAX_FREQ: Rescale()
NextContext()
```

**Update1** — found at non-first position in multi-symbol context:
```
found_state.freq += 4
min_context.summ_freq += 4
if found_state.freq > found_state.prev.freq:
    swap found_state with predecessor
    if found_state.freq > MAX_FREQ: Rescale()   // INSIDE the swap branch
NextContext()
```

Note the rescale call sits **inside** the swap branch in the reference
implementation. If `freq > MAX_FREQ` but no swap occurred (because the
predecessor's freq is even higher), Rescale is **not** called from
Update1 — the next update will retry. An implementation that hoists the
Rescale outside the swap branch will diverge from the reference at the
exact boundary `freq == MAX_FREQ + 4`, desynchronising encoder and
decoder.

**Update2** — found after escape (in a lower-order context):
```
found_state.freq += 4
min_context.summ_freq += 4
if found_state.freq > MAX_FREQ: Rescale()
run_length = init_rl
UpdateModel()
```

**UpdateBin** — found in binary (single-symbol) context:
```
found_state.freq += (found_state.freq < 128 ? 1 : 0)
prev_success = 1
run_length += 1
NextContext()
```

### 8.2 NextContext

```
procedure NextContext():
    c = successor(found_state) as Context
    if order_fall == 0 and c is in units area (not text area):
        min_context = max_context = c
    else:
        UpdateModel()
```

### 8.3 UpdateModel

Called when the context tree needs to be extended or when a symbol was found
after escaping.

```
procedure UpdateModel():
    // 1. Boost found symbol in parent context
    if found_state.freq < MAX_FREQ / 4 and min_context.suffix != 0:
        c = suffix(min_context)
        if c.num_stats == 1:
            s = one_state(c)
            if s.freq < 32: s.freq += 1
        else:
            find s in stats(c) where s.symbol == found_state.symbol
            if s.freq < MAX_FREQ - 9:
                s.freq += 2
                c.summ_freq += 2

    // 2. Handle successor creation
    if order_fall == 0:
        // Found at max order — create successor chain
        min_context = max_context = CreateSuccessors(skip=true)
        if min_context == null: RestartModel(); return
        set found_state successor to min_context
        return

    // 3. Append symbol to text buffer
    text[text_ptr++] = found_state.symbol
    successor = text_ptr
    if text_ptr >= units_start: RestartModel(); return

    // 4. Resolve existing successor
    f_successor = successor(found_state)
    if f_successor != 0:
        if f_successor <= successor:
            cs = CreateSuccessors(skip=false)
            if cs == null: RestartModel(); return
            f_successor = cs
        if --order_fall == 0:
            successor = f_successor
            text_ptr -= (max_context != min_context)
    else:
        set found_state successor to successor
        f_successor = min_context

    // 5. Add symbol to all ancestor contexts between max and min
    s0 = min_context.summ_freq - min_context.num_stats - (found_state.freq - 1)
    ns = min_context.num_stats

    for c = max_context; c != min_context; c = suffix(c):
        ns1 = c.num_stats

        if ns1 != 1:
            // Expand stats array if needed (when num_stats is even)
            // Adjust summ_freq for relative context sizes
            c.summ_freq += (2*ns1 < ns) + 2*((4*ns1 <= ns) & (c.summ_freq <= 8*ns1))
        else:
            // Convert single-symbol to multi-symbol. The doubling is
            // discontinuous, not a capped double — see "MAX_FREQ rescale
            // boundary behavior" below.
            allocate new stats array
            copy old single state
            if old_freq < MAX_FREQ/4 - 1:      // < 30
                new_freq = old_freq * 2
            else:
                new_freq = MAX_FREQ - 4        // 120
            stats[0].freq = new_freq
            c.summ_freq = new_freq + init_esc + (ns > 3)

        // Compute inherited frequency for the new symbol
        cf = 2 * found_state.freq * (c.summ_freq + 6)
        sf = s0 + c.summ_freq
        if cf < 6 * sf:
            cf = 1 + (cf > sf) + (cf >= 4 * sf)     // result: 1, 2, or 3
            c.summ_freq += 3
        else:
            cf = 4 + (cf >= 9*sf) + (cf >= 12*sf) + (cf >= 15*sf)  // result: 4-7
            c.summ_freq += cf

        // Add symbol to this context
        new_state = stats(c)[ns1]
        new_state.symbol = found_state.symbol
        new_state.freq = cf
        new_state.successor = successor
        c.num_stats = ns1 + 1

    max_context = min_context = Context(f_successor)
```

### 8.4 CreateSuccessors

Walks up the suffix chain finding states whose successors point to the same
text position, then creates actual context nodes for them.

```
function CreateSuccessors(skip: bool) -> Context:
    up_branch = successor(found_state)
    states_to_update = []
    c = min_context

    if not skip:
        states_to_update.push(found_state)

    while c.suffix != 0:
        c = suffix(c)
        s = find state with symbol == found_state.symbol in c
        if successor(s) != up_branch:
            c = Context(successor(s))
            break
        states_to_update.push(s)

    // Compute frequency for new contexts
    up_state.symbol = byte at up_branch
    up_state.successor = up_branch + 1

    if c.num_stats == 1:
        up_state.freq = one_state(c).freq
    else:
        s = find state with symbol == up_state.symbol in c
        cf = s.freq - 1
        s0 = c.summ_freq - c.num_stats - cf
        if 2 * cf <= s0:
            up_state.freq = 1 + (5 * cf > s0)
        else:
            up_state.freq = 1 + (2 * cf + 3 * s0 - 1) / (2 * s0)

    // Create context nodes
    for each state in states_to_update (reverse order):
        c1 = allocate Context
        c1.num_stats = 1
        one_state(c1) = up_state
        c1.suffix = c
        state.successor = c1
        c = c1

    return c
```

---

## 9. Secondary Escape Estimation (SEE)

SEE replaces fixed escape probability formulas with adaptive estimators selected
by context features.

### 9.1 SEE Table Lookup

The SEE table is `see[25][16]`. The row and column are selected by:

```
function MakeEscFreq(num_masked) -> (See, esc_freq):
    non_masked = min_context.num_stats - num_masked

    if min_context.num_stats != 256:
        row = ns2indx[non_masked - 1]
        // wrapping_sub, not a checked one: the suffix context is often the
        // smaller of the two, and the underflow is load-bearing. See below.
        suffix_delta = wrapping_sub(suffix(min_context).num_stats,
                                    min_context.num_stats)
        col = (non_masked < suffix_delta)
            + 2 * (min_context.summ_freq < 11 * min_context.num_stats)
            + 4 * (num_masked > non_masked)
            + hi_bits_flag

        see = see_table[row][col]
        r = see.summ >> see.shift
        see.summ -= r
        esc_freq = r + (r == 0)     // minimum 1
    else:
        see = dummy_see
        esc_freq = 1

    return (see, esc_freq)
```

When `suffix.num_stats < min_context.num_stats`, `suffix_delta` wraps to a
huge value and `non_masked < suffix_delta` is therefore true. That true is the
intended answer, not an accident to be repaired: a checked subtraction raises,
a saturating one returns 0 and flips the comparison to false, and either way
the column is wrong and the decoder diverges from that point on. Use whatever
your language calls wrapping subtraction.

### 9.2 SEE Update

After finding a symbol (not escape):
```
See_Update(see):
    if see.shift < PPMD_PERIOD_BITS and --see.count == 0:
        see.summ <<= 1
        see.count = 3 << see.shift
        see.shift += 1
```

After escape:
```
see.summ += freq_sum    // total frequency of all non-masked symbols + escape
```

---

## 10. Binary Context Optimization

When a context has exactly one symbol (`num_stats == 1`), the range coder
performs a simple binary decision using a 14-bit probability from `bin_summ`.

### 10.1 Probability Lookup

```
function GetBinSumm() -> &u16:
    freq_idx = one_state(min_context).freq - 1      // 0..127
    suffix_ns = suffix(min_context).num_stats
    return &bin_summ[freq_idx][
        prev_success
        + ns2bs_indx[suffix_ns - 1]
        + (hi_bits_flag = hb2flag[found_state.symbol])
        + 2 * hb2flag[one_state(min_context).symbol]
        + ((run_length >> 26) & 0x20)
    ]
```

### 10.2 Probability Update

```
UPDATE_PROB_0(prob) = prob + (1 << 7) - GET_MEAN(prob)     // symbol matched
UPDATE_PROB_1(prob) = prob - GET_MEAN(prob)                 // escape

where GET_MEAN(summ) = (summ + (1 << 5)) >> 7
```

---

## 11. Rescaling

Called when any symbol's frequency exceeds `MAX_FREQ` (124).

```
procedure Rescale():
    stats = stats(min_context)
    s = found_state

    // Move found state to front
    while s != stats:
        swap s with s-1
        s = s-1

    esc_freq = min_context.summ_freq - s.freq
    s.freq += 4
    adder = (order_fall != 0) ? 1 : 0
    s.freq = (s.freq + adder) >> 1
    sum_freq = s.freq

    // Halve all other frequencies, maintain sort order
    for each remaining state (s+1 to end):
        esc_freq -= state.freq
        state.freq = (state.freq + adder) >> 1
        sum_freq += state.freq
        // insertion sort: move state up if freq > predecessor
        if state.freq > state.prev.freq:
            shift state leftward to correct position

    // Remove zero-frequency symbols
    count_removed = count of states with freq == 0
    if count_removed > 0:
        min_context.num_stats -= count_removed
        esc_freq += count_removed

        if min_context.num_stats == 1:
            // Convert to single-symbol context
            tmp = stats[0]
            loop:
                tmp.freq = tmp.freq - (tmp.freq >> 1)
                esc_freq >>= 1
            until esc_freq <= 1
            free stats array
            one_state(min_context) = tmp
            found_state = one_state(min_context)
            return

        shrink stats array if possible

    min_context.summ_freq = sum_freq + esc_freq - (esc_freq >> 1)
    found_state = stats(min_context)
```

### 11.1 Corner Cases and Boundary Conditions

The three edge conditions below trip implementations during testing.
They are derivable from §5.2, §8.3, and §11 above, but implementations
miss them often enough that they warrant explicit coverage.

#### Model restart triggers (full reset vs incremental)

PPMd has exactly **one** recovery mechanism for allocator / model
overflow: a full `RestartModel()` that discards every context, SEE
entry, and statistics array and rebuilds the root context from
scratch. There is no "partial restart" and no "compacting" pass
separate from the free-list glue pass (§4.4). An encoder and decoder
must restart in lockstep — both sides trigger on the same conditions
(model-driven, not data-driven) so the range coder stays in sync.

Restart trigger callsites (in `UpdateModel` and `CreateSuccessors`):

| Site                              | Cause |
|-----------------------------------|-------|
| `UpdateModel` order-fall == 0     | `CreateSuccessors()` returned null at MAX-order successor lookup. |
| `UpdateModel` text-buffer append  | `text >= units_start` after appending the new symbol byte. |
| `UpdateModel` minSuccessor branch | `CreateSuccessors()` returned null when materialising a RAW successor into a real context. |
| `UpdateModel` stats expand        | `AllocUnits(i + 1)` returned null growing a stats array from one bucket to the next. Only fires on the even-to-odd `ns1` transition (when the bucket size actually changes). |
| `UpdateModel` unary → multi       | `AllocUnits(0)` returned null converting a unary context (`NumStats == 1`) to a 2-symbol state array. |
| `CreateSuccessors` chain alloc    | `AllocUnitsRare(0)` returned null inside the loop that builds new context records. Surfaced via the `CreateSuccessors → null` return paths above. |

The model does **not** restart on ordinary escape, on `MAX_FREQ`
rescale, on SEE saturation, or on the rescale-shrink path — those are
in-band operations. (Older spec text listed "ShrinkUnits failure during
rescale" as a restart trigger; this is **not** reachable in the variant
H reference. Rescale's shrink uses `SplitBlock` on the existing oversize
block when the target bucket's free list is empty, so it cannot run out
of memory.)

#### MAX_FREQ rescale boundary behavior

Rescale (§11) halves every symbol's frequency. Three details that the
pseudocode encodes but are easy to misread:

1. **Minimum post-rescale frequency is 1, not 0.** The computation
   `(freq + adder) >> 1` with `adder = 1` in any non-root context
   guarantees `freq ≥ 1` for every surviving symbol (since the
   smallest pre-rescale freq is 1 → `(1 + 1) >> 1 = 1`). A zero-freq
   symbol can only appear at the root context, where `adder = 0` and
   `(1 + 0) >> 1 = 0`. Zero-freq symbols are then **removed** and
   their count added back to `esc_freq`.
2. **When only one symbol survives, the context collapses.** The
   stats array is freed, the surviving state is copied into the
   inline `OneState` slot, and `NumStats` becomes 1. The escape
   frequency is repeatedly halved until `esc_freq ≤ 1` so that the
   unary state's frequency dominates. Implementations must handle
   this degenerate case — a multi-state context can legally become
   unary mid-stream.
3. **Timing: rescale fires after the update, before the next decode.**
   The update path is: decode symbol → update frequencies
   (`Freq += 4`) → if `Freq > MAX_FREQ` rescale → fall through to
   `UpdateModel` tree maintenance. The range coder state is not
   touched by rescale; only the probability model changes.

There is no lazy deferral — rescale must fire on the exact update that
pushes `Freq` past 124. Deferring by one
symbol desyncs encoder and decoder.

#### Information inheritance on escape

When a symbol is added to an ancestor context during `UpdateModel` (§8.3,
step 5), its initial frequency is **not** 1 — it is computed from the
probability ratio in the current (lower-order) context, so that
subsequent entries into the ancestor context predict the symbol with
proportionally similar probability. The formula in §8.3 produces a
value in the range [1, 7], using two branches:

- **Low-probability branch** (`cf < 6 * sf`): `cf ∈ {1, 2, 3}`, and
  `c.summ_freq += 3`.
- **High-probability branch** (`cf ≥ 6 * sf`): `cf ∈ {4, 5, 6, 7}`,
  and `c.summ_freq += cf` (the actual inherited frequency, not the
  constant `3`).

The two branches increment `SummFreq` by different amounts because the
high-probability symbol contributes proportionally more to the
distribution. An encoder/decoder that uses `c.summ_freq += 3` in both
branches will diverge from compatible RAR reader within the first few hundred symbols
of non-trivial input.

#### Order-0 unary context edge case

A context with exactly one symbol (`NumStats == 1`) stores its state
inline (`OneState`) rather than allocating a stats array. Transitions:

- **1 → 2 symbols:** Allocate a stats array, copy `OneState` into
  `stats[0]` with frequency assigned by the **piecewise rule** below
  (this is *not* `min(2*x, MAX_FREQ-4)` — see the warning), set
  `stats[1] = new symbol` with the inherited freq from §8.3, set
  `SummFreq = stats[0].freq + init_esc + (ns > 3)` where `init_esc`
  depends on the escape count at the root (`InitEsc` table).

  Frequency assignment (matches reference):
  ```
  if OneState.freq < (MAX_FREQ / 4 - 1):          // < 30 for MAX_FREQ=124
      stats[0].freq = OneState.freq * 2
  else:
      stats[0].freq = MAX_FREQ - 4                // = 120
  ```

  **Warning — common spec error.** The intuitive formulation
  `min(2*OneState.freq, MAX_FREQ - 4)` is **wrong**. The reference
  caps to 120 as soon as `OneState.freq >= 30`, jumping discontinuously
  over the entire range `[30, 60)` instead of smoothly doubling there.
  Implementations using `min(2*f, 120)` produce frequencies in [60, 120)
  that the reference would have set to exactly 120, which desynchronises
  the next several symbol updates in that subtree.
- **2 → 1 symbol** (post-rescale): See MAX_FREQ rescale point 2 above.
- **Unary context successor:** `OneState.freq` is used as the symbol's
  inherited frequency in `CreateSuccessors` without any branching —
  the `c.num_stats == 1` short-circuit at §8.4 line 628 is the
  special case.

---

## 12. Range Coder

PPMd drives the coder through two operations:
- `GetThreshold(total)` → scaled count for symbol identification
- `Decode(start, size)` → narrow the range once the symbol is known

A binary context uses those same two with `total = PPMD_BIN_SCALE`. The 7z
coder in §12.1 also carries a `DecodeBit` shortcut; the RAR coder in §12.2 has
no such routine, and §12.2 shows the two calls a binary decision reduces to.

### 12.1 7-Zip Range Coder

Used by 7z archives. State: `Range`, `Code`.

**Initialization:**
```
read 1 byte (must be 0)
Code = read 4 bytes big-endian
Range = 0xFFFFFFFF
```

**GetThreshold(total):**
```
Range /= total
return Code / Range
```

**Decode(start, size):**
```
Code -= start * Range
Range *= size
Normalize()
```

**DecodeBit(size0):**
```
bound = (Range >> 14) * size0
if Code < bound:
    Range = bound
    Normalize()
    return 0
else:
    Code -= bound
    Range -= bound
    Normalize()
    return 1
```

**Normalize:**
```
while Range < kTopValue:
    Code = (Code << 8) | read_byte()
    Range <<= 8
```

### 12.2 RAR Range Coder (PpmdRAR variant)

Used by RAR 3.x/4.x archives. State: `Range`, `Code`, `Low`, `Bottom`.

**Initialization:**
```
Code = read 4 bytes big-endian
Range = 0xFFFFFFFF
Low = 0
Bottom = 0x8000
```

There is **no** preamble byte here. The 7z coder in §12.1 skips a leading zero
byte; this one does not, and §12.3 writes none. Consume one anyway and the four
`Code` bytes land shifted by eight bits, which desynchronises the decoder on the
first symbol.

**GetThreshold(total):**
```
Range /= total
return (Code - Low) / Range
```

**Decode(start, size):**
```
Low += start * Range
Range *= size
```

**`Decode` does not normalize.** It is interval arithmetic and nothing
else. Normalization is a separate step the caller runs at two specific
points:

- once per iteration of the escape loop, **before** descending to the
  next shorter context, and
- once after a symbol is decoded, **before** returning it.

Folding the normalize into `Decode` pulls bytes out of the stream during
escape evaluation, when the decoder has not yet committed to a symbol,
and the reads land in the wrong order. The interval update and the byte
refill are on different clocks: every `Decode` narrows, only some
positions refill.

**Binary decisions.** There is no `DecodeBit` in this coder. A binary context
calls the same two operations with `total = PPMD_BIN_SCALE` (`1 << 14`) and a
split point `bs`:

```
threshold = GetThreshold(PPMD_BIN_SCALE)
if threshold < bs:
    Decode(0, bs)                        # bit 0
else:
    Decode(bs, PPMD_BIN_SCALE - bs)      # bit 1
```

Implementations usually inline that. Writing `size0` for `(Range >> 14) * bs`,
with `Range` still holding its pre-shift value:

```
if (Code - Low) < size0:
    Range = size0                                     # bit 0
else:
    Low  += size0
    Range = (Range & ~(PPMD_BIN_SCALE - 1)) - size0    # bit 1
```

Normalization follows on the same schedule as above, from the caller,
not from inside the branch.

The mask on the "1" branch is what `Range * (PPMD_BIN_SCALE - bs)` leaves after
the shift, since `(Range >> 14) << 14` is `Range & ~(PPMD_BIN_SCALE - 1)`. Write
`Range -= size0` instead and the low 14 bits of `Range` survive, which puts the
decoder on a different interval from the encoder that did the multiply.

**Normalize (carry-less variant):**
```
loop:
    if (Low ^ (Low + Range)) >= kTopValue:
        if Range >= Bottom:
            break
        else:
            Range = (-Low) & (Bottom - 1)    // align to Bottom boundary
    Code = (Code << 8) | read_byte()
    Range <<= 8
    Low <<= 8
```

The `Bottom = 0x8000` secondary threshold and the `Low` state variable are the
key differences from the 7z variant. This is the "carry-less" range coder by
Dmitry Subbotin (1999).

### 12.3 RAR Range Encoder (PpmdRAR variant)

Used by RAR 3.x/4.x archives when writing. This is the write-side companion of
Section 12.2 and uses the same Subbotin (1999) carry-less design. State mirrors
the decoder: `Low` (u32), `Range` (u32). Unlike the 7-Zip range encoder
(Section 12.4), there is **no** `Cache` / `CacheSize` buffering — bytes are
emitted directly from the high byte of `Low`, because the carry-less design
guarantees no carry propagation past already-emitted output.

**Constants:**
```
kTop    = 1 << 24    // 0x01000000
kBottom = 1 << 15    // 0x00008000
```

**Initialization:**
```
Low = 0
Range = 0xFFFFFFFF
```
No preamble byte is written at init; the first output byte is produced by
`Normalize()` during the first `Encode()` call.

**Encode(start, size, total):**
```
Low  += start * (Range /= total)
Range *= size
Normalize()
```

**EncodeBit0(size0):**  (binary context, size0 is numerator over `BIN_SCALE`)
```
Range = (Range >> 14) * size0
Normalize()
```

**EncodeBit1(size0):**
```
bound = (Range >> 14) * size0
Low  += bound
Range = (Range & ~(PPMD_BIN_SCALE - 1)) - bound
Normalize()
```
The mask is `PPMD_BIN_SCALE - 1`, clearing 14 bits, not `kBottom - 1`, which
would clear 15. Both this and the decoder's "1" branch (§12.2) are the same
`Range * (PPMD_BIN_SCALE - bs)` written without the multiply.

**Normalize (carry-less):**
```
while (Low ^ (Low + Range)) < kTop
     or (Range < kBottom and ((Range = (-Low) & (kBottom - 1)), true)):
    write_byte(Low >> 24)
    Range <<= 8
    Low   <<= 8
```
Two conditions drive a normalize step:
1. **Top-byte stable.** If `Low` and `Low + Range` share their top byte
   (XOR < `kTop`), that byte can never change regardless of future state, so
   it is safe to emit. This is the carry-less invariant — no deferred-carry
   buffering is needed because the top byte is only emitted once it is frozen.
2. **Underflow avoidance.** If `Range` has shrunk below `kBottom`, force a
   rescale by clamping `Range` to the distance from `Low` up to the next
   `kBottom` boundary (`(-Low) & (kBottom - 1)`), then shift. This throws away
   a sliver of the interval but keeps the coder from stalling, matching the
   decoder's identical fixup in Section 12.2.

**Flush (after the final symbol is encoded):**
```
repeat 4 times:
    write_byte(Low >> 24)
    Low <<= 8
```
Four shift-and-emit steps drain the 32-bit `Low` register so the decoder sees a
complete final interval. (7-Zip's variant-I encoder uses 5 shifts to also drain
the `Cache` slot; the RAR carry-less variant has no such slot.)

**Reference:** `_refs/7zip/C/Ppmd8Enc.c` (same range coder form, paired with
PPMd variant-I model ops); `_refs/7zip/C/Ppmd7aDec.c` (symmetric decoder paired
with PPMd variant-H model ops — the RAR combination).

### 12.4 7-Zip Range Encoder

State: `Low` (u64), `Range` (u32), `Cache` (u8), `CacheSize` (u64).

**Initialization:**
```
Low = 0
Range = 0xFFFFFFFF
Cache = 0
CacheSize = 1
```

**Encode(start, size, total):**
```
Low += start * (Range /= total)
Range *= size
while Range < kTopValue:
    Range <<= 8
    ShiftLow()
```

**EncodeBit0(size0):**
```
Range = (Range >> 14) * size0
while Range < kTopValue:
    Range <<= 8
    ShiftLow()
```

**EncodeBit1(size0):**
```
bound = (Range >> 14) * size0
Low += bound
Range -= bound
while Range < kTopValue:
    Range <<= 8
    ShiftLow()
```

**ShiftLow:**
```
if (Low as u32) < 0xFF000000 or (Low >> 32) != 0:
    temp = Cache
    loop CacheSize times:
        write_byte(temp + (Low >> 32) as u8)
        temp = 0xFF
    Cache = (Low >> 24) as u8
CacheSize += 1
Low = (Low as u32) << 8     // keep only low 32 bits, shift
```

**Flush (after all symbols):**
```
repeat 5 times: ShiftLow()
```

---

## 13. RAR-Specific Integration

### 13.1 Block Parameters

In RAR 3.x/4.x archives, PPMd is selected per-block within the compressed data
stream. The block header provides:

- **Maximum order**: 5 bits, value = `(flags & 0x1F) + 1`. If result > 16:
  `order = 16 + (order - 16) * 3`. Range: 2 to 64.
- **Dictionary size**: 8 bits, value = `(byte + 1)` megabytes. Range: 1 to 256 MB.
- **Escape character**: 8 bits, default 2. The escape byte value triggers
  special operations within the decompressed output.

### 13.2 Escape Processing

When a decoded byte equals the escape value, the next PPM-decoded byte
indicates the action. Confirmed by the RAR 3.00 PPMd archives under
`rar15_40/ppmd/` in the rars tree (the
`NextCh` switch in `Unpack29` / `Unpack29Phase`):

| Value | Meaning |
|-------|---------|
| 0     | End of PPMd block. Switch back to the surrounding stream (call `ReadTables30` for a new LZ block header) and continue. |
| 1     | Literal byte equal to the escape value itself. Emit `PPMEscChar` to the window. |
| 2     | End of file in PPMd mode. Break out of the PPMd decode loop entirely. |
| 3     | RARVM filter program follows (read via `ReadVMCodePPM`). |
| 4     | New LZ-style match. Read **three** further PPM-decoded bytes as a big-endian 24-bit value; the distance is that value **+ 2**. Read one more byte; the match length is that value **+ 32**. Four decoded bytes in total. Does **not** touch the repeat-distance buffer. |
| 5     | One-byte distance RLE match. Read one further PPM-decoded byte giving `length`; copy `length + 4` bytes from distance 1. |
| ≥ 6   | Literal byte equal to the escape value. Same handling as value 1. |

Values 4 and 5 are the "new distance match" and "single-byte-distance
repeat" inside PPMd mode; both feed the LZ window directly without
exiting PPMd mode.

**Neither updates `OldDist`.** In an LZ block every match pushes its
distance into the repeat buffer, so it is natural to assume the same
here. These do not: the match is copied straight into the window and the
repeat buffer keeps whatever the LZ side last put there. A decoder that
pushes here diverges from the encoder the moment the stream returns to
LZ mode and uses a repeat symbol.

The distance field is three bytes, not four. Reading four consumes the
length byte as part of the distance and every byte after that is off by
one.

### 13.3 Model Persistence

The PPMd model can persist across blocks within the same file (solid mode).
If the block reset flag (0x20) is set, the model is reinitialized with new
parameters. If not set, only the range decoder is reinitialized while the
statistical model retains its accumulated context tree.

### 13.4 Range Coder Selection

RAR archives use the PpmdRAR range coder variant — Section 12.2 for reading,
Section 12.3 for writing. The 7-Zip variants (Sections 12.1 and 12.4) are
**not** interoperable: they use a different normalization invariant and a
`Cache` byte-buffering scheme on the encode side that the RAR decoder will not
accept. The PPMd model logic itself (context tree, updates, SEE, rescaling) is
identical across all four range coder variants — only the bit-packing differs.

### 13.5 Encoder Recipe (implementation guide)

To build a RAR-compatible PPMd compressor from the 7-Zip reference sources:

1. **Model:** take `_refs/7zip/C/Ppmd7.c` + `Ppmd7.h` verbatim. This is the
   PPMdH model Shkarin published in 2001 and is what every RAR reader
   implements (both use `MAX_O = 64`, same SEE tables, same escape handling).
2. **Symbol encode loop:** port `Ppmd8_EncodeSymbol` from
   `_refs/7zip/C/Ppmd8Enc.c` but rebind every `Ppmd8_*` model call to its
   `Ppmd7_*` equivalent (`Update1_0`, `Update1`, `Update2`, `UpdateBin`,
   `MakeEscFreq`, `GetContext`, `GetStats`, `GetBinSumm`, `UpdateModel`). The
   control flow — binary-context fast path, linear scan of stats, escape
   descent, SEE update — is unchanged between variants H and I.
3. **Range coder:** use the carry-less encoder from Section 12.3. The
   corresponding C code is already embedded in `Ppmd8Enc.c` (lines 11–64 of
   that file) and transfers directly with no edits.
4. **Block framing:** wrap encoded output with the RAR 3.x block header
   described in Section 13.1 (order, dictionary size, escape character, reset
   flag). On model reset, call `Ppmd7_Init` before the first `EncodeSymbol`.
5. **End of stream:** emit the escape pair `(esc, 0)` (Section 13.2) through
   `EncodeSymbol`, then call `Flush` from Section 12.3. Do not flush before
   the end marker — the decoder reads four trailing bytes to drain its own
   `Range`/`Code` register pair.

Note that `_refs/7zip/C/Ppmd7Enc.c` cannot be used as-is: it is paired with the
**7-Zip** range coder (UInt64 `Low`, `Cache`, `CacheSize`, 5-byte flush) and
would produce a byte stream the RAR decoder rejects.
