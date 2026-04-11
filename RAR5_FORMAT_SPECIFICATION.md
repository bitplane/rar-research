# RAR 5.0 Archive Format Specification

**Independent documentation derived from publicly available sources:**
- RARLAB's published technote (https://www.rarlab.com/technote.htm)
- 7-Zip source code (LGPL, independently written by Igor Pavlov)

**No UnRAR source code was referenced in the creation of this document.**

---

## Table of Contents

1. [Data Types](#1-data-types)
2. [Archive Layout](#2-archive-layout)
3. [Archive Signature](#3-archive-signature)
4. [General Block Format](#4-general-block-format)
5. [Extra Area Format](#5-extra-area-format)
6. [Archive Encryption Header](#6-archive-encryption-header)
7. [Main Archive Header](#7-main-archive-header)
8. [File Header and Service Header](#8-file-header-and-service-header)
9. [End of Archive Header](#9-end-of-archive-header)
10. [Service Headers](#10-service-headers)
11. [Compression Algorithm](#11-compression-algorithm)
12. [Post-Processing Filters](#12-post-processing-filters)

---

## 1. Data Types

### vint (Variable-Length Integer)

A variable-length encoding for unsigned integers. Each byte contributes 7 bits of
data (bits 0-6). Bit 7 is the continuation flag: if set, another byte follows.

```
Byte 0: [C][d6][d5][d4][d3][d2][d1][d0]   bits 0-6 of value
Byte 1: [C][d13][d12][d11][d10][d9][d8][d7]  bits 7-13 of value
...
```

- The first byte contains the 7 least significant bits.
- Maximum encoding: 10 bytes for a 64-bit integer.
- Leading `0x80` bytes (zero with continuation set) are valid padding, used when
  space is pre-allocated before the final value is known.

### Fixed-Size Types

| Type   | Size    | Encoding       |
|--------|---------|----------------|
| byte   | 1 byte  | unsigned       |
| uint16 | 2 bytes | little-endian  |
| uint32 | 4 bytes | little-endian  |
| uint64 | 8 bytes | little-endian  |

---

## 2. Archive Layout

```
[Self-extracting module]       (optional, up to ~1 MB)
RAR 5.0 signature              (8 bytes)
[Archive encryption header]    (optional, if headers are encrypted)
Main archive header
[Archive comment service header]  (optional)

File header 1
  [Service headers for file 1]    (optional: ACL, streams, etc.)
...
File header N
  [Service headers for file N]    (optional)

[Recovery record]              (optional)
End of archive header
```

---

## 3. Archive Signature

8 bytes: `0x52 0x61 0x72 0x21 0x1A 0x07 0x01 0x00`

This is ASCII "Rar!" followed by `0x1A 0x07 0x01 0x00`.

For comparison, RAR 4.x uses a 7-byte signature:
`0x52 0x61 0x72 0x21 0x1A 0x07 0x00`

The signature may be preceded by a self-extracting module. Search from the
beginning of the file up to the maximum SFX size (currently ~1 MB).

---

## 4. General Block Format

Every archive block (header) follows this structure:

| Field         | Type   | Description |
|---------------|--------|-------------|
| Header CRC32  | uint32 | CRC32 of everything from Header Size through the end of the Extra Area (inclusive). |
| Header Size   | vint   | Size of data from Header Type through end of Extra Area. Max 3 bytes (2 MB limit). |
| Header Type   | vint   | Block type identifier (see below). |
| Header Flags  | vint   | Common flags (see below). |
| Extra Area Size | vint | Size of extra area. Present only if flag `0x0001` is set. |
| Data Size     | vint   | Size of data area. Present only if flag `0x0002` is set. |
| ...           | ...    | Type-specific fields. |
| Extra Area    | ...    | Optional, present if flag `0x0001` is set. |
| Data Area     | ...    | Optional, present if flag `0x0002` is set. Not included in Header CRC or Header Size. |

### Header Types

| Value | Type |
|-------|------|
| 1     | Main archive header |
| 2     | File header |
| 3     | Service header |
| 4     | Archive encryption header |
| 5     | End of archive header |

### Common Header Flags

| Flag     | Meaning |
|----------|---------|
| `0x0001` | Extra area is present. |
| `0x0002` | Data area is present. |
| `0x0004` | Blocks with unknown type and this flag should be skipped when updating. |
| `0x0008` | Data area continues from previous volume. |
| `0x0010` | Data area continues in next volume. |
| `0x0020` | Block depends on preceding file block. |
| `0x0040` | Preserve child block if host block is modified. |

---

## 5. Extra Area Format

The extra area consists of one or more records:

| Field | Type | Description |
|-------|------|-------------|
| Size  | vint | Size of record data starting from Type. |
| Type  | vint | Record type (block-type-specific). |
| Data  | ...  | Record-specific data. May be empty. |

Unknown record types must be skipped without error.

---

## 6. Archive Encryption Header

Present only in archives with encrypted headers (header type = 4).

| Field             | Type     | Description |
|-------------------|----------|-------------|
| Header CRC32      | uint32   |  |
| Header Size       | vint     |  |
| Header Type       | vint     | 4 |
| Header Flags      | vint     |  |
| Encryption Version | vint    | 0 = AES-256. |
| Encryption Flags  | vint     | `0x0001`: password check data is present. |
| KDF Count         | 1 byte   | Binary logarithm of PBKDF2 iteration count. |
| Salt              | 16 bytes | Global salt for all encrypted headers. |
| Check Value       | 12 bytes | Password verification. Present if flag `0x0001` set. First 8 bytes from additional PBKDF2 rounds, last 4 are a checksum. Combined with header CRC32, provides 64-bit integrity check. |

When this header is present, every subsequent header is preceded by a 16-byte
AES-256 IV, followed by encrypted header data padded to a 16-byte boundary.

---

## 7. Main Archive Header

Header type = 1.

| Field           | Type   | Description |
|-----------------|--------|-------------|
| Header CRC32    | uint32 |  |
| Header Size     | vint   |  |
| Header Type     | vint   | 1 |
| Header Flags    | vint   | Common flags. |
| Extra Area Size | vint   | Present if flag `0x0001` is set. |
| Archive Flags   | vint   | See below. |
| Volume Number   | vint   | Optional: present if archive flag `0x0002` is set. Not present for first volume, 1 for second, etc. |
| Extra Area      | ...    | Optional. |

### Archive Flags

| Flag     | Meaning |
|----------|---------|
| `0x0001` | Volume (part of a multi-volume set). |
| `0x0002` | Volume number field is present (all volumes except first). |
| `0x0004` | Solid archive. |
| `0x0008` | Recovery record is present. |
| `0x0010` | Locked archive. |

### Main Archive Extra Records

#### Locator (type 0x01)

Contains positions of service blocks for fast access.

| Field                | Type | Description |
|----------------------|------|-------------|
| Size                 | vint |  |
| Type                 | vint | 1 |
| Flags                | vint | `0x0001`: quick open offset present. `0x0002`: recovery record offset present. |
| Quick Open Offset    | vint | Distance from quick open service block to main archive header. Present if `0x0001` flag. Zero means ignore. |
| Recovery Record Offset | vint | Distance from recovery record to main archive header. Present if `0x0002` flag. Zero means ignore. |

#### Metadata (type 0x02)

Stores archive original name and creation time.

| Field       | Type          | Description |
|-------------|---------------|-------------|
| Size        | vint          |  |
| Type        | vint          | 2 |
| Flags       | vint          | `0x0001`: name present. `0x0002`: creation time present. `0x0004`: Unix time format (else Windows FILETIME). `0x0008`: nanosecond precision (with Unix time). |
| Name Length | vint          | Present if `0x0001`. |
| Name        | (Name Length) bytes | UTF-8 archive name. No trailing zero (but may have padding zeros from over-provisioning; truncate at first zero). If first byte is zero, no name stored. Present if `0x0001`. |
| Time        | 4 or 8 bytes  | Creation time. FILETIME (8 bytes) if `0x0004` is 0. Unix seconds (4 bytes) if `0x0004` is 1 and `0x0008` is 0. Unix nanoseconds (8 bytes) if both `0x0004` and `0x0008` are 1. Present if `0x0002`. |

---

## 8. File Header and Service Header

File header (type 2) and service header (type 3) share the same structure.

| Field                  | Type   | Description |
|------------------------|--------|-------------|
| Header CRC32           | uint32 |  |
| Header Size            | vint   |  |
| Header Type            | vint   | 2 (file) or 3 (service). |
| Header Flags           | vint   | Common flags. |
| Extra Area Size        | vint   | Present if common flag `0x0001`. |
| Data Size              | vint   | Packed file size. Present if common flag `0x0002`. |
| File Flags             | vint   | See below. |
| Unpacked Size          | vint   | Uncompressed size (ignored if file flag `0x0008` is set). |
| Attributes             | vint   | OS-specific file attributes (file header) or reserved (service header). |
| mtime                  | uint32 | Unix time. Present if file flag `0x0002`. |
| Data CRC32             | uint32 | CRC32 of unpacked data. For split files (except last part): CRC32 of packed data in this volume. Present if file flag `0x0004`. |
| Compression Information | vint  | See below. |
| Host OS                | vint   | `0x0000` = Windows, `0x0001` = Unix. |
| Name Length             | vint   |  |
| Name                   | (Name Length) bytes | UTF-8, no trailing zero. Forward slash as path separator on all OSes. |
| Extra Area             | ...    | Present if common flag `0x0001`. |
| Data Area              | ...    | Compressed file data. Present if common flag `0x0002`. |

### File Flags

| Flag     | Meaning |
|----------|---------|
| `0x0001` | Directory (file header only). |
| `0x0002` | Unix mtime field is present. |
| `0x0004` | CRC32 field is present. |
| `0x0008` | Unpacked size unknown (extract until end of stream). |

### Compression Information (bitfield)

| Bits    | Mask       | Description |
|---------|------------|-------------|
| 0-5     | `0x003F`   | Algorithm version. 0 = RAR 5.0+, 1 = RAR 7.0+. |
| 6       | `0x0040`   | Solid flag. Reuses dictionary from previous file. File headers only. |
| 7-9     | `0x0380`   | Compression method (0-5). 0 = stored (no compression). |
| 10-14   | `0x7C00`   | Dictionary size exponent N. Size = 128 KB * 2^N. Range: 0 (128 KB) to 23 (1 TB theoretical max). Values above 15 require algorithm version 1. |
| 15-19   | `0xF8000`  | Dictionary size fraction (version 1 only). Multiplied by dict size from bits 10-14, divided by 32, added to dict size. Allows 31 intermediate sizes between powers of 2. |
| 20      | `0x100000` | Version 1 only. Indicates version 0 algorithm with version 1 dictionary size encoding. Used when appending v1 files to a v0 solid stream with increased dictionary. |

### File Name Encoding

File names are UTF-8. On Unix, high-ASCII bytes that can't be correctly converted
to Unicode are mapped to the `0xE080`-`0xE0FF` private use area, with a `0xFFFE`
non-character inserted somewhere in the string to indicate mapped characters are
present. These names are only portable on the originating system.

### Service Header Names

| Name | Purpose |
|------|---------|
| CMT  | Archive comment |
| QO   | Quick open data |
| ACL  | NTFS file permissions |
| STM  | NTFS alternate data stream |
| RR   | Recovery record |

### File/Service Extra Area Record Types

| Type | Name | Description |
|------|------|-------------|
| 0x01 | File encryption | Encryption parameters. |
| 0x02 | File hash | Non-CRC32 hash (BLAKE2sp). |
| 0x03 | File time | High-precision timestamps. |
| 0x04 | File version | File version number. |
| 0x05 | Redirection | Symlinks, junctions, hard links, file copies. |
| 0x06 | Unix owner | User/group name and numeric IDs. |
| 0x07 | Service data | Additional service header parameters. |

#### File Encryption Record (type 0x01)

| Field        | Type     | Description |
|--------------|----------|-------------|
| Size         | vint     |  |
| Type         | vint     | 0x01 |
| Version      | vint     | 0 = AES-256. |
| Flags        | vint     | `0x0001`: password check data present. `0x0002`: tweaked checksums (checksums made key-dependent to prevent content guessing). |
| KDF Count    | 1 byte   | Binary log of PBKDF2 iterations. |
| Salt         | 16 bytes | Per-file salt. |
| IV           | 16 bytes | AES-256 initialization vector. |
| Check Value  | 12 bytes | Password verification (if flag `0x0001`). Same format as archive encryption header. |

#### File Hash Record (type 0x02)

| Field     | Type     | Description |
|-----------|----------|-------------|
| Size      | vint     |  |
| Type      | vint     | 0x02 |
| Hash Type | vint     | `0x00` = BLAKE2sp. |
| Hash Data | 32 bytes | BLAKE2sp hash (for type 0x00). |

For split files (except last part): hash of packed data in current volume.
For non-split files and last parts: hash of unpacked data.

#### File Time Record (type 0x03)

| Field             | Type            | Description |
|-------------------|-----------------|-------------|
| Size              | vint            |  |
| Type              | vint            | 0x03 |
| Flags             | vint            | See below. |
| mtime             | uint32/uint64   | If flag `0x0002`. |
| ctime             | uint32/uint64   | If flag `0x0004`. |
| atime             | uint32/uint64   | If flag `0x0008`. |
| mtime nanoseconds | uint32          | If flags `0x0001` + `0x0002` + `0x0010`. |
| ctime nanoseconds | uint32          | If flags `0x0001` + `0x0004` + `0x0010`. |
| atime nanoseconds | uint32          | If flags `0x0001` + `0x0008` + `0x0010`. |

**Time flags:**

| Flag     | Meaning |
|----------|---------|
| `0x0001` | Unix time_t format (else Windows FILETIME). |
| `0x0002` | Modification time present. |
| `0x0004` | Creation time present. |
| `0x0008` | Last access time present. |
| `0x0010` | Nanosecond precision (Unix time only). |

Times are uint32 (Unix seconds) or uint64 (Windows FILETIME / Unix nanoseconds)
depending on flags.

#### File Version Record (type 0x04)

| Field          | Type | Description |
|----------------|------|-------------|
| Size           | vint |  |
| Type           | vint | 0x04 |
| Flags          | vint | Currently 0. |
| Version Number | vint | File version number. |

#### File System Redirection Record (type 0x05)

| Field            | Type | Description |
|------------------|------|-------------|
| Size             | vint |  |
| Type             | vint | 0x05 |
| Redirection Type | vint | See below. |
| Flags            | vint | `0x0001`: target is a directory. |
| Name Length       | vint | Length of target name. |
| Name             | (Name Length) bytes | Link target, UTF-8, no trailing zero. |

**Redirection types:**

| Value  | Type |
|--------|------|
| 0x0001 | Unix symlink |
| 0x0002 | Windows symlink |
| 0x0003 | Windows junction |
| 0x0004 | Hard link |
| 0x0005 | File copy |

#### Unix Owner Record (type 0x06)

| Field             | Type | Description |
|-------------------|------|-------------|
| Size              | vint |  |
| Type              | vint | 0x06 |
| Flags             | vint | `0x0001`: user name. `0x0002`: group name. `0x0004`: numeric UID. `0x0008`: numeric GID. |
| User Name Length  | vint | If `0x0001`. |
| User Name         | ...  | Native encoding, not zero-terminated. If `0x0001`. |
| Group Name Length | vint | If `0x0002`. |
| Group Name        | ...  | Native encoding, not zero-terminated. If `0x0002`. |
| User ID           | vint | If `0x0004`. |
| Group ID          | vint | If `0x0008`. |

#### Service Data Record (type 0x07)

| Field | Type | Description |
|-------|------|-------------|
| Size  | vint |  |
| Type  | vint | 0x07 |
| Data  | ...  | Contents depend on service header type. |

---

## 9. End of Archive Header

Header type = 5.

| Field              | Type   | Description |
|--------------------|--------|-------------|
| Header CRC32       | uint32 |  |
| Header Size        | vint   |  |
| Header Type        | vint   | 5 |
| Header Flags       | vint   |  |
| End of Archive Flags | vint | `0x0001`: archive is a volume and is not the last in the set. |

RAR does not read anything past this header.

---

## 10. Service Headers

### Archive Comment (CMT)

A service header (type 3) with name "CMT". Placed after the main archive header,
before any file headers. Comment data is stored in UTF-8 as uncompressed data
(compression method = 0) immediately following the header. Packed and unpacked
sizes are equal. Comment size is limited to 256 KB.

### Quick Open (QO)

A service header (type 3) with name "QO". Placed after all file headers but before
the recovery record and end of archive header. Can be located via the Locator record
in the main archive header.

Data is uncompressed (method = 0). Contains an array of data cache structures:

| Field          | Type   | Description |
|----------------|--------|-------------|
| Structure CRC32 | uint32 | CRC32 from Structure Size onward. |
| Structure Size | vint   | Size from Flags onward. Max 3 bytes (2 MB). |
| Flags          | vint   | Currently 0. |
| Offset         | vint   | Distance from start of QO header to the cached archive data. Offsets are always increasing. |
| Data Size      | vint   | Size of cached data. |
| Data           | ...    | Cached archive data (typically file/service headers). |

**Security note:** Use the same access pattern (quick open vs. direct) for both
displaying and extracting files. Divergence could allow showing one filename while
extracting different content.

---

## 11. Compression Algorithm

RAR5 uses an LZ-based compression scheme with Huffman coding. The compressed data
stream is organized into blocks, each containing Huffman tables followed by
compressed symbols.

### 11.1 Decoder Properties

The decoder receives 2 bytes of properties:

| Byte | Field |
|------|-------|
| 0    | Dictionary size exponent (power). |
| 1    | Bits 3-7: dictionary size fraction. Bit 0: solid flag. Bit 1: version 7 flag. |

Dictionary size = `(fraction + 32) << (power + 12)` bytes.

The minimum effective window size is 256 KB (`1 << 18`).

### 11.2 Compressed Block Structure

Each compressed block begins with a header read from the byte-aligned bitstream:

| Field      | Size             | Description |
|------------|------------------|-------------|
| Flags      | 1 byte           | See below. |
| Checksum   | 1 byte           | XOR checksum: `checksum ^ flags ^ blocksize_bytes...` must equal `0x5A`. |
| Block Size | 1-3 bytes        | Little-endian. Number of size bytes determined by `(flags >> 3) & 3` (0 = 1 byte, 1 = 2 bytes, 2 = 3 bytes; value 3 is invalid). |

**Flag bits:**

| Bits  | Meaning |
|-------|---------|
| 0-2   | Extra bits count for sub-byte block boundary (value + 1 gives the bit precision; after adjustment: `blockSizeBits7 = (flags & 7) + 1`, then `blockSizeBits7 &= 7`). |
| 3-4   | Number of extra block size bytes (0-2). |
| 5     | Reserved (bit 5 / `0x20` is ignored). |
| 6     | Last block flag. If set, this is the final block in the stream. |
| 7     | Table present flag. If set, new Huffman tables follow. If clear, reuse previous tables. |

### 11.3 Huffman Table Construction

When the table-present flag is set, the block contains new Huffman tables encoded
using a two-level scheme.

#### Level Table (20 symbols)

First, a "level" table of 20 symbols is read. Each symbol's bit length is encoded
in 4 bits. A bit length of 15 followed by a 4-bit count N means the next
`N + 2 + current_position` entries are zero (run-length of zeros).

This level table is used to Huffman-decode the bit lengths for the four main tables.

#### Main Tables

The level decoder is used to read code lengths for all four tables concatenated:

| Table          | Size (symbols)    | Purpose |
|----------------|-------------------|---------|
| Main           | 302 (v6) or 302 (v7: 302 base) | Literals + match length slots + control symbols. |
| Distance       | 64 (v6) or 80 (v7: 64 + 16 extra) | Distance slots. |
| Align          | 16                | Low-order distance bits (when align mode active). |
| Length          | 44                | Match length slots for repeat matches. |

**Main table = 256 + 1 + 1 + 4 + 44 = 306 symbols:**
- Symbols 0-255: literal bytes.
- Symbol 256: filter trigger.
- Symbol 257: use last match length with rep0 distance.
- Symbols 258-261: repeat distance match (rep0-rep3), length follows via Length table.
- Symbols 262+: new match. Length is encoded in this symbol, distance follows via Distance table.

The level decoder symbols have these meanings:
- 0-15: literal bit length value.
- 16: repeat previous bit length. Run count = `3 + read_bits(3)` (i.e., 3-10 times).
- 17: set to zero. Run count = `3 + read_bits(7)` (i.e., 3-130 times).
- 18: set to zero. Run count = `11 + read_bits(7)` (i.e., 11-138 times).

**Correction for the level decoder in the actual 7-Zip implementation:** symbols 16
and 17 use a simpler scheme: symbol `& 1` determines whether to repeat previous value
(even) or use zero (odd). The run count is `3 + read_bits(num)` where
`num = ((symbol & 1) * 4) * 2 + 3`.

All Huffman codes use a maximum of 15 bits.

### 11.4 LZ Match Decoding

The main decode loop reads symbols from the Main Huffman table:

1. **Literal (sym < 256):** Output the byte directly.

2. **Filter trigger (sym == 256):** A post-processing filter follows (see Section 12).

3. **Last-length match (sym == 257):** Repeat the previous match using `_lastLen` bytes
   from distance `rep0`. If `_lastLen` is 0, the symbol is ignored.

4. **Repeat match (sym 258-261):** Use distance from the repeat buffer `_reps[sym - 258]`.
   The repeat distances are rotated: the selected distance moves to position 0, others
   shift. The match length is decoded from the Length table (see below).

5. **New match (sym >= 262):** The length is encoded in the symbol itself:
   `len_slot = sym - 262`. The distance is decoded from the Distance table (see below).
   Repeat distances shift: `_reps[3] = _reps[2]; _reps[2] = _reps[1]; _reps[1] = _reps[0]`.
   The new distance becomes `_reps[0]`.

### 11.5 Match Length Decoding

Match length slots (from either the Main table or Length table) are decoded as follows:

- For slots 0-7: length = slot (+ 2 added by caller).
- For slots >= 8: `numBits = (slot >> 2) - 1`, then
  `length = ((4 | (slot & 3)) << numBits) + read_bits(numBits)` (+ 2 added by caller).

The minimum match length is 2.

### 11.6 Distance Decoding

Distance slots from the Distance table are decoded:

- For slots 0-3: distance = slot.
- For slots >= 4: `numBits = (slot - 2) >> 1`, then
  `distance = ((2 | (slot & 1)) << numBits) + extra_bits`.

  When `numBits >= 4` (the align threshold) and align mode is active:
  - Read `numBits - 4` bits as the high portion.
  - Read the low 4 bits from the Align Huffman table.
  - `distance = base + (high_bits << 4) + align_decoded_value`.

  When `numBits < 4` or align mode is inactive:
  - Read all `numBits` bits directly from the bitstream.

The final distance is `slot_value + 1` (1-based: distance 1 means the immediately
preceding byte).

**Align mode:** active when any symbol in the Align table has a code length different
from 4. When all Align lengths are 4, direct bit reading is equivalent and faster.

### 11.7 Length Bonus for Large Distances

For distances requiring many bits (`numBits >= 4`), the match length receives a
bonus addition based on the number of distance bits:

| numBits range | Bonus |
|---------------|-------|
| 0-6           | 0     |
| 7-11          | 1     |
| 12-16         | 2     |
| 17+           | 3     |

This is stored as a lookup table:
```
{0,0,0,0,0,0,0,1,1,1,1,1,2,2,2,2,2,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3}
```

### 11.8 Repeat Distance Buffer

Four repeat distances are maintained (`_reps[0..3]`), initially set to 0.
The `_lastLen` variable (used by symbol 257) is also initially 0, which causes
symbol 257 to be ignored until a match has been emitted.

When a new match is used (sym >= 262), the distances shift right and the new distance
enters at position 0.

When a repeat match is used (sym 258-261), the selected distance moves to position 0
and the others shift to fill the gap.

### 11.9 Solid Mode

In solid mode, the dictionary window and repeat distances carry over from the
previous file's decompression. The solid flag is indicated in both the Compression
Information field of the file header (bit 6) and the decoder properties (byte 1,
bit 0).

When starting a non-solid file (or the first file), the window is zeroed, repeat
distances are reset to invalid, and the Huffman tables are cleared.

### 11.10 Dictionary Size Limits

The maximum dictionary size depends on the platform pointer size:
- 32-bit: up to 2^31 bytes (2 GB).
- 64-bit: up to 2^40 bytes (1 TB).

---

## 12. Post-Processing Filters

Filters are signaled by Main table symbol 256. After the filter trigger, the
following is read from the bitstream:

### 12.1 Filter Parameters

| Field       | Encoding | Description |
|-------------|----------|-------------|
| Block Start | ReadUInt32 | Offset from current unpack position to filter region start. |
| Block Size  | ReadUInt32 | Size of the filtered region. |
| Filter Type | 3 bits   | Filter type (0-3). |
| Channels    | 5 bits   | Number of channels (1-32). Only read if type is DELTA. |

The `ReadUInt32` function reads a variable-width integer: 2 bits select the byte
count (0=1 byte, 1=2 bytes, 2=3 bytes, 3=4 bytes), which gives `(n+1)*8` total
bits read in `n+1` bytes, least significant byte first.

Maximum filter block size: 4 MB (`1 << 22`).
Maximum concurrent filters: 8192.
Filters must not overlap (each filter's start must be >= previous filter's end).

### 12.2 Filter Types

#### DELTA (type 0)

Delta encoding with N channels. Data is deinterleaved by channel, then each
channel is delta-decoded (each byte = previous byte minus current encoded byte).

**Decode:**
```
For each channel c (0 to channels-1):
    prevByte = 0
    For position p = c, stepping by channels:
        output[p] = prevByte = prevByte - input[inputPos++]
```

#### E8 (type 1)

Intel x86 E8 (CALL) address translation. Scans for `0xE8` bytes and converts
relative CALL addresses to absolute, using a 24-bit (16 MB) file size model.

**Decode (reverse transform):**
```
fileSize = 1 << 24   (16 MB)
For each 0xE8 byte found:
    offset = current_position_in_output & (fileSize - 1)
    addr = read_uint32_le(position + 1)
    if addr < fileSize:
        write_uint32_le(position + 1, addr - offset)
    else if addr > 0xFFFFFFFF - offset:
        write_uint32_le(position + 1, addr + fileSize)
```

#### E8E9 (type 2)

Same as E8 but also processes `0xE9` (JMP) bytes. The scan matches bytes where
`byte & 0xFE == 0xE8` (i.e., both `0xE8` and `0xE9`).

#### ARM (type 3)

ARM branch instruction (BL) address translation. Processes 4-byte aligned
instructions where the high byte is `0xEB`.

**Decode (reverse transform):**
```
For each 4-byte aligned position:
    if instruction[3] == 0xEB:
        offset = (current_position - 4) >> 2   (PC-relative)
        value = read_uint32_le(position)
        value = value - offset
        value = (value & 0x00FFFFFF) | 0xEB000000
        write_uint32_le(position, value)
```

---

## Appendix A: CRC32

RAR5 uses standard CRC32 with the polynomial 0xEDB88320 (reversed representation).
This is the same as used in zlib/gzip/PNG.

## Appendix B: BLAKE2sp

RAR5 uses BLAKE2sp for file hashing when the hash type is 0x00 in the File Hash
extra record. BLAKE2sp is the parallelized version of BLAKE2s, producing a 32-byte
(256-bit) hash. See https://blake2.net for the specification.

## Appendix C: AES-256 Encryption

RAR5 uses AES-256 in CBC mode. The encryption key is derived using PBKDF2 with
HMAC-SHA256. The iteration count is `2^(KDF_Count)` where KDF_Count is the byte
value stored in the encryption header. The salt is 16 bytes.

For password check values, additional PBKDF2 rounds are performed beyond the
key derivation to produce verification data.

## Appendix D: Version History

- **Algorithm version 0 (RAR 5.0+):** Original RAR5 compression. Distance table has
  64 symbols. Dictionary size up to 4 GB (exponent 0-15 in 5-bit field).
- **Algorithm version 1 (RAR 7.0+):** Extended distance table (64 + 16 = 80 symbols).
  Dictionary sizes include fractional values between powers of 2 (bits 15-19) and
  can exceed 4 GB. Bit 20 allows marking v0-algorithm data with v1-style dictionary
  encoding.
