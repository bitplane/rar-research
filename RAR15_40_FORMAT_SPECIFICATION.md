# RAR 1.5-4.x Archive Format Specification

**Independent documentation derived from publicly available sources:**
- `technote.txt` distributed with RAR 3.93 (public domain technical note)
- libarchive `archive_read_support_format_rar.c` (BSD-licensed clean-room implementation by Andres Mejia)
- Kaitai Struct RAR format specification (CC0-1.0)

- `binref/refinery` Python RAR implementation (LGPL-compatible)
- AROS `contrib` repository: `aminet/util/arc/unrar/` (old C implementation, public/AROS license)

**No modern UnRAR source code was referenced in the creation of this document.**

---

## Table of Contents

1. [Overview and Version History](#1-overview-and-version-history)
2. [Archive Signature](#2-archive-signature)
3. [General Block Format](#3-general-block-format)
4. [Marker Block](#4-marker-block)
5. [Archive Header](#5-archive-header)
6. [File Header](#6-file-header)
7. [End of Archive Header](#7-end-of-archive-header)
8. [Comment Header](#8-comment-header)
9. [Subblock / Service Headers](#9-subblock--service-headers)
10. [Authenticity Information](#10-authenticity-information)
11. [Recovery Record](#11-recovery-record)
12. [Extended Time Fields](#12-extended-time-fields)
13. [Unicode Filename Encoding](#13-unicode-filename-encoding)
14. [Encryption](#14-encryption)
15. [Compression Algorithm (RAR 1.5, UNP_VER 15)](#15-compression-algorithm-rar-15-unp_ver-15)
16. [Compression Algorithm (RAR 2.0, UNP_VER 20)](#16-compression-algorithm-rar-20-unp_ver-20)
17. [Audio Compression (RAR 2.0)](#17-audio-compression-rar-20)
18. [Compression Algorithm (RAR 2.9/3.x, UNP_VER 29)](#18-compression-algorithm-rar-293x-unp_ver-29)
19. [PPMd Compression (RAR 3.x)](#19-ppmd-compression-rar-3x)
20. [RARVM Filters (RAR 3.x)](#20-rarvm-filters-rar-3x)
21. [Multi-Volume Archives](#21-multi-volume-archives)
22. [CRC32](#22-crc32)

---

## 1. Overview and Version History

This document covers the RAR archive format used by versions 1.50 through 4.20 of
the RAR archiver. All these versions share the same container format (block structure
and header layout), though compression capabilities evolved significantly.

### Software Version to Format Feature Mapping

| Software Version | UNP_VER | Key Features |
|-----------------|---------|--------------|
| RAR 1.50 | 15 | First version with `Rar!` signature. Basic LZ77. |
| RAR 2.0 | 20 | Multimedia compression (audio filter). Larger dictionaries. |
| RAR 2.9 / 3.0 | 29 | PPMd compression. RARVM filters. AES-128 encryption. Unicode filenames. Recovery volumes. 4 MB dictionary. |
| RAR 3.6 | 36 | Extended time fields (sub-second precision). Large file support (>2 GB). Alternative volume naming (`volname.partNN.rar`). |
| RAR 4.0 | 40 | `MHD_ENCRYPTVER` flag added. Encryption is still AES-128 (same as 3.x). |

The `UNP_VER` field in file headers encodes the minimum RAR version required to
extract: `10 * major + minor`. For example, 29 means RAR 2.9.

### Relationship to RAR 1.3

RAR 1.3 and earlier use a different 4-byte signature (`0x52 0x45 0x7E 0x5E`, ASCII
"RE~^") and a completely different block structure. This document does not cover the
RAR 1.3 format.

### Relationship to RAR 5.0

RAR 5.0 (introduced with WinRAR 5.0 in 2013) is a complete redesign with a different
8-byte signature, variable-length integer encoding, and a new compression algorithm.
See `RAR5_FORMAT_SPECIFICATION.md` for that format.

---

## 2. Archive Signature

7 bytes: `0x52 0x61 0x72 0x21 0x1A 0x07 0x00`

This is ASCII `Rar!` followed by `0x1A 0x07 0x00`.

The signature is actually a valid marker block (see Section 4) with fixed field
values. It may be preceded by a self-extracting (SFX) module. The SFX module itself
never contains the signature byte sequence, so scanning forward from the start of
the file will locate the archive.

---

## 3. General Block Format

Every block in a RAR 1.5-4.x archive follows this structure:

| Offset | Field      | Type   | Description |
|--------|-----------|--------|-------------|
| +0     | HEAD_CRC  | uint16 | CRC of block data (scope varies by block type). |
| +2     | HEAD_TYPE | uint8  | Block type identifier (see below). |
| +3     | HEAD_FLAGS | uint16 | Block flags (type-specific + common flags). |
| +5     | HEAD_SIZE | uint16 | Size of the block header (including these 7 bytes and any type-specific fields). Does not include ADD_SIZE. |
| +7     | ADD_SIZE  | uint32 | Size of additional data following the header. Only present if `HEAD_FLAGS & 0x8000`. |

All multi-byte fields are little-endian.

### Total Block Size

```
if HEAD_FLAGS & 0x8000:
    total_size = HEAD_SIZE + ADD_SIZE
else:
    total_size = HEAD_SIZE
```

### Block Types

| Value | Constant     | Description |
|-------|-------------|-------------|
| 0x72  | MARK_HEAD   | Marker block (archive signature). |
| 0x73  | MAIN_HEAD   | Archive header. |
| 0x74  | FILE_HEAD   | File header. |
| 0x75  | COMM_HEAD   | Old-style comment header (RAR 2.x). |
| 0x76  | AV_HEAD     | Old-style authenticity verification (RAR 2.x). |
| 0x77  | SUB_HEAD    | Old-style subblock (RAR 2.x). |
| 0x78  | PROTECT_HEAD | Old-style recovery record (RAR 2.x). |
| 0x79  | SIGN_HEAD   | Old-style authenticity information (RAR 2.x). |
| 0x7A  | NEWSUB_HEAD | Subblock (RAR 3.x+ service headers, same structure as FILE_HEAD). |
| 0x7B  | ENDARC_HEAD | End of archive header. |

### Common Header Flags

These flags apply to all block types:

| Flag     | Meaning |
|----------|---------|
| `0x4000` | Old versions of RAR should delete this block when updating the archive. If clear, the block is copied as-is. |
| `0x8000` | ADD_SIZE field is present. Total block size is `HEAD_SIZE + ADD_SIZE`. |

---

## 4. Marker Block

The marker block is a fixed 7-byte sequence that serves as the archive signature.
It can be parsed as a standard block with predetermined field values:

| Field      | Value    |
|-----------|----------|
| HEAD_CRC  | `0x6152` |
| HEAD_TYPE | `0x72`   |
| HEAD_FLAGS | `0x1A21` |
| HEAD_SIZE | `0x0007` |

These bytes are: `52 61 72 21 1A 07 00`

---

## 5. Archive Header

Block type `0x73` (MAIN_HEAD). Immediately follows the marker block.

| Offset | Field      | Type   | Description |
|--------|-----------|--------|-------------|
| +0     | HEAD_CRC  | uint16 | CRC of fields from HEAD_TYPE to RESERVED2. |
| +2     | HEAD_TYPE | uint8  | `0x73` |
| +3     | HEAD_FLAGS | uint16 | See below. |
| +5     | HEAD_SIZE | uint16 | Total header size. |
| +7     | RESERVED1 | uint16 | Reserved. |
| +9     | RESERVED2 | uint32 | Reserved. |
| +13    | ENCRYPT_VER | uint8 | Encryption version. Only present if `HEAD_FLAGS & 0x0200`. |

HEAD_CRC is computed as: `CRC32(HEAD_TYPE .. RESERVED2) & 0xFFFF`

### Archive Header Flags

| Flag     | Constant        | Meaning |
|----------|----------------|---------|
| `0x0001` | MHD_VOLUME     | Archive is part of a multi-volume set. |
| `0x0002` | MHD_COMMENT    | Archive comment present. Not set by RAR 3.x+, which uses a subblock instead. |
| `0x0004` | MHD_LOCK       | Archive is locked (cannot be modified). |
| `0x0008` | MHD_SOLID      | Solid archive. |
| `0x0010` | MHD_NEWNUMBERING | New volume naming scheme: `name.partNN.rar` instead of `name.rNN`. |
| `0x0020` | MHD_AV         | Authenticity verification present. Not set by RAR 3.x+. |
| `0x0040` | MHD_PROTECT    | Recovery record is present. |
| `0x0080` | MHD_PASSWORD   | Archive headers are encrypted. |
| `0x0100` | MHD_FIRSTVOLUME | First volume of a multi-volume set (RAR 3.0+). |
| `0x0200` | MHD_ENCRYPTVER | ENCRYPT_VER field is present (RAR 4.0+). |

---

## 6. File Header

Block type `0x74` (FILE_HEAD). One per archived file.

| Offset | Field      | Type   | Description |
|--------|-----------|--------|-------------|
| +0     | HEAD_CRC  | uint16 | CRC of fields from HEAD_TYPE to FILE_NAME (and SALT/EXT_TIME if present). |
| +2     | HEAD_TYPE | uint8  | `0x74` |
| +3     | HEAD_FLAGS | uint16 | See below. |
| +5     | HEAD_SIZE | uint16 | Header size (includes filename, salt, ext_time). |
| +7     | PACK_SIZE | uint32 | Compressed file size (low 32 bits). |
| +11    | UNP_SIZE  | uint32 | Uncompressed file size (low 32 bits). |
| +15    | HOST_OS   | uint8  | Operating system. See below. |
| +16    | FILE_CRC  | uint32 | CRC32 of uncompressed file data. |
| +20    | FTIME     | uint32 | Modification time in MS-DOS format. |
| +24    | UNP_VER   | uint8  | Minimum RAR version needed to extract (`10 * major + minor`). |
| +25    | METHOD    | uint8  | Compression method. See below. |
| +26    | NAME_SIZE | uint16 | File name length in bytes. |
| +28    | ATTR      | uint32 | File attributes (OS-specific). |

Following the fixed fields, in order:

| Field          | Type       | Condition |
|---------------|------------|-----------|
| HIGH_PACK_SIZE | uint32    | Present if `HEAD_FLAGS & 0x0100`. High 32 bits of compressed size. |
| HIGH_UNP_SIZE  | uint32    | Present if `HEAD_FLAGS & 0x0100`. High 32 bits of uncompressed size. |
| FILE_NAME      | NAME_SIZE bytes | File name. See Section 13 for encoding. |
| SALT           | 8 bytes   | Present if `HEAD_FLAGS & 0x0400`. Encryption salt. |
| EXT_TIME       | variable  | Present if `HEAD_FLAGS & 0x1000`. See Section 12. |

The `0x8000` flag is always set for file headers. The data area (compressed file
content) follows the header. Its size is:
```
if HEAD_FLAGS & 0x0100:
    data_size = (HIGH_PACK_SIZE << 32) | PACK_SIZE
else:
    data_size = PACK_SIZE
```

### File Header Flags

| Flag     | Constant       | Meaning |
|----------|---------------|---------|
| `0x0001` | FHD_SPLIT_BEFORE | File continued from previous volume. |
| `0x0002` | FHD_SPLIT_AFTER  | File continued in next volume. |
| `0x0004` | FHD_PASSWORD     | File is encrypted. |
| `0x0008` | FHD_COMMENT      | File comment present. Not set by RAR 3.x+. |
| `0x0010` | FHD_SOLID        | Information from previous files is used (solid compression). |
| `0x00E0` | (bits 5-7)       | Dictionary size. See below. |
| `0x0100` | FHD_LARGE        | HIGH_PACK_SIZE and HIGH_UNP_SIZE fields are present. Used for files >2 GB. |
| `0x0200` | FHD_UNICODE      | FILE_NAME contains Unicode data. See Section 13. |
| `0x0400` | FHD_SALT         | 8-byte SALT field is present (encryption). |
| `0x0800` | FHD_VERSION      | Old file version; version number appended to name as `;n`. |
| `0x1000` | FHD_EXTTIME      | Extended time field is present. See Section 12. |
| `0x2000` | FHD_EXTFLAGS     | Reserved for internal use. |
| `0x8000` | (always set)     | ADD_SIZE present. Data area follows header. |

### Dictionary Size (HEAD_FLAGS bits 5-7)

Encoded in bits 5, 6, and 7 of HEAD_FLAGS (`(HEAD_FLAGS >> 5) & 0x07`):

| Value | Dictionary Size |
|-------|----------------|
| 0     | 64 KB |
| 1     | 128 KB |
| 2     | 256 KB |
| 3     | 512 KB |
| 4     | 1024 KB (1 MB) |
| 5     | 2048 KB (2 MB) |
| 6     | 4096 KB (4 MB) |
| 7     | Entry is a directory (no compressed data). |

### Host OS Values

| Value | OS |
|-------|-----|
| 0     | MS-DOS |
| 1     | OS/2 |
| 2     | Windows (Win32) |
| 3     | Unix |
| 4     | Mac OS |
| 5     | BeOS |

### Compression Methods

| Value | Name    | Description |
|-------|---------|-------------|
| 0x30  | Store   | No compression (stored). |
| 0x31  | Fastest | Fastest compression. |
| 0x32  | Fast    | Fast compression. |
| 0x33  | Normal  | Normal compression. |
| 0x34  | Good    | Good compression. |
| 0x35  | Best    | Best compression. |

### File Attributes

Interpretation depends on HOST_OS:

- **MS-DOS, OS/2, Windows**: Standard DOS/Windows file attributes. Bit 4 (`0x10`) indicates a directory.
- **Unix, Mac OS, BeOS**: Unix-style mode bits (permissions, file type).

---

## 7. End of Archive Header

Block type `0x7B` (ENDARC_HEAD).

| Offset | Field      | Type   | Description |
|--------|-----------|--------|-------------|
| +0     | HEAD_CRC  | uint16 | CRC. |
| +2     | HEAD_TYPE | uint8  | `0x7B` |
| +3     | HEAD_FLAGS | uint16 | Flags. |
| +5     | HEAD_SIZE | uint16 | Header size. |

If `HEAD_FLAGS & 0x8000`, an ADD_SIZE field follows at offset +7.

RAR stops reading after this header. For multi-volume archives, the presence of
this header signals the end of the current volume.

---

## 8. Comment Header

Block type `0x75` (COMM_HEAD). Old-style comment used by RAR 2.x.

RAR 3.x and later store comments as subblocks (type `0x7A`) instead, and do not
set the `MHD_COMMENT` flag or use this block type.

Comment blocks are embedded within other blocks (archive header or file header)
rather than existing as standalone blocks. The comment text is compressed.

---

## 9. Subblock / Service Headers

### Old-Style Subblocks (types 0x76-0x79)

These are legacy block types from RAR 2.x:

| Type | Name | Description |
|------|------|-------------|
| 0x76 | AV_HEAD | Authenticity verification (RAR 2.x). |
| 0x77 | SUB_HEAD | Generic subblock (RAR 2.x). |
| 0x78 | PROTECT_HEAD | Recovery record (RAR 2.x). |
| 0x79 | SIGN_HEAD | Authenticity information (RAR 2.x). |

### New-Style Subblocks (type 0x7A, RAR 3.x+)

Block type `0x7A` (NEWSUB_HEAD). Uses the same structure as a file header
(Section 6), where the FILE_NAME field contains a short identifier indicating
the subblock type.

Known subblock names:

| Name | Description |
|------|-------------|
| `CMT` | Archive or file comment. Data is UTF-8 text, compressed. |
| `STM` | NTFS alternate data stream. |
| `ACL` | NTFS access control list. |
| `EA`  | OS/2 extended attributes. |
| `UO`  | Unix file owner (user/group). |
| `MAC` | Mac OS file info. |
| `BEEA` | BeOS extended attributes. |
| `NTACL` | NTFS ACL (alternative form). |
| `RR` | Recovery record (RAR 3.x+). |

Subblocks immediately follow the file header they are associated with, or follow
the archive header for archive-level metadata (e.g., archive comment).

---

## 10. Authenticity Information

RAR 2.x supported authenticity verification (AV) to identify who created the
archive. This feature was deprecated in RAR 3.x.

Block type `0x76` (AV_HEAD) or `0x79` (SIGN_HEAD). Indicated by the `MHD_AV`
flag (`0x0020`) in the archive header.

---

## 11. Recovery Record

The recovery record allows partial reconstruction of damaged archives using
Reed-Solomon error correction.

- **RAR 2.x**: Uses the `PROTECT_HEAD` block type (`0x78`). Indicated by
  `MHD_PROTECT` flag (`0x0040`) in the archive header.
- **RAR 3.x+**: Uses a `NEWSUB_HEAD` subblock (`0x7A`) with name `RR`.

The internal structure of recovery records (RS parameters, sector size, parity
layout) is not specified in this document. Recovery records are optional
metadata for archive repair and are not required for extraction. For
implementation reference, see the `PROTECT_HEAD` handling in libarchive and
the `RecVolumes3` class in the official unrar source.

---

## 12. Extended Time Fields

Present when `HEAD_FLAGS & 0x1000` (FHD_EXTTIME) is set in a file header.

The extended time field provides sub-second precision and additional timestamps
(creation time, access time, archive time) beyond the basic MS-DOS mtime in the
file header.

### Structure

The field begins with a 16-bit flags word:

| Field | Type   | Description |
|-------|--------|-------------|
| FLAGS | uint16 | Four 4-bit groups controlling each timestamp. |

The 16-bit flags value is divided into four 4-bit groups, from most significant
to least significant:

| Bits  | Timestamp |
|-------|-----------|
| 15-12 | mtime (modification time) |
| 11-8  | ctime (creation time) |
| 7-4   | atime (access time) |
| 3-0   | arctime (archive time) |

### Per-Timestamp Flag Bits

Each 4-bit group (`rmode`) contains:

| Bit | Mask | Meaning |
|-----|------|---------|
| 3   | 0x8  | Timestamp is present. |
| 2   | 0x4  | Add 1 second to the timestamp (odd-second correction for DOS time rounding). |
| 1-0 | 0x3  | Number of additional bytes of sub-second precision (0-3). |

### Decoding Algorithm

For each timestamp, from mtime (i=3) down to arctime (i=0):

```
rmode = (flags >> (i * 4)) & 0xF

if rmode & 0x8:
    if this is mtime (i==3) and the DOS mtime from the file header is valid:
        time = mtime_from_file_header
    else:
        time = read_uint32()    # MS-DOS format timestamp

    count = rmode & 0x3        # number of sub-second precision bytes
    remainder = 0
    for j in 0..count:
        byte = read_uint8()
        remainder = (byte << 16) | (remainder >> 8)

    nanoseconds = remainder / 10_000_000   # convert 100ns units

    if rmode & 0x4:
        time += 1 second       # odd-second correction
```

The sub-second bytes provide up to 24 bits of precision in units of 100
nanoseconds (Windows FILETIME resolution). The bytes are packed in a specific
order: each new byte shifts into the high 8 bits of a 24-bit accumulator, with
existing bits shifting right by 8.

---

## 13. Unicode Filename Encoding

When `HEAD_FLAGS & 0x0200` (FHD_UNICODE) is set, the FILE_NAME field may contain
Unicode data in one of two forms:

### Form 1: Compressed Unicode (RAR 3.x)

If the FILE_NAME data contains a zero byte (i.e., `strlen(FILE_NAME) < NAME_SIZE`),
the field contains both an ASCII filename and encoded Unicode data:

```
[ASCII name] [0x00] [highbyte] [encoded Unicode data...]
```

- The ASCII name runs from the start to the first `0x00` byte.
- The byte after the zero is the `highbyte` — the default high byte for Unicode
  characters.
- The remaining bytes encode Unicode characters using a flag-based scheme.

The encoded data is processed in pairs of 2-bit flags packed into flag bytes
(4 pairs per byte, MSB first):

| Flag Value | Meaning |
|-----------|---------|
| 0 | Next byte is the low byte of a Unicode character. High byte is `0x00`. |
| 1 | Next byte is the low byte of a Unicode character. High byte is `highbyte`. |
| 2 | Next 2 bytes are the Unicode character (high byte first, then low byte). |
| 3 | Run-length encoding. Next byte encodes length and optional correction. |

For flag value 3 (run-length):
- Read one byte as `length_byte`.
- If `length_byte & 0x80`: read another byte as `extra`. High byte = `highbyte`.
  Run length = `(length_byte & 0x7F) + 2` characters, each constructed from
  `highbyte` (high) and `ASCII_char_at_position + extra` (low).
- If `length_byte & 0x80` is clear: `extra = 0`, `high = 0`.
  Same run-length construction.

The resulting encoding is UTF-16BE (big-endian).

### Form 2: UTF-8 (RAR 3.x+)

If `FHD_UNICODE` is set but the FILE_NAME data contains no zero bytes (i.e.,
`strlen(FILE_NAME) == NAME_SIZE`), the filename is encoded as UTF-8.

### Path Separator

Regardless of the originating OS, path separators in filenames are stored as
backslash (`\`). Implementations should convert to the native separator on
extraction (typically `/` on Unix).

---

## 14. Encryption

### Header Encryption

When `MHD_PASSWORD` (`0x0080`) is set in the archive header, all headers after
the archive header are encrypted. The encrypted data cannot be parsed without
the password.

### File Encryption

Individual files can be encrypted when `FHD_PASSWORD` (`0x0004`) is set in the
file header. Only the compressed data is encrypted; the header remains readable.

When `FHD_SALT` (`0x0400`) is set, an 8-byte salt value follows the filename in
the file header.

### 14.3 RAR 1.5 Encryption (CRYPT_RAR15)

RAR 1.5 uses a CRC-based XOR stream cipher. Stronger than RAR 1.3 but still
considered broken by modern standards.

**Key derivation:**

```
crc_tab = standard_CRC32_table

# Compute CRC32 of password
psw_crc = 0xFFFFFFFF
for each byte B in password:
    psw_crc = (psw_crc >> 8) ^ crc_tab[(psw_crc ^ B) & 0xFF]

Key[0] = psw_crc & 0xFFFF           # low 16 bits of CRC
Key[1] = (psw_crc >> 16) & 0xFFFF   # high 16 bits of CRC
Key[2] = 0
Key[3] = 0

for each byte B in password:
    Key[2] = (Key[2] ^ (B ^ crc_tab[B])) & 0xFFFF
    Key[3] = (Key[3] + B + (crc_tab[B] >> 16)) & 0xFFFF
```

**Decryption (XOR stream cipher):**

```
for each byte at position i in encrypted data:
    Key[0] = (Key[0] + 0x1234) & 0xFFFF
    idx = (Key[0] & 0x1FE) >> 1
    crc_val = crc_tab[idx]
    Key[1] = (Key[1] ^ crc_val) & 0xFFFF
    Key[2] = (Key[2] - (crc_val >> 16)) & 0xFFFF
    Key[0] = (Key[0] ^ Key[2]) & 0xFFFF
    Key[3] = rotate_right_16(Key[3], 1) ^ Key[1]
    Key[3] = rotate_right_16(Key[3], 1)
    Key[0] = (Key[0] ^ Key[3]) & 0xFFFF
    output[i] = data[i] ^ ((Key[0] >> 8) & 0xFF)
```

Where `rotate_right_16(x, 1) = ((x >> 1) | (x << 15)) & 0xFFFF`.

### 14.4 RAR 2.0 Encryption (CRYPT_RAR20)

RAR 2.0 uses a custom Feistel block cipher operating on 16-byte blocks, with
a 256-byte substitution table.

**Initial substitution table:**

A fixed 256-byte permutation (shown as decimal values, row-major, 16 per row):

```
215  19 149  35  73 197 192 205 249  28  16 119  48 221   2  42
232   1 177 233  14  88 219  25 223 195 244  90  87 239 153 137
255 199 147  70  92  66 246  13 216  40  62  29 217 230  86   6
 71  24 171 196 101 113 218 123  93  91 163 178 202  67  44 235
107 250  75 234  49 167 125 211  83 114 157 144  32 193 143  36
158 124 247 187  89 214 141  47 121 228  61 130 213 194 174 251
 97 110  54 229 115  57 152  94 105 243 212  55 209 245  63  11
164 200  31 156  81 176 227  21  76  99 139 188 127  17 248  51
207 120 189 210   8 226  41  72 183 203 135 165 166  60  98   7
122  38 155 170  69 172 252 238  39 134  59 128 236  27 240  80
131   3  85 206 145  79 154 142 159 220 201 133  74  64  20 129
224 185 138 103 173 182  43  34 254  82 198 151 231 180  58  10
118  26 102  12  50 132  22 191 136 111 162 179  45   4 148 108
161  56  78 126 242 222  15 175 146  23  33 241 181 190  77 225
  0  46 169 186  68  95 237  65  53 208 253 168   9  18 100  52
116 184 160  96 109  37  30 106 140 104 150   5 204 117 112  84
```

**Initial round keys:**

```
Key[0] = 0xD3A3B879
Key[1] = 0x3F6D12F7
Key[2] = 0x7515A235
Key[3] = 0xA4E7F123
```

**Key setup (substitution table shuffling):**

```
crc_tab = standard CRC32 table

for j = 0 to 255:
    for i = 0, step 2, while i < password_length:
        n1 = crc_tab[(password[i] - j) & 0xFF] & 0xFF
        i1 = min(i + 1, password_length - 1)
        n2 = crc_tab[(password[i1] + j) & 0xFF] & 0xFF
        k = 1
        while n1 != n2:
            swap(SubstTable[n1], SubstTable[(n1 + i + k) & 0xFF])
            n1 = (n1 + 1) & 0xFF
            k += 1
```

After shuffling, the password is padded to a 16-byte boundary with zeros, then
each 16-byte block is encrypted using the Feistel cipher to further mix the
key state.

**Feistel cipher (32 rounds per 16-byte block):**

```
function SubstLong(val):
    return SubstTable[val & 0xFF]
         | (SubstTable[(val >> 8) & 0xFF] << 8)
         | (SubstTable[(val >> 16) & 0xFF] << 16)
         | (SubstTable[(val >> 24) & 0xFF] << 24)

function EncryptBlock(data[0..15]):
    A = uint32_le(data[0:4])  ^ Key[0]
    B = uint32_le(data[4:8])  ^ Key[1]
    C = uint32_le(data[8:12]) ^ Key[2]
    D = uint32_le(data[12:16]) ^ Key[3]

    for i = 0 to 31:
        T = (C + rotl32(D, 11)) ^ Key[i & 3]
        TA = A ^ SubstLong(T)
        T = ((D ^ rotl32(C, 17)) + Key[i & 3])
        TB = B ^ SubstLong(T)
        A = C;  B = D;  C = TA;  D = TB

    write_uint32_le(data[0:4],   C ^ Key[0])
    write_uint32_le(data[4:8],   D ^ Key[1])
    write_uint32_le(data[8:12],  A ^ Key[2])
    write_uint32_le(data[12:16], B ^ Key[3])
    UpdateKeys(data[0:16])

function DecryptBlock(data[0..15]):
    save = copy of data[0:16]
    A = uint32_le(data[0:4])  ^ Key[0]
    B = uint32_le(data[4:8])  ^ Key[1]
    C = uint32_le(data[8:12]) ^ Key[2]
    D = uint32_le(data[12:16]) ^ Key[3]

    for i = 31 down to 0:        # reversed round order
        T = (C + rotl32(D, 11)) ^ Key[i & 3]
        TA = A ^ SubstLong(T)
        T = ((D ^ rotl32(C, 17)) + Key[i & 3])
        TB = B ^ SubstLong(T)
        A = C;  B = D;  C = TA;  D = TB

    write_uint32_le(data[0:4],   C ^ Key[0])
    write_uint32_le(data[4:8],   D ^ Key[1])
    write_uint32_le(data[8:12],  A ^ Key[2])
    write_uint32_le(data[12:16], B ^ Key[3])
    UpdateKeys(save)             # update from the original ciphertext
```

**Key update after each block:**

```
function UpdateKeys(block[0..15]):
    for i = 0, 4, 8, 12:
        Key[0] ^= crc_tab[block[i]   & 0xFF]
        Key[1] ^= crc_tab[block[i+1] & 0xFF]
        Key[2] ^= crc_tab[block[i+2] & 0xFF]
        Key[3] ^= crc_tab[block[i+3] & 0xFF]
```

All arithmetic is 32-bit unsigned. `rotl32(v, n)` is left rotation of a
32-bit value by `n` bits. Data is processed in 16-byte blocks. The key
evolves after each block, providing a form of cipher block chaining.

### 14.5 RAR 3.x Encryption (CRYPT_RAR30)

AES-128 in CBC mode. The key and IV are derived from the password and an 8-byte
salt using a custom KDF based on iterative SHA-1 hashing.

**Key derivation (rar3_kdf):**

```
ROUNDS = 0x40000    # 262144 iterations

pw_utf16 = encode_utf16le(password)
raw_data = pw_utf16 + salt[0:8]

iv = array of 16 bytes, all zero
sha1 = new SHA1 context

for i = 0 to ROUNDS - 1:
    sha1.update(raw_data)
    sha1.update(i as 3 bytes, little-endian)    # low 24 bits of i

    # Extract IV bytes at intervals of ROUNDS/16 = 0x4000
    if i % 0x4000 == 0:
        iv_idx = i / 0x4000
        if iv_idx < 16:
            temp_digest = sha1.copy().digest()   # snapshot, don't finalize
            iv[iv_idx] = temp_digest[19]         # last byte of SHA-1 output

key_digest = sha1.digest()

# Byte-swap within each 32-bit word
key = array of 16 bytes
for i = 0 to 3:
    key[i*4 + 0] = key_digest[i*4 + 3]
    key[i*4 + 1] = key_digest[i*4 + 2]
    key[i*4 + 2] = key_digest[i*4 + 1]
    key[i*4 + 3] = key_digest[i*4 + 0]

return (key, iv)
```

The SHA-1 context is **not** reset between iterations — it accumulates all
262144 rounds into a single running hash. The 3-byte iteration counter means
this KDF only works correctly for up to 16 million iterations (24-bit counter),
but since ROUNDS = 262144 this is not a practical limitation.

**Decryption:**

Standard AES-128-CBC decrypt using the derived key and IV. Data is padded to
a 16-byte boundary with zeros if necessary.

### 14.6 RAR 4.x Encryption (Same as RAR 3.x)

When `MHD_ENCRYPTVER` (`0x0200`) is set in the archive header, a 1-byte
`ENCRYPT_VER` field follows the reserved fields. Despite its name, this field
**does not change the encryption algorithm**. Every known implementation either
ignores it entirely or uses it only for a non-cryptographic buffer safety flag.

RAR 4.x (UNP_VER 36+) uses exactly the same `CRYPT_RAR30` scheme described in
Section 14.5: SHA-1 KDF with 262144 iterations, producing a 16-byte AES-128
key and 16-byte IV.

There is no `CRYPT_RAR40` in any implementation. The encryption method enum
jumps directly from `CRYPT_RAR30` to `CRYPT_RAR50`. AES-256 was only
introduced with the RAR 5.0 format.

Note: Some WinRAR documentation mentions "AES-128/256" for RAR 4.x. This is
inaccurate — examination of the official unrar source, hashcat, John the
Ripper, libarchive, 7-Zip, and multiple Python implementations all confirm
RAR 3.x and 4.x use identical AES-128 encryption.

---

## 15. Compression Algorithm (RAR 1.5, UNP_VER 15)

RAR 1.5 uses the same adaptive Huffman + LZ77 algorithm as RAR 1.3. The
decompressor is shared (`Unpack15`). See `RAR13_FORMAT_SPECIFICATION.md`
Section 6 for the complete algorithm description.

The dictionary window size is selected by the file header flags (bits 5-7),
ranging from 64 KB to 4 MB. The algorithm itself is identical regardless of
window size — only the window mask changes.

---

## 16. Compression Algorithm (RAR 2.0, UNP_VER 20)

RAR 2.0 uses a standard Huffman + LZ77 scheme with explicit code tables (unlike
the adaptive scheme in RAR 1.5). It also introduces an audio compression mode
for multimedia data.

### 16.1 Window Size

RAR 2.0 uses a 1 MB (0x100000 byte) sliding window, regardless of the
dictionary size field in the file header. The window mask is `0xFFFFF`.

### 16.2 Huffman Table Constants

| Constant | Value | Description |
|----------|-------|-------------|
| NC20     | 298   | Main (literal + length) table size. |
| DC20     | 48    | Distance table size. |
| RC20     | 28    | Repeat-length table size. |
| BC20     | 19    | Level (bit-length) table size. |
| MC20     | 257   | Multimedia (audio) table size. |

### 16.3 Block Structure

Each compressed block begins with a 2-bit header and Huffman tables:

```
BitField = read_bits(16)

AudioBlock = (BitField & 0x8000) != 0    # bit 15: audio mode
KeepTables = (BitField & 0x4000) != 0    # bit 14: keep existing tables
consume_bits(2)

if not KeepTables:
    clear OldTable (set all to zero)

if AudioBlock:
    Channels = ((BitField >> 12) & 3) + 1   # 1-4 channels
    consume_bits(2)
    TableSize = MC20 * Channels
else:
    TableSize = NC20 + DC20 + RC20           # = 374
```

### 16.4 Huffman Table Construction

The same two-level scheme as RAR 2.9+:

1. Read 19 x 4-bit code lengths for the level table.
2. Build a Huffman table from these 19 lengths.
3. Use the level table to decode `TableSize` code lengths for the main tables.

Level decoder symbols:

| Symbol | Meaning |
|--------|---------|
| 0-15   | Code length. Added to previous modulo 16: `new = (old + val) & 0xF`. |
| 16     | Repeat previous length. Count = `3 + read_bits(2)` (3-6 times). |
| 17     | Set to zero. Count = `3 + read_bits(3)` (3-10 times). |
| 18     | Set to zero. Count = `11 + read_bits(7)` (11-138 times). |

For normal blocks, the decoded lengths are split into three tables:

| Table | Offset | Size | Purpose |
|-------|--------|------|---------|
| LD    | 0      | NC20 (298) | Literals (0-255) + control + length slots. |
| DD    | NC20   | DC20 (48)  | Distance slots. |
| RD    | NC20+DC20 | RC20 (28) | Repeat-match length slots. |

For audio blocks, the lengths are split per channel:

| Table | Offset | Size | Purpose |
|-------|--------|------|---------|
| MD[0] | 0 | MC20 (257) | Channel 0 audio symbols. |
| MD[1] | MC20 | MC20 | Channel 1 (if Channels >= 2). |
| MD[2] | MC20*2 | MC20 | Channel 2 (if Channels >= 3). |
| MD[3] | MC20*3 | MC20 | Channel 3 (if Channels == 4). |

### 16.5 LZ Match Decode Loop

```
while DestUnpSize >= 0:
    if AudioBlock:
        # Audio mode (see Section 17)
        AudioNumber = DecodeNumber(MD[CurChannel])
        if AudioNumber == 256:
            ReadTables()      # new block
            continue
        output_byte(DecodeAudio(AudioNumber))
        CurChannel = (CurChannel + 1) % Channels
        continue

    Number = DecodeNumber(LD)

    if Number < 256:
        output_literal(Number)                    # literal byte
        continue

    if Number == 256:
        CopyString(LastLength, LastDist)           # repeat last match
        continue

    if Number < 261:                               # repeat distance match
        Distance = OldDist[(OldDistPtr - (Number - 256)) & 3]
        LengthNumber = DecodeNumber(RD)
        Length = LDecode[LengthNumber] + 2
        if LBits[LengthNumber] > 0:
            Length += read_bits(LBits[LengthNumber])
        # Distance-based length bonus
        if Distance >= 0x101:  Length += 1
        if Distance >= 0x2000: Length += 1
        if Distance >= 0x40000: Length += 1
        CopyString(Length, Distance)
        continue

    if Number < 270:                               # short-distance match
        idx = Number - 261
        Distance = SDDecode[idx] + 1
        if SDBits[idx] > 0:
            Distance += read_bits(SDBits[idx])
        CopyString(2, Distance)
        continue

    if Number == 269:
        ReadTables()                               # new block
        continue

    if Number > 269:                               # new match
        idx = Number - 270
        Length = LDecode[idx] + 3
        if LBits[idx] > 0:
            Length += read_bits(LBits[idx])

        DistNumber = DecodeNumber(DD)
        Distance = DDecode[DistNumber] + 1
        if DBits[DistNumber] > 0:
            Distance += read_bits(DBits[DistNumber])

        # Distance-based length bonus
        if Distance >= 0x2000:  Length += 1
        if Distance >= 0x40000: Length += 1
        CopyString(Length, Distance)
        continue
```

### 16.6 Match Length Tables

Shared with RAR 2.9+ (same `LDecode`/`LBits` tables):

```
LDecode = [0, 1, 2, 3, 4, 5, 6, 7, 8, 10, 12, 14, 16, 20,
           24, 28, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224]
LBits   = [0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2,
           2, 2, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5]
```

### 16.7 Match Distance Tables

```
DDecode = [0, 1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64, 96, 128, 192,
           256, 384, 512, 768, 1024, 1536, 2048, 3072, 4096, 6144, 8192,
           12288, 16384, 24576, 32768, 49152, 65536, 98304, 131072, 196608,
           262144, 327680, 393216, 458752, 524288, 589824, 655360, 720896,
           786432, 851968, 917504, 983040]
DBits   = [0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6,
           7, 7, 8, 8, 9, 9, 10, 10, 11, 11, 12, 12, 13, 13, 14, 14,
           15, 15, 16, 16, 16, 16, 16, 16, 16, 16, 16, 16, 16, 16, 16, 16]
```

### 16.8 Short-Distance Match Tables

```
SDDecode = [0, 4, 8, 16, 32, 64, 128, 192]
SDBits   = [2, 2, 3,  4,  5,  6,   6,   6]
```

### 16.9 Length Bonus for Large Distances

Different from RAR 2.9+. For new matches:

```
if Distance >= 0x2000:  Length += 1
if Distance >= 0x40000: Length += 1
```

For repeat-distance matches:

```
if Distance >= 0x101:   Length += 1
if Distance >= 0x2000:  Length += 1
if Distance >= 0x40000: Length += 1
```

### 16.10 Repeat Distance Buffer

Four repeat distances (`OldDist[0..3]`), same semantics as RAR 2.9+.
Circular pointer `OldDistPtr` advances modulo 4 on each new match.

---

## 17. Audio Compression (RAR 2.0)

RAR 2.0 introduced a specialized audio compression mode for multimedia data.
When `AudioBlock` is set in the block header, data is processed through
per-channel adaptive prediction instead of LZ matching.

### 17.1 Audio Variables

All audio state is initialized to zero at the start of a non-solid file:
`CurChannel = 0`, `Channels = 1`, `ChannelDelta = 0`, and all four
`AudioVariables` structs are zeroed.

Each channel (up to 4) maintains independent prediction state:

| Field | Type | Description |
|-------|------|-------------|
| K1-K5 | int | Adaptive filter coefficients (range -16 to +16). |
| D1-D4 | int | Delta history (successive differences). |
| LastDelta | int | Most recent delta value. |
| LastChar | int | Most recent output byte. |
| ByteCount | uint | Bytes decoded on this channel. |
| Dif[11] | uint | Accumulated prediction error for each coefficient adjustment. |

### 17.2 Audio Decode Algorithm

In audio mode, each byte is decoded independently per channel. The Huffman
table MD[channel] decodes a "delta" value (0-255). Symbol 256 signals a new
block (re-read tables).

```
procedure DecodeAudio(Delta) -> byte:
    V = AudioVariables[CurChannel]
    V.ByteCount += 1

    # Shift delta history
    V.D4 = V.D3
    V.D3 = V.D2
    V.D2 = V.LastDelta - V.D1
    V.D1 = V.LastDelta

    # Predict next sample
    PCh = 8 * V.LastChar
        + V.K1 * V.D1
        + V.K2 * V.D2
        + V.K3 * V.D3
        + V.K4 * V.D4
        + V.K5 * ChannelDelta
    PCh = (PCh >> 3) & 0xFF

    # Apply correction
    Ch = (PCh - Delta) & 0xFF

    # Accumulate prediction errors for coefficient adaptation
    D = (signed_byte(Delta)) << 3    # sign-extended, then left-shifted
    Dif[0]  += abs(D)
    Dif[1]  += abs(D - V.D1)
    Dif[2]  += abs(D + V.D1)
    Dif[3]  += abs(D - V.D2)
    Dif[4]  += abs(D + V.D2)
    Dif[5]  += abs(D - V.D3)
    Dif[6]  += abs(D + V.D3)
    Dif[7]  += abs(D - V.D4)
    Dif[8]  += abs(D + V.D4)
    Dif[9]  += abs(D - ChannelDelta)
    Dif[10] += abs(D + ChannelDelta)

    ChannelDelta = V.LastDelta = signed_byte(Ch - V.LastChar)
    V.LastChar = Ch

    # Every 32 bytes: adapt the coefficient with lowest error
    if (V.ByteCount & 0x1F) == 0:
        MinDif = Dif[0]
        NumMinDif = 0
        Dif[0] = 0
        for I = 1 to 10:
            if Dif[I] < MinDif:
                MinDif = Dif[I]
                NumMinDif = I
            Dif[I] = 0

        # Adjust the winning coefficient
        # Odd NumMinDif (1,3,5,7,9) → decrement K[(N-1)/2]
        # Even NumMinDif (2,4,6,8,10) → increment K[(N-1)/2]
        if NumMinDif >= 1:
            index = (NumMinDif - 1) / 2    # K1=0, K2=1, K3=2, K4=3, K5=4
            if NumMinDif is odd:
                if K[index] >= -16: K[index] -= 1
            else:
                if K[index] < 16:   K[index] += 1

    return Ch & 0xFF
```

The prediction uses a 5-tap adaptive linear filter. Every 32 samples, the
coefficient producing the lowest prediction error is adjusted by ±1 (bounded
to the range [-16, +16]). This adapts the filter to the local signal
characteristics.

### 17.3 Channel Interleaving

Audio data is interleaved by channel. Channels cycle in order:
`0, 1, ..., (Channels-1), 0, 1, ...`

The `ChannelDelta` variable carries state between channels (the delta from the
most recently decoded byte on the previous channel).

---

## 18. Compression Algorithm (RAR 2.9/3.x, UNP_VER 29)

RAR 2.9/3.x uses an LZSS-based compression scheme with Huffman coding. The
compressed data stream consists of blocks, each beginning with control flags
that determine whether the block uses LZ or PPMd compression.

### 18.1 Block Header

Each compressed block begins byte-aligned. The first bit determines the
compression mode:

| Bit | Meaning |
|-----|---------|
| 0   | LZ block (described below). |
| 1   | PPMd block (see Section 16). |

For LZ blocks, the next bit controls Huffman table construction:

| Bit | Meaning |
|-----|---------|
| 0   | Clear existing Huffman tables, then read new tables. |
| 1   | Keep existing tables (continuation of previous tables). |

### 18.2 Huffman Table Construction

The LZ compressor uses four Huffman code tables, read as a single concatenated
array of code lengths:

| Table          | Symbol Count | Purpose |
|----------------|-------------|---------|
| MainCode       | 299         | Literals (0-255) + control symbols + match lengths. |
| OffsetCode     | 60          | Match distance slots. |
| LowOffsetCode  | 17          | Low-order distance bits (for large distances). |
| LengthCode     | 28          | Match length slots (for repeat matches). |

Total: 299 + 60 + 17 + 28 = 404 symbols.

The code lengths are encoded using a 20-symbol level table (identical scheme to
RAR 5.0; see RAR5 spec Section 11.3). Each symbol's bit length is encoded in 4
bits. A length of 15 followed by a 4-bit count N means `N + 2` zero entries.

The level table is used to Huffman-decode the code lengths for all four main
tables. Level decoder symbols:

| Symbol | Meaning |
|--------|---------|
| 0-15   | Literal code length. Added to previous length modulo 16: `new = (old + val) & 0xF`. |
| 16     | Repeat previous length. Count = `3 + read_bits(3)` (3-10 times). |
| 17     | Set to zero. Count = `3 + read_bits(7)` (3-130 times). |
| 18     | Set to zero. Count = `11 + read_bits(7)` (11-138 times). |
| 19     | Not used (only 20 level symbols: 0-19, but 18 is the last special). |

Maximum code length: 15 bits.

### 18.3 LZ Match Decoding

The main decode loop reads symbols from the MainCode Huffman table:

**Literal (symbol < 256):** Output the byte directly.

**New block / filter (symbol == 256):** Read 1 bit:
- If 1: new file marker. Read 1 more bit for "new table" flag. Block ends.
- If 0: RARVM filter follows (see Section 17).

**Filter trigger (symbol == 257):** Read a filter definition from the bitstream.

**Last-length repeat (symbol == 258):** Repeat the previous match using
`lastlength` bytes from distance `lastoffset`. If `lastlength` is 0, ignored.

**Repeat match (symbols 259-262):** Use a distance from the repeat buffer
`oldoffset[symbol - 259]`. The selected distance rotates to position 0.
Match length is read from the LengthCode table.

**Short match (symbols 263-270):** Short-distance match with length 2.
Distance is decoded from lookup tables:

```
shortbases = [0, 4, 8, 16, 32, 64, 128, 192]
shortbits  = [2, 2, 3,  4,  5,  6,   6,   6]

offset = shortbases[symbol - 263] + 1
if shortbits[symbol - 263] > 0:
    offset += read_bits(shortbits[symbol - 263])
length = 2
```

**New match (symbols 271+):** Length is encoded in the symbol, distance follows.

### 18.4 Match Length Tables

```
lengthbases = [0,  1,  2,  3,  4,  5,  6,  7,  8, 10, 12, 14, 16, 20,
              24, 28, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224]
lengthbits  = [0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2,
               2, 2, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5]
```

For new matches (symbol >= 271):
```
slot = symbol - 271
length = lengthbases[slot] + 3
if lengthbits[slot] > 0:
    length += read_bits(lengthbits[slot])
```

For repeat matches (from LengthCode):
```
length = lengthbases[slot] + 2
if lengthbits[slot] > 0:
    length += read_bits(lengthbits[slot])
```

### 18.5 Match Distance Tables

```
offsetbases = [0, 1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48,
               64, 96, 128, 192, 256, 384, 512, 768, 1024, 1536,
               2048, 3072, 4096, 6144, 8192, 12288, 16384, 24576,
               32768, 49152, 65536, 98304, 131072, 196608,
               262144, 327680, 393216, 458752, 524288, 589824,
               655360, 720896, 786432, 851968, 917504, 983040,
               1048576, 1310720, 1572864, 1835008, 2097152, 2359296,
               2621440, 2883584, 3145728, 3407872, 3670016, 3932160]
offsetbits  = [0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4,
               5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 10,
               11, 11, 12, 12, 13, 13, 14, 14, 15, 15, 16, 16,
               16, 16, 16, 16, 16, 16, 16, 16, 16, 16, 16, 16,
               18, 18, 18, 18, 18, 18, 18, 18, 18, 18, 18, 18]
```

Distance decoding from the OffsetCode table:

```
slot = read_huffman(OffsetCode)
offset = offsetbases[slot] + 1

if offsetbits[slot] > 0:
    if slot > 9:
        # Large distance: split into high bits + low offset code
        if offsetbits[slot] > 4:
            offset += read_bits(offsetbits[slot] - 4) << 4

        # Low 4 bits from LowOffsetCode table
        if numlowoffsetrepeats > 0:
            numlowoffsetrepeats -= 1
            offset += lastlowoffset
        else:
            low_sym = read_huffman(LowOffsetCode)
            if low_sym == 16:
                numlowoffsetrepeats = 15
                offset += lastlowoffset
            else:
                offset += low_sym
                lastlowoffset = low_sym
    else:
        # Small distance: all extra bits read directly
        offset += read_bits(offsetbits[slot])
```

### 18.6 Length Bonus for Large Distances

For new matches, the length receives a bonus based on distance:

```
if offset >= 0x2000:   length += 1
if offset >= 0x40000:  length += 1
```

### 18.7 Repeat Distance Buffer

Four repeat distances are maintained (`oldoffset[0..3]`). When a new match
is used, distances shift right and the new distance enters at position 0.
When a repeat match is used, the selected distance rotates to position 0.

---

## 19. PPMd Compression (RAR 3.x)

RAR 3.x can use PPMd (Prediction by Partial Matching, variant H by Dmitry
Shkarin) as an alternative to LZ compression. The choice is per-block.

### 19.1 PPMd Block Parameters

When the PPMd block flag is set, 7 flag bits follow:

| Bits | Mask | Meaning |
|------|------|---------|
| 0-4  | 0x1F | Maximum model order minus 1 (if flag 0x20 set). |
| 5    | 0x20 | Model reset: new dictionary size and model order. |
| 6    | 0x40 | New escape character value. |

If bit 5 (model reset):
- Read 8 bits: dictionary size in MB = `(value + 1)`. Maximum 256 MB.
- Model order = `(flags & 0x1F) + 1`. If order > 16: `order = 16 + (order - 16) * 3`.
- Reinitialize the PPMd context with new dictionary and order.

If bit 6 (new escape):
- Read 8 bits: new PPMd escape character value. Default is 2.

If neither reset flag is set, continue using the existing PPMd context
(re-initialize the range decoder only).

### 19.2 PPMd Data Format

PPMd data uses a range coder. The RAR implementation uses the "PpmdRAR" variant
of the range decoder, which differs slightly from the standard Ppmd7z variant.

Decompressed bytes are read one at a time from the PPMd context. The escape
character signals a special operation: when the decoded byte equals the escape
value, the next byte indicates:

| Value | Meaning |
|-------|---------|
| 0     | End of PPMd block. Return to main block parsing. |
| 2     | Reserved. |
| 3     | RARVM filter follows. |
| 4     | New distance match. |
| 5     | Last-distance repeat match. |
| Other | Literal byte equal to escape value. |

### 19.3 PPMd Algorithm Reference

The PPMd variant H algorithm is fully specified in `PPMD_ALGORITHM_SPECIFICATION.md`.
That document covers the complete context model, symbol encoding/decoding, model
update with information inheritance, SEE (Secondary Escape Estimation), binary
context optimization, rescaling, and both 7z and RAR range coder variants.

RAR uses the PpmdRAR range coder variant (carry-less, with `Low` and `Bottom`
state variables) rather than the 7z variant. The model logic is identical.

---

## 20. RARVM Filters (RAR 3.x)

RAR 3.x introduced a virtual machine (RARVM) for post-processing filters.
The VM executes bytecode programs that transform decompressed data in the
output buffer.

### 20.1 VM Properties

| Property | Value |
|----------|-------|
| Memory size | 256 KB (`0x40000`) |
| Memory mask | `0x3FFFF` |
| Work area size | 240 KB (`0x3C000`) |
| Global data size | 8 KB (`0x2000`) |
| System global offset | `0x3C000` |
| System global size | 64 bytes (`0x40`) |
| User global offset | `0x3C040` |
| Registers | 8 general-purpose 32-bit registers (R0-R7) |

### 20.2 Filter Parsing

When a filter is triggered (symbol 257 in the main decode loop), the filter
program is read from the compressed data:

| Field | Encoding | Description |
|-------|----------|-------------|
| Flags | 1 byte from bitstream | Control flags. |
| Program number | varlen | Which stored program to use (if flag 0x80). |
| Block start | varlen | Offset from current position. |
| Block length | varlen | Size of filtered region (if flag 0x20). |
| Register init | 7 * varlen | Initial register values (if flag 0x10). |
| Bytecode | varlen | Program bytecode (if new program). |
| Global data | varlen | Additional global data (if flag 0x08). |

The variable-length number format (`membr_next_rarvm_number`) reads:
- 2 bits selecting the byte count (0-3, meaning 1-4 bytes)
- The corresponding number of bytes, little-endian

### 20.3 Standard Filter Programs

Rather than executing arbitrary bytecode, all known clean-room implementations
(libarchive, 7-Zip, refinery) recognize standard filter programs by CRC32
fingerprints of their bytecode and use hardcoded native implementations. No
known RAR archive in the wild uses non-standard RARVM programs.

Known filter fingerprints (CRC32 of bytecode):

| CRC32        | Filter   | Block Length | Purpose |
|-------------|----------|-------------|---------|
| `0xAD576887` | E8       | any         | x86 CALL (0xE8) address translation. |
| `0x3CD7E57E` | E8E9     | any         | x86 CALL+JMP (0xE8/0xE9) address translation. |
| `0x3769893F` | DELTA    | any         | Multi-channel delta encoding. Channels in R0. |
| `0x30F8C3E6` | RGB      | any         | RGB image delta (width in R0, posR in R1). |
| `0xD8BC85E1` | AUDIO    | any         | Audio delta (channels in R0). |
| `0x0E06077D` | ITANIUM  | any         | Itanium branch address translation. |

The E8, E8E9, DELTA, and ARM filter algorithms are functionally equivalent to the
fixed filter types in RAR 5.0 (see RAR5 spec Section 12). The RGB, AUDIO, and
ITANIUM filters were dropped in RAR 5.0.

### 20.4 RARVM Bytecode Specification

The full RARVM instruction set (approximately 40 opcodes including MOV, CMP, ADD,
SUB, JZ, JNZ, INC, DEC, JMP, XOR, AND, OR, TEST, JS, JNS, JB, JBE, JA, JAE,
PUSH, POP, CALL, RET, NOT, SHL, SHR, SAR, NEG, PUSHA, POPA, PUSHF, POPF,
MOVZX, MOVSX, XCHG, MUL, DIV, PRINT, MOVB, CMPB, standard conditional jumps)
is deliberately not specified in this document.

The bytecode VM was a security liability (arbitrary code execution in the
decompressor) and was removed in RAR 5.0 in favour of fixed filter types.
All RAR 3.x/4.x archives created by official WinRAR use only the standard
filters listed above. An implementation that handles the six standard
fingerprints will correctly decompress all known archives.

For the rare case of encountering non-standard RARVM bytecode, the libarchive
BSD-licensed implementation (`archive_read_support_format_rar.c`) contains a
complete VM executor.

---

## 21. Multi-Volume Archives

RAR archives can be split across multiple files (volumes).

### Volume Detection

A multi-volume archive is indicated by `MHD_VOLUME` (`0x0001`) in the archive
header. The first volume is identified by `MHD_FIRSTVOLUME` (`0x0100`, RAR 3.0+).

### Volume Naming

Two naming schemes exist:

**Old scheme** (default before RAR 3.x):
- First volume: `archive.rar`
- Subsequent: `archive.r00`, `archive.r01`, ..., `archive.r99`, `archive.s00`, ...

**New scheme** (when `MHD_NEWNUMBERING` / `0x0010` is set):
- `archive.part01.rar`, `archive.part02.rar`, ...

### Split Files

When a file spans multiple volumes:
- The file header in the continuation volume has `FHD_SPLIT_BEFORE` (`0x0001`) set.
- The file header in the preceding volume has `FHD_SPLIT_AFTER` (`0x0002`) set.
- The filename must match across volumes.

---

## 22. CRC32

RAR uses standard CRC32 with polynomial `0xEDB88320` (reversed representation),
the same as zlib/gzip/PNG.

For header CRCs: the CRC32 is computed over the specified range, then truncated
to 16 bits (`crc32_value & 0xFFFF`) and stored as HEAD_CRC.

For file data CRCs: the full 32-bit CRC32 of the uncompressed data is stored in
the FILE_CRC field.

---

## Appendix A: Archive Processing Algorithm

The following pseudocode describes how to iterate through a RAR 1.5-4.x archive:

```
1. Read and verify 7-byte marker block signature.
2. Read archive header (MAIN_HEAD):
   a. Read 7 bytes for the common header.
   b. Read HEAD_SIZE - 7 additional bytes.
   c. Parse flags and reserved fields.
3. Loop:
   a. Read 7 bytes for the next block header.
   b. If end of file: stop.
   c. Based on HEAD_TYPE:
      - FILE_HEAD (0x74):
        Read remaining header (HEAD_SIZE - 7 bytes).
        Parse file fields.
        Skip or read data_size bytes of compressed data.
        (data_size = PACK_SIZE, or HIGH_PACK_SIZE<<32 | PACK_SIZE if FHD_LARGE)
      - NEWSUB_HEAD (0x7A):
        Parse as file header (same structure).
        Skip data area.
      - ENDARC_HEAD (0x7B):
        End of archive. Stop (or open next volume).
      - Other types (0x75-0x79):
        Read HEAD_SIZE - 7 bytes.
        If HEAD_FLAGS & 0x8000: also read ADD_SIZE bytes.
        Skip the block.
   d. Continue loop.
```

## Appendix B: MS-DOS Date/Time Format

The FTIME field uses the standard MS-DOS date/time format packed into 32 bits:

| Bits  | Field   | Range |
|-------|---------|-------|
| 0-4   | Second / 2 | 0-29 (0-58 seconds) |
| 5-10  | Minute  | 0-59 |
| 11-15 | Hour    | 0-23 |
| 16-20 | Day     | 1-31 |
| 21-24 | Month   | 1-12 |
| 25-31 | Year - 1980 | 0-127 (1980-2107) |

Note: seconds have 2-second resolution. The EXT_TIME field (Section 12) provides
an odd-second correction bit and sub-second precision.

## Appendix C: Differences Between RAR Versions Within This Format

While the container format is shared, implementations should be aware of these
version-specific differences:

| Feature | RAR 1.5 (UNP_VER 15) | RAR 2.0 (UNP_VER 20) | RAR 2.9/3.x (UNP_VER 29) | RAR 3.6+ (UNP_VER 36+) |
|---------|----------------------|----------------------|--------------------------|------------------------|
| Max dictionary | 64 KB | 1 MB | 4 MB | 4 MB |
| Compression | LZ77 | LZ77 + audio filter | LZSS + PPMd + RARVM | Same |
| Encryption | CRC-XOR stream (§14.3) | Substitution + block (§14.4) | AES-128 (§14.5) | AES-128 (§14.6, same as 3.x) |
| Unicode names | No | No | Yes (compressed) | Yes (UTF-8) |
| Extended time | No | No | Partial | Full |
| Large files (>2GB) | No | No | Via FHD_LARGE | Yes |
| Recovery volumes | No | No | Yes | Yes |
| RARVM filters | No | No | Yes | Yes |

### Compression Algorithm Dispatch

Implementations must dispatch to the correct decoder based on UNP_VER:

| UNP_VER | Decoder | Spec Section |
|---------|---------|-------------|
| 15      | Unpack15 (adaptive Huffman + LZ77) | §15 (see also RAR13 spec §6) |
| 20      | Unpack20 (Huffman + LZ77 + audio) | §16, §17 |
| 29+     | Unpack29 (LZSS + PPMd + RARVM) | §18, §19, §20 |
