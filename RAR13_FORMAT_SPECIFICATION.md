# RAR 1.3/1.4 Archive Format Specification

**Independent documentation derived from publicly available sources:**
- AROS `contrib` repository: `aminet/util/arc/unrar/` (old C implementation, public/AROS license)
- `binref/refinery` Python RAR implementation (LGPL-compatible)
- `file` command magic database (public domain)
- Struct definitions and constants from public GitHub mirrors

**No modern UnRAR source code was referenced in the creation of this document.**
The AROS implementation is a pre-C++ era unrar from the mid-1990s, predating the
current UnRAR license restrictions.

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
around 1996. It is extremely rare in the wild, but may be encountered on vintage
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
- Trivial 3-byte XOR encryption (effectively broken)

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

If `HeadSize > 7`, the remaining `HeadSize - 7` bytes should be skipped (may
contain comment data or padding).

### Archive Flags

Single byte (not a 16-bit word like RAR 1.5+):

| Bit  | Mask   | Constant        | Meaning |
|------|--------|-----------------|---------|
| 0    | `0x01` | MHD_VOLUME      | Archive is part of a multi-volume set. |
| 1    | `0x02` | MHD_COMMENT     | Archive comment is present. |
| 2    | `0x04` | MHD_LOCK        | Archive is locked (cannot be modified). |
| 3    | `0x08` | MHD_SOLID       | Solid archive. |
| 4    | `0x10` | MHD_PACK_COMMENT | Packed (compressed) comment present. |
| 5-7  |        |                 | Reserved. |

---

## 5. File Entry Header

Each file entry consists of a fixed 21-byte header, followed by the filename,
followed by the compressed data.

| Offset | Size | Field     | Type   | Description |
|--------|------|-----------|--------|-------------|
| +0     | 4    | PackSize  | uint32 | Compressed data size (little-endian). |
| +4     | 4    | UnpSize   | uint32 | Uncompressed file size (little-endian). |
| +8     | 2    | FileCRC   | uint16 | CRC16 of uncompressed file data (little-endian). |
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

The raw byte value maps to the decompressor version:

| Raw Value | Version | Meaning |
|-----------|---------|---------|
| 2         | 13      | Compressed with RAR 1.3 algorithm. |
| Other     | 10      | Stored or minimal compression. |

Both versions dispatch to the same `Unpack15` decompressor (Section 6). The
version distinction primarily indicates the era of the archiver that created
the file.

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
`Buf60 + 3`, where `Buf60` toggles between 0 and 1 as a special signal.

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
| OldDist[4] | undefined  | Repeat distance buffer (4 entries). |
| LastDist | undefined    | Most recent match distance. |
| LastLength | undefined  | Most recent match length. |

### 6.10 Main Decode Loop

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
    if DistancePlace - 1 >= 0:
        LastDistance = ChSetA[DistancePlace - 1]
        ChSetA[DistancePlace] = LastDistance
        ChSetA[DistancePlace - 1] = Distance
        # PlaceA updated implicitly

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
a key byte that evolves via addition and XOR of the three key state bytes.

### 7.3 Comment Encryption

Archive comments use fixed encryption keys: `Key = [0, 7, 77]`.

---

## 8. Comments

When the `MHD_COMMENT` (`0x02`) flag is set in the archive header, a comment
follows the main header. When `MHD_PACK_COMMENT` (`0x10`) is set, the comment
is compressed.

File-level comments are indicated by the `LHD_COMMENT` (`0x08`) flag in the
file entry.

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

---

## 10. SFX Archives

Self-extracting archives prepend a DOS executable stub before the archive data.
To locate the archive, search for the `RE~^` signature within the first ~128 KB
of the file.

Additionally, SFX archives may contain the marker `RSFX` (`0x52 0x53 0x46 0x58`)
at offset 28 within the SFX module.

---

## 11. CRC16

RAR 1.3 uses a 16-bit CRC for file data integrity. This is the low 16 bits of
the standard CRC32 computation with polynomial `0xEDB88320`:

```
CRC16 = CRC32(uncompressed_data) & 0xFFFF
```

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
| File data CRC | CRC16 | CRC32 |
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
| Official `unrar` / WinRAR | Full read support |
| `binref/refinery` (Python) | Full read support (parse, decrypt, decompress) |
| AROS `contrib` unrar (C) | Full read support (historical implementation) |
| `unarr` | Detected but rejected ("Ancient RAR format") |
| The Unarchiver (XADMaster) | Detected but rejected |
| libarchive | Not supported |
| 7-Zip | Not supported |
