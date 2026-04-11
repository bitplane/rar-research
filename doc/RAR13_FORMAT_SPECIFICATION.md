# RAR 1.3/1.4 Archive Format Specification

**Independent documentation derived from publicly available sources:**
- AROS `contrib` repository: `aminet/util/arc/compatible RAR reader/` (old C implementation, public/AROS license)
- `binref/refinery` Python RAR implementation (LGPL-compatible)
- `file` command magic database (public domain)
- Struct definitions and constants from public GitHub mirrors

The AROS implementation is a pre-C++ era public implementation from the
mid-1990s.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Archive Signature](#2-archive-signature)
3. [Archive Layout](#3-archive-layout)
4. [Main Archive Header](#4-main-archive-header)
5. [File Entry Header](#5-file-entry-header)
6. [Compression Algorithm](#6-compression-algorithm)
7. [Encryption](#7-encryption)
8. [Comments](#8-comments)
9. [Multi-Volume Archives](#9-multi-volume-archives)
10. [SFX Archives](#10-sfx-archives)
11. [CRC16](#11-crc16)

---

## 1. Overview

This document covers the RAR archive format used by RAR versions prior to 1.50,
commonly referred to as "RAR 1.3" or "RAR 1.4" (internally designated `RARFMT14`).
This was the original archive format created by Eugene Roshal in 1993 for MS-DOS.

This format was superseded by the RAR 1.5 format (the `Rar!` signature format)
around 1997. It is extremely rare in the wild, but may be encountered on vintage
DOS disk images and BBS archives from 1993-1995.

### Key Characteristics

- 4-byte signature (`RE~^`) instead of 7-byte `Rar!` signature
- No block type field — fixed structure of main header followed by file entries
- 16-bit CRC on file data only (no header CRC)
- Flags are single bytes (not 16-bit words)
- Maximum filename length: 255 bytes (single byte size field)
- Fixed 64 KB sliding window dictionary
- MS-DOS only: OEM charset filenames, DOS attributes, DOS date/time
- No Unicode support, no extended timestamps, no large file support
- Trivial 3-byte stream cipher with subtractive output (effectively broken; see §7)

### Relationship to RAR 1.5+

Despite the format differences, the compression algorithm is shared. RAR 1.3
archives use the same decompression routine (`Unpack15`) as RAR 1.5 archives,
with a fixed 64 KB window. The decompressor is an adaptive Huffman + LZ77
scheme described in Section 6.

---

## 2. Archive Signature

4 bytes: `0x52 0x45 0x7E 0x5E`

This is ASCII `RE~^`.

For comparison:
- RAR 1.5-4.x: `0x52 0x61 0x72 0x21 0x1A 0x07 0x00` (7 bytes, `Rar!...`)
- RAR 5.0+: `0x52 0x61 0x72 0x21 0x1A 0x07 0x01 0x00` (8 bytes)

All three formats share the first byte `0x52` (`R`), allowing quick
differentiation by examining bytes 1-3.

The `file` command identifies these archives as:
```
RAR archive data (<v1.5)
```

---

## 3. Archive Layout

The layout is simpler than all later RAR formats. There are no block type
fields — the structure is implicit:

```
Main Archive Header        (7 bytes minimum)
[Comment data]             (optional, if flags indicate)
File Entry 1
  File Header              (21 bytes + filename)
  Compressed Data          (PackSize bytes)
File Entry 2
  File Header
  Compressed Data
...
(EOF — no end-of-archive marker)
```

There is no end-of-archive block. Parsing continues until either:
- End of file is reached
- A file header fails validation
- The remaining data is too small for a file header (< 21 bytes)

---

## 4. Main Archive Header

Total size: 7 bytes minimum. May be larger if `HeadSize > 7`.

| Offset | Size | Field    | Description |
|--------|------|----------|-------------|
| +0     | 4    | Mark     | Signature: `0x52 0x45 0x7E 0x5E` (`RE~^`). |
| +4     | 2    | HeadSize | Total header size in bytes (little-endian). Minimum 7. |
| +6     | 1    | Flags    | Archive flags (see below). |

If `HeadSize > 7`, the remaining `HeadSize - 7` bytes are an extension whose
shape depends on the flag bits:

- `MHD_COMMENT (0x02)` set → comment data (see §8).
- `MHD_AV (0x20)` set → AV payload (see §4.1).
- Neither set → padding; readers skip to `archive_start + HeadSize`.

The two extension shapes are mutually exclusive in observed RAR 1.40 output
(no fixture or wild archive has been seen with both `MHD_COMMENT` and
`MHD_AV` set on the same main header). Spec leaves the layout undefined when
both bits are set; readers may treat the combination as malformed or skip the
extension entirely. Modern UnRAR (`_refs/unrar/arcread.cpp::ReadHeader14`)
takes the latter approach: it parses neither extension and just advances to
`NextBlockPos = CurBlockPos + HeadSize` regardless of which flags are set.

### Archive Flags

Single byte (not a 16-bit word like RAR 1.5+):

| Bit  | Mask   | Constant        | Meaning |
|------|--------|-----------------|---------|
| 0    | `0x01` | MHD_VOLUME      | Archive is part of a multi-volume set. |
| 1    | `0x02` | MHD_COMMENT     | Archive comment is present. |
| 2    | `0x04` | MHD_LOCK        | Archive is locked (cannot be modified). |
| 3    | `0x08` | MHD_SOLID       | Solid archive. |
| 4    | `0x10` | MHD_PACK_COMMENT | Packed (compressed) comment present. |
| 5    | `0x20` | MHD_AV          | Authenticity-verification (AV) payload is appended to the main header (see §4.1). |
| 6    | `0x40` |                 | Reserved. |
| 7    | `0x80` |                 | Reserved. Always set by the RAR 1.40 encoder; readers should ignore. |

### 4.1 AV payload (when MHD_AV is set)

When the `MHD_AV` flag is set, `HeadSize` is `> 7` and the bytes
between the 7-byte main-header prefix and the end of the main
header (i.e. `HeadSize - 7` bytes at offset `+7..HeadSize-1`)
contain a length-prefixed AV payload:

| Offset | Size | Field      | Description |
|--------|------|------------|-------------|
| `+7`   | 2    | AVSize     | Length of the cipher-output body that follows, little-endian (`uint16`). Total payload = `AVSize + 2`. |
| `+9`   | 6    | AVPrefix   | Fixed constant `0x1A 0x69 0x6D 0x02 0xDA 0xAE` ("`\x1aim\x02\xda\xae`"). Observed identical across every captured RAR 1.40 AV-bearing fixture; treat as a magic. |
| `+15`  | AVSize − 6 | AVCipher | Variable-length cipher output of the legacy `'0'` AV transform. The transform is documented in `RAR15_40_FORMAT_SPECIFICATION.md §10.8` (RAR 2.x's compatibility implementation of the same scheme). The exact length depends on the cipher mode: byte-stream mode produces 6+38 = 44 bytes (typical for a small archive), block mode produces a multiple of 16 bytes. |

The AV-bearing main header therefore has total `HeadSize = 9 +
AVSize` (= 7-byte fixed prefix + 2-byte AVSize field +
`AVSize`-byte body).

Verified against the paired fixtures
`fixtures/1.402/rar140_av/rar140_{noav_baseline,av_patched}.rar`
(generated by the RAR 1.40 encoder under DOSBox-X with the
`research/re/rar140/scripts/patch_force_registered.py`
registration patch applied).

A real registered build would populate the BSS-resident registration
data (creator name, signing key) before invoking the cipher; the
patched-build fixture exercises the cipher over BSS-zero data so
the body is deterministic but not a "real" signature. The block
*layout* is byte-identical to what a real registered build would
produce.

---

## 5. File Entry Header

Each file entry consists of a fixed 21-byte header, followed by the filename,
followed by the compressed data.

| Offset | Size | Field     | Type   | Description |
|--------|------|-----------|--------|-------------|
| +0     | 4    | PackSize  | uint32 | Compressed data size (little-endian). |
| +4     | 4    | UnpSize   | uint32 | Uncompressed file size (little-endian). |
| +8     | 2    | FileCRC   | uint16 | 16-bit rolling checksum of uncompressed file data (little-endian). See §11. |
| +10    | 2    | HeadSize  | uint16 | Total header size including filename (little-endian). Should equal `21 + NameSize`. |
| +12    | 4    | FileTime  | uint32 | Modification time in MS-DOS format (little-endian). See Appendix A. |
| +16    | 1    | FileAttr  | uint8  | MS-DOS file attributes. Bit `0x10` = directory. |
| +17    | 1    | Flags     | uint8  | File entry flags (see below). |
| +18    | 1    | UnpVer    | uint8  | Decompressor version required (see below). |
| +19    | 1    | NameSize  | uint8  | Filename length in bytes (max 255). |
| +20    | 1    | Method    | uint8  | Compression method (0-5). |
| +21    | NameSize | FileName | bytes | Filename in OEM (DOS codepage) encoding. |

After the header (`21 + NameSize` bytes), `PackSize` bytes of compressed data
follow.

### File Entry Flags

Single byte:

| Bit  | Mask   | Constant         | Meaning |
|------|--------|------------------|---------|
| 0    | `0x01` | LHD_SPLIT_BEFORE | File continued from previous volume. |
| 1    | `0x02` | LHD_SPLIT_AFTER  | File continued in next volume. |
| 2    | `0x04` | LHD_PASSWORD     | File is encrypted. |
| 3    | `0x08` | LHD_COMMENT      | File comment present. |
| 4    | `0x10` | LHD_SOLID        | Solid flag (uses data from previous files). |
| 5-7  |        |                  | Reserved. |

### UnpVer (Decompressor Version)

The raw byte value maps to the decompressor version. Two implementation
strategies are observed in the wild:

| Raw Value | Internal version | Meaning |
|-----------|------------------|---------|
| 2         | 13               | Compressed with RAR 1.3 / Unpack15 codec. |
| anything else | 10           | Stored or minimal compression — reader skips Huffman/LZ and emits the raw payload bytes (subject to method-byte further refinement). |

Modern UnRAR (`_refs/unrar/arcread.cpp::ReadHeader14`) implements exactly
this mapping: `FileHead.UnpVer = (raw == 2) ? 13 : 10` with no rejection.
The AROS reader is stricter and rejects raw values `0` and `>20` outright
(see §5 "Header Validation" below); both behaviors are valid against
known RAR 1.40 output, which only ever emits raw `2` for compressed and
raw `1` for stored.

Both internal versions dispatch to the same `Unpack15` codec implementation
(Section 6); the wire byte just selects whether the decompressor pipeline runs
or the data is treated as stored. Internal "version 13" is the name `READ_SIDE_OVERVIEW.md` §6.1 uses
for the dispatch table — the wire byte itself is `2`. A reader that exposes
"version" to the user should map wire→internal via this table.

### Compression Method

| Value | Name    | Description |
|-------|---------|-------------|
| 0     | Store   | No compression (stored). |
| 1     | Fastest | Fastest compression. |
| 2     | Fast    | Fast compression. |
| 3     | Normal  | Normal compression. |
| 4     | Good    | Good compression. |
| 5     | Best    | Best compression. |

Note: these are stored as raw values 0-5. In RAR 1.5+, the same methods are
encoded as `0x30`-`0x35` (with `0x30` added).

### Header Validation

The following conditions indicate an invalid file header (from the AROS
implementation):

```
if Method > 7
   or NameSize == 0
   or NameSize > 80
   or UnpVer == 0
   or UnpVer > 20
   or HeadSize <= 21
   or (Flags < 8 and HeadSize != 21 + NameSize):
    invalid header
```

Note: the `NameSize > 80` limit is an implementation constraint from the DOS
era (8.3 filenames plus path). Later implementations may accept up to 255.

### File Attributes

MS-DOS file attributes (always DOS, regardless of creating system):

| Bit  | Mask   | Meaning |
|------|--------|---------|
| 0    | `0x01` | Read-only. |
| 1    | `0x02` | Hidden. |
| 2    | `0x04` | System. |
| 3    | `0x08` | Volume label. |
| 4    | `0x10` | Directory. |
| 5    | `0x20` | Archive. |

---

## 6. Compression Algorithm

RAR 1.3 uses an adaptive Huffman coding scheme combined with LZ77 dictionary
compression. The dictionary is a fixed 64 KB (0x10000 byte) sliding window.
This is the same algorithm used by RAR 1.5 (`Unpack15`).

### 6.1 Overview

The decompressor operates in three modes, selected dynamically by flag bits in
the compressed data stream:

| Mode | Function | Description |
|------|----------|-------------|
| ShortLZ | Short-distance LZ matches | Small offsets, length 2+. |
| LongLZ | Long-distance LZ matches | Large offsets, length 3+. |
| HuffDecode | Literal byte output | Adaptive Huffman-coded bytes. |

Mode selection uses a flag byte system and running statistics (`Nlzb`, `Nhfb`)
to choose between LZ and Huffman decoding. A special "StMode" (static mode) is
entered after 16 consecutive Huffman decodes without flag consumption.

### 6.2 Bitstream

The compressed data is read as a big-endian bitstream. Two operations are used:
- `GetBits()`: read the next 16 bits from the stream into `BitField`.
- `AddBits(n)`: consume `n` bits from the stream.

### 6.3 Adaptive Huffman Tables

Four character sets maintain frequency statistics:

| Table  | Purpose |
|--------|---------|
| ChSet  | Literal byte Huffman frequencies. |
| ChSetA | Short LZ distance frequencies. |
| ChSetB | Long LZ distance frequencies. |
| ChSetC | Flag byte frequencies. |

Each table contains 256 entries. Each entry packs a character value in the high
byte and a frequency ranking in the low byte.

Corresponding placement arrays (`NToPl`, `NToPlB`, `NToPlC`) track the reverse
mapping from frequency rank to table position.

### 6.4 Table Initialization

```
for i in 0..255:
    ChSet[i]  = i << 8          # character i, rank 0
    ChSetB[i] = i << 8
    ChSetA[i] = i
    ChSetC[i] = ((~i + 1) & 0xFF) << 8   # inverted order

NToPl  = all zeros
NToPlB = all zeros
NToPlC = all zeros

CorrHuff(ChSetB, NToPlB)   # normalize ChSetB rankings
```

### 6.5 CorrHuff (Frequency Correction)

When a frequency counter overflows (`(entry & 0xFF) > 0xA1` for ChSet, or
`(entry & 0xFF) == 0` for ChSetB/ChSetC), the table is renormalized:

```
procedure CorrHuff(CharSet, NumToPlace):
    for rank = 7 down to 0:
        for j = 0..31:
            CharSet[current++] = (CharSet[current] & 0xFF00) | rank

    NumToPlace = all zeros
    for rank = 6 down to 0:
        NumToPlace[rank] = (7 - rank) * 32
```

This divides the 256 entries into 8 groups of 32, assigning ranks 7 down to 0.

### 6.6 DecodeNum (Number Decoding)

A shared function for decoding variable-length numbers from the bitstream:

```
function DecodeNum(Num, StartPos, DecTab, PosTab) -> uint:
    Num = Num & 0xFFF0
    i = 0
    while DecTab[i] <= Num:
        StartPos++
        i++
    AddBits(StartPos)
    return ((Num - (i > 0 ? DecTab[i-1] : 0)) >> (16 - StartPos)) + PosTab[StartPos]
```

**Loop termination.** The mask `Num &= 0xFFF0` guarantees `Num <= 0xFFF0`,
and every `DecTab[]` array in §6.7 ends with one or more `0xFFFF` entries.
Since `0xFFFF > 0xFFF0`, the comparison `DecTab[i] <= Num` is guaranteed to
become false at or before the first `0xFFFF` slot — so `i` never advances
past the array. The trailing `0xFFFF` entries are explicit sentinels: an
implementation may rely on them and does not need a separate bounds check.
Verified against `_refs/unrar/unpack15.cpp:493` (`Unpack::DecodeNum`).

### 6.7 Decode Tables

Six sets of decode/position tables are used by `DecodeNum`:

```
STARTL1 = 2
DecL1 = [0x8000, 0xA000, 0xC000, 0xD000, 0xE000, 0xEA00,
         0xEE00, 0xF000, 0xF200, 0xF200, 0xFFFF]
PosL1 = [0, 0, 0, 2, 3, 5, 7, 11, 16, 20, 24, 32, 32]

STARTL2 = 3
DecL2 = [0xA000, 0xC000, 0xD000, 0xE000, 0xEA00, 0xEE00,
         0xF000, 0xF200, 0xF240, 0xFFFF]
PosL2 = [0, 0, 0, 0, 5, 7, 9, 13, 18, 22, 26, 34, 36]

STARTHF0 = 4
DecHf0 = [0x8000, 0xC000, 0xE000, 0xF200, 0xF200, 0xF200,
          0xF200, 0xF200, 0xFFFF]
PosHf0 = [0, 0, 0, 0, 0, 8, 16, 24, 33, 33, 33, 33, 33]

STARTHF1 = 5
DecHf1 = [0x2000, 0xC000, 0xE000, 0xF000, 0xF200, 0xF200,
          0xF7E0, 0xFFFF]
PosHf1 = [0, 0, 0, 0, 0, 0, 4, 44, 60, 76, 80, 80, 127]

STARTHF2 = 5
DecHf2 = [0x1000, 0x2400, 0x8000, 0xC000, 0xFA00, 0xFFFF,
          0xFFFF, 0xFFFF]
PosHf2 = [0, 0, 0, 0, 0, 0, 2, 7, 53, 117, 233, 0, 0]

STARTHF3 = 6
DecHf3 = [0x0800, 0x2400, 0xEE00, 0xFE80, 0xFFFF, 0xFFFF,
          0xFFFF]
PosHf3 = [0, 0, 0, 0, 0, 0, 0, 2, 16, 218, 251, 0, 0]

STARTHF4 = 8
DecHf4 = [0xFF00, 0xFFFF, 0xFFFF, 0xFFFF, 0xFFFF, 0xFFFF]
PosHf4 = [0, 0, 0, 0, 0, 0, 0, 0, 0, 255, 0, 0, 0]
```

### 6.8 Short Match Tables

Used by the `ShortLZ` function to decode short match lengths:

```
ShortLen1 = [1, 3, 4, 4, 5, 6, 7, 8, 8, 4, 4, 5, 6, 6, 4, 0]
ShortXor1 = [0x00, 0xA0, 0xD0, 0xE0, 0xF0, 0xF8, 0xFC, 0xFE,
             0xFF, 0xC0, 0x80, 0x90, 0x98, 0x9C, 0xB0]

ShortLen2 = [2, 3, 3, 3, 4, 4, 5, 6, 6, 4, 4, 5, 6, 6, 4, 0]
ShortXor2 = [0x00, 0x40, 0x60, 0xA0, 0xD0, 0xE0, 0xF0, 0xF8,
             0xFC, 0xC0, 0x80, 0x90, 0x98, 0x9C, 0xB0]
```

Note: `ShortLen1[1]` and `ShortLen2[3]` are dynamically modified to
`Buf60 + 3`, where `Buf60` toggles between 0 and 1.

**Buf60 semantics.**

- Initial value at decoder/encoder start: `0` (set by `UnpInitData15`,
  see §6.10). With `Buf60 == 0` the static-table values `ShortLen1[1] = 3`
  and `ShortLen2[3] = 3` are in effect. With `Buf60 == 1` both become `4`.
- Toggle trigger: only inside ShortLZ on the *rep-match* path (§6.13)
  with decoded short length `SaveLength == 10` and the subsequent
  `DecodeNum(STARTL1, DecL1, PosL1) + 2 == 0x101` (i.e. raw decoded
  value `0xFF`). On that exact pair, `Buf60 ^= 1` and the rep-match is
  *not* emitted — the encoder is signalling a state change only.
- The toggle persists across symbols until the next trigger event;
  there is no implicit reset. (`_refs/unrar/unpack15.cpp:117-118`,
  `:194-195`.)

In implementation, declaring `ShortLen1` / `ShortLen2` as constants and
using accessor macros that substitute `Buf60 + 3` for indices 1 and 3
respectively is preferable to mutating the tables — keeps the codec
state thread-friendly and matches the upstream macro form.

### 6.9 State Variables

| Variable | Initial Value | Description |
|----------|--------------|-------------|
| AvrPlc   | 0x3500       | Average Huffman placement (selects HuffDecode table). |
| AvrPlcB  | 0            | Average LongLZ distance placement. |
| AvrLn1   | 0            | Average ShortLZ length. |
| AvrLn2   | 0            | Average LongLZ length. |
| AvrLn3   | 0            | Auxiliary length average. |
| MaxDist3 | 0x2001       | Distance threshold for length bonus. |
| Nhfb     | 0x80         | Huffman frequency bias. |
| Nlzb     | 0x80         | LZ frequency bias. |
| NumHuf   | 0            | Consecutive Huffman decode count. |
| Buf60    | 0            | Short table modifier toggle (0 or 1). |
| StMode   | 0            | Static mode flag. |
| LCount   | 0            | Last-match repeat counter. |
| FlagBuf  | 0            | Current flag byte (shifted left as consumed). |
| FlagsCnt | 0            | Remaining flag bits. |
| OldDist[4] | all 1-bits (`(size_t)-1`) on non-solid; carries on solid | Repeat distance buffer (4 entries). The all-ones sentinel exceeds any valid 64-KiB-window distance, so a rep-match against an unwritten slot dereferences out-of-window memory and must be rejected. |
| OldDistPtr | 0            | Write cursor into `OldDist[]`, always masked `& 3`. New matches go to `OldDist[OldDistPtr]` then `OldDistPtr = (OldDistPtr+1) & 3`; ShortLZ rep-matches with decoded length `9..12` read from `OldDist[(OldDistPtr - (Length-9)) & 3]` (0 = most recent, 3 = oldest). |
| LastDist | `(uint)-1`     | Most recent match distance (used by `LCount==2` immediate-repeat path in ShortLZ). |
| LastLength | 0            | Most recent match length (used by the same immediate-repeat path; 0 means "no prior match" so the path is effectively disabled until the first real match). |

### 6.10 Main Decode Loop

`OldUnpInitData(solid)` is the codec-state reset, split across the shared
`UnpInitData` (`_refs/unrar/unpack.cpp:206`) and the codec-specific
`UnpInitData15` (`unpack15.cpp:430`). On a non-solid file it resets:

| State | Reset value |
|-------|-------------|
| `OldDist[0..3]` | `(size_t)-1` |
| `OldDistPtr` | `0` |
| `LastDist` | `(uint)-1` |
| `LastLength` | `0` |
| `BlockTables` | zero-filled |
| `UnpPtr`, `WrPtr`, `PrevPtr` | `0` |
| `FirstWinDone` | `false` |
| `AvrPlcB`, `AvrLn1`, `AvrLn2`, `AvrLn3`, `NumHuf`, `Buf60` | `0` |
| `AvrPlc` | `0x3500` |
| `MaxDist3` | `0x2001` |
| `Nhfb`, `Nlzb` | `0x80` |

These are **always** reset (independent of the solid flag):
`FlagsCnt = 0`, `FlagBuf = 0`, `StMode = 0`, `LCount = 0`, `ReadTop = 0`.

On a solid file none of the above carry-over state is touched; only the
unconditional fields and the input bit cursor are reset.

**Window contents on non-solid.** The 64-KiB window is *not* explicitly
zero-filled. `CopyString15` (`_refs/unrar/unpack15.cpp:474`) instead guards
the copy: while `FirstWinDone == false`, any back-reference whose
`Distance > UnpPtr` (or `Distance == 0`, or `Distance > MaxWinSize`)
emits zero bytes instead of reading the window. So an encoder may assume
every byte before its first match is implicitly zero, and a decoder
implementation may leave the window in whatever state it had previously
without correctness consequences.

```
OldUnpInitData(solid)
if not solid:
    InitHuff()
    UnpPtr = 0

DestUnpSize -= 1
if DestUnpSize >= 0:
    GetFlagsBuf()       # read first flag byte via Huffman
    FlagsCnt = 8

while DestUnpSize >= 0:
    UnpPtr &= 0xFFFF    # wrap within 64KB window

    if StMode:
        HuffDecode()
        continue

    FlagsCnt -= 1
    if FlagsCnt < 0:
        GetFlagsBuf()
        FlagsCnt = 7

    if FlagBuf & 0x80:                    # bit 1
        FlagBuf <<= 1
        if Nlzb > Nhfb:
            LongLZ()
        else:
            HuffDecode()
    else:
        FlagBuf <<= 1
        FlagsCnt -= 1
        if FlagsCnt < 0:
            GetFlagsBuf()
            FlagsCnt = 7

        if FlagBuf & 0x80:                # bit 2
            FlagBuf <<= 1
            if Nlzb > Nhfb:
                HuffDecode()
            else:
                LongLZ()
        else:
            FlagBuf <<= 1
            ShortLZ()                      # bit 3
```

The flag bits select between three modes using a 2-bit prefix tree:
- `1x` → LZ or Huffman (based on Nlzb vs Nhfb bias)
- `01` → Huffman or LZ (opposite of above)
- `00` → ShortLZ

### 6.11 GetFlagsBuf

Reads a flag byte from the ChSetC adaptive Huffman table:

```
procedure GetFlagsBuf():
    BitField = GetBits()
    FlagsPlace = DecodeNum(BitField, STARTHF2, DecHf2, PosHf2)

    loop:
        Flags = ChSetC[FlagsPlace]
        FlagBuf = Flags >> 8
        NewFlagsPlace = NToPlC[Flags & 0xFF]
        NToPlC[Flags & 0xFF] += 1
        Flags += 1

        if (Flags & 0xFF) == 0:
            CorrHuff(ChSetC, NToPlC)
        else:
            break

    ChSetC[FlagsPlace] = ChSetC[NewFlagsPlace]
    ChSetC[NewFlagsPlace] = Flags
```

### 6.12 HuffDecode (Literal Byte Output)

```
procedure HuffDecode():
    BitField = GetBits()

    # Select decode table based on running average
    if AvrPlc > 0x75FF:
        BytePlace = DecodeNum(BitField, STARTHF4, DecHf4, PosHf4)
    elif AvrPlc > 0x5DFF:
        BytePlace = DecodeNum(BitField, STARTHF3, DecHf3, PosHf3)
    elif AvrPlc > 0x35FF:
        BytePlace = DecodeNum(BitField, STARTHF2, DecHf2, PosHf2)
    elif AvrPlc > 0x0DFF:
        BytePlace = DecodeNum(BitField, STARTHF1, DecHf1, PosHf1)
    else:
        BytePlace = DecodeNum(BitField, STARTHF0, DecHf0, PosHf0)

    BytePlace &= 0xFF

    if StMode:
        # Static mode: special handling
        if BytePlace == 0 and BitField > 0xFFF:
            BytePlace = 0x100
        BytePlace -= 1
        if BytePlace == -1:
            BitField = GetBits()
            AddBits(1)
            if BitField & 0x8000:
                NumHuf = 0
                StMode = 0      # exit static mode
                return
            else:
                Length = 4 if (BitField & 0x4000) else 3
                AddBits(1)
                Distance = DecodeNum(GetBits(), STARTHF2, DecHf2, PosHf2)
                Distance = (Distance << 5) | (GetBits() >> 11)
                AddBits(5)
                CopyString(Distance, Length)
                return
    else:
        if NumHuf >= 16 and FlagsCnt == 0:
            StMode = 1          # enter static mode

    # Update statistics
    AvrPlc += BytePlace
    AvrPlc -= AvrPlc >> 8

    Nhfb += 16
    if Nhfb > 0xFF:
        Nhfb = 0x90
        Nlzb >>= 1

    # Output literal byte
    output_byte(ChSet[BytePlace] >> 8)
    DestUnpSize -= 1

    # Update adaptive table
    loop:
        CurByte = ChSet[BytePlace]
        NewBytePlace = NToPl[CurByte & 0xFF]
        NToPl[CurByte & 0xFF] += 1
        CurByte += 1

        if (CurByte & 0xFF) > 0xA1:
            CorrHuff(ChSet, NToPl)
        else:
            break

    ChSet[BytePlace] = ChSet[NewBytePlace]
    ChSet[NewBytePlace] = CurByte
```

### 6.13 ShortLZ (Short-Distance Matches)

Handles matches with small distances. Minimum match length is 2.

```
procedure ShortLZ():
    NumHuf = 0
    BitField = GetBits()

    if LCount == 2:
        AddBits(1)
        if BitField >= 0x8000:
            CopyString(LastDist, LastLength)
            return
        BitField <<= 1
        LCount = 0

    BitField >>= 8

    # Select table based on average length
    if AvrLn1 < 37:
        for Length = 0..:
            mask = ~(0xFF >> ShortLen1[Length])   # note: [1] uses Buf60+3
            if (BitField ^ ShortXor1[Length]) & mask == 0:
                break
        AddBits(ShortLen1[Length])
    else:
        for Length = 0..:
            mask = ~(0xFF >> ShortLen2[Length])   # note: [3] uses Buf60+3
            if (BitField ^ ShortXor2[Length]) & mask == 0:
                break
        AddBits(ShortLen2[Length])

    if Length >= 9:
        if Length == 9:
            LCount += 1
            CopyString(LastDist, LastLength)     # repeat last match
            return

        if Length == 14:
            LCount = 0
            Length = DecodeNum(GetBits(), STARTL2, DecL2, PosL2) + 5
            Distance = (GetBits() >> 1) | 0x8000
            AddBits(15)
            LastLength = Length
            LastDist = Distance
            CopyString(Distance, Length)
            return

        # Length 10-13: repeat distance match
        LCount = 0
        SaveLength = Length
        Distance = OldDist[(OldDistPtr - (Length - 9)) & 3]
        Length = DecodeNum(GetBits(), STARTL1, DecL1, PosL1) + 2

        if Length == 0x101 and SaveLength == 10:
            Buf60 ^= 1     # toggle short table modifier
            return

        if Distance > 256:   Length += 1
        if Distance >= MaxDist3: Length += 1

        OldDist[OldDistPtr] = Distance
        OldDistPtr = (OldDistPtr + 1) & 3
        LastLength = Length
        LastDist = Distance
        CopyString(Distance, Length)
        return

    # Length 0-8: new short match
    LCount = 0
    AvrLn1 += Length
    AvrLn1 -= AvrLn1 >> 4

    DistancePlace = DecodeNum(GetBits(), STARTHF2, DecHf2, PosHf2) & 0xFF
    Distance = ChSetA[DistancePlace]
    if DistancePlace > 0:
        # MTF for ChSetA: swap the decoded entry with its immediate
        # predecessor, moving frequently-decoded distances one slot
        # toward index 0. Unlike ChSet/ChSetB/ChSetC, ChSetA does not
        # use the (CharSet, NumToPlace) frequency-corrected scheme —
        # the swap-up-by-one is the entire MTF rule, and there is no
        # corresponding NToPlA array. Verified against
        # `_refs/unrar/unpack15.cpp:217-222`.
        LastDistance = ChSetA[DistancePlace - 1]
        ChSetA[DistancePlace] = LastDistance
        ChSetA[DistancePlace - 1] = Distance

    Length += 2
    Distance += 1
    OldDist[OldDistPtr] = Distance
    OldDistPtr = (OldDistPtr + 1) & 3
    LastLength = Length
    LastDist = Distance
    CopyString(Distance, Length)
```

### 6.14 LongLZ (Long-Distance Matches)

Handles matches with large distances. Minimum match length is 3.

```
procedure LongLZ():
    NumHuf = 0
    Nlzb += 16
    if Nlzb > 0xFF:
        Nlzb = 0x90
        Nhfb >>= 1

    OldAvr2 = AvrLn2

    BitField = GetBits()

    # Decode match length
    if AvrLn2 >= 122:
        Length = DecodeNum(BitField, STARTL2, DecL2, PosL2)
    elif AvrLn2 >= 64:
        Length = DecodeNum(BitField, STARTL1, DecL1, PosL1)
    elif BitField < 0x100:
        Length = BitField
        AddBits(16)
    else:
        for Length = 0..:
            if ((BitField << Length) & 0x8000) != 0:
                break
        AddBits(Length + 1)

    AvrLn2 += Length
    AvrLn2 -= AvrLn2 >> 5

    # Decode distance slot
    BitField = GetBits()
    if AvrPlcB > 0x28FF:
        DistancePlace = DecodeNum(BitField, STARTHF2, DecHf2, PosHf2)
    elif AvrPlcB > 0x6FF:
        DistancePlace = DecodeNum(BitField, STARTHF1, DecHf1, PosHf1)
    else:
        DistancePlace = DecodeNum(BitField, STARTHF0, DecHf0, PosHf0)

    AvrPlcB += DistancePlace
    AvrPlcB -= AvrPlcB >> 8

    # Decode distance from ChSetB adaptive table
    loop:
        Distance = ChSetB[DistancePlace & 0xFF]
        NewDistancePlace = NToPlB[Distance & 0xFF]
        NToPlB[Distance & 0xFF] += 1
        Distance += 1

        if (Distance & 0xFF) == 0:
            CorrHuff(ChSetB, NToPlB)
        else:
            break

    ChSetB[DistancePlace & 0xFF] = ChSetB[NewDistancePlace]
    ChSetB[NewDistancePlace] = Distance

    # Combine high byte from table with low bits from bitstream
    BitField = GetBits()
    Distance = ((Distance & 0xFF00) | (BitField >> 8)) >> 1
    AddBits(7)

    # Update MaxDist3 threshold
    OldAvr3 = AvrLn3
    if Length != 1 and Length != 4:
        if Length == 0 and Distance <= MaxDist3:
            AvrLn3 += 1
            AvrLn3 -= AvrLn3 >> 8
        elif AvrLn3 > 0:
            AvrLn3 -= 1

    # Length bonuses
    Length += 3
    if Distance >= MaxDist3: Length += 1
    if Distance <= 256:      Length += 8

    if OldAvr3 > 0xB0 or (AvrPlc >= 0x2A00 and OldAvr2 < 0x40):
        MaxDist3 = 0x7F00
    else:
        MaxDist3 = 0x2001

    OldDist[OldDistPtr] = Distance
    OldDistPtr = (OldDistPtr + 1) & 3
    LastLength = Length
    LastDist = Distance
    CopyString(Distance, Length)
```

### 6.15 CopyString

```
procedure CopyString(Distance, Length):
    DestUnpSize -= Length
    while Length > 0:
        Window[UnpPtr] = Window[(UnpPtr - Distance) & 0xFFFF]
        UnpPtr = (UnpPtr + 1) & 0xFFFF
        Length -= 1
```

### 6.16 Encoder (RAR 1.3 / RAR 1.5 LZ compressor)

The RAR 1.3/1.5 format is decoder-defined: the bitstream is a series of
implicit decisions (flag bits, mode switches, DecodeNum lookups, MTF updates)
driven by running averages that the decoder updates from the stream as it
goes. Any encoder must therefore maintain a **bit-exact shadow** of the
decoder's state and produce bits that, when fed through Sections 6.3–6.15,
reproduce the intended literals and matches. This section describes how.

Primary reference: `_refs/7zip/CPP/7zip/Compress/Rar1Decoder.cpp` (the cleanest
implementation of the decoder side — note its license warning: the 7-Zip file
is GPL-derived from historical decompressor code and cannot be used to build a RAR **encoder**
verbatim, only as a format reference).

#### 6.16.1 General strategy

An encoder is a state machine running alongside a match finder
(`LZ_MATCH_FINDING.md`). At each input position the encoder performs:

```
shadow_state = fresh decoder state (same init as §6.9)
for each input position pos while unwritten_bytes > 0:
    # 1. ask the match finder for candidates
    matches = match_finder.get_matches(pos)
    best    = choose_match(matches)                     # §6.16.2

    # 2. decide mode using the shadow running averages
    if shadow_state.StMode:
        emit_stmode(pos, best, shadow_state)             # §6.16.6
    else:
        choose literal vs ShortLZ vs LongLZ              # §6.16.3
        emit_flag_bits(choice, shadow_state)             # §6.16.4
        emit_payload(choice, pos, best, shadow_state)    # §6.16.5

    # 3. mirror the decoder's state update (identical code path)
    shadow_state.update(choice)
```

The *shadow* is critical: every MTF rotation, every running-average decay,
every table switch must happen in the encoder at the same moment it would
happen in the decoder. A single missed update desynchronizes the output
forever.

#### 6.16.2 Match choice

RAR 1.3 has three distinct LZ modes with different cost profiles:

| Mode | Min len | Max dist | Cost profile |
|---|---|---|---|
| ShortLZ short match | 2 | ~0xFF | Lowest; 1-byte bitfield via `ShortLen1/2` |
| ShortLZ rep match | `LastLength` | from `OldDist[4]` | Zero-distance cost |
| LongLZ | 3 | 0xFFFF (full 64 KB window) | Highest; 2 DecodeNum + 7 raw bits |

Because the encoder lacks a public cost function, a reasonable greedy policy:

1. **Rep match preferred.** If any of `OldDist[0..3]` gives a match of
   length ≥ 3 at current position, emit it via ShortLZ's `len ∈ [9..13]` path.
2. **Short match for small distances.** If `delta ≤ 0xFF` and match length ≥
   2, emit via ShortLZ short path (len 0–8 in the decoder table).
3. **Long match otherwise.** Any match with `delta > 0xFF` or length ≥ 3 goes
   through LongLZ.
4. **Literal fallback.** If no match of length ≥ 2 exists, encode the byte
   via HuffDecode.

Lazy matching (`LZ_MATCH_FINDING.md` §5.2) is worth the minor complexity — a
one-byte lookahead typically gains 2–4% on adaptive-Huffman formats.

#### 6.16.3 Literal vs LZ mode gate

The decoder reads flag bits and consults `Nlzb` / `Nhfb` to pick mode. The
encoder must emit flag bits that *drive the decoder to the mode the encoder
chose*, using the same bias variables. Decoder rule (§6.10), inverted for
encoding:

```
if StMode:
    no flag bits; go straight to HuffDecode / StMode LZ path
else:
    # First flag bit
    bit1 = next_flag_bit()
    if bit1 == 1:                   # (FlagBuf & 0x80) in decoder
        if Nlzb > Nhfb: LongLZ
        else:           HuffDecode
    else:
        # Second flag bit
        bit2 = next_flag_bit()
        if bit2 == 0: ShortLZ
        else:
            if Nlzb <= Nhfb: LongLZ
            else:             HuffDecode
```

So to emit mode `M`, the encoder picks the flag-bit pair that maps to `M`
under the **current** `(Nlzb, Nhfb)` biases:

| Target mode | bit1 | bit2 | Precondition |
|---|---|---|---|
| LongLZ (via bit1=1) | 1 | — | `Nlzb > Nhfb` |
| HuffDecode (via bit1=1) | 1 | — | `Nlzb ≤ Nhfb` |
| ShortLZ | 0 | 0 | always |
| LongLZ (via bit1=0) | 0 | 1 | `Nlzb ≤ Nhfb` |
| HuffDecode (via bit1=0) | 0 | 1 | `Nlzb > Nhfb` |

There can be **two** ways to reach LongLZ (or HuffDecode) depending on the
current bias, and only **one** way to reach ShortLZ. The encoder should pick
the cheapest path — bit1=1 costs one flag bit, bit1=0 costs two — which means
always preferring the bit1=1 path when the bias allows it.

The flag bits themselves are not emitted directly as raw output; they come
from a per-byte "flag byte" produced by `GetFlagsBuf()` (§6.11), which is a
Huffman-decoded entry from `ChSetC`. The encoder must therefore:

1. Accumulate 8 desired flag bits into a target byte `T`.
2. Find the `ChSetC` rank whose character-value high byte is `T`.
3. Encode that rank via `DecodeNum(PosHf2)` inverse (see §6.16.7).
4. Perform the same `ChSetC` MTF rotation as the decoder.

Because `GetFlagsBuf()` is only called when `FlagsCnt < 0`, the encoder has 8
flag bits of lookahead before it must decide which `T` byte to emit. In
practice this means the encoder greedily chooses mode for 8 positions,
records their flag bits, then packs them into `T`.

**Edge case:** StMode can interrupt the 8-bit flag cycle. When the decoder
enters StMode (after 16 consecutive HuffDecode without FlagsCnt consumption),
no flag bits are consumed until StMode exits. The encoder's shadow state
tracks this and skips the flag-byte packing until StMode ends.

#### 6.16.4 Encoding HuffDecode (literal bytes)

To emit literal byte `b`:

1. Pick the DecodeNum table for literals using the current `AvrPlc`:
   `PosHf0..PosHf4` per the thresholds in §6.10.
2. Find `bytePlace` such that `ChSet[bytePlace] >> 8 == b`. This is a
   256-entry linear scan in the naïve implementation; maintain a reverse
   lookup `byte_to_place[b]` updated alongside MTF swaps for O(1).
3. If `StMode`, bias `bytePlace += 1` (because the decoder subtracts 1 after
   consuming the StMode signal — see §6.16.6).
4. Emit `bytePlace` via the inverse of `DecodeNum(tab)` (§6.16.7).
5. Mirror the decoder's MTF update: swap `ChSet[bytePlace]` with
   `ChSet[newBytePlace]` where `newBytePlace = NToPl[curByte & 0xFF]++`;
   CorrHuff on overflow as in §6.5.
6. Mirror the decoder's running-average updates:
   `AvrPlc += bytePlace; AvrPlc -= AvrPlc >> 8; Nhfb += 16;` with Nlzb/Nhfb
   balance per §6.10.
7. Increment `NumHuf`; enter StMode if `NumHuf >= 16 and FlagsCnt == 0`.

#### 6.16.5 Encoding ShortLZ and LongLZ

**ShortLZ short match (len 0–8 in the decoder table):** the decoder reads an
8-bit field and does a linear search over `ShortXor1/2[]` masked to
`ShortLen1/2[]` bits. To invert: the encoder emits the `ShortXor[i]` prefix
in the top `ShortLen[i]` bits, where `i = match_len - 2`. The table selection
between 1 and 2 mirrors the decoder: `AvrLn1 < 37 → table 1 else table 2`.
Don't forget to toggle `Buf60` — the encoder can emit a length-14 "null
match" pseudo-symbol to toggle it when its running statistics change, same as
the decoder swallows.

**ShortLZ rep match (len 9–13):** the decoder uses len=9 for "same as last
match" and len=10..13 to index into `OldDist[]`. The encoder emits the
corresponding entry via the same inverse `ShortLen1/2` path. For len=10 it
must also emit the `PosL1` DecodeNum for the new length. Note the "Buf60
toggle" backdoor: decoder len=10 with `DecodeNum(PosL1) == 0xFF` toggles
`Buf60` and skips the match entirely. The encoder can use this as a free
table-switch signal when its statistics predict the other short table will
perform better for upcoming matches.

**ShortLZ len=14 (big distance with literal length):** emits
`DecodeNum(PosL2) + 5` for length, then raw 15-bit distance `0x8000 + d`.

**LongLZ:** emits length via `PosL1` or `PosL2` (selected by `AvrLn2`) or the
raw 16-bit small-length fast path when `AvrLn2 < 64`. Distance slot via
`PosHf0/1/2` (selected by `AvrPlcB`), then 7 raw bits of low distance bits,
then MTF update on `ChSetB`. The decoder's `MaxDist3` selector and
`AvrLn3`-driven length bonuses apply to the final match-length computation
and must be mirrored.

After emitting either mode the encoder pushes the distance onto
`OldDist[m_RepDistPtr++ & 3]` and updates `LastLength` / `LastDist`, exactly
as the decoder does.

#### 6.16.6 StMode

In StMode the encoder may emit either:

- **Exit StMode:** encode `bytePlace = 0` via `PosHf4`, then emit a single
  `1` bit. Decoder sets `StMode = false` and returns.
- **Short LZ in StMode:** encode `bytePlace = 0` via `PosHf4`, then emit `0`,
  then `len - 3` (1 bit), then `dist + 1` via `DecodeNum(PosHf2)` concatenated
  with 5 raw bits. Length is restricted to 3 or 4.
- **Literal byte in StMode:** encode the literal as in §6.16.4 but with
  `bytePlace + 1` (to avoid the reserved `bytePlace = 0` signal).

StMode exit is the encoder's only way to return to the flag-driven main loop
— so if a long LZ match is the best next token, the encoder must first exit
StMode (one extra literal+bit cost), then emit the LongLZ. In practice this
is rare because StMode is only entered when the stream has been overwhelmingly
literal-heavy anyway.

#### 6.16.7 Inverse DecodeNum

`DecodeNum(tab)` consumes `kNumBits = 12` bits and returns an integer. The
decoder iterates `i = 2 … 12` and narrows by `tab[i] << (12 - i)` at each
step. The encoder's inverse is a precomputed table built once per `tab[]`:

```
build encode_tab from tab[]:
    sum_base = 0
    offset   = 0
    for i in 2..12:
        count = tab[i]
        bits_len = i
        for k in 0..count-1:
            value = sum_base + k
            # the decoder returns (val >> (12 - i)) + sum_base
            # so the bit pattern is (offset + k) as an i-bit value
            encode_tab[value] = (offset + k, bits_len)
        sum_base += count
        offset = (offset + count) << 1     # shift up for next level
```

Then emitting `DecodeNum(tab)` inverse is just `emit_bits(encode_tab[value])`.
Build one `encode_tab[]` per decoder table (`PosL1, PosL2, PosHf0..PosHf4`
— seven tables total) at encoder startup.

**Sanity check:** the final `offset` after all 11 iterations must equal
`1 << kNumBits`. Any other value means the decoder table is malformed (or
the encoder has a bug in its construction). Section §4 of
`HUFFMAN_CONSTRUCTION.md` describes the same Kraft equality condition.

#### 6.16.8 Initial block and EOF

There is no explicit block structure in RAR 1.3/1.5. The encoder:

1. Calls `InitHuff()` (identical to §6.4) for a non-solid file, or inherits
   state from the previous file for a solid file.
2. Emits exactly `DestUnpSize` bytes worth of decoded output through the
   above pipeline.
3. On the last position, stops emitting. There is **no end-of-stream marker**
   — the decoder stops when `DestUnpSize` bytes have been produced. The
   encoder must flush its bit buffer (pad with zero bits up to a byte
   boundary) and emit any trailing bytes for the inbuf lookahead window. The
   compatible decoders read from a prefetched bit buffer, so 4–8 bytes of trailing
   zero padding is safe and necessary.

#### 6.16.9 Test oracle

The encoder is correct iff, for every test input, the bytes it produces
decompress back to the original input via a RAR 1.5-compatible decoder. Cross-
check against `_refs/7zip/CPP/7zip/Compress/Rar1Decoder.cpp` for the
bit-exact state machine. Compare compressed sizes against `rar a -m5 -mt1 -ma1.5`
(if any vintage RAR build is available) to gauge parser quality.

#### 6.16.10 Implementation cost and recommendation

RAR 1.3/1.5 is the single most format-idiosyncratic version: no explicit
tables, state-switched DecodeNum, MTF with CorrHuff renormalization, StMode
interrupts, rep-distance encoded as length slots. Writing an encoder is
straightforward but laborious and **not a priority** for a modern RAR tool —
this format has been obsolete since 1997 and is extremely rare in the wild.

A practical implementation strategy: do not write a new RAR 1.3/1.5 encoder.
Instead, re-emit any such file as RAR 2.0 or RAR 5.0 after decoding. The
decoder we already have (§6) is sufficient to read any legacy archive, and
the use cases for *writing* new RAR 1.3/1.5 streams in 2025+ are essentially
zero. The format is documented here for completeness and in case of a
recovery scenario where byte-identical re-emission is required.

### 6.17 Solid mode

RAR 1.3 supports solid archives via `LHD_SOLID` (file entry flag `0x10`) and
`MHD_SOLID` (archive flag `0x08`), identical in semantics to the same flags
in RAR 1.5–4.x. The state that persists across a solid boundary is exactly
what `Unpack15` carries:

| State | Carries in solid | Resets per file (non-solid) |
|---|:---:|:---:|
| 64 KB LZ window | ✓ | ✓ |
| Adaptive Huffman arrays (`ChSet`, `NToPl`, `Place`) | ✓ | ✓ |
| `LastDist`, `LastLength` | ✓ | ✓ |
| `FlagBuf`, `FlagsCnt` bitstream state | ✗ (new stream per file) | ✗ |
| `StMode` literal-run flag | ✗ (forced clear) | ✗ |
| `Nhfb`, `Nlzb` balance counters | ✓ | ✓ |

The encoder obligation is identical to RAR 1.5–4.x:

1. First file of a solid group has `LHD_SOLID = 0`; every subsequent file
   sets it.
2. Archive-level `MHD_SOLID` is set whenever any file in the archive has
   `LHD_SOLID = 1`.
3. `InitHuff()` runs once at the start of the group and is not called
   again until a non-solid boundary.
4. Do not emit a file-level output reset (`UnpPtr = 0`) inside a solid
   group — the window is shared.

See `ARCHIVE_LEVEL_WRITE_SIDE.md` §1.1 for the cross-codec state table and
§1.2 for the generic encoder loop; the RAR 1.3 rules are the Unpack15
row of that table.

---

## 7. Encryption

RAR 1.3 uses a trivially weak encryption scheme based on a 3-byte key derived
from the password. This encryption is effectively broken and provides no
meaningful security.

### 7.1 Key Derivation

```
Key = [0, 0, 0]

for each byte P in password:
    Key[0] = (Key[0] + P) & 0xFF
    Key[1] = (Key[1] ^ P) & 0xFF
    Key[2] = (Key[2] + P) & 0xFF
    Key[2] = rotate_left_8bit(Key[2], 1)
```

Where `rotate_left_8bit(x, 1) = ((x << 1) | (x >> 7)) & 0xFF`.

### 7.2 Decryption

```
for each byte B in encrypted data:
    Key[1] = (Key[1] + Key[2]) & 0xFF
    Key[0] = (Key[0] + Key[1]) & 0xFF
    output = (B - Key[0]) & 0xFF
```

The encryption is a simple stream cipher: each byte is modified by subtracting
a key byte that evolves via addition of the three key state bytes. (The XOR of
`P` into `Key[1]` and the rotate of `Key[2]` happen during key derivation
(§7.1) only; the per-byte update during decryption is purely additive.)
Verified against `_refs/unrar/crypt1.cpp` (`SetKey13` / `Decrypt13`).

### 7.3 Comment Encryption

Archive comments use fixed encryption keys: `Key = [0, 7, 77]`.

---

## 8. Comments

When the `MHD_COMMENT` (`0x02`) flag is set in the archive header, comment
metadata/data is carried in the main-header extension: `HeadSize` is larger
than 7, and the first file header starts at `archive_start + HeadSize`.
When `MHD_PACK_COMMENT` (`0x10`) is also set, the comment payload is packed.

RAR 1.4 archive comments have a compact inline layout:

| Offset in main-header extension | Size | Field | Description |
|---------------------------------|------|-------|-------------|
| `+0` | 2 | `CmtLength` | For stored comments, raw comment byte length. For packed comments, `2 + packed_payload_len`. |
| `+2` | 2 if packed | `UnpCmtLength` | Unpacked comment byte length. Present only when `MHD_PACK_COMMENT` is set. |
| `+4` | `CmtLength - 2` if packed | `PackedComment` | RAR 1.3 comment-encrypted Unpack15 payload. |
| `+2` | `CmtLength` if stored | `Comment` | Raw OEM comment bytes. |

Packed comments are decoded as:

1. Read `CmtLength` from extension bytes `+0..+1`.
2. Read `UnpCmtLength` from extension bytes `+2..+3`; the packed payload is
   the next `CmtLength - 2` bytes.
3. Decrypt the packed payload with the fixed RAR 1.3 comment key
   `Key13 = [0, 7, 77]`. For each byte, first advance
   `Key[1] += Key[2]`, `Key[0] += Key[1]`, then subtract `Key[0]` from the
   ciphertext byte.
4. Decode the decrypted payload with the **Unpack15** (`UnpVer=15`) codec
   using `UnpCmtLength` as the output bound and a non-solid 64 KiB window.
   Note: this is fixed at the `UnpVer=15` variant for packed comments
   regardless of the surrounding archive's file-data `UnpVer` (which is
   `13` for RAR 1.4 file payloads). Modern UnRAR pins this explicitly via
   `CommHead.UnpVer = 15` on the RAR 1.4 path (`_refs/unrar/arccmt.cpp`).

RAR 1.4 comments do not carry a separate comment CRC in this inline form.
Malformed packed comments are detected by header bounds and by the Unpack15
decoder reaching or exceeding the declared output bound.

Observed RAR 1.402 fixture `fixtures/1.402/COMMENT.RAR`:

| Field | Value |
|-------|-------|
| Main `Flags` | `0x92` (`0x80` reserved + `MHD_COMMENT` + `MHD_PACK_COMMENT`) |
| Main `HeadSize` | `43` |
| Main-header extension length | `36` bytes |
| `CmtLength` | `34` (`0x22`; 2-byte unpacked length + 32-byte encrypted packed payload) |
| `UnpCmtLength` | `30` (`0x1e`) |
| Extension bytes | `22 00 1e 00 79 da 20 65 4f 71 0f 5d 05 71 e8 be 5e 71 a0 bd d8 1e 04 37 20 dc 1a 54 cc cb e6 05 5b 68 c9 90` |
| First file header offset | `43` |
| Decoded comment | `This is the archive comment.\r\n` |

Readers that do not display comments must still honor `HeadSize` and skip the
extension bytes before parsing file headers.

File-level comments are indicated by the `LHD_COMMENT` (`0x08`) flag in the
file entry. When present, the file header's `HeadSize` extends past the file
name. The post-name extension begins with a little-endian `uint16` comment
length followed by that many raw OEM comment bytes. The packed-comment bit is
an archive-main-header flag, so this observed RAR 1.402 file-comment form is
stored, not Unpack15-packed.

Observed RAR 1.402 fixture `fixtures/1.402/FCOMM.RAR`:

| Field | Value |
|-------|-------|
| File name | `HELLO.TXT` |
| File `Flags` | `0x08` (`LHD_COMMENT`) |
| File `HeadSize` | `38` |
| Name length | `9` |
| Post-name extension length | `8` bytes |
| Extension bytes | `06 00 46 43 4f 4d 0d 0a` |
| Decoded file comment | `FCOM\r\n` |

Comment text uses OEM (DOS codepage) encoding.

---

## 9. Multi-Volume Archives

Multi-volume archives are indicated by `MHD_VOLUME` (`0x01`) in the archive
header flags. Files split across volumes use:

- `LHD_SPLIT_BEFORE` (`0x01`): file data continues from previous volume.
- `LHD_SPLIT_AFTER` (`0x02`): file data continues in next volume.

Volume naming follows the old RAR scheme:
- First volume: `archive.rar`
- Subsequent: `archive.r00`, `archive.r01`, etc.

For a split file, concatenate each part's `PACK_SIZE` bytes in volume order
before applying the file method. Stored files concatenate to the plaintext
payload directly. Compressed files concatenate to one logical Unpack15 packed
bitstream, which is then decoded once using the final file's `UNP_SIZE` as the
output bound. The final split part carries the checksum for the complete
unpacked file; earlier parts may carry intermediate checksum values and must
not be treated as complete-file validation.

Fixture coverage:

- `fixtures/1.402/MULTIVOL.RAR` + `.R00`..`.R02`: stored split payload.
- `fixtures/1.402/CMULTIV.RAR` + `.R00`..`.R06`: compressed Unpack15 stream
  split across old-style volumes; final checksum `0x87cd`.

---

## 10. SFX Archives

Self-extracting archives prepend a DOS executable stub before the archive data.
To locate the archive, search for the `RE~^` signature within the first ~128 KB
of the file.

Additionally, SFX archives may contain the marker `RSFX` (`0x52 0x53 0x46 0x58`)
at offset 28 within the SFX module.

### 10.1 RARSFX14 stub structure

The official `RARSFX14.EXE` stub (the SFX runtime shipped with
RAR 1.40 — `RAR1_402.EXE` is itself one) follows this layout:

```
+0x00..+0x1F   custom MZ header (32 bytes, e_cparhdr=2)
+0x20..+SS-1   LZEXE 0.91-compressed SFX runtime payload
SS = (e_cs * 16) + 32       LZEXE 14-byte control header
SS+14..SS+343              330-byte LZEXE 0.91 decompression stub
SS+344..stub_end           relocation stream (typically empty for SFX)
stub_end..EOF              appended RAR archive data
```

Where `stub_end` is the **MZ-declared load image size**:

```python
def mz_image_size(e_cblp, e_cp):
    return e_cp * 512 if e_cblp == 0 else (e_cp - 1) * 512 + e_cblp
```

(`e_cblp == 0` means "the last 512-byte page is full"; otherwise
`e_cblp` is the byte count actually used in the last page.) The
runtime locates the appended archive by simply seeking past the
MZ-declared image size — DOS only reads `image_size` bytes when
loading the executable, so anything past that is invisible to the
loader and free for the runtime to consume itself.

#### MZ header field overlay (the RSFX marker)

The DOS MZ header layout has `e_lfarlc` (reloc-table offset) at
`+0x18`, `e_ovno` at `+0x1A`, and **four bytes of reserved space
at `+0x1C..+0x1F`**. LZEXE 0.91 repurposes those four reserved
bytes to store its `'LZ91'` signature; RAR's SFX stub repurposes
the same four bytes again, this time to store `'RSFX'`:

| Offset | Standard MZ | LZEXE 0.91 use | RAR SFX 1.4 use |
|-------:|-------------|----------------|------------------|
| `+0x18` | `e_lfarlc`   | unchanged      | `0x001C` (zero relocations) |
| `+0x1A` | `e_ovno`     | unchanged      | `0x0000` |
| `+0x1C..+0x1F` | reserved | `'LZ91'` signature | `'RSFX'` marker |

So the four bytes that are "reserved" in plain MZ become the LZEXE
signature in an LZEXE-packed binary, and the RAR SFX 1.4 stub —
which IS LZEXE-packed underneath — wipes that signature with
`RSFX`. The dual effect: RAR readers can identify a genuine 1.4
SFX module (`READ_SIDE_OVERVIEW.md §2.2`), and LZEXE-aware
unpackers (which check for `'LZ91'`) refuse the file even though
the rest of the LZEXE structure is intact.

#### LZEXE-compressed payload

The compressed payload from `+0x20` to `SS` decompresses to a
~25 KB SFX runtime — a stripped-down RAR 1.x extractor. The
control header at `SS` is the standard LZEXE 0.91 14-byte block:
`start_ip`, `start_cs`, `start_sp`, `start_ss`, `lz_paras` (=
`e_cs`), `delta`, `stub_total`. The 330-byte decompression stub
that follows is byte-identical to the public LZEXE 0.91 stub,
ending with the canonical segment-fixup epilogue
`2D 10 00 8E D8 8E C0 31 DB FA 8E D6 8B E7 FB 2E FF 2F`.

A reader / repacker can therefore:

1. Read the MZ header and confirm `bytes[0x1C..0x20] == "RSFX"`.
2. Compute `stub_end` via the formula above (do **not** simplify
   to `e_cp * 512 - (512 - e_cblp)` — that gets `e_cblp == 0`
   wrong).
3. Treat `[0..stub_end)` as the opaque stub (re-use byte-for-byte
   when repackaging) and `[stub_end..EOF)` as the inner RAR archive.

#### Runtime behaviour (informational)

Once decompressed, the SFX runtime contains the strings
`"RAR SFX Archive"`, `"Created by RAR 1.40"`, password / overwrite
prompts, multi-volume support (`"Insert disk with"`,
`"Extract from SFX volume"`), and AV verification glue
(`"Created by"` / `"Modified by"`). It opens its own `.EXE` file,
seeks to `stub_end`, and runs the standard RAR 1.x extraction
loop on the appended archive bytes — there is no separate
table-of-contents block, just the same stream-walked block format
documented in §3 above.

#### Writer guidance

A clean-room encoder that wants to produce byte-compatible RAR 1.4
SFX output should treat `RARSFX14.EXE` as an opaque blob: copy the
~6.5 KB stub verbatim and **append the RAR archive bytes after it
without modifying any MZ field**. The `e_cblp`/`e_cp` fields must
keep describing the stub image only — they are what the runtime
uses to compute `stub_end` and locate the appended archive. If you
update them to cover the appended archive, the runtime's computed
archive start moves to EOF and extraction breaks. (Verified
against `fixtures/1.402/SFXSRC.EXE`: total file 6561 bytes, MZ
fields compute `stub_end = 6491`, and `RE~^` does in fact start
at offset 6491.)

Reconstructing the LZEXE-compressed runtime from scratch is
unnecessary, and would lose byte compatibility with the
RAR-internal SFX detection at offset `0x1C`. Encoders that emit a
stub *without* the `RSFX` marker will be **rejected** by the
RAR 1.4 SFX detector — see `READ_SIDE_OVERVIEW.md §2.2` for the
exact gating logic.

---

## 11. File Checksum Field

RAR 1.3 stores a 16-bit per-file checksum at file-header offset `+8`.
It is **not** CRC32-truncated-to-16-bit. It is a rolling 16-bit sum with
a rotate-left-by-one after every input byte:

```text
checksum = 0
for byte in uncompressed_data:
    checksum = (checksum + byte) & 0xffff
    checksum = ((checksum << 1) | (checksum >> 15)) & 0xffff
```

This was pinned from the RAR 1.40/1.402 binary:

- The file-header buffer lives at `DAT_32d6_485e`; `DAT_32d6_4866` is
  offset `+8`, the `FileCRC` field.
- During archive creation and extraction, that field is copied from the
  running checksum word at `2668:0002`.
- The update routine at `2668:0004` loads the running word, adds each byte
  with carry (`ADD AL,[SI]`; `ADC AH,0`), then rotates the 16-bit word left
  once (`ROL AX,1`) before storing it back.

Verified stored fixtures:

| Fixture entry | Stored `FileCRC` | Rolling checksum | `CRC32(data) & 0xFFFF` |
|---------------|------------------|------------------|-------------------------|
| `README_store.rar` / `README` | `0xe079` | `0xe079` | `0x4172` |
| `MULTIFIL.RAR` / `HELLO.TXT` | `0x7a6e` | `0x7a6e` | `0x0511` |
| `WITHDIR.RAR` / `SUBDIR\INNER.TXT` | `0x83ad` | `0x83ad` | `0x37e4` |
| `EMPTY.RAR` / `EMPTY.BIN` | `0x0000` | `0x0000` | `0x0000` |

Readers should compute this checksum over the final plaintext output bytes
after decryption and decompression. Writers should store this checksum for
new entries.

Note: unlike RAR 1.5+, there is no CRC on the file headers themselves. Header
integrity is not verified.

---

## Appendix A: MS-DOS Date/Time Format

Same as RAR 1.5-4.x. The `FileTime` field uses standard MS-DOS date/time
packing in 32 bits:

| Bits  | Field   | Range |
|-------|---------|-------|
| 0-4   | Second / 2 | 0-29 (0-58 seconds) |
| 5-10  | Minute  | 0-59 |
| 11-15 | Hour    | 0-23 |
| 16-20 | Day     | 1-31 |
| 21-24 | Month   | 1-12 |
| 25-31 | Year - 1980 | 0-127 (1980-2107) |

## Appendix B: Differences from RAR 1.5

| Feature | RAR 1.3 | RAR 1.5+ |
|---------|---------|----------|
| Signature | `RE~^` (4 bytes) | `Rar!...` (7 bytes) |
| Block types | None (implicit) | Explicit HEAD_TYPE field |
| Header CRC | None | CRC16 on headers |
| File data checksum | 16-bit rolling sum+rotate | CRC32 |
| Flags | 8-bit | 16-bit |
| Filename max | 255 (8-bit length) | 65535 (16-bit length) |
| Dictionary | Fixed 64 KB | 64 KB - 4 MB (selectable) |
| Host OS | DOS only | DOS, OS/2, Win32, Unix, Mac, BeOS |
| Encryption | 3-byte XOR | Version-dependent (CRC-XOR through AES-256) |
| End marker | None (EOF) | ENDARC_HEAD block |
| Unicode | No | Yes (RAR 3.x+) |
| Extended time | No | Yes (RAR 3.x+) |
| Large files | No (32-bit sizes) | Yes (64-bit, RAR 3.x+) |
| Compression | Adaptive Huffman + LZ77 (Unpack15) | Same for v15/v20; + PPMd + RARVM for v29+ |

## Appendix C: Known Tools

| Tool | RAR 1.3 Support |
|------|----------------|
| WinRAR-compatible RAR readers | Full read support |
| `binref/refinery` (Python) | Full read support (parse, decrypt, decompress) |
| AROS `contrib` historical reader (C) | Full read support (historical implementation) |
| `unarr` | Detected but rejected ("Ancient RAR format") |
| The Unarchiver (XADMaster) | Detected but rejected |
| libarchive | Not supported |
| 7-Zip | Not supported |
