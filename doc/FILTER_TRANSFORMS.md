# RAR Filter Forward Transforms (Encoder Side)

RAR compression filters preprocess input data before LZ encoding, converting
patterns that LZ cannot match into patterns it can. The decoder reverses
each filter after LZ decoding to restore the original bytes. This document
describes the **forward** (encode-side) transforms for all filter types
used in RAR 2.0 through 7.0 — the decoder transforms are already in the
format specs.

Filter detection heuristics (deciding *when* to apply each filter) are **not**
covered here — they are WinRAR parity-only items in `IMPLEMENTATION_GAPS.md`.
This document only covers the mechanical forward transform, given that an
upstream heuristic has decided to apply it.

References:
- Public RAR readers — all six RAR 3.x standard filters
- Public RAR 5.0 readers — fixed filter executors
- `RAR15_40_FORMAT_SPECIFICATION.md` §20 — RAR 3.x filter framing and CRC fingerprints
- `RAR5_FORMAT_SPECIFICATION.md` §12 — RAR 5.0 hardcoded filter framing

---

## 1. Filter coverage matrix

| Filter    | RAR 3.x (VM) | RAR 5.0 fixed | Shared transform? |
|-----------|:---:|:---:|---|
| E8        | ✓   | ✓   | Yes (type 1)      |
| E8E9      | ✓   | ✓   | Yes (type 2)      |
| DELTA     | ✓   | ✓   | Yes (type 0)      |
| ARM (BL)  | —   | ✓   | 5.0 only          |
| ITANIUM   | ✓   | —   | 3.x only          |
| RGB       | ✓   | —   | 3.x only          |
| AUDIO     | ✓   | —   | 3.x only          |

Where "RAR 3.x (VM)" means executed by the RARVM bytecode engine, and
"RAR 5.0 fixed" means invoked directly by a filter-type enum. The transforms
are behaviorally identical between the two; only the invocation framing differs
(see §8).

---

## 2. E8 (x86 CALL, filter type 1 in RAR 5.0)

### Forward transform

```
def forward_e8(buf, file_offset, file_size = 1 << 24):
    pos = 0
    end = len(buf) - 4
    while pos < end:
        if buf[pos] == 0xE8:
            disp   = read_u32_le(buf, pos + 1)
            offset = (pos + file_offset) & 0xFFFFFFFF
            # Inverse of the decoder rule:
            # decoder writes Addr - Offset if Addr < file_size.
            # We write disp + offset iff the round-trip holds.
            new_addr = (disp + offset) & 0xFFFFFFFF
            if new_addr < file_size:
                # Normal case — decoder's "Addr < FileSize" branch.
                write_u32_le(buf, pos + 1, new_addr)
            elif disp >= 0xFFFFFFFF - offset + 1:
                # Wrap case — decoder's "Addr<0, Addr+Offset>=0" branch.
                # This triggers when disp is "negative" and the round-trip
                # restores it via + file_size.
                write_u32_le(buf, pos + 1, (new_addr - file_size) & 0xFFFFFFFF)
            # else: leave disp unchanged — the decoder would not round-trip.
            pos += 5
        else:
            pos += 1
```

### Why it helps

A near CALL in 32-bit x86 is `E8 xx xx xx xx` where the 4 bytes are a
*relative* 32-bit displacement from the instruction following the CALL
to the call target. Identical functions called from different places in
the binary therefore have *different* 4-byte immediate operands — LZ
cannot match them. The forward transform converts the relative
displacement to an absolute-like value by adding the CALL's position:
identical targets then produce identical 4-byte fields, and LZ matches
explode. On x86 binaries the compression ratio gain is typically 5–15%.

### Trap: file_size is not the actual file size

`file_size = 0x1000000` (16 MB) is a **constant modulus**, not the real
file size. It's the maximum distance at which the transform considers two
calls "related". Setting it higher catches more long-range calls but also
false-positives more random `0xE8` bytes in data sections. RAR's choice of
16 MB has been stable since 2001; don't change it unless you're
intentionally breaking wire format compatibility.

### Encoder-side round-trip check

Because the decoder rejects some transforms (the "else: leave unchanged"
branch above), the encoder must test each candidate `E8` byte: run the
forward transform, then mentally run the reverse transform, and only commit
the transform if the reverse matches the
original. A simpler equivalent: accept the transform iff
`new_addr < file_size or disp > 0xFFFFFFFF - offset`.

---

## 3. E8E9 (x86 CALL + JMP, filter type 2 in RAR 5.0)

Identical to §2 but the outer byte check is `buf[pos] in (0xE8, 0xE9)`.
Both `E8 disp32` (near CALL) and `E9 disp32` (near JMP) share the same
5-byte encoding and the same relative-displacement operand.

Forward and reverse transforms are otherwise identical. Use E8E9 when the
input contains many near-JMP instructions (longer functions, optimized
binaries); use plain E8 when the input is dominated by calls and JMPs are
rare. In practice, E8E9 is almost always the better choice for x86
binaries and E8 is kept mainly for compatibility.

---

## 4. DELTA (type 0 in both RAR 3.x and 5.0)

### Forward transform

```
def forward_delta(buf, channels):
    n = len(buf)
    out = bytearray(n)
    # For each channel, compute byte-to-byte deltas, then pack channels
    # sequentially (not interleaved).
    src_pos = 0
    for c in range(channels):
        prev = 0
        for i in range(c, n, channels):
            out[src_pos] = (prev - buf[i]) & 0xFF
            prev = buf[i]
            src_pos += 1
    return out
```

The output is *not* interleaved by channel — all of channel 0 comes first,
then channel 1, and so on. The decoder re-interleaves on the reverse
pass. This matters for the encoder: after the forward transform, the
output has a sequential per-channel layout, which is what LZ sees.

### Reverse transform

```
src = input
for channel c in 0..channels-1:
    prev = 0
    for dest in (channel, channel + channels, ...):
        out[dest] = prev = (prev - src[pos++]) & 0xFF
```

### Why it helps

Multi-byte signals (PCM, uncompressed bitmap, sensor logs) have strong
inter-sample correlation but near-zero inter-channel correlation. Channel
de-interleaving separates the streams so LZ can exploit intra-channel
repeats. Byte-to-byte delta differencing then converts slowly-varying
signals (common in PCM) to streams of small values near zero, which
compress much better via Huffman than raw samples.

### Channel choice

- 1 channel → raw byte delta, use for 8-bit PCM or flat grayscale
- 2 channels → 16-bit PCM mono (low byte / high byte) or stereo 8-bit
- 3 channels → 24-bit PCM mono, 8-bit RGB, or stereo 12-bit
- 4 channels → 32-bit PCM mono or 8-bit RGBA
- N channels → user override for exotic formats

An encoder that wants to auto-pick should try `channels ∈ {1, 2, 3, 4}`
and measure compressed size of each on a probe block. Typical gain on
PCM: 20–40% vs. no filter.

---

## 5. ARM (BL, filter type 3 in RAR 5.0)

### Forward transform

```
def forward_arm(buf, file_offset):
    pos = 0
    end = len(buf) - 4
    while pos <= end:
        if buf[pos + 3] == 0xEB:
            # 32-bit ARM little-endian; high byte at offset +3.
            insn   = read_u32_le(buf, pos)
            offset = (pos + file_offset) // 4         # PC is in 4-byte units
            # Inverse of the RAR 5.0 ARM filter:
            # reverse does insn = insn - offset; high byte forced to 0xEB.
            # Forward: insn = insn + offset.
            low24  = (insn & 0x00FFFFFF) + offset
            new_insn = (low24 & 0x00FFFFFF) | 0xEB000000
            write_u32_le(buf, pos, new_insn)
        pos += 4
```

### Why it helps

ARM BL (Branch with Link, the ARM equivalent of x86 CALL) is encoded as
`cond=1110 101 L imm24`, where the high byte is `0xEB` for unconditional
BL with link. `imm24` is a 24-bit signed word-offset from the current PC,
giving a ±32 MB branch range. Just like x86 CALL, identical BL targets
from different call sites have different `imm24` values. Adding `PC/4` (the
word-index of the instruction) normalizes them: identical targets become
identical 24-bit fields, and LZ matches again.

### Trap: only the low 24 bits are transformed

The high byte must be left as `0xEB` because the transform only applies to
BL-like instructions. If the encoder accidentally also touches the high
byte, the decoder will mis-recognize the instruction on the reverse pass
and either corrupt it or skip it entirely.

### Trap: only `cond=1110` (AL/Always) BL is touched

The transform's gate is the literal byte equality `D[3] == 0xEB`. The high
byte of an ARM-32 instruction encodes `cond << 4 | top4_of_opcode`, so
`0xEB == 1110 1011` matches **only** the unconditional `BL` instruction
(`cond=1110` AL with link bit set, opcode `1011`). All conditional
variants (`BLEQ`, `BLNE`, `BLLT`, etc., where `cond != 1110`) and all
non-BL instructions whose high byte happens to differ from `0xEB` are
left untransformed by both the encoder and the decoder. This is a
limitation of the filter, not a bug — it keeps the inverse path
trivial. Verified against `_refs/unrar/unpack50.cpp:474`
(`if (D[3]==0xeb)` — sole gate, no condition-code dispatch).

### Trap: there is no alignment scan in the inverse

The RAR 5.0 inverse transform iterates with a fixed 4-byte stride from
`offset 0` of the filter region (`for (CurPos=0; CurPos+3<DataSize;
CurPos+=4)` in `unpack50.cpp:471`). It does **not** scan `pos = 0..3`
for the best alignment — that's an encoder-side heuristic only. The
encoder is responsible for placing the filter region's start so that
the first ARM instruction lands at offset 0 modulo 4; if it doesn't,
the inverse runs against misaligned bytes and corrupts them. In
practice the encoder's filter-detection heuristic should refuse to
emit an ARM filter unless the candidate region is already 4-byte
aligned within the unpacked stream.

ARM-64 (AArch64) uses a completely different BL encoding (6-bit opcode +
26-bit immediate) and this filter **does not work** on it. RAR 5.0 has no
ARM-64 filter; you'll get 5–10% worse compression on ARM-64 binaries than
on 32-bit ARM binaries of similar size.

---

## 6. ITANIUM (RAR 3.x VM only)

Itanium instructions are bundled 128-bit packets containing three 41-bit
operations plus a 5-bit template byte. The filter transforms only branch
instructions (`OpType == 5`) within the bundle and only when the template
byte indicates a valid branch-containing slot.

### Forward transform

```
def forward_itanium(buf, file_offset):
    masks = [4,4,6,6,0,0,7,7,4,4,0,0,4,4,0,0]
    pos = 0
    fo  = file_offset >> 4                     # 16-byte bundle index
    while pos + 21 < len(buf):
        byte0   = buf[pos]
        tmpl    = (byte0 & 0x1F) - 0x10
        if tmpl >= 0:
            cmd_mask = masks[tmpl]
            if cmd_mask != 0:
                for slot in range(3):
                    if cmd_mask & (1 << slot):
                        start = slot * 41 + 5
                        op_type = get_bits(buf, pos, start + 37, 4)
                        if op_type == 5:
                            offset_field = get_bits(buf, pos, start + 13, 20)
                            new_off = (offset_field + fo) & 0xFFFFF
                            set_bits(buf, pos, new_off, start + 13, 20)
        pos += 16
        fo  += 1
```

Where `get_bits` / `set_bits` are 32-bit-wide bit-field read/write helpers at
arbitrary bit positions within a byte buffer.

### Reverse transform

The decoder subtracts `fo` instead of adding — identical structure, opposite
sign. Encoder adds, decoder subtracts.

### When to use

Itanium binaries are extraordinarily rare today. This filter is included
for archive-level compatibility only. An encoder can skip it entirely and
never miss a real-world use case. If an implementer is targeting ONLY
RAR 5.0, skip this filter — it was removed in the 5.0 format redesign.

---

## 7. RGB (RAR 3.x VM only)

This is the most complex filter. It's a 3-channel Paeth-like predictor
operating on raw RGB bitmap scanlines, similar to PNG's filter type 4
(Paeth).

### Forward transform

```
def forward_rgb(buf, width, posR):
    # width  = (R[0] - 3) in the decoder; scanline width in bytes
    # posR   = R[1]; which of the 3 channel slots is the red channel
    #          (0, 1, or 2) — typically 2 for BGR or 0 for RGB
    n        = len(buf)
    channels = 3
    out      = bytearray(n)
    # For each channel, run a Paeth predictor and emit the residual.
    for c in range(channels):
        prev_byte = 0
        for i in range(c, n, channels):
            if i >= width + 3:
                upper        = out[i - width]     # byte one scanline up
                upper_left   = out[i - width - 3] # diagonal neighbor
                predicted    = prev_byte + upper - upper_left
                pa = abs(predicted - prev_byte)
                pb = abs(predicted - upper)
                pc = abs(predicted - upper_left)
                if pa <= pb and pa <= pc:
                    predicted = prev_byte
                elif pb <= pc:
                    predicted = upper
                else:
                    predicted = upper_left
            else:
                predicted = prev_byte
            out[i] = (predicted - buf[i]) & 0xFF  # residual, forward
            prev_byte = buf[i]                    # NOTE: not `out[i]`
    # Red/blue green-subtraction pass (PNG sub-filter variant):
    for i in range(posR, n - 2, 3):
        g = out[i + 1]
        out[i]     = (out[i]     - g) & 0xFF
        out[i + 2] = (out[i + 2] - g) & 0xFF
    return out
```

### Reverse transform

The decoder uses the opposite sign on the residual
(`DestData[I] = Predicted - *SrcData++`) and the G add-back is `+= G`.

### Encoder-side subtlety

The decoder updates `prev_byte = out[i]` (the *reconstructed* byte), but
the encoder must update `prev_byte = buf[i]` (the *original* byte) so that
the predictor matches what the decoder will compute from its own
reconstructed stream. This is the same trick as PNG's filter 4: the
encoder predicts from the original, the decoder predicts from the
reconstructed, and both arrive at the same predictor value because the
residual closes the loop.

### When to use

Raw 24-bit RGB or BGR bitmaps (BMP, uncompressed TGA). Not useful for
already-compressed formats (PNG, JPEG, WebP). Gain on raw bitmaps: 30–50%.
A naive encoder can detect BMP headers by magic bytes and invoke this
filter automatically on the pixel data region.

---

## 8. AUDIO (RAR 3.x VM only)

A 3-tap adaptive linear predictor per channel, with coefficient adaptation
every 32 samples. Structurally identical to the RAR 2.0 audio compression
mode (§17 of `RAR15_40_FORMAT_SPECIFICATION.md`), but 3 taps instead of 5
and used as a filter rather than a block-level compression mode.

### Forward transform

The forward operation is literally `Delta = Predicted - CurByte`, the
inverse of the decoder's `DestData[I] = Predicted - *SrcData++`. Because
the decoder's predictor state update depends only on the *reconstructed*
(post-decode) byte, and the encoder knows that byte (it's the input byte),
both sides maintain identical state.

```
def forward_audio(buf, channels):
    out = bytearray(len(buf))
    for c in range(channels):
        prev_byte  = 0
        prev_delta = 0
        D1 = D2 = D3 = 0
        K1 = K2 = K3 = 0
        dif = [0] * 7
        byte_count = 0
        for i in range(c, len(buf), channels):
            D3 = D2
            D2 = prev_delta - D1
            D1 = prev_delta
            predicted = (8*prev_byte + K1*D1 + K2*D2 + K3*D3) >> 3
            predicted &= 0xFF
            cur_byte  = buf[i]
            out[i]    = (predicted - cur_byte) & 0xFF
            prev_delta = signed_byte(out[i] - prev_byte)  # reconstructed
            prev_byte  = out[i]
            # coefficient adaptation (same as decoder, lines 290-325)
            D = signed_byte(cur_byte) << 3
            dif[0] += abs(D)
            dif[1] += abs(D - D1); dif[2] += abs(D + D1)
            dif[3] += abs(D - D2); dif[4] += abs(D + D2)
            dif[5] += abs(D - D3); dif[6] += abs(D + D3)
            byte_count += 1
            if byte_count & 0x1F == 0:
                min_dif = dif[0]; num_min = 0; dif[0] = 0
                for j in range(1, 7):
                    if dif[j] < min_dif:
                        min_dif = dif[j]; num_min = j
                    dif[j] = 0
                # K[(num_min-1)//2] ± 1 based on parity
                if   num_min == 1 and K1 >= -16: K1 -= 1
                elif num_min == 2 and K1 <  16:  K1 += 1
                elif num_min == 3 and K2 >= -16: K2 -= 1
                elif num_min == 4 and K2 <  16:  K2 += 1
                elif num_min == 5 and K3 >= -16: K3 -= 1
                elif num_min == 6 and K3 <  16:  K3 += 1
    return out
```

### The state-symmetry trap

Note the critical line `prev_byte = out[i]` — the encoder must use the
*residual* (out[i]), **not** the input byte `buf[i]`, because the decoder's
state machine operates on its reconstructed output. Get this wrong and the
decoder will drift within the first few samples and destroy the file.

This is *opposite* to the RGB filter trap in §7, where the encoder must
use `buf[i]` because the RGB predictor operates on the original byte. The
difference: RGB's predictor is a Paeth-like function of neighbors, so it
must be fed the value that will be visible after reverse; audio's
predictor is a linear filter on the *delta history*, which the decoder
builds from its own output.

---

## 9. Framing: how the encoder invokes a filter

### RAR 5.0 (hardcoded filter enum)

See `RAR5_FORMAT_SPECIFICATION.md` §11.11.8. Emit Main symbol 256, then:

```
emit ReadUInt32-format filter_offset (relative to current unpack position)
emit ReadUInt32-format filter_length (bytes covered)
emit 3 bits: filter_type   # 0=DELTA, 1=E8, 2=E8E9, 3=ARM
if filter_type == DELTA:
    emit 5 bits: channel_count - 1
```

`ReadUInt32` here is the filter-local bitstream integer from the RAR5 decoder:
2 bits select 1-4 following little-endian bytes. It is not the archive-wide
RAR5 `vint`.

No bytecode, no VM. Forward transform happens in memory before LZ encoding;
the emitted filter trigger tells the decoder to reverse the same transform
on the decoded output before passing bytes to the user.

### RAR 3.x (RARVM bytecode, standard filters)

See `RAR15_40_FORMAT_SPECIFICATION.md` §20. The encoder emits Main
symbol 256 + bit 0 (filter follows) or Main symbol 257 (alt path), then a
VM filter record. The record begins with one byte whose low 3 bits encode the
length of the following filter payload and whose high 5 bits are flags:

- low 3 bits: payload length selector, per `RAR15_40_FORMAT_SPECIFICATION.md`
  §20.2
- `0x80`: explicit stored-program number follows
- `0x40`: add 258 to the decoded block-start offset
- `0x20`: block length follows
- `0x10`: register-init mask and values follow
- `0x08`: user global data follows

The payload is a RARVM bitstream containing, in order:

- RARVM number: program number, if flag `0x80` is set
- RARVM number: block start offset
- RARVM number: block length, if flag `0x20` is set
- 7-bit register mask plus RARVM-number values, if flag `0x10` is set
- RARVM number: bytecode length, followed by bytecode bytes, if this selects a
  new stored program
- RARVM number: global data size, followed by global data bytes, if flag `0x08`
  is set

Program-number rule: value `0` resets the stored-program table; otherwise the
program index is `value - 1`. If the selected index equals the current stored
program count, this record defines a new program and must include the bytecode.
If the selected index is less than the current count, the record reuses a
previously emitted program and omits the bytecode.

For a **standard filter**, the bytecode is one of six hardcoded blobs.
The invocation state carries the parameters:

| Filter | Encoder-supplied register overrides | Decoder-supplied state |
|--------|-------------------------------------|------------------------|
| E8     | none                                | `R4 = block length`, `R6 = output file position` |
| E8E9   | none                                | `R4 = block length`, `R6 = output file position` |
| ITANIUM| none                                | `R4 = block length`, `R6 = output file position` |
| DELTA  | `R0 = channel count`                | `R4 = block length` |
| RGB    | `R0 = scanline width`, `R1 = posR`  | `R4 = block length` |
| AUDIO  | `R0 = channel count`                | `R4 = block length` |

`R3`, `R4`, `R5`, `R6`, and `R7` are not blob constants. Public readers
initialize them at invocation time: `R3` is the system-global address, `R4` is
the selected block length, `R5` is the stored program execution count, `R6` is
the output position when the filter executes, and `R7` is VM memory size.

### Capturing the bytecode blobs

Neither public reader checked here (`_refs/7zip`, `_refs/XADMaster`, or
libarchive as published upstream) ships the six filter bytecode blobs as
encoder-ready raw bytes. They recognize them by length+CRC32 fingerprint and
execute hardcoded native implementations; they never emit or re-encode the
bytecode.

An encoder has three options:

1. **Blob capture.** Decompress any existing RAR 3.x archive that uses a
   given filter type (a common x86 binary archive for E8/E8E9, a BMP
   archive for RGB, etc.). Dump the first VM program definition of each
   type from the bitstream. Verify by CRC32 against §20's table. Store as
   a static byte array keyed by filter type. One-time cost, produces
   bit-identical output to any existing archive.
2. **Assemble from source.** Find or reconstruct the RARVM assembly source
   for each filter (E8/E8E9 are ~20 lines, DELTA is ~10, RGB is ~60). Hand-
   assemble to bytecode using the RARVM instruction encoding in
   `_refs/7zip/CPP/7zip/Compress/Rar3Vm.cpp`.
   More work, but reproducible without any vintage archive.
3. **Disassemble a vintage WinRAR.** Recover the exact bytecode from a RAR 3.x
   encoder binary.

**Decision: option 1 (blob capture).** Option 2 (hand-assemble) is
work without a corresponding benefit — the captured blobs are
bit-identical to the ones WinRAR emits, so an encoder built on
captured blobs round-trips perfectly. Option 3 (ghidra) is
unnecessary since option 1 already produces byte-identical output
to the WinRAR encoder via publicly observable behavior.

#### Capture procedure

One-time work. Run this procedure once per target environment; the
resulting blob array is a fixed data constant in the encoder.

1. **Source archives.** Obtain a small RAR 3.x archive that exercises
   each standard filter:

   | Filter  | Test archive content |
   |---------|----------------------|
   | E8      | A compiled x86-32 binary (any non-trivial `.exe` or `.elf` built for i386). |
   | E8E9    | Same as E8 — WinRAR picks E8E9 over E8 when both CALL and JMP opcodes are present above a threshold. |
   | ITANIUM | An IA-64 binary. In practice, essentially any IA-64 archive from 2003–2010 WinRAR distribution. |
   | DELTA   | Any file with regular byte-level spatial correlation (uncompressed WAV mono works). |
   | RGB     | An uncompressed 24-bit BMP or TGA. |
   | AUDIO   | An uncompressed multi-channel PCM WAV (stereo is simplest). |

   Create each archive with WinRAR 3.x or 4.x using `rar a -mt1 -m3`
   (default compression, single-threaded to simplify interleaving). Historical
   generator binaries can be kept locally in `_refs/rarbins/`; a public backup
   is available at https://archive.org/details/old_winrar_binaries.

2. **Dump VM program definitions.** Feed the archive to a RAR 3.x
   decoder instrumented to log each
   `Unpack29::ReadVMCode()` call (a public RAR reader).
   When the decoder encounters a filter that has never been seen
   before in the current archive, it reads a fresh bytecode blob
   from the bitstream. Log the bytes.

   Minimal instrumentation: in `Unpack29::ReadVMCode()`, after the
   length prefix is decoded, capture the next `CodeSize` bytes
   verbatim before the interpreter parses them.

3. **Verify CRC32.** Each captured blob's CRC32 must match the table
   in §9.2 above. Any mismatch indicates the blob is a non-standard
   user-supplied filter, not a WinRAR stock filter.

4. **Verify XOR checksum byte.** The blob's first byte must equal the
   XOR of its remaining bytes (§9.3 `XOR integrity byte`). This is
   independent of the CRC32 check; both must pass.

5. **Commit as a constant.** Store the six blobs as a fixed byte array
   keyed by filter type:

   The captured constants are stored in
   `fixtures/rarvm/captured-blobs.md`. The JSONL capture logs are in
   `fixtures/rarvm/capture-logs/`.

   At archive-emit time, the encoder emits one of these blobs as the first
   instance of each filter type, then refers back to it by stored-program index
   on subsequent invocations (see §9.4 RAR 3.x framing). Reuse records still
   carry a new block start, and carry a new block length only when it changes.

Captured result: all six stock blobs match the public length+CRC32
fingerprints and pass the leading-XOR checksum. The committed synthetic
fixture matrix naturally triggers DELTA across RAR 3.00/3.93/4.20 and AUDIO
under RAR 4.20. E8/E8E9 were captured from a local RAR 3.93 executable archive;
RGB and ITANIUM were captured with RAR 3.93 advanced-module forcing
(`-mcC+` and `-mcD- -mcI+`). The capture-only source archives live under
`_refs/rarvm-local/` and are not intended for git.

Option 1's stability assumption is now confirmed for the captured blobs:
their CRC32 values are checked by public readers against hardcoded constants.
Deviation would break existing decoders, so WinRAR cannot change them without a
format version bump.

### Standard filter fingerprints (corrected)

The following CRC32 + length pairs are authoritative (cross-checked against
`_refs/7zip/CPP/7zip/Compress/Rar3Vm.cpp`):

| Length | CRC32      | Filter  |
|-------:|:-----------|:--------|
|     53 | 0xAD576887 | E8      |
|     57 | 0x3CD7E57E | E8E9    |
|    120 | 0x3769893F | ITANIUM |
|     29 | 0x0E06077D | DELTA   |
|    149 | 0x1C2C5DC8 | RGB     |
|    216 | 0xBC85E701 | AUDIO   |

These match the table in `RAR15_40_FORMAT_SPECIFICATION.md` §20.3, which
is the authoritative copy.

### XOR integrity byte

Every RARVM bytecode blob begins with a 1-byte XOR checksum equal to the
XOR of all remaining bytes. Any encoder
emitting a bytecode blob must ensure this byte is correct; the decoder
silently rejects programs that fail the check, and the filter becomes a
no-op.

```
def xor_checksum(blob_bytes):
    return functools.reduce(operator.xor, blob_bytes)

def validate_filter_blob(blob):
    return blob[0] == xor_checksum(blob[1:])
```

---

## 10. Test oracle

For each filter:

1. Generate a test input (random bytes, or a real x86 binary / BMP / PCM).
2. Run the forward transform in this document.
3. Run the reverse transform from the decoder source.
4. Assert byte-exact recovery of the original.
5. Run the forward transform, then LZ-compress; run the reverse after
   decompression. Assert byte-exact recovery.
6. For RAR 3.x, verify the bytecode blob's CRC32 matches §9's table and
   its XOR byte validates.

---

## 11. Recommendation

For a first implementation, target RAR 5.0 only and implement just **E8E9**
and **DELTA**. These two cover:

- All x86 binaries (E8E9 dominant gain)
- All PCM audio (DELTA dominant gain)
- Raw bitmaps (DELTA with channels=3 is acceptable; RGB does better but
  requires more code)

Skip ARM, ITANIUM, RGB, and AUDIO on a first pass — they are either rare
(ITANIUM), RAR 3.x-only (RGB, AUDIO), or subtle (ARM's alignment
detection). Adding them later is purely additive.

Detection heuristics (the Filter Detection Heuristics item in
`IMPLEMENTATION_GAPS.md`) are the genuine unknowns. For E8E9 a simple
threshold — "≥ 0.5% of bytes in the block are `0xE8` or `0xE9`, and the
block is ≥ 4 KB" — catches most cases. For DELTA, a first-order byte
entropy between 4 and 7.5 bits per byte is a good signal (same threshold
as RAR 2.0 audio mode auto-selection in §16.11.6).
