# RAR 1.5-4.x Archive Format Specification

**Independent documentation derived from publicly available sources:**
- `technote.txt` distributed with RAR 3.93 (public domain technical note)
- libarchive `archive_read_support_format_rar.c` (BSD-licensed clean-room implementation by Andres Mejia)
- Kaitai Struct RAR format specification (CC0-1.0)

- `binref/refinery` Python RAR implementation (LGPL-compatible)
- AROS `contrib` repository: `aminet/util/arc/compatible RAR reader/` (old C implementation, public/AROS license)

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

**UNP_VER is a compatibility gate, not a codec selector.** Compatible readers dispatch
LZ decoders on four codec generations only: 15 (Unpack15), 20 (Unpack20),
26 (Unpack26 — Audio-only variant), and 29 (Unpack29). Files with UNP_VER
between two codec generations use the lower codec plus any format features
that require the higher version:

| UNP_VER | LZ codec | Why the bump over the codec's native version |
|---------|----------|-----------------------------------------------|
| 15      | Unpack15 | — |
| 20, 26  | Unpack20 | 26 = same codec, declared by encoders for files > 2 GB so older readers reject correctly (`_refs/unrar/unpack.cpp:172-173`). Audio mode is per-block (bit 15 of the block header), not UnpVer-gated. |
| 29      | Unpack29 | PPMd, RARVM filters, 4 MB dict |
| 36      | Unpack29 | Extended time, >2 GB files, `.partNN.rar` naming |
| 40      | Unpack29 | `MHD_ENCRYPTVER` flag; encryption unchanged from 29 |

**Encoder rule:** pick the lowest UNP_VER that admits the features actually
used. A file compressed with Unpack29 and no 3.6-era features gets
UNP_VER 29; adding a sub-second mtime bumps it to 36; adding the
`MHD_ENCRYPTVER` archive flag bumps the archive's own reference version
to 40. Never emit UNP_VER values above 40 — they are reserved and cause
decoders to reject the file with "unsupported compression method".

There is no "Unpack36" or "Unpack40" codec to implement.

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
| +7     | RESERVED1 | uint16 | Reserved. When `MHD_AV` is set, this field is `HighPosAV` (high 16 bits of the 48-bit byte offset to the AV block; see §10.1). |
| +9     | RESERVED2 | uint32 | Reserved. When `MHD_AV` is set, this field is `PosAV` (low 32 bits of the AV byte offset; see §10.1). |
| +13    | ENCRYPT_VER | uint8 | Encryption version. Only present if `HEAD_FLAGS & 0x0200`. |

HEAD_CRC is computed as: `CRC32(HEAD_TYPE .. RESERVED2) & 0xFFFF`

### Archive Header Flags

| Flag     | Constant        | Meaning |
|----------|----------------|---------|
| `0x0001` | MHD_VOLUME     | Archive is part of a multi-volume set. |
| `0x0002` | MHD_COMMENT    | Archive comment present. Not set by RAR 3.x+, which uses a subblock instead. |
| `0x0004` | MHD_LOCK       | Archive is locked (cannot be modified). |
| `0x0008` | MHD_SOLID      | Solid archive. |
| `0x0010` | MHD_NEWNUMBERING | New volume naming scheme: `name.partNN.rar` instead of `name.rNN`. (Same bit was `MHD_PACK_COMMENT` in RAR 1.4 — see `RAR13_FORMAT_SPECIFICATION.md` §4. Format detection by signature determines which meaning applies.) |
| `0x0020` | MHD_AV         | Authenticity verification present. Not set by RAR 3.x+. |
| `0x0040` | MHD_PROTECT    | Recovery record is present. |
| `0x0080` | MHD_PASSWORD   | Archive headers are encrypted. |
| `0x0100` | MHD_FIRSTVOLUME | First volume of a multi-volume set (RAR 3.0+). |
| `0x0200` | MHD_ENCRYPTVER | ENCRYPT_VER field is present (RAR 4.0+). |

### Encoder emit recipe (single-pass)

Field order and length (in bytes) an encoder writes for `MAIN_HEAD`,
with `HEAD_SIZE` typically 13 (no `ENCRYPT_VER`) or 14 (`ENCRYPT_VER`
present):

```
  [+0]   HEAD_CRC         uint16   placeholder 0x0000 → backpatch after CRC
  [+2]   HEAD_TYPE        uint8    0x73
  [+3]   HEAD_FLAGS       uint16   OR of MHD_* bits set for this archive
  [+5]   HEAD_SIZE        uint16   13 or 14 depending on ENCRYPT_VER
  [+7]   RESERVED1        uint16   0x0000
  [+9]   RESERVED2        uint32   0x00000000
  [+13]  ENCRYPT_VER      uint8    only if HEAD_FLAGS & MHD_ENCRYPTVER

  compute HEAD_CRC = CRC32(bytes [+2 .. HEAD_SIZE-1]) & 0xFFFF
  backpatch HEAD_CRC at offset +0
```

Flags to set based on encoder decisions and derivable content:

- `MHD_VOLUME (0x0001)`: set iff emitting a multi-volume archive.
  Paired with per-volume `END_OF_ARCHIVE`'s `EARC_NEXT_VOLUME` /
  `EARC_VOLNUMBER` flags. Encoder choice, but once committed must be
  consistent across all volumes.
- `MHD_LOCK (0x0004)`: set iff the archive is explicitly marked
  read-only by the encoder operator. Informational for WinRAR's UI;
  readers do not enforce it.
- `MHD_SOLID (0x0008)`: set iff at least two files share LZ state
  (see `ARCHIVE_LEVEL_WRITE_SIDE.md` §1). Derivable from file-header
  solid flags but must also be flagged at the archive level for
  decoders that skip straight to a file.
- `MHD_NEWNUMBERING (0x0010)`: set iff using
  `archive.partNN.rar` naming rather than the legacy `.r00`, `.r01`,
  ... scheme. Encoder choice.
- `MHD_AV (0x0020)`: only set if emitting an Authenticity
  Verification subblock (`HEAD3_AV = 0x76`). Legacy; modern encoders
  leave clear.
- `MHD_PROTECT (0x0040)`: set iff at least one `PROTECT_HEAD`
  recovery block is present in the archive.
- `MHD_PASSWORD (0x0080)`: set iff archive headers are encrypted
  (the whole-archive `-hp` mode). Paired with a salt immediately
  after the marker and before this header (see §14).
- `MHD_FIRSTVOLUME (0x0100)`: set only on volume 1 of a
  multi-volume set. Useful for out-of-order discovery; not
  mechanically required by every reader but conventionally emitted.
- `MHD_ENCRYPTVER (0x0200)`: set only when the encoder wants to
  declare the encryption method version in `ENCRYPT_VER`. RAR 3.x
  does not require this; RAR 4.0+ archives with encryption
  typically set it.

The two `RESERVED*` fields must be zero **unless** `MHD_AV` is set, in
which case `RESERVED1`/`RESERVED2` carry `HighPosAV`/`PosAV` (the 48-bit
byte offset to the `HEAD3_AV` block; see §10.1). Verified against
`_refs/unrar/arcread.cpp` (`MainHead.HighPosAV = Raw.Get2();
MainHead.PosAV = Raw.Get4();`) and `headers.hpp` (`HighPosAV` is
`ushort`, `PosAV` is `uint`). Modern encoders that don't emit AV leave
both fields zero.

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

#### File size encoding rules (encoder)

Three distinct cases an encoder must handle when emitting `PACK_SIZE` /
`UNP_SIZE` / `FHD_LARGE`:

1. **Small file, size known (< 4 GiB, both compressed and uncompressed).**
   Store `PACK_SIZE` and `UNP_SIZE` as normal 32-bit values. Do **not**
   set `FHD_LARGE`. This is the overwhelmingly common case.

2. **Large file, size known (≥ 4 GiB either side).** Set `FHD_LARGE`
   (`0x0100`) in `HEAD_FLAGS`. Split the 64-bit sizes into low/high
   halves:
   ```
   PACK_SIZE       = packed_size & 0xFFFFFFFF
   UNP_SIZE        = unpack_size & 0xFFFFFFFF
   HIGH_PACK_SIZE  = (packed_size >> 32) & 0xFFFFFFFF
   HIGH_UNP_SIZE   = (unpack_size >> 32) & 0xFFFFFFFF
   ```
   `HIGH_PACK_SIZE` and `HIGH_UNP_SIZE` are emitted as u32 fields
   immediately after `ATTR`, before `FILE_NAME`. Both fields are part
   of `HEAD_SIZE` and covered by the header CRC (§8.1 of
   `INTEGRITY_WRITE_SIDE.md`).

   Edge case: a file that happens to be exactly a multiple of 4 GiB
   (e.g., 4 GiB, 8 GiB, 12 GiB) still needs `FHD_LARGE` because the low
   32 bits alone (zero) cannot distinguish "zero-length" from "4 GiB."
   The trigger is `packed_size ≥ 2^32` **or** `unpack_size ≥ 2^32`, not
   both.

3. **Streaming write with unknown uncompressed size.** The encoder is
   compressing stdin, a growing log, or any source whose size is not
   known when the file header must be written. Two sub-cases:

   a. **Unknown size, file guaranteed < 4 GiB.** Set
      `UNP_SIZE = 0xFFFFFFFF` and leave `FHD_LARGE` clear. The decoder
      recognises the sentinel and unpacks
      until it hits the end-of-file marker in the compressed stream.
      `PACK_SIZE` must still be correct — the encoder backpatches it
      after the data is written (the file header is seekable; rewrite
      the 4-byte `PACK_SIZE` field once the compressed length is
      known).

   b. **Unknown size, file may exceed 4 GiB.** Set `FHD_LARGE`, and set
      both `UNP_SIZE = 0xFFFFFFFF` and `HIGH_UNP_SIZE = 0xFFFFFFFF`.
      The decoder treats the `0xFFFFFFFFFFFFFFFF` sentinel as "unknown,
      stream to EOF marker". `PACK_SIZE` /
      `HIGH_PACK_SIZE` are still backpatched after the data is written.

   Setting `PACK_SIZE = 0xFFFFFFFF` without backpatching is **not** a
   valid option — the header-CRC covers those bytes and the decoder
   uses `PACK_SIZE` to locate the next block on disk. Streaming
   encoders must reserve the bytes, write the data, then seek back and
   overwrite the size fields before finalising the header CRC.

**Backpatch order for streaming large-file encode:**

```
1. Write file header with placeholder PACK_SIZE / HIGH_PACK_SIZE = 0.
2. Stream-compress the file data.
3. Seek back to PACK_SIZE offset in the header.
4. Overwrite PACK_SIZE and (if LHD_LARGE) HIGH_PACK_SIZE.
5. Recompute HeadCRC over the (now correct) header bytes.
6. Seek back to HeadCRC offset and overwrite.
7. Seek forward to end of data payload; continue with next block.
```

Step 5 matters: the header CRC covers `PACK_SIZE` / `HIGH_PACK_SIZE`,
so it must be recomputed after backpatching. An encoder that forgets
step 5 emits a valid-looking archive that fails CRC at open time.

**Interaction with LHD_SALT and LHD_EXTTIME.** The `HIGH_PACK_SIZE`
and `HIGH_UNP_SIZE` fields sit **before** `FILE_NAME`, which is
before `SALT` and `EXT_TIME`. When `FHD_LARGE` is set together with
`FHD_SALT` or `FHD_EXTTIME`, the field order is:

```
[base 32 bytes][HIGH_PACK_SIZE][HIGH_UNP_SIZE][FILE_NAME][SALT?][EXT_TIME?]
```

All of these contribute to `HEAD_SIZE` and all are inside the CRC
range. Encoders composing optional fields must emit them in this
exact order — the decoder parses them positionally.

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

The `ATTR` uint32 field at offset +28 is OS-specific. Its interpretation
depends on `HOST_OS`. Encoders must pick the layout matching the host the
archive is being produced on — an encoder that writes Windows attrs with
`HOST_OS=3` (Unix) will produce an archive readers cannot interpret
correctly.

**Windows family (`HOST_OS` = 0 MS-DOS, 1 OS/2, 2 Win32):**

Store the Windows `FILE_ATTRIBUTE_*` bitmask directly in `ATTR`. Only
the low byte is commonly used:

| Bit      | Meaning |
|----------|---------|
| `0x0001` | `FILE_ATTRIBUTE_READONLY` |
| `0x0002` | `FILE_ATTRIBUTE_HIDDEN` |
| `0x0004` | `FILE_ATTRIBUTE_SYSTEM` |
| `0x0010` | `FILE_ATTRIBUTE_DIRECTORY` |
| `0x0020` | `FILE_ATTRIBUTE_ARCHIVE` |

Higher bits (`0x0040` and above — reparse point, sparse, compressed,
encrypted, etc.) are preserved by readers but rarely set by the official
encoder on archive creation. The "archive" bit (`0x0020`) is the
conventional default for a plain file.

**Unix family (`HOST_OS` = 3 Unix, 4 Mac OS, 5 BeOS):**

Store the POSIX `st_mode` value (as returned by `stat(2)`) directly in
`ATTR`. Only the low 16 bits are meaningful:

| Bits      | Meaning |
|-----------|---------|
| `0xF000`  | File-type mask |
| `0x8000`  | `S_IFREG` (regular file) |
| `0x4000`  | `S_IFDIR` (directory) |
| `0xA000`  | `S_IFLNK` (symbolic link) |
| `0x2000`  | `S_IFCHR` (char device) |
| `0x6000`  | `S_IFBLK` (block device) |
| `0x1000`  | `S_IFIFO` (FIFO) |
| `0xC000`  | `S_IFSOCK` (socket) |
| `0x0800`  | `S_ISUID` |
| `0x0400`  | `S_ISGID` |
| `0x0200`  | Sticky bit |
| `0x01FF`  | rwxrwxrwx permission bits |

Typical regular file with mode 0644: `ATTR = 0x81A4`
(`S_IFREG | 0644`). Typical directory with mode 0755: `ATTR = 0x41ED`.

#### Directory signaling

Directories are signaled two different ways depending on archive format
version. An encoder must use the version-appropriate one:

- **RAR 2.0+ (UNP_VER ≥ 20):** Set the window-size field in `HEAD_FLAGS`
  to `0x00E0` (`LHD_DIRECTORY`, i.e. all three window bits set — see
  §4 Dictionary Size). The archive has no compressed data area for a
  directory entry, so `PACK_SIZE = 0`. The `ATTR` field should **also**
  carry the platform-appropriate directory bit (`0x10` for Windows,
  `0x4000` for Unix) for correct attribute restoration, but detection
  is driven by the window-bits field.
- **RAR 1.5 (UNP_VER < 20):** The window-bits-equals-directory
  convention did not exist. Detection falls back to `ATTR & 0x10`.
  Encoders targeting 1.5 must set `ATTR = 0x10`
  for directories on any `HOST_OS`; an encoder targeting 2.0+ does not
  need to emit 1.5-compatible directory entries.

#### Symlink encoding (Unix only)

RAR 3.x encodes Unix symlinks in-band: the link target goes in the
compressed data area, as if it were the file content. A decoder
recognizes a symlink when **both** of these hold:

- `HOST_OS == 3` (`HOST_UNIX`), and
- `(ATTR & 0xF000) == 0xA000` (file-type bits are `S_IFLNK`)

Encoder rules:

1. Set `HOST_OS = 3`.
2. Set `ATTR = 0xA000 | perms` (permissions are usually `0777`, matching
   `lstat(2)` on a typical symlink; readers do not enforce a value).
3. Write the link target as plain bytes into the file's data area.
   `UNP_SIZE` is the byte length of the target string. `METHOD` is
   usually `0x30` (stored), though a compressed symlink is legal.
4. `FILE_CRC` covers the target string bytes, same as any other payload.
5. Do **not** set the directory window-bits field — a symlink is not a
   directory even if its target is one.

Symlinks with `HOST_OS != HOST_UNIX` are not recognized as symlinks by
RAR readers; the bytes will extract as a regular file containing the target
string. An encoder producing Unix symlinks must always use
`HOST_OS = HOST_UNIX`.

Hardlinks, Windows symlinks, and junctions are **not** representable in
the RAR 3.x format. They were added in RAR 5.0 via extra records (see
`RAR5_FORMAT_SPECIFICATION.md` §8, type `0x05`). An encoder that needs
to preserve them must either degrade them (resolve to the target) or
upgrade the archive to RAR 5.0.

#### Extraction fallbacks (decoder behavior, informational)

When a reader extracts a file whose `HSType` (derived from `HOST_OS`) does
not match the extraction host, it substitutes defaults:

- Extracting Windows-attributed file on Unix: directory → `0777 & ~umask`;
  readonly-file → `0444 & ~umask`; other → `0666 & ~umask`. Original
  `ATTR` bits are discarded.
- Extracting Unix-attributed file on Windows: directory → `0x10`;
  other → `0x20`. Unix permission bits are discarded.
- Extracting an archive with `HSType = HSYS_UNKNOWN` (unknown host):
  directory → `0x41FF & ~umask` on Unix, `0x10` on Windows; other →
  `0x81B6 & ~umask` on Unix, `0x20` on Windows.

Encoders do not need to emit anything special to trigger these
fallbacks — they happen whenever the archive and extract host differ.
However, an encoder that wants both Windows and Unix hosts to extract
to sensible attributes must choose one `HOST_OS` and accept that the
other host will fall back.

---

## 7. End of Archive Header

Block type `0x7B` (ENDARC_HEAD). Used by RAR 1.5 through 4.x. The RAR 5.0
end marker uses a different header structure (see `RAR5_FORMAT_SPECIFICATION.md`
§9).

| Offset | Field      | Type   | Description |
|--------|-----------|--------|-------------|
| +0     | HEAD_CRC  | uint16 | CRC. |
| +2     | HEAD_TYPE | uint8  | `0x7B` |
| +3     | HEAD_FLAGS | uint16 | See flag table below. |
| +5     | HEAD_SIZE | uint16 | Header size. |

**End of Archive Flags:**

| Bit      | Name                | Meaning |
|----------|---------------------|---------|
| `0x0001` | `EARC_NEXT_VOLUME`  | This volume is **not** the last in the set. A next volume exists. |
| `0x0002` | `EARC_DATACRC`      | Archive data CRC32 is stored following the header. Used mainly in multi-volume archives to verify cross-volume integrity. |
| `0x0004` | `EARC_REVSPACE`     | 7 bytes of reserved space follow, used by `.rev` recovery volume files to store a trailer record without resizing the archive. |
| `0x0008` | `EARC_VOLNUMBER`    | Current volume number is stored following the header (2 bytes). |
| `0x4000` | `SKIP_IF_UNKNOWN`   | Standard flag — decoders may skip this block if they don't understand the type. |
| `0x8000` | `LONG_BLOCK`        | An `ADD_SIZE` field follows at offset +7, extending the block with additional bytes. |

Field ordering when multiple flags are set: ADD_SIZE (if `LONG_BLOCK`),
then the `ADD_SIZE`
payload in the order: data CRC32 (4 bytes), volume number (2 bytes),
rev-space (7 bytes). Absence of a flag means its bytes are omitted.

If `HEAD_FLAGS & 0x8000`, an ADD_SIZE field follows at offset +7.

RAR stops reading after this header. For multi-volume archives, the presence of
this header signals the end of the current volume — the decoder opens the next
volume iff `EARC_NEXT_VOLUME` is set.

---

## 8. Comment Header

Block type `0x75` (COMM_HEAD). Old-style comment used by RAR 1.5 and 2.x.
Standalone block on disk; positioned immediately after the main archive
header when `MHD_COMMENT` is set on the main, or immediately after a file
header when `LHD_COMMENT` is set on the file. Modern readers
(`_refs/unrar/arccmt.cpp::DoGetComment`) locate the archive comment by
seeking to `SFXSize + SIZEOF_MARKHEAD3 + SIZEOF_MAINHEAD3` and asserting
the next block has `HEAD_TYPE == 0x75`.

RAR 3.x and later store comments as `HEAD3_NEWSUB` subblocks (type
`0x7A`, name `"CMT"`) instead, and do not set the `MHD_COMMENT` flag or
emit this block type.

| Offset | Field      | Type   | Description |
|--------|-----------|--------|-------------|
| +0     | HEAD_CRC  | uint16 | CRC. |
| +2     | HEAD_TYPE | uint8  | `0x75` |
| +3     | HEAD_FLAGS | uint16 | Standard common flags. |
| +5     | HEAD_SIZE | uint16 | Total header size including the compressed comment payload that follows. |
| +7     | UNP_SIZE  | uint16 | Uncompressed comment size. |
| +9     | UNP_VER   | uint8  | Decompressor version required to inflate the comment. Must be in range `[15, VER_UNPACK]`. |
| +10    | METHOD    | uint8  | Compression method (`0x30` for stored). Must be `≤ 0x35`. |
| +11    | COMM_CRC  | uint16 | Low 16 bits of CRC32 over the uncompressed comment text. |

(Total `SIZEOF_COMMHEAD = 13` bytes.) The compressed comment payload
follows at offset `+13`, occupying `HEAD_SIZE - 13` bytes. Decoders
inflate it with the codec selected by `UNP_VER` / `METHOD` and verify
`CRC32(unpacked_text) & 0xFFFF == COMM_CRC`.

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

RAR 2.x supported authenticity verification (AV) — a per-archive RSA
signature identifying the creator's registered WinRAR licence, verified
against a public key baked into WinRAR. Deprecated in RAR 3.x and
removed from the UI in RAR 3.60+; no new archives produce AV headers.
Present mostly in shareware-era archives from 1996–2002.

### 10.1 Signal path

Three on-disk signals:

| Source | Field | Semantics |
|--------|-------|-----------|
| Main archive header | `MHD_AV` flag (`0x0020`) | AV data is present somewhere in the archive. |
| Main archive header | `HighPosAV` (uint16) + `PosAV` (uint32) | 48-bit byte offset from archive start to the AV block. `Signed = (PosAV != 0 || HighPosAV != 0)`. |
| Block stream | Block type `0x76` (`HEAD3_AV`) or `0x79` (`HEAD3_SIGN`) | The AV / signature block itself. |

A reader that only needs the flag ("is this archive signed?") can
consult `MHD_AV` or the non-zero `PosAV` pair from the main header
without walking to the block. A reader that needs to *verify* must
locate the AV block — either via the `PosAV` shortcut or by scanning
for block type `0x76` / `0x79`.

### 10.2 Block-header quirk: CRC is not validated

`HEAD3_AV` and `HEAD3_SIGN` blocks are **exempt** from the generic
block-CRC check. WinRAR's original encoder wrote these blocks without
populating the `HEAD_CRC` field correctly (the AV data itself was the
integrity check), so readers must not reject an AV block on CRC
mismatch. All other standard block types must still pass.

An encoder that chooses to emit AV blocks should set `HEAD_CRC` to any
value (typically 0 or the correct CRC) — real readers ignore it.

### 10.3 Block-header layout (confirmable)

Both `HEAD3_AV` and `HEAD3_SIGN` use the standard RAR 1.5–4.x block
header (§3 "General Block Format"):

| Offset | Field     | Type   | Notes |
|--------|-----------|--------|-------|
| +0     | HEAD_CRC  | uint16 | Not validated (see §10.2). |
| +2     | HEAD_TYPE | uint8  | `0x76` (AV) or `0x79` (SIGN). |
| +3     | HEAD_FLAGS | uint16 | Standard flags; no AV-specific bits publicly documented. |
| +5     | HEAD_SIZE | uint16 | Total block size in bytes including the 7-byte header. |
| +7     | body      | bytes  | AV payload — layout not publicly documented (see §10.5). |

### 10.4 Body framing

The `HEAD3_AV` body is opaque on disk: a 14-byte AV-specific sub-header
followed by an encrypted payload. The format byte at body `+1` selects
which §10.8 cipher branch the verifier applies before decoding:

| Body offset | Field                | Notes |
| ---         | ---                  | ---   |
| `+0`        | AV version byte      | Reader requires `0x0F..0x14`. |
| `+1`        | AV format byte       | ASCII. `'0'` selects the legacy byte-stream cipher path (§10.8); `'4'` (and likely other non-`'0'`) selects the modern path: pre-decrypt the encoded payload with the §10.8 16-byte block cipher, then decode the result via the §17 audio codec (see §10.6). |
| `+2`        | Auxiliary version byte | Reader requires `<= 0x14`; selects digest setup parameters. |
| `+3..+6`    | CRC32 of the *decoded payload* | Standard CRC32 (zlib/Ethernet convention, polynomial `0xEDB88320`, init `0xFFFFFFFF`, final XOR `0xFFFFFFFF`), little-endian. Verified on the rar250_sfx.exe fixture: bytes `df 90 ba 7f` → `0x7fba90df` == `zlib.crc32(decoded_305_bytes)`. The disassembly tracks the pre-finalize LFSR state and applies a `~` before comparing — that `~` *is* the standard CRC32 finalization XOR, so the on-disk value matches a plain `zlib.crc32` over the decoded bytes (not a separate post-CRC32 inversion). |
| `+7..end`   | Encoded payload      | `HEAD_SIZE - 14` bytes. For modern `'4'` mode this is the *encrypted* form; the verifier decrypts it via `av_block_decrypt_or_inverse` (§10.8) per 16-byte block before feeding the plaintext to the §17 audio codec. The codec produces a 305-byte buffer (output length `0x131` set at `0x1333:284f` in unpacked RAR 2.50 `UNRAR.EXE`). |

The RAR 2.50 verifier resets four 16-bit cipher/state words to the
constants `0x4765`, `0x9021`, `0x7382`, `0x5215` before invoking the
cipher, and seeds the internal substitution-table cipher from the
built-in key string `awbw` (data-segment offset `0x0c63` in the
unpacked `UNRAR.EXE` — see §10.8 for the full cipher init). Verified
empirically against `_refs/rarbins/rar250.exe` and the unpacked
`UNRAR.EXE` decompile (see `research/re/rar250/notes.md`).

### 10.5 Decoded payload layout

The 305-byte decoded payload (captured via a binary patch dumping the
post-decode buffer for the rar250_sfx.exe fixture; see
`research/re/rar250/scripts/patch_dump_av.py`) has the following
field layout:

| Offset       | Size | Field                                                                  | Fixture value |
| ---          | ---  | ---                                                                    | --- |
| `0x00..0x19` | 26   | Timestamp string (ASCIIZ, padded with NULs to slot end)                | `"22:47:00  24 Mar 1999"` |
| `0x1A`       | 1    | Verb selector byte                                                     | `0x01` (`"modified"`; `0x00` is presumed `"created"` based on RAR 3.00's "Old style authenticity" verb when reading the same fixture) |
| `0x1B..0x1E` | 4    | Inverted CRC32 (little-endian) of archive bytes `[0..AV_block_offset)` | `0xD6C83867` (verified: `~CRC32(rar250_sfx.exe[0:269392]) == 0xD6C83867`) |
| `0x1F..0x40` | 34   | Reserved / padding (zero in fixture)                                   | |
| `0x41..0x90` | 80   | Archive name (ASCIIZ in fixed-width slot)                              | `"RAR250.EXE"` |
| `0x91..0x130`| 160  | Creator string (ASCIIZ in fixed-width slot)                            | `"Eugene Roshal"` |

Total: 305 bytes (`0x131`), matching the verifier's configured output length.

Slot widths (timestamp 26, name 80, creator 160) are inferred from
field positions in the single observed fixture. A second fixture with
longer strings would pin the maximums precisely. The inverted-CRC32
field at `+0x1B` is fully verified — the bytes match
`~CRC32(archive[0..AV_header])` exactly. (Note: this *is* a true bitwise
inversion of standard CRC32, distinct from the body `+3..+6` field
above which is plain standard CRC32.)

### 10.6 Decoder structure

The encoded body (80 → 305 bytes for the rar250_sfx.exe fixture, modern
format byte `'4'`) is decoded in two stages:

1. **Pre-decryption** (newly identified, 2026-04-28). The 80 wire
   bytes are passed through `av_block_decrypt_or_inverse` (the §10.8
   block cipher) before reaching the audio codec. The decryption
   happens inside the verifier's I/O layer (`FUN_1333_2a4b` in
   RAR 2.50 `UNRAR.EXE`), which conditionally applies the cipher
   based on the AV-mode flag `DAT_1b5a_1788`:
    - `DAT_1b5a_1788 == 0` → no transform (non-AV reads).
    - `0 < DAT_1b5a_1788 < 0x14` → byte-stream cipher
      (`av_stream_transform_dispatch`, used by legacy `'0'` AV).
    - `DAT_1b5a_1788 >= 0x14` → 16-byte block cipher
      (`av_block_decrypt_or_inverse`, used by **modern `'4'` AV**;
      verifier sets `DAT_1b5a_1788 = 0x14` for `'4'` mode entry).

   Two independent pieces of evidence support this:
   - **Static**: `FUN_1333_2a4b`'s decompile shows the conditional
     cipher dispatch directly above. The cipher type and gating
     flag (`DAT_1b5a_1788`) are read from the function body.
   - **Dynamic**: a binary-patched UNRAR.EXE
     (`research/re/rar250/scripts/patch_dump_av_wire.py`) dumps
     the buffer at the `[DAT_1b5a_38f1:DAT_1b5a_38ef]`
     far-pointer target at the verifier's post-codec hook point.
     The captured bytes do **not** appear in the on-disk archive
     — proving the codec sees something other than the wire bytes
     verbatim. The captured buffer is post-codec and state-mutated,
     so it does not constitute a clean "post-decrypt, pre-decode"
     snapshot; pairing it byte-for-byte with `decrypt(wire)` needs
     a re-hook at the fill boundary (inside/after
     `FUN_17eb_08dd`'s call to `FUN_1333_2a4b`, before any decode
     consumption) plus a Python port of the §10.8 block decrypt.

2. **Audio decode**. The decrypted bytes feed the **RAR 2.x
   multimedia audio codec** (`§17`) configured to enter audio mode
   unconditionally with two channels (described below).

The cipher state initialisation, round keys, S-box build, and
seed string `"awbw"` are all documented in §10.8 — the same cipher
cluster is shared by the legacy `'0'` AV (which uses it on the
encoder side as a stream cipher over the AV body) and the modern
`'4'` AV (which uses its block-mode form on the *decoder* side as
a wire-byte pre-decrypt).

#### Inner primitive: same state machine as §17

Disassembly of the inner per-byte routine shows the exact state machine
of the §17 audio predictor: a 5-tap weighted-sum predictor with
adaptive weight selection driven by 11 squared-difference accumulators.
The binary's per-channel state struct appears at these offsets, matching
the §17 state machine:

| Struct offset | §17 field | Role |
| --- | --- | --- |
| `+0`  | `K1` | weight on `D1` |
| `+2`  | `K2` | weight on `D2` |
| `+4`  | `K3` | weight on `D3` |
| `+6`  | `K4` | weight on `D4` |
| `+8`  | `K5` | weight on `UnpChannelDelta` |
| `+a`  | `D1` | last decoded delta |
| `+c`  | `D2` | second-last delta |
| `+e`  | `D3` | third-last delta |
| `+10` | `D4` | fourth-last delta |
| `+14..+28` | `Dif[0..10]` | 11 absolute-difference accumulators |
| `+2a` | `ByteCount` | decoded-byte counter (mod 32 triggers weight re-select) |
| `+2c` | `LastChar` | last decoded sample |

#### How it enters audio mode

The modern AV path calls a dispatcher equivalent to "decode this many
bytes of audio output from this many bytes of bit-coded input", with
two differences from a normal audio-block invocation:

1. **`UnpChannels = 2` is forced**, not read from the standard
   bit-15-of-peek audio-block header. The verifier sets the channel
   count explicitly before the call.
2. **Output length is fixed at 305 bytes** (not derived from a
   per-block end-symbol; the loop exits when the output counter
   reaches zero).

Otherwise the bit stream is the standard §17 framing: an initial
section of code-length-encoded canonical Huffman tables (one `MD[]`
table per channel), followed by a stream of Huffman-coded delta
symbols that feed the audio predictor.

#### Verification

The codec's runtime Huffman table region (256 bytes from the
data-segment offset corresponding to the table-build output) was
captured via a binary patch and contains, in its symbol-to-byte
column, exactly the literal alphabet of the AV plaintext: `'0'`,
`'2'`, `'E'`, `'R'`, `'4'`, `':'`, `'a'`, `'e'`, `'g'` — every
non-zero byte that appears in the timestamp, archive-name, and
creator-string slots. This is consistent with a tree built
per-archive from the wire stream's code-length section, not a static
table.

#### Items still unrecovered

- The bit-level arithmetic of `HEAD3_SIGN`'s signature-shaped
  transform. The block's wire layout, the GF(2^15)-based scheme,
  and the embedded constants are now documented in §10.9.1; the
  inner-loop operations of `FUN_00405a54` (and the bignum helpers
  beneath it) have not been traced step-by-step. Earlier guesses
  that this might be RSA-512 with public-key material in
  `RAR.EXE` were **wrong** — see §10.9.1 for the actual scheme
  shape (proprietary GF(2^15), 256-bit / 272-bit length-prefixed
  bignum constants, retry-loop signature output).
- A round-trip test fixture for the legacy `'0'` AV format (see §10.8).
  RAR 1.40 — the first version with `-av` — gates the signing path
  behind a registration check, so an unregistered run of the original
  encoder cannot generate one.
- A `HEAD3_SIGN`-bearing fixture. WinRAR 2.90 only emits the block
  in registered builds; same blocker shape as the legacy `'0'`
  case (§10.8).

### 10.8 Legacy `'0'` AV format

Archives produced by RAR 1.40 through ~2.0x carry AV with format byte
`'0'` (in the archive header byte at the position where `'4'`
indicates the modern format). The body codec is **not** the §17 audio
codec — it is a smaller bespoke cipher cluster, recovered from
disassembling RAR 2.50's `UNRAR.EXE` legacy-AV path
(`research/re/rar250/symbols.tsv` cluster:
`init_av_cipher_from_string`, `mix_av_key_string`,
`av_block_encrypt_or_forward`, `av_block_decrypt_or_inverse`,
`av_stream_transform_dispatch`, `av_stream_add_transform`,
`av_stream_sub_transform`, `av_stream_xor_transform`).

The legacy path branches on a one-byte flag in the AV header
(`av_flags_or_method` in our notation):

- `flags == 0` → **byte-stream cipher** over the entire body.
- `flags != 0` → **16-byte-block cipher**, applied independently to
  each 16-byte block of the body. The verifier processes exactly
  `0x13` (19) blocks = 304 bytes.

#### Cipher state setup (both paths)

`init_av_cipher_from_string(seed)` performs four steps:

1. **`mix_av_key_string(seed)`** seeds a small stream-cipher state from
   the seed string `s = "awbw"` (for the verifier's standard call;
   encoder may use a different per-archive string). The lookup tables
   `crc_lo[b]` / `crc_hi[b]` are the standard CRC32 (poly
   `0xedb88320`) precomputed table, low and high halves of each
   32-bit entry respectively (the same table the rest of the binary
   uses for `update_crc32_dispatch` — built once at startup by
   `build_crc32_tables`). The mix is:

   ```text
   state.crc_lo, state.crc_hi   = CRC32(s)        # 32-bit (vestigial)
   state.acc_xor   = 0          # 8-bit
   state.acc_rot   = 0          # 8-bit
   state.acc_sum   = 0          # 8-bit
   state.acc_xor16 = 0          # 16-bit
   state.acc_add16 = 0          # 16-bit
   for b in s:
       state.acc_sum   = (state.acc_sum + b) & 0xFF
       state.acc_xor   = state.acc_xor ^ b
       state.acc_rot   = ROL8(state.acc_rot + b, 1)
       state.acc_xor16 = state.acc_xor16 ^ b ^ crc_lo[b]
       state.acc_add16 = state.acc_add16 + b + crc_hi[b]
   ```

2. **Reset the CRC32 words and the two 16-bit accumulators** to
   four hard-coded constants:

   ```text
   state.crc_lo   = 0x4765
   state.crc_hi   = 0x9021
   state.acc_xor16 = 0x7382
   state.acc_add16 = 0x5215
   ```

   Only `acc_sum`, `acc_xor`, `acc_rot` survive from the seed (the
   stream cipher uses `acc_rot` as its base); the 16-bit state used by
   the XOR transform is freshly seeded from the constants.

3. **Round-key constants for the block cipher** — the 8 round-key
   words at `state.k[0..7]` are then **overwritten** with hard-coded
   values, independent of the seed:

   | Index | Hex |
   | ----: | :-- |
   | `k[0]` | `0xb879` |
   | `k[1]` | `0xd3a3` |
   | `k[2]` | `0x12f7` |
   | `k[3]` | `0x3f6d` |
   | `k[4]` | `0xa235` |
   | `k[5]` | `0x7515` |
   | `k[6]` | `0xf123` |
   | `k[7]` | `0xa4e7` |

4. **Build the runtime S-box.** Copy 256 bytes from a static table
   embedded in the binary (in RAR 2.50: 256-byte block at offset
   `0x1138` within the data segment) to a 256-byte working buffer,
   then perform a per-byte position-dependent shuffle controlled by
   `crc_lo` / `crc_hi` lookups, and finally pass the buffer through
   the block cipher's forward direction in 16-byte blocks. The result
   is the live S-box used by both the stream cipher (in the
   `xor_transform` mode) and as a per-byte lookup inside the block
   cipher's round function.

#### Byte-stream cipher (flags `== 0`)

Three transforms share the same state but differ in which output byte
they produce:

- **`av_stream_add_transform`** (encoder direction):
  ```text
  for b in plaintext:
      acc_xor = (acc_xor + acc_rot) & 0xFF
      acc_sum = (acc_sum + acc_xor) & 0xFF
      out_byte = (b + acc_sum) & 0xFF
  ```
- **`av_stream_sub_transform`** (decoder direction): same state
  evolution, output is `(b - acc_sum) & 0xFF`.
- **`av_stream_xor_transform`** (symmetric): walks
  `acc_xor16`/`acc_add16`/CRC words through a stir routine and emits
  `out = b ^ HI8(crc_lo)`. Used only on certain header subfields, not
  on the body.

A reader of a legacy `'0'` AV body with `flags == 0` runs
`av_stream_sub_transform` over the entire body, length taken from
`HEAD_SIZE − 14` (the 14-byte AV-record header is unencrypted).

#### 16-byte-block cipher (flags `!= 0`)

`av_block_decrypt_or_inverse(block, ks)` is a 32-round Feistel-like
network on a 128-bit (eight 16-bit words) state, using the eight
hard-coded round keys above plus byte-level lookups into the runtime
S-box. After each block it applies a key-mixing step that XORs the
just-recovered plaintext bytes into the round keys (via `sbox_lo`
/`sbox_hi`), so the cipher is keyed both by the static round-key
constants *and* by the plaintext history of the same body.

The verifier loop is:

```text
for i in range(0, body_len, 16):
    av_block_decrypt_or_inverse(body[i:i+16], round_keys)
```

For RAR 1.40 archives observed in the disassembly the verifier
unconditionally processes 19 blocks (304 bytes). The encoder's
`av_block_encrypt_or_forward` is the inverse direction with the same
key-mixing step.

#### Integrity check

After decoding, the verifier computes a plain CRC32 of the recovered
body (still using the standard zlib convention) and compares against
two 16-bit fields stored in the AV header:
`av_expected_crc_low` and `av_expected_crc_high`. The check is:

```text
crc = zlib.crc32(decoded_body)
ok  = (crc & 0xFFFF) == av_expected_crc_low and \
      (crc >> 16)    == av_expected_crc_high
```

This is the same plain-CRC32 finalization used by the modern `'4'`
body field at `+3..+6`, just split across two 16-bit AV-header fields
instead of one 32-bit body field.

#### Status of this section

The cipher state, round keys, S-box-build steps, and the body-length
behaviour above all come from disassembly of `UNRAR.EXE` 2.50's
legacy-AV path — i.e., the *consumer* side. **The block cipher
branch is now empirically validated end-to-end**: the
clean-room Python port at
`research/re/rar250/scripts/decode_av_legacy.py` produces output
that matches the binary's runtime decryption byte-for-byte (80/80
bytes) for the modern `'4'` AV body in `rar250_sfx.exe`, with
ground truth captured under DOSBox-X at the buffer-fill boundary
(`research/re/rar250/scripts/patch_dump_full.py` →
`bin/unpacked/WIRE3.B`) and the post-init round-key state captured
at the init epilogue (`patch_dump_init_keys.py` →
`bin/unpacked/KEYSI.B`). Round-trip verification on the legacy `'0'`
byte-stream branch is still pending an encoder-produced fixture
(RAR 1.40 gates signing on a registration check; the in-tree
`fixtures/1.402/rar140_av/` shape fixtures use a BSS-zero
registration buffer and are not byte-verifiable against a real
signature).

### 10.7 Reader / encoder guidance

A conforming reader for modern use should:

1. Detect the presence of AV (via `MHD_AV` or non-zero `PosAV`).
2. Report "this archive has an AV block — content not verified" to the
   user.
3. Skip the AV block using its `HEAD_SIZE` and continue normal
   processing.

A reader that wants to surface the metadata strings needs both stages
of the §10.6 pipeline:

1. **Pre-decrypt** the encoded payload (body `+7..end`) with the
   appropriate §10.8 cipher branch — the format byte at body `+1`
   selects which branch:
    - `'4'` (modern): 16-byte-block decrypt
      (`av_block_decrypt_or_inverse`), processing one 16-byte block
      at a time.
    - `'0'` (legacy): byte-stream cipher
      (`av_stream_sub_transform`, the verifier-side inverse of the
      encoder's `add` direction).

   Cipher init for both branches: §10.8 with seed string `"awbw"`
   and the four hard-coded reset constants for the stream-state
   words; the block branch additionally uses the eight round keys
   and runtime S-box also documented there.

2. **Audio decode** the decrypted bytes with the §17 codec,
   `UnpChannels = 2` forced and a fixed output length of 305 bytes
   (see §10.6). Once the 305-byte plaintext is recovered, read the
   field layout in §10.5. (For legacy `'0'` mode, only the cipher
   inverse is applied — the §17 audio decode does not run; the
   cipher output is the metadata payload directly.)

The integrity checks are:

- **Body `+3..+6`**: plain standard CRC32 of the 305-byte decoded payload
  (matches `zlib.crc32(decoded)` directly).
- **Payload `+0x1B..+0x1E`**: bitwise-inverted standard CRC32 of
  `archive[0..AV_header_offset)` (matches `~zlib.crc32(prefix) & 0xFFFFFFFF`).

Two different conventions in the same block — readers must not assume
both fields use the same finalization.

An encoder targeting modern interop should **not** produce AV blocks —
modern public readers do not validate them, and WinRAR stopped emitting
them nearly two decades ago.

The `-av` switch is advertised by RAR 2.50, RAR 3.00, and RAR 4.20 help
as "registered versions only". An empirical RAR 3.00 shareware run with
`-av` created a normal archive with main flags `0x0000` and no
`HEAD3_AV`/`HEAD3_SIGN` blocks, so the currently available unregistered
binaries cannot generate new AV fixtures.

### 10.9 Block type `0x79` (`HEAD3_SIGN`) — second-generation AV

`HEAD3_SIGN = 0x79` is a parallel AV block alongside
`HEAD3_AV = 0x76`. Tracing the WinRAR `TechNote.txt` block-type
table across versions:

| Source | What it says about `0x79` |
|--------|---------------------------|
| RAR 2.50 `TECHNOTE.TXT` (1999) | Not mentioned — block-type table only goes up to `0x78`. |
| WinRAR 2.90 `TechNote.txt` | `HEAD_TYPE=0x76 = "old style authenticity information"`, **`HEAD_TYPE=0x79 = "authenticity information"`** — i.e. `0x79` is introduced here as the *new* AV block, `0x76` is its now-legacy predecessor. |
| WinRAR 3.93 `TechNote.txt` | Both `0x76` and `0x79` listed as "old style authenticity information" — `0x79` itself is now legacy. |
| WinRAR 4.20 `TechNote.txt` | Same: both `0x76` and `0x79` "old style". |
| WinRAR 5.x+ | Legacy block-type table dropped from the docs entirely. |
| In-tree fixtures | None. |

So `0x79` first appears in the WinRAR 2.90 TechNote as the newer
AV block and is already labelled legacy by 3.93. It never reached
wide deployment; current public readers treat it identically to a
generic unknown block (read header, skip body, no validation) —
verifiable by feeding any synthetic `0x79`-bearing archive to
recent unrar / WinRAR builds.

**Practical consequence for readers:** Treat `HEAD3_SIGN` exactly
the same as `HEAD3_AV`: detect via `MHD_AV` flag or block-type
scan, report "archive has an AV block — content not verified",
skip past using `HEAD_SIZE`. **Do not enforce `HEAD_CRC`** — patched
2.90 (see §10.9.1.1) shows the field *is* computed over the
13-byte fixed prefix, so it has a well-defined value, but readers
historically ignore it on these block types and a strict check
would reject archives written by readers/encoders that don't
maintain the same CRC convention. The same exemption that applies
to `HEAD3_AV` (§10.2) applies here.

#### 10.9.1 Body wire layout (WinRAR 2.90 encoder, reverse-engineered)

Recovered from `Rar.exe` 2.90 (`research/re/wrar290/`, function
`FUN_004286a8` at VA `0x004286a8`). The block extends the standard
7-byte block header with a 4-byte file-time field and two 16-bit
length fields, then a body containing two registration name strings
followed by three hex-decoded hash payloads with 1-byte length
prefixes, zero-padded to a fixed total.

```
HEAD3_SIGN block (HEAD_TYPE = 0x79, HEAD_FLAGS = 0x4000):

+0x00   uint16 LE   HEAD_CRC      (not validated — see §10.2)
+0x02   uint8       HEAD_TYPE     = 0x79
+0x03   uint16 LE   HEAD_FLAGS    = 0x4000   (old-version-delete flag, same as HEAD3_AV — old RAR versions delete this block on archive update)
+0x05   uint16 LE   HEAD_SIZE     = 15 + name1_size + name2_size + 0xa7
+0x07   uint32 LE   ARCFILE_DTIME archive file's DOS date/time, encoded
                                  via FileTimeToDosDateTime — i.e. the
                                  on-disk modification time of the
                                  archive *file*, not the original
                                  source files.
+0x0b   uint16 LE   NAME1_SIZE    length of NAME1 (the archive name)
+0x0d   uint16 LE   NAME2_SIZE    length of NAME2 (the registered creator string)

  ; body begins here (HEAD_SIZE - 15 bytes total):
+0x0f   bytes       NAME1[NAME1_SIZE]   archive name being signed (e.g. `"test.rar"`)
+ ...   bytes       NAME2[NAME2_SIZE]   registered creator string (empty in
                                        BSS-zero-registration runs; expected
                                        to carry the registered-user identity
                                        in real registered builds)

+ ...   uint8       HASH1_LEN     length of hash 1 in bytes
+ ...   bytes       HASH1[HASH1_LEN]    hex-decoded hash payload 1
+ ...   uint8       HASH2_LEN
+ ...   bytes       HASH2[HASH2_LEN]
+ ...   uint8       HASH3_LEN
+ ...   bytes       HASH3[HASH3_LEN]

+ ...   bytes       PADDING       zero bytes; emitted only when
                                  positive. Total padding length =
                                  0xa4 - (HASH1_LEN + HASH2_LEN +
                                  HASH3_LEN). The fixed sum
                                  3 + 0xa4 = 0xa7 makes the body
                                  length deterministic from name
                                  lengths only.
```

`NAME1` is set per-archive at write time from the archive's own
filename (verified empirically — a patched 2.90 invoked as
`Rar.exe a -av test.rar testinput.txt` writes `NAME1 = "test.rar"`).
`NAME2`, the three `HASH` blobs, and the gating flags all live in
BSS and are populated at runtime from registration data — i.e.
WinRAR 2.90 only emits a `HEAD3_SIGN` block when running as a
registered build with a valid `rarreg.key` (or equivalent
registry/file source). An unregistered build skips the writer
entirely (the function's leading guard tests
`DAT_00436794 != 0 && DAT_00436e7c != '\0' && *(char *)(param_1 + 0x2574) == '\0'`
— the third condition is "the archive-context flag at offset
`+0x2574` is clear"; the first two are BSS-initialised at startup
from registration data).

At runtime each of the three `HASH*` blobs is represented as a
null-terminated ASCII hex string and hex-decoded byte-by-byte
into the body via `FUN_004193df` (a hex-string-to-binary helper:
each output byte is `(nibble(src[2i]) << 4) | nibble(src[2i+1])`,
so the on-disk length is `strlen(hex_string) / 2`). Of the three:
`HASH1`'s pre-encoding bytes are **computed at write time** — the
writer calls `FUN_004071cc` (a thin wrapper over `FUN_004070a0`)
on the registration buffer at `DAT_00436b78` and the result is the
hex string that gets decoded. `HASH2` and `HASH3` are hex-decoded
directly from static/runtime buffers `DAT_00436a78` and
`DAT_00436c78` (populated from the registration source at program
start, not recomputed per archive).

`FUN_004070a0`'s structure identifies the scheme as a
**signature-shaped authentication transform** — a homegrown
construction that follows the surface shape of a DSA/Schnorr-style
signature but is not any published primitive. Calling it an
"asymmetric signature" outright would overstate the evidence: the
encoder side is identified, but no separate public-verification
path or distinct public key has been traced, so we don't yet know
whether it's truly asymmetric or a keyed-hash construction with
signing-shaped boilerplate. The decompiled shape is:

- Operates over the binary finite field **GF(2^15)** with primitive
  polynomial `0x8003` (= x^15 + x + 1). The log and exp tables for
  this field (each 0x8000 ushorts = 64 KB) are built lazily at
  first call by `FUN_00406060`, stored in BSS at `DAT_004304a4`
  (exp) and `DAT_004304a8` (log).
- Two embedded constants drive the arithmetic. **Both are stored
  in the binary as length-prefixed bignums** — the first 16-bit
  word is the limb count, then that many 16-bit limbs follow
  (little-endian, low limb first). The limb-count word is *not*
  itself part of the bignum value:
  - `DAT_004304d6`: 17-limb bignum (272-bit), prefix `0x0011`,
    limbs `0x38cc 0x052f 0x2510 0x45aa 0x1b89 0x4468 0x4882 0x0d67
    0x4feb 0x55ce 0x0025 0x4cb7 0x0cc2 0x59dc 0x289e 0x65e3 0x56fd`
    — likely the field-element seed or generator polynomial.
  - `DAT_004304b0`: 16-limb bignum (256-bit), prefix `0x0010`,
    limbs `0xcd31 0x42bb 0x2584 0x5e0d 0x2d8b 0x4bf7 0x840e 0x0547
    0xbec3 0xed9b 0x691c 0x2314 0x81b8 0xd850 0x026d 0x0001` —
    likely the modulus. The leading low limb is `0xcd31`; the
    high limb `0x0001` makes the modulus value just over 2^240.
- Inner-loop body (`FUN_00405a54`, called repeatedly) does
  polynomial-style modular operations until a per-iteration
  candidate value `local_114[0] != 0` — the retry-on-zero pattern
  typical of DSA/Schnorr-style signing where one keeps generating
  ephemeral values until a valid signature component falls out.
- Inputs to the loop: the registration buffer `param_5` and
  optionally the archive context `param_3` (when nonzero, takes
  the alternate path through `FUN_00406eec` / `FUN_00406da0` which
  reads archive bytes through the `param_1[6]` file handle).
- Output: a textual concatenation written into `param_4` —
  formatted prefix (`%02.2d`-style 2-digit decimal version, likely
  `"02"` since the format string is fixed) followed by hex of two
  big-integer signature components (`local_214` from `local_ee`,
  then `local_114`). Each big integer is 15 ushort limbs encoded
  as 60 hex chars (4 hex per limb) by `FUN_0040703c`. Total
  pre-decode hex string is roughly 2 + 60 + 60 = ~122 ASCII chars,
  hex-decoded to ~61 bytes (the actual byte count is what
  `HASH1_LEN` reports on disk).

The transform is unambiguously **homegrown / proprietary** —
GF(2^15) is an unusual field choice, the 256-bit-or-so modulus is
small by modern standards, and the scheme matches no published
primitive. Pinning the exact algorithm down to the bit level
(including whether it is truly asymmetric versus a keyed-hash with
signature-shaped boilerplate) would need a deeper read of the
`FUN_00405a54` / `FUN_00405f54` / `FUN_00406[067-9]xx` call tree
and a registered-build dynamic capture for ground truth. That is a
tractable continuation but well beyond what a clean-room reader
needs — readers don't validate the signature (§10.2), and a
clean-room encoder would also need valid registration material
matching the original signed identity (or a patched test build).

The CRC field at `+0x00` of the block header is computed by
`FUN_00419440` as the standard RAR 1.5–4.x block-CRC scope —
`zlib.crc32(bytes [HEAD_TYPE..NAME2_SIZE+1]) & 0xFFFF` (i.e. the
13-byte run from the byte after `HEAD_CRC` through the end of
`NAME2_SIZE`). The CRC does **not** cover the body bytes (which
are written by separate `FUN_00402870` calls after the header has
already been emitted). Empirically confirmed on a patched 2.90
fixture (`HEAD_CRC = 0x160e` matched
`zlib.crc32(13_byte_prefix) & 0xFFFF` exactly). The CRC is
therefore present and meaningful at write time but not enforced
at read time per §10.2 — readers exempt `HEAD3_SIGN` (and
`HEAD3_AV`) from the generic header-CRC check.

#### 10.9.1.1 Empirical confirmation (2026-04-28)

Running `bin/Rar.regpatched.exe a -av test.rar testinput.txt` (the
WinRAR 2.90 binary with the registration patch from
`research/re/wrar290/scripts/patch_force_registered.py`) under
wine produced a `HEAD3_SIGN` block at archive offset `+0x56`.
Every field in §10.9.1 matched the on-disk bytes, every body-math
prediction held, and the new HASH1-length prediction was exact:

| Field | Spec prediction | On-disk value | Match |
| --- | --- | --- | --- |
| `HEAD_TYPE` | `0x79` | `0x79` | ✓ |
| `HEAD_FLAGS` | `0x4000` | `0x4000` | ✓ |
| `HEAD_SIZE` | `15 + n1 + n2 + 0xa7` | `0x00be = 190 = 15 + 8 + 0 + 0xa7` | ✓ |
| `ARCFILE_DTIME` | DOS date/time of archive file | `0x5c9c2ae9` decodes to 2026-04-28 (matches archive mtime) | ✓ |
| `NAME1` | archive name | `"test.rar"` | ✓ |
| `NAME2` | registered creator string | empty (BSS-zero in this run) | as expected |
| `HASH1_LEN` | ≈ 61 bytes (proprietary signature transform output) | `61` | ✓ |
| `HASH2_LEN` / `HASH3_LEN` | hex-decoded BSS-zero buffer = 0 bytes | both `0` | ✓ |
| `PADDING` | `0xa4 - HASH1_LEN - HASH2_LEN - HASH3_LEN` zero bytes | 103 zeros | ✓ |
| `HEAD_CRC` (13-byte prefix scope) | matches `zlib.crc32(prefix) & 0xFFFF` | `0x160e == zlib.crc32(13-byte prefix) & 0xFFFF` | ✓ |

The captured archive is committed at
`fixtures/1.5-4.x/wrar290/wrar290_head3_sign_patched.rar` (276
bytes). It exercises the block writer end-to-end with a
degenerate signature (BSS-zero registration buffer drives
`FUN_004070a0` over zeros, so `HASH1` is deterministic but not a
"real" signature). Suitable for clean-room reader verification;
not byte-identical to a real registered build. See
`fixtures/1.5-4.x/wrar290/README.md` for full provenance and the
"shape only" caveat.

#### 10.9.2 Bit-level arithmetic of the signature

The inner loop's call tree (`FUN_00405a54` and the bignum helpers
below it) was traced 2026-04-28. The scheme is a **discrete-log
signature over a binary elliptic curve**, not a keyed-hash with
signature-shaped boilerplate. Two arithmetic systems are in play:

1. **Curve arithmetic** — points on an elliptic curve over a
   binary extension field. Each point is a pair (x, y) of 16-limb
   length-prefixed bignums (16 × 15 = 240 bits per coordinate),
   stored at struct offsets `+0x00` and `+0x24` ushorts. The base
   field is GF(2^15) with reduction polynomial `0x8003`
   (= x^15 + x + 1). The full coordinate field is GF(2^15)^16
   (treated as a degree-16 polynomial extension); each ushort
   limb holds one GF(2^15) element. `FUN_00406060` lazily builds
   the GF(2^15) log/exp tables on first use.

   - `FUN_004061a8` — polynomial **add** in the binary field
     (XOR per limb).
   - `FUN_0040628c` — polynomial **multiply** (per-limb-pair
     multiply via `log[a] + log[b] mod 0x7fff` then XOR into the
     destination at offset `i+j-1`).
   - `FUN_004063a4` — polynomial **square** (per-limb scalar `× 2`
     in log domain).
   - `FUN_00406480` — scalar **divide** by a single GF(2^15)
     element.
   - `FUN_004064e0` — polynomial **inverse** / divide.
   - `FUN_00405d24` — **point addition** with the standard chord-
     and-tangent branches (identity, equal x with opposite y →
     identity, equal point → double, generic add via slope =
     (y2-y1)/(x2-x1)).
   - `FUN_00405ebc` — **point doubling**.
   - `FUN_00405e84` — point subtraction (negate-then-add).
   - `FUN_00405f54` — **scalar multiplication** of a point by a
     hash-derived value, using a non-adjacent-form bit scan: at
     each bit `i`, if `nonce_bit_i == 1 && hash_bit_i == 0` add the
     accumulator, if the bits are reversed subtract; double the
     accumulator at the end of each step.

2. **Integer arithmetic on length-prefixed 16-bit-limb bignums**,
   used after the curve operation produces a point:
   - `FUN_00406900` — bignum **add** (multi-precision add with
     carry, base 2^16 limbs).
   - `FUN_0040698c` — bignum **subtract** with borrow.
   - `FUN_004069f8` / `FUN_00406a68` — bignum **shift**
     left/right.
   - `FUN_00406abc` — bignum × small scalar.
   - `FUN_00406b38` — bignum **compare**.
   - `FUN_00406b88` — bignum **mod** (reduce mod
     `DAT_004304b0`).
   - `FUN_00406bec` — bignum **modular multiply**.
   - `FUN_004067cc` — **field-to-integer reordering**: walks the
     limbs of a polynomial-over-GF(2^15) representation top-down,
     repeatedly shifting the integer accumulator left by 15 bits
     and adding the next 15-bit limb. Converts a 240-bit
     polynomial element into the integer modular ring used for
     the signature components.

The signature inner loop (`FUN_00405a54(P_priv, hash, k, sig)`)
proceeds as follows:

```
local_98 = G                     # base point from DAT_004304d6 (Gx, Gy)
local_98 = local_98 · hash       # scalar multiplication via FUN_00405f54
sig      = int(local_98_x)       # field-to-integer via FUN_004067cc
sig      = (sig + k) mod n       # n = DAT_004304b0
if sig != 0:
    local_c0 = (P_priv * sig) mod n      # FUN_00406bec
    sig[+0x13]   = hash                  # second component buffer
    if local_c0 > hash:
        sig[+0x13] += n                  # ensure non-negative diff
    sig[+0x13] -= local_c0
    # Final signature components: r = sig, s = sig[+0x13]
```

This is structurally a Schnorr-like signature over a binary EC,
with `(r, s)` written hex-encoded into the
`HASH1` / `HASH2` / `HASH3` slots of the `HEAD3_SIGN` body via
`FUN_0040703c` (`bignum_to_hex_string`). The retry loop in
`FUN_004070a0` regenerates `k` until `sig != 0` (the `*param_4
!= 0` guard at line 3915 of the decompile).

**Practical implication for readers:** The signature is a true
public-key construction; verification requires WinRAR's embedded
public key (the `(Gx, Gy)` base point at `DAT_004304d6` plus the
public key derived from the registration material). Without that
public key, readers can validate the **block layout** (HEAD3_SIGN
field offsets, padding sum 0xa7, length prefixes) but not the
signature itself. The 2.90 patched fixture in §10.9.1.1 has a
deterministic-but-meaningless `HASH1` because the BSS-zero
private key drives the curve multiplication over zeros.

**Practical implication for writers:** Producing a real
HEAD3_SIGN block requires either (a) a valid registration key
(equivalent to forging the curve's discrete log), or (b) a
patched WinRAR with the private-key gates bypassed (which is
what the 2.90 fixture does, and what produces "shape-correct but
verification-meaningless" signatures).

#### 10.9.3 What's still open

- A **wild / real-registered** fixture. The shape-only fixture in
  §10.9.1.1 was produced by a patched 2.90 with a BSS-zero
  registration buffer; closing the spec's verification end-to-end
  (with realistic `NAME2`, real-`HASH1` matching a real-registered
  signature, real `HASH2` / `HASH3` content) needs either a wild
  signed archive from the 2.90-3.93 window or a fully-registered
  2.90 build (= a leaked or manually-constructed `rarreg.key`).
- `NAME2` semantics. NAME1 is now confirmed as the archive name
  being signed (§10.9.1.1). NAME2 is empty in the patched-build
  fixture because the BSS-resident registered-creator buffer is
  zero; in a real registered build it should carry the registered
  user's identity (analogous to RAR 2.x AV's "by E. Roshal"
  creator field), but this is inferred from analogy not observed.

---

## 11. Recovery Record

The recovery record allows partial reconstruction of damaged archives using
Reed-Solomon error correction.

- **RAR 2.x**: Uses the `PROTECT_HEAD` block type (`0x78`). Indicated by
  `MHD_PROTECT` flag (`0x0040`) in the archive header.
- **RAR 3.x+**: Uses a `NEWSUB_HEAD` subblock (`0x7A`) with name `RR`.

Recovery records are optional metadata for archive repair and are not required
for extraction. Full byte-level layout — including the 8-bit Reed–Solomon
parameters (RSCoder), sector size, parity placement, inline vs `.rev`
variants, and the encoder recipe — is specified in
`INTEGRITY_WRITE_SIDE.md` §3.4 (inline `PROTECT_HEAD`) and §3.5 (separate
`.rev` files). Readers that only extract file data can skip these blocks
using the common block-length field; only repair tooling needs to parse the
contents.

RAR 2.50 fixtures pin every `PROTECT_HEAD` field — see
`INTEGRITY_WRITE_SIDE.md §3.4`. Highlights:

- `Mark[8]` = the literal ASCII `"Protect!"` (was previously listed as
  reserved/unknown).
- `Version` = the protected archive's `UNP_VER` (`0x14` for RAR 2.0
  inputs), not the previously-claimed constant `1`.
- `ADD_SIZE = TotalBlocks * 2 + RecSectors * 512`. `TotalBlocks` is the
  data-sector count; `RecSectors` is the parity-sector count (`-rrN`,
  N ≤ 8 in RAR 2.50).
- Two committed fixtures exercise the format:
  `fixtures/1.5-4.x/rar250_protect_head_rr1.rar` (N=1, smallest case)
  and `…_rr5.rar` (N=5).

Empirical boundary: RAR 3.00 and WinRAR/RAR 4.20 `rr[N]` do **not** emit
`PROTECT_HEAD` (`0x78`). Both tested builds emit a `NEWSUB_HEAD` (`0x7a`) named
`RR`, with main-header flag `MHD_PROTECT` set. For a tiny one-file archive with
`-rr10`, the observed recovery block was:

```text
RAR 3.00: offset 106, type 0x7a, flags 0xc000, HEAD_SIZE 54, ADD_SIZE 514, name "RR"
RAR 4.20: offset 111, type 0x7a, flags 0xc000, HEAD_SIZE 54, ADD_SIZE 514, name "RR"
```

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
| 2 | Next 2 bytes are the Unicode character (low byte first, then high byte). |
| 3 | Run-length encoding. Next byte encodes length and optional correction. |

For flag value 3 (run-length):
- Read one byte as `length_byte`.
- If `length_byte & 0x80`: read another byte as `correction`. Run length =
  `(length_byte & 0x7F) + 2` characters, each with high byte = `highbyte`
  and low byte = `(ASCII_name[dst_pos] + correction) & 0xFF`, where
  `dst_pos` is the current destination index in the output string.
- If `length_byte & 0x80` is clear: run length = `length_byte + 2`
  characters. Each character is copied directly from the ASCII fallback
  name: codepoint = `ASCII_name[dst_pos]`, high byte = `0x00`.

The flag byte itself is consumed 2 bits at a time (MSB first), so one
flag byte covers exactly 4 characters. A new flag byte is read each
time `FlagBits == 0`.

Internally the resulting code units are UTF-16; on the wire, mode 2 places the low byte
first, matching host-endian x86 storage — the flag-byte encoding is
**not** UTF-16BE as earlier versions of this doc claimed.

### Form 1 encoder (write side)

An encoder can be derived from the inverse of the decode logic. This
section specifies one correct implementation.

#### 13.1 Output layout

```
[ASCII fallback name] [0x00] [HighByte] [flag+char stream...]
```

- **ASCII fallback.** A best-effort 7-bit representation of the filename
  with non-ASCII characters replaced by `?` (the standard WinRAR
  substitution) or any deterministic filler. The decoder uses this
  string as the source buffer for mode 3 run copies, so the filler
  bytes matter: for mode 3 to be useful, choose filler that matches the
  low byte of the corresponding Unicode character when possible.
- **HighByte.** One byte. Typically the most common high byte of the
  non-ASCII codepoints in the name (e.g., `0x04` for Cyrillic, `0x00`
  for pure-ASCII + occasional extended, `0x05` for Armenian, etc.).
  Picking HighByte well maximises the number of characters that can use
  mode 1 instead of mode 2, halving their byte cost.
- **Flag+char stream.** Interleaved flag bytes and per-mode payload
  bytes. Exactly one flag byte per 4 characters; flag bits are
  consumed MSB-first.

The `NAME_SIZE` field in the file header counts **every byte** of the
above, from the start of the ASCII fallback through the last byte of
the flag+char stream. There is no separate length field for the
Unicode portion — the decoder walks the flag stream until it has
emitted enough code units or runs off the end of the buffer.

#### 13.2 Mode selection per character

For each Unicode codepoint `cp` at destination position `i`:

```
if (cp >> 8) == 0:
    # Plain ASCII or Latin-1. Mode 0 costs 1 byte + 2 flag bits.
    mode = 0

elif (cp >> 8) == HighByte:
    # High byte matches the archive HighByte. Mode 1 costs 1 byte + 2 flag bits.
    mode = 1

else:
    # Arbitrary codepoint. Mode 2 costs 2 bytes + 2 flag bits.
    mode = 2
```

Mode 3 (run copy) is an **optional** optimisation. It is worth emitting
when a run of ≥ 2 consecutive characters can all be reconstructed from
the ASCII fallback, either directly (mode 3 without 0x80) or via a
single constant correction on the HighByte plane (mode 3 with 0x80).
Skipping mode 3 entirely produces valid, correctly-decodable output at
a ~1–2× size penalty on names with long runs of one script.

Minimal-correct encoder: use only modes 0, 1, 2.
Optimal-size encoder: add mode 3 detection as a peephole pass.

#### 13.3 Emitting flag bits

The flag byte is packed MSB-first, 4 modes per byte. An encoder tracks
a pending flag-byte offset:

```python
class FlagWriter:
    def __init__(self, out: bytearray):
        self.out = out
        self.flag_pos = -1   # offset of current flag byte, or -1 if none
        self.flag_bits = 0   # bits used in current flag byte so far

    def emit_mode(self, mode: int):
        assert 0 <= mode <= 3
        if self.flag_bits == 0:
            # Allocate a new flag byte and remember its position.
            self.flag_pos = len(self.out)
            self.out.append(0)
        # The first mode goes into bits 7-6, second into 5-4, etc.
        shift = 6 - self.flag_bits
        self.out[self.flag_pos] |= (mode << shift)
        self.flag_bits += 2
        if self.flag_bits == 8:
            self.flag_bits = 0
```

After all characters are emitted, the last flag byte may have unused
low bits. These decode as mode 0 reads, but the decoder's loop condition
`while (EncPos < EncSize)` halts before consuming them — **as long as
the encoder does not emit trailing per-mode payload bytes for slots it
never intended to use**. In practice: stop writing as soon as the last
real character's payload is out, even if the flag byte is partially
filled.

#### 13.4 Complete minimal encoder

```python
def encode_filename_rar3x(name_unicode: str,
                          ascii_fallback: bytes,
                          high_byte: int = 0) -> bytes:
    """Produce the FILE_NAME body for LHD_UNICODE form 1 (minimal modes only).

    `ascii_fallback` must be at least as long as `name_unicode`; positions
    beyond the unicode name are ignored by the decoder but the fallback
    bytes are read by mode 3 runs (unused here). Using a same-length
    fallback with `?` for non-ASCII positions is the safe default.
    """
    out = bytearray(ascii_fallback)
    out.append(0x00)
    out.append(high_byte)
    fw = FlagWriter(out)

    for ch in name_unicode:
        cp = ord(ch)
        if (cp >> 8) == 0:
            fw.emit_mode(0)
            out.append(cp & 0xFF)
        elif (cp >> 8) == high_byte:
            fw.emit_mode(1)
            out.append(cp & 0xFF)
        else:
            fw.emit_mode(2)
            out.append(cp & 0xFF)          # low byte first
            out.append((cp >> 8) & 0xFF)   # then high byte

    return bytes(out)
```

**Interleave rule.** The trap above is easy to miss: flag bytes and
per-mode payload bytes appear in the same output stream, but a flag
byte describes **the next 4 characters**, not a whole run. When the
encoder reaches a character position whose flag slot is empty (i.e.,
`flag_bits == 0`), it must emit a new flag byte **before** writing
that character's payload bytes. Concretely, the emission order is:

```
flag_byte_A   ← describes chars 0,1,2,3
char0_payload
char1_payload
char2_payload
char3_payload
flag_byte_B   ← describes chars 4,5,6,7
char4_payload
...
```

Not:

```
flag_byte_A flag_byte_B ... | char0_payload char1_payload ...
```

The decoder's interleaved read checks for a fresh flag byte before each
output unit, which is what forces this.

#### 13.5 Mode 3 encoder (optional, for ratio)

To emit mode 3 run copies, after choosing the ASCII fallback:

```
1. For each position i, precompute match_len_0[i] = the longest run
   starting at i for which unicode_low[i..i+k] == ascii_fallback[i..i+k]
   AND unicode_high[i..i+k] == 0.
2. For each position i and each 8-bit correction c, precompute
   match_len_c[i][c] = the longest run starting at i for which
   unicode_low[i..i+k] == (ascii_fallback[i..i+k] + c) & 0xFF AND
   unicode_high[i..i+k] == high_byte.
3. At each character position, prefer:
     mode 3 (no correction) if match_len_0[i] >= 2
     mode 3 (correction c)  if max_c match_len_c[i][c] >= 2
     otherwise fall through to mode 0/1/2
   Greedy selection within the 127-char maximum run length.
```

The run length stored is `actual_length - 2`, so the minimum encoded
run is 2 characters. A run of 129 decodes as `length_byte = 127` (with
or without 0x80). Runs longer than 129 characters must be split into
multiple mode-3 emissions.

The correction form (mode 3 with 0x80) is particularly valuable for
names that consist of ASCII plus accented letters where the low byte
of the accented form differs from the ASCII form by a fixed constant —
e.g., some Cyrillic transliteration conventions.

#### 13.6 HighByte selection

For an optimal (minimum-size) encoder:

```python
def pick_high_byte(name: str) -> int:
    """Pick the HighByte that maximises mode 1 usage."""
    histogram = {}
    for ch in name:
        hb = ord(ch) >> 8
        if hb != 0:  # mode 0 already handles hb==0
            histogram[hb] = histogram.get(hb, 0) + 1
    if not histogram:
        return 0  # all-ASCII name; HighByte is unused
    return max(histogram.items(), key=lambda kv: kv[1])[0]
```

A single-script name always converges on one HighByte (all characters
use mode 1, 1 byte each). A mixed-script name may do better with
HighByte chosen for the most common non-ASCII script, with the minority
script falling back to mode 2.

#### 13.7 When to use Form 1 vs Form 2

RAR 3.x introduced Form 2 (UTF-8) specifically because Form 1 is
complex and has awkward edge cases. **An encoder targeting RAR 3.x or
newer should prefer Form 2 unconditionally.** Form 1 exists only for
back-compatibility with RAR 2.x decoders, which predate UTF-8 support.

The target matrix:

| Decoder target | Form to emit |
|----------------|--------------|
| RAR 2.x only                              | Form 1 (no choice) |
| RAR 3.x or newer (all-ASCII name)         | neither — just emit ASCII, no LHD_UNICODE |
| RAR 3.x or newer (non-ASCII name)         | Form 2 (UTF-8) |
| RAR 2.x + RAR 3.x interop (rare)          | Form 1 |

A clean-room encoder that targets "WinRAR 3.x and later" never needs
the Form 1 encoder at all. Form 2 is a straight UTF-8 encode of the
name into the `FILE_NAME` field with `FHD_UNICODE` set — no flag
stream, no HighByte, no mode selection.

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
inaccurate — cross-checking public password-recovery implementations, John the
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

### 16.11 Encoder (RAR 2.0 LZ compressor)

Unlike RAR 1.3/1.5 (§6.16 of the RAR 1.3 spec), RAR 2.0 uses explicit
per-block Huffman tables serialized in the stream. This makes encoding
structurally much cleaner: the encoder picks a block boundary, accumulates
symbol frequencies over the block, builds Huffman tables via
`HUFFMAN_CONSTRUCTION.md`, and emits the tables followed by the encoded
symbols. No adaptive MTF state, no running averages to mirror.

Primary reference: `_refs/7zip/CPP/7zip/Compress/Rar2Decoder.cpp` (reader).
Treat reader implementations as format references, not encoder source.

#### 16.11.1 Encoder pipeline

```
for each block:
    1. run match finder over block_size input bytes → token stream
    2. count Main, Dist, Length (or audio MD[ch]) symbol frequencies
    3. build Huffman lengths for all required tables (HUFFMAN_CONSTRUCTION §3)
    4. delta-encode and RLE-pack the lengths against m_LastLevels
    5. emit block header bits (§16.11.3)
    6. emit the RLE-packed lengths via a 19-symbol level Huffman
    7. emit each token as canonical codes for the token's Huffman alphabet
    8. update m_LastLevels for next block's delta baseline
```

Block size is an encoder choice. Larger blocks share Huffman table overhead
over more symbols but may have worse adaptation to local statistics. A
reasonable default is 16–64 KB of input per block. The block's actual end is
signalled by emitting symbol 269 (new-block marker) through the Main table
or symbol 256 in an audio Main table.

#### 16.11.2 LZ token selection

The Main table's symbol alphabet (298 symbols, per §16.2 `NC20`) encodes
several token types. To emit a `(len, dist)` match from the match finder, the
encoder picks the cheapest legal representation:

| Match condition | Main symbol | Extra |
|---|---|---|
| `len == LastLength` and `dist == LastDist` | 256 | — |
| `dist == OldDist[k]` for `k ∈ 0..3` | `257 + k` | Length slot via Length table |
| `len == 2` and `dist ≤ 255` | `261..268` (short-dist) | Low distance bits per `SDBits[]` |
| otherwise (new match) | `270 + length_slot` | Distance slot via Distance table |

For the "new match" case, the length slot is the `LDecode[]` bin containing
`len` after removing the large-distance bonus (§16.9); the low-order
`LBits[slot]` bits of `len - LDecode[slot] - 3` follow as raw bits. Similarly,
the distance slot is the `DDecode[]` bin containing `dist - 1`, with
`DBits[slot]` raw bits of low-order distance.

**The bonus trap.** §16.9 says the decoder *adds* 1 to `Length` when
`Distance >= 0x2000` and another 1 when `Distance >= 0x40000`. The encoder
must therefore *subtract* those bonuses **before** computing the length slot,
otherwise the slot is off by 1–2. The same trap applies to the repeat-
distance path with its `Distance >= 0x101` bonus. Easy to miss; the test
oracle (§16.11.8) will catch it.

For literals, the Main symbol is simply the byte value `0..255`.

#### 16.11.3 Block header

```
emit 1 bit:  audio_mode ? 1 : 0
emit 1 bit:  keep_tables ? 1 : 0    # 0 on first block; 1 only if reusing tables
if audio_mode:
    emit 2 bits:  channels - 1      # 1..4 channels
```

`keep_tables = 0` zeros the encoder's `m_LastLevels[]` baseline (and the
decoder's, symmetrically) before the delta decode. In practice, most blocks
set `keep_tables = 0` because the new block's tables differ from the
previous block's; setting it to 1 only helps when reusing *identical* tables
across blocks, which is rare in adaptive encoding.

#### 16.11.4 Huffman level table emission

For LZ blocks, the encoder has `374 = NC20 + DC20 + RC20` concatenated code
lengths (298 + 48 + 28). For audio blocks, it has
`MC20 * Channels = 257 * Channels` code lengths.

Step 1: compute `deltas[i] = (lens[i] - m_LastLevels[i]) & 15`. This keeps
the level-table alphabet small (values 0–15 for literals), which improves the
level table's own compression.

Step 2: RLE-pack the delta stream into level symbols:

| Input pattern | Level symbol | Extra bits |
|---|---|---|
| Any single `delta` in 0..15 | `delta` | — |
| Repeat previous value `N` times, `N ∈ [3, 6]` | 16 | `N - 3` as 2 bits |
| Run of zero `N` times, `N ∈ [3, 10]` | 17 | `N - 3` as 3 bits |
| Run of zero `N` times, `N ∈ [11, 138]` | 18 | `N - 11` as 7 bits |

RLE is optional in principle — you can always emit literal deltas — but an
encoder that skips RLE produces much larger tables. The heuristic: emit 17/18
whenever the zero run is ≥ 3, and 16 whenever a non-zero value repeats ≥ 3
times. For runs longer than 138 zeros, split into multiple 18 runs.

Step 3: count the 19 level symbols' frequencies, build a Huffman over them
via `HUFFMAN_CONSTRUCTION.md` §3 with `maxLen = 15`. Emit the 19 level code
lengths as raw 4-bit fields (the decoder reads them directly at line 157 of
`Rar2Decoder.cpp`).

Step 4: emit the RLE-packed level symbols using the level Huffman's canonical
codes (via §4 of `HUFFMAN_CONSTRUCTION.md`).

#### 16.11.5 Payload emission

Once the tables are emitted, walk the token stream and for each token emit:

- **Literal:** 1 Main-table code (symbol `b`).
- **Repeat last:** 1 Main-table code (symbol 256). No extras.
- **Repeat distance:** 1 Main-table code (symbols 257–260) + 1 Length-table
  code for the length slot + raw bits.
- **Short distance:** 1 Main-table code (symbols 261–268) + raw distance bits.
- **New match:** 1 Main-table code (symbol 270 + length slot) + raw length
  bits + 1 Distance-table code for the distance slot + raw distance bits.

After the last token of the block, emit the new-block symbol (269). If the
next block will re-read tables, it begins after the padding bit alignment
performed by the decoder. In practice the encoder just emits the next
block's header bits without any alignment — the decoder reads them straight
after the 269 symbol.

**Repeat distance buffer update.** Both encoder and decoder maintain
`OldDist[0..3]` and `OldDistPtr`. On every non-repeat-match, push the new
distance to `OldDist[OldDistPtr++]` (modulo 4). On a repeat-distance match,
the decoder does NOT rotate — the distance value stays where it was — so the
encoder must not rotate either. `LastLength` / `LastDist` are updated on
every match (repeat or new).

#### 16.11.6 Audio mode encoding

Audio mode replaces LZ with per-channel adaptive prediction (§17.1–17.3).
The encoder runs the same prediction filter forward:

```
for each sample s:                        # one byte per sample per channel
    V = AudioVariables[CurChannel]
    PCh = predict(V, ChannelDelta)        # identical to §17.2 prediction
    Delta = (PCh - s) & 0xFF              # inverse of Ch = (PCh - Delta) & 0xFF
    emit Main-table code for Delta        # via MD[CurChannel]
    update(V, Delta, s)                   # identical to §17.2 post-update
    CurChannel = (CurChannel + 1) % Channels
```

Because the state update in §17.2 depends only on `Delta` and the resulting
`Ch` (which the encoder knows — it's the input byte), the encoder's update
is bit-exact with the decoder's. The predictor adapts to the signal
automatically; the encoder doesn't choose coefficients. The only encoder
decision is **channel count**, which fundamentally determines which samples
are correlated with which. A natural mapping:

- Mono 8-bit PCM → `Channels = 1`
- Stereo 8-bit PCM → `Channels = 2` (L, R, L, R, ...)
- Stereo 16-bit LE → `Channels = 2`, but the 2-channel split interprets
  low-byte and high-byte of each sample as two independent "channels". This
  is what the RAR 2.0 encoder actually did — it doesn't know about sample
  boundaries, only byte interleavings.
- Arbitrary interleaved multi-track → `Channels = 4` (the format maximum)

An encoder that's not sure should try `Channels = 1..4`, compress each, and
pick the smallest. The cost is 4× the audio-encode time for typically a 5–15%
size savings.

**Per-block granularity, not per-file.** `Channels` is read fresh at
each table-read boundary, which means a new audio
block inside the same file — or inside the next file in a solid stream —
can switch the channel count freely. The decoder resets
`UnpCurChannel = 0` whenever the new count is smaller than the previous
channel cursor. An encoder is therefore free to
repartition a single file into audio sub-blocks of different channel
counts (e.g. a header in mono followed by stereo PCM body), and must
likewise reset its own channel cursor on every block emit. There is no
global "file channel count" in the wire format.

**WinRAR's actual selection heuristic is closed source** — `_refs/` contains no reference implementation of the
channel-count decision. Flagged in `IMPLEMENTATION_GAPS.md` as a ratio
knob, not a correctness issue; the exhaustive 1..4 search above is the
safe clean-room default.

**When to use audio mode at all.** Audio mode wins on smooth multi-byte
signals (PCM, uncompressed bitmap) and loses on everything else. A simple
heuristic: compute the first-order byte entropy of the input; if it's > 7.5
bits/byte (nearly uniform) OR < 4 bits/byte (highly redundant), use LZ. For
the 4–7.5 range, try audio mode and compare block sizes.

#### 16.11.7 Reverse DecodeNumber

Unlike RAR 1.3/1.5's static DecodeNum tables, RAR 2.0 uses standard canonical
Huffman built per-block from the code lengths. Encoder-side, the reverse is
the canonical code assignment from `HUFFMAN_CONSTRUCTION.md` §4: once
`lens[]` is finalized the encoder has `codes[symbol]` and `code_lens[symbol]`
and emits `code_lens[symbol]` bits of `codes[symbol]`. No RAR-specific
reverse table construction needed.

#### 16.11.8 Test oracle and sanity checks

1. Round-trip every encoded block through an independent RAR 2.0 decoder
   and assert byte-exact recovery.
2. For each Main/Distance/Length table emitted, verify Kraft equality
   (`HUFFMAN_CONSTRUCTION.md` §4) on the non-zero lengths.
3. Cross-check audio mode against an LZ encoding of the same data; audio mode
   should produce at most ~5% more bytes than LZ on non-PCM input, and
   measurably fewer on PCM.
4. Use a vintage RAR build (`rar a -m5 -mm` for multimedia) as a reference
   point for compression ratio.

#### 16.11.9 Ratio tuning knobs

- **Block size.** Larger blocks amortize the ~1–2 KB table overhead over more
  symbols. Below 8 KB input per block, the table overhead dominates.
- **Parser strategy.** Greedy vs lazy vs optimal (per
  `LZ_MATCH_FINDING.md` §5). Lazy gains a few percent over greedy at minimal
  cost.
- **Short-distance preference.** The short-distance slots 261–268 use a
  dedicated length-2 fast path; a naive encoder that always prefers length
  ≥ 3 matches leaves compression on the table. Price the short-dist slot in
  the parser cost function.
- **Repeat-distance preference.** Symbols 257–260 are typically cheaper than
  new matches (smaller distance alphabet). The parser should check all four
  `OldDist[]` entries before falling through to a new-match encoding.

---

## 17. Audio Compression (RAR 2.0)

RAR 2.0 introduced a specialized audio compression mode for multimedia
data. When the audio bit (bit 15 of the table-read peek word in §16.3)
is set in a block header, data is processed through per-channel
adaptive prediction instead of LZ matching. Verified against
`_refs/unrar/unpack20.cpp` (`Unpack20`, `DecodeAudio`, `ReadTables20`)
and `_refs/unrar/unpack.hpp:170-178` (`AudioVariables` struct).

### 17.1 Audio Variables

Reset rules (`_refs/unrar/unpack20.cpp:280-292`):

| State | Reset on non-solid file open | Carries on solid file open | Carries across audio blocks within a file |
|---|---|---|---|
| `AudV[0..3]` (per-channel state) | yes — `memset(AudV, 0, sizeof(AudV))` | yes | **yes** — never reset between blocks |
| `UnpChannelDelta` | yes — set to `0` | yes | yes (continuous across block boundaries) |
| `UnpCurChannel` | yes — set to `0` | yes | mostly yes; reset to `0` only when the new block's `Channels` is **smaller** than the previous cursor (`unpack20.cpp:192-193`) |
| `UnpChannels` | yes — set to `1` | yes | re-read from each audio block header (§16.3) |
| `UnpAudioBlock` | yes — set to `false` | yes | re-read from each block header |
| `UnpOldTable20[]` (Huffman tables) | yes — zeroed | yes | re-read or kept per the table-control bit |

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

The `ChannelDelta` variable carries state between channels (the delta
from the most recently decoded byte on the previous channel). It is
the *same* variable across all channels — distinct from each
channel's per-channel `LastDelta`, which carries the previous delta on
the same channel. Both are sign-extended `int8_t` values stored in
`int` for arithmetic.

### 17.4 Audio symbol 256 — table reread

In an audio block the per-channel Huffman table `MD[CurChannel]`
decodes a 9-bit alphabet (0..256, `MC20 = 257`). Symbol 256 means
"end of current Huffman segment, read new tables" — exactly the same
role as in LZ-mode (the equivalent LZ symbol is 269; see `unpack20.cpp:103-107`).
On symbol 256:

1. The audio decode loop calls `ReadTables20()`
   (`unpack20.cpp:56-60`), which reads a fresh block header.
2. The new block can switch back to LZ mode, change channel count, or
   stay in audio mode with new Huffman tables.
3. **Per-channel `AudV` state is preserved across this reread**, so
   adaptive coefficients (K1..K5) keep their learned values into the
   next block — the table change is independent of the predictor
   adaptation.
4. `UnpCurChannel` advances normally (no reset on table reread alone),
   except for the smaller-channel-count case noted in §17.1.

### 17.5 Bit-stream context

Audio symbols are read via the standard bit-stream cursor (`Inp.fgetbits`
/ canonical Huffman decode), exactly like LZ symbols. The audio block
introduces no separate range-coder or byte-aligned reader — it just
substitutes one 257-symbol Huffman table per channel for the LZ
decoder's main alphabet. Block boundaries follow the same byte-alignment
rules as LZ blocks (the audio bit is detected at the same fresh-table
peek; see §16.3).

Filter records (RAR 3.x VM) and PPMd are RAR 2.9+ features and **never**
appear in an Unpack20 stream regardless of UnpVer. Audio blocks only
co-exist with LZ blocks within the same file.

---

## 18. Compression Algorithm (RAR 2.9/3.x, UNP_VER 29)

RAR 2.9/3.x uses an LZSS-based compression scheme with Huffman coding. The
compressed data stream consists of blocks, each beginning with control flags
that determine whether the block uses LZ or PPMd compression.

### 18.1 Block Header

Each compressed block begins byte-aligned. The decoder forces alignment
via `Inp.faddbits((8 - Inp.InBit) & 7)` before peeking the header
(`_refs/unrar/unpack30.cpp:638`, `ReadTables30`). The first bit at the
aligned position determines the compression mode:

| First bit at aligned byte | Meaning |
|---|---|
| 1 | PPMd block (see §19). The remaining 7 bits of this same byte are PPMd parameter flags — see §19.1. ReadTables30 does **not** consume any bits in the PPMd path; the entire indicator byte is consumed by PPMd's own initialiser via `GetChar()`. |
| 0 | LZ block (described below). The decoder consumes 2 bits (the indicator bit + the table-control bit) before reading the level table. |

For LZ blocks, the second bit controls Huffman table construction:

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

The code lengths are encoded using a 20-symbol level table (identical scheme
to RAR 5.0; see RAR5 spec §11.3).

The level table itself is preceded by a 20-entry **level-of-level** table
(`BC30 = 20`, one 4-bit length per symbol). Inside the level-of-level table,
a value of 15 is overloaded:

- 15 followed by a 4-bit count `Z == 0` → literal length 15 (the actual symbol
  length is 15 bits).
- 15 followed by a 4-bit count `Z != 0` → run of `Z + 2` zero lengths in the
  level-of-level table (3..17 entries).

The decoded level table is then used to Huffman-decode the code lengths for
all four main tables (404 symbols total). Level decoder symbols:

| Symbol | Meaning |
|--------|---------|
| 0-15   | Literal code length. Added to previous length modulo 16: `new = (old + val) & 0xF`. |
| 16     | Repeat previous length. Count = `3 + read_bits(3)` (3-10 times). |
| 17     | Repeat previous length. Count = `11 + read_bits(7)` (11-138 times). |
| 18     | Set to zero. Count = `3 + read_bits(3)` (3-10 times). |
| 19     | Set to zero. Count = `11 + read_bits(7)` (11-138 times). |

Symbol 16 at position 0 (no previous length yet) is illegal — the decoder
must reject the block. Maximum code length: 15 bits. Verified against
`_refs/unrar/unpack30.cpp` (`ReadTables30`).

### 18.3 LZ Match Decoding

The main decode loop reads symbols from the MainCode Huffman table:

**Literal (symbol < 256):** Output the byte directly.

**New block / filter (symbol == 256):** Read 1 bit:
- If 1: new file marker. Read 1 more bit for "new table" flag. Block ends.
- If 0: RARVM filter follows (see Section 20).

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

### 18.8 Encoder (RAR 2.9/3.x/4.x LZ compressor)

RAR 2.9+ extends the RAR 2.0 format (§16.11) with four key changes: four
Huffman tables instead of three (adding `LowOffsetCode`), split-mode distance
encoding for large distances, repeat-distance **rotation** (opposite of
2.0's non-rotation), and an inline filter dispatch through the Main table's
256/257 symbols. The encoder pipeline is otherwise identical to §16.11 —
match finder → symbol frequencies → Huffman tables → RLE level encoding →
canonical code emission.

Primary reference: `_refs/7zip/CPP/7zip/Compress/Rar3Decoder.cpp` (reader,
~940 lines). Filter handling (Rar3Vm) is out of scope for this section and
covered by the Filters item in `IMPLEMENTATION_GAPS.md`.

#### 18.8.1 Mode selection (LZ vs PPMd)

Per-block choice: one bit at block start selects LZ (0) or PPMd (1). A
good encoder heuristic:

- PPMd wins on text, source code, XML, structured logs, and any input with
  strong context predictability. Typical gain over LZ: 10–25%.
- LZ wins on already-compressed data, binaries with repeated sequences at
  distance, and any input where the match finder can exploit repeats across
  more than a few hundred bytes.
- A two-pass auto-selection is cheap: compress a 16–32 KB probe block both
  ways, pick the smaller. Sticky bias toward the winning mode for subsequent
  blocks until entropy changes.

PPMd encoding is covered by `PPMD_ALGORITHM_SPECIFICATION.md` §13.5. The
rest of this section is the LZ sub-mode.

#### 18.8.2 Encoder pipeline (LZ blocks)

```
for each block:
    1. run match finder → token stream (incl. any filter triggers)
    2. count Main(299), Offset(60), LowOffset(17), Length(28) frequencies
    3. build four Huffman tables, maxLen = 15
    4. delta-encode + RLE-pack the 404 concatenated lengths
    5. emit 2-bit block header (§18.8.3)
    6. emit 20-symbol level Huffman (raw 4-bit lengths + level codes)
    7. emit payload tokens as canonical codes
    8. stash lens[] as baseline for next block's delta
```

The level table is identical to RAR 5.0's (§11.3 of the RAR 5.0 spec), so
the level-table packing in `HUFFMAN_CONSTRUCTION.md` §5.3 applies here
directly — with one difference: RAR 3.x's level alphabet is 20 symbols while
RAR 2.0 used 19.

#### 18.8.3 Block header

```
emit 1 bit:  0         # LZ mode (1 = PPMd, see PPMD spec §13.5)
emit 1 bit:  keep_tables ? 1 : 0
```

No audio mode (RAR 2.0's multimedia mode was removed in 2.9; the audio
encoder in §16.11.6 does not apply to RAR 3.x).

`keep_tables = 0` forces the decoder's `m_LastLevels[]` baseline to zero
before the delta decode. Most blocks should set it to 0.

#### 18.8.4 LZ token selection

The Main table's 299-symbol alphabet assigns:

| Match / literal | Main symbol | Extra fields |
|---|---|---|
| Literal byte `b` | `b` (0..255) | — |
| Filter trigger (inline VM) | 256 + bit `0` | VM bytecode (see Filters item) |
| End of block | 256 + bit `1` + bit `new_table` | — |
| Filter (alternate) | 257 | VM bytecode |
| Last-length repeat | 258 | — |
| Repeat distance `k` (k ∈ 0..3) | 259 + k | Length via Length table |
| Short match (len 2) | 263..270 | Raw low distance bits |
| New match | 271 + length_slot | Raw length bits + OffsetCode slot + (split) distance bits |

The 256 symbol is **overloaded**: after emitting it, a bit distinguishes
"filter follows" (0) from "end of block" (1). An end-of-block 256 is then
followed by another bit indicating whether the next block re-reads tables.
Encoders writing a clean single-block file emit `256, 1, 0` at end.

**Length bonus trap** (same structure as RAR 2.0 §16.11.2, simpler values):
for new matches only, `length += 1` if `dist ≥ 0x2000`, another `+1` if
`dist ≥ 0x40000`. The encoder subtracts these bonuses *before* computing
`length_slot` from `lengthbases[]`. Note: unlike RAR 2.0, RAR 3.x does **not**
have the `dist ≥ 0x101` bonus for repeat-distance matches.

#### 18.8.5 Split distance encoding

For `offsetbits[slot] ≤ 4` (small distances, `slot ≤ 9`):
the encoder emits the OffsetCode canonical code for the slot, then
`offsetbits[slot]` raw bits of `(dist - 1 - offsetbases[slot])` LSB-first.

For `offsetbits[slot] > 4` (large distances, `slot > 9`):
```
emit OffsetCode canonical code for slot
low = (dist - 1 - offsetbases[slot]) & 0xF         # low 4 bits
high = (dist - 1 - offsetbases[slot]) >> 4         # high bits
if offsetbits[slot] > 4:
    emit raw bits(high, offsetbits[slot] - 4)

# low 4 bits via LowOffsetCode Huffman with repeat fast path:
if low == lastlowoffset and in_low_repeat_run:
    # do nothing — the repeat run already covers this
    numlowoffsetrepeats -= 1
else:
    if run_length_of(low) ≥ 2 starting here:
        emit LowOffsetCode code for symbol 16   # "repeat last 15 times"
        numlowoffsetrepeats = 15
        # subsequent identical lows consume the run for free
    else:
        emit LowOffsetCode code for symbol low  # 0..15
        lastlowoffset = low
```

The LowOffsetCode alphabet is 17 symbols: 0..15 are literal low-nibble
values, and symbol 16 triggers "repeat previous low value 15 more times",
where "previous" means `lastlowoffset`. The encoder should use symbol 16
only when it actually has ≥ 2 upcoming matches sharing the same low nibble —
otherwise the repeat-mode entropy gain is lost. Tracking the match finder's
next N distances and their low-nibble distribution is a small but measurable
win (typically 1–3% on binaries with aligned pointer patterns).

**Edge case.** If `numlowoffsetrepeats > 0` when a new LowOffsetCode symbol
would be emitted, the decoder does *not* read a symbol — it reuses
`lastlowoffset` and decrements the counter. The encoder must therefore
*not* emit a LowOffsetCode symbol during an active repeat run, even if the
actual low nibble matches. This is the same subtlety as RAR 2.0's rep-dist
non-rotation trap: the decoder's state machine has sticky modes the encoder
must shadow.

#### 18.8.6 Repeat distance handling — rotation

Unlike RAR 2.0 (§16.11.5), RAR 3.x **rotates** the repeat-distance buffer on
rep-dist matches. Encoder rule:

```
on new match (dist, length):
    oldoffset[3] = oldoffset[2]
    oldoffset[2] = oldoffset[1]
    oldoffset[1] = oldoffset[0]
    oldoffset[0] = dist

on repeat-distance match (symbol 259 + k, k ∈ 0..3):
    selected = oldoffset[k]
    # rotate selected to position 0:
    if k == 1: swap(oldoffset[0], oldoffset[1])
    if k == 2: oldoffset[2..0] = oldoffset[1,0,2]   # shift 0,1 down
    if k == 3: oldoffset[3..0] = oldoffset[2,1,0,3]
    # simpler: remove oldoffset[k], insert at position 0
```

Last-length repeat (symbol 258) reuses both `lastlength` and `lastoffset`
**without** touching the repeat buffer — it's a separate single-slot cache.

`lastlength` and `lastoffset` are updated on every match.

#### 18.8.7 Filter dispatch (encoder view)

The encoder can insert a filter block at any LZ token boundary. It emits:

```
emit Main code for 256, then raw bit 0      # or alternatively, Main code 257
emit filter bytecode                        # see Filters section
```

The filter bytecode is a compact VM program (RARVM, ~16 opcodes). Forward
filter generation — detecting when an E8/E8E9/delta/ARM/Itanium filter helps
and emitting the bytecode for it — is a large separate problem, tracked as
the RARVM Bytecode Emitter item in `IMPLEMENTATION_GAPS.md`. A
filter-agnostic encoder can simply never emit a filter trigger; compression
ratio on x86 binaries will be 5–15% worse than an encoder that emits E8E9
filters, but correctness is unaffected.

#### 18.8.8 Test oracle

Same as §16.11.8 but targeting a RAR 3.x LZ decoder:

1. Round-trip every encoded block through an independent RAR 3.x decoder
   and assert byte-exact recovery.
2. Verify Kraft equality on all four Huffman tables.
3. Test all token types: pure literals, long single match, many short
   matches, rep-dist rotation sequence, large-distance split mode,
   end-of-block transition.
4. Cross-check PPMd sub-mode by running a block through both LZ and PPMd
   and verifying both decode to the same bytes.
5. Exercise the LowOffset repeat fast path with a crafted input containing
   runs of same-low-nibble distances.

#### 18.8.9 Ratio tuning knobs

- **Match finder cut value** — same impact as RAR 2.0.
- **PPMd vs LZ per block** — biggest single knob. See §18.8.1.
- **LowOffset repeat utilization** — measurable on pointer-heavy binaries.
- **Filter emission** — 5–15% on x86 binaries once implemented.
- **Solid mode across files** — covered by the Solid Archives item.

---

## 19. PPMd Compression (RAR 3.x)

RAR 3.x can use PPMd (Prediction by Partial Matching, variant H by Dmitry
Shkarin) as an alternative to LZ compression. The choice is per-block.

### 19.1 PPMd Block Parameters

The PPMd block opens with a single byte at the (already byte-aligned)
header position. Bit 7 of this byte was the §18.1 mode indicator
(value `1` selecting the PPMd path); the lower 7 bits carry the PPMd
parameter flags. The byte is consumed by `ModelPPM::DecodeInit` via
`UnpackRead->GetChar()` — i.e. the **whole byte** is read at once via
the byte stream, not via the bit-stream cursor.

| Bits | Mask | Meaning |
|------|------|---------|
| 0-4  | 0x1F | Maximum model order minus 1 (if flag 0x20 set). |
| 5    | 0x20 | Model reset: new dictionary size and model order. |
| 6    | 0x40 | New escape character value. |
| 7    | 0x80 | (Mode indicator from §18.1; always `1` here.) |

The follow-up bytes — also read via `GetChar()` — are conditional on
the parameter flags:

```
header_byte = GetChar()                       # 1 byte (incl. mode bit)
if header_byte & 0x20:                        # Model reset
    max_mb = GetChar()                        # 1 byte
                                              # Dictionary = (max_mb + 1) MB, max 256
if header_byte & 0x40:                        # New escape
    PPMEscChar = GetChar()                    # 1 byte; default 2 if not present
range_coder.init():                           # consumes 4 bytes via GetChar()
    code = 0
    for i in 0..3:
        code = (code << 8) | GetChar()
if header_byte & 0x20:
    model_order = (header_byte & 0x1F) + 1
    if model_order > 16:
        model_order = 16 + (model_order - 16) * 3
    re-init model with dictionary and order
```

Model-order verified against `_refs/unrar/model.cpp:586-593`. Range
coder init verified against `_refs/unrar/coder.cpp:9-17`
(`RangeCoder::InitDecoder` reads 4 bytes via `GetChar`).

If neither reset flag is set, continue using the existing PPMd context
— but the range coder state is **always** re-initialised on every
PPMd block (the `Coder.InitDecoder` call is unconditional in
`DecodeInit`).

### 19.2 PPMd / LZ transition mechanics

PPMd reads through `Unpack::GetChar()` (`unpack.hpp:403`):

```
byte GetChar() { return Inp.InBuf[Inp.InAddr++]; }
```

This is a **direct byte fetch** — it advances `Inp.InAddr` by 1 and
ignores `Inp.InBit`. Consequence: PPMd cannot start mid-byte. This is
why `ReadTables30` aligns to the next byte boundary before peeking the
header bit. PPMd never uses `Inp.getbits()` / `Inp.addbits()`; LZ never
uses `Inp.GetChar()` for compressed-data reads.

Switches between modes:

| Transition | Mechanism |
|---|---|
| LZ → PPMd (PPMd flag set in `ReadTables30`) | Already byte-aligned by `ReadTables30`. PPMd takes over from `InAddr` via `GetChar()`. The shared 1-byte header carries both the mode indicator and the PPMd flags. |
| PPMd → LZ (escape value 0 inside PPMd) | `ReadTables30` is called again (`unpack30.cpp:89`). It byte-aligns (`InBit` is already 0 because PPMd never set it) and reads the next mode indicator. The PPMd model state is preserved unless a Reset flag (`0x20`) appears in the next PPMd block. |
| PPMd → end-of-file (escape value 2 inside PPMd) | Decoder breaks out of the main loop. Surrounding caller observes `FileExtracted` to learn the file ended cleanly. |

Volume boundaries are transport-layer only (see
`ARCHIVE_LEVEL_WRITE_SIDE.md` §2.2): the byte stream is continuous, so
PPMd's state — model context, range-coder `low`/`code`/`range`
registers, escape character — carries across the boundary unchanged.
A volume cut may land at any byte boundary, including mid-symbol; the
range-coder state is internal and survives the source-buffer switch.

### 19.3 Filter records inside PPMd vs LZ

When PPMd emits escape value 3 (RARVM filter; see
`PPMD_ALGORITHM_SPECIFICATION.md` §13.2), the filter program follows
**inside the PPMd stream** — every byte of the bytecode is read via
`SafePPMDecodeChar()`, the PPMd-decoded byte source. The decoder
remains in `BLOCK_PPM` mode throughout. Verified at
`unpack30.cpp:97-102` (`ReadVMCodePPM`), `:326-340`.

By contrast, when an LZ block embeds a filter (Main-symbol value
range that triggers VM-code reading), the filter bytes are read from
the **bit stream** via `Inp.getbits() >> 8; Inp.addbits(8)`. Verified
at `unpack30.cpp:296-302` (`ReadVMCode`).

So filter records have two distinct on-disk encodings depending on the
surrounding block type:

| Surrounding block | Filter byte source | Encoder cost |
|---|---|---|
| LZ | Raw bit stream (8 bits per byte, byte-padded by `addbits(8)` calls) | Bytecode is uncompressed |
| PPMd | PPMd-decoded byte stream (same model that produced the file data) | Bytecode benefits from PPMd's context model |

The filter's *content* (the bytecode itself, the register-init mask,
the data-block length) is identical in both cases — only the
transport differs.

PPMd-embedded LZ-style matches (escape value 4: new distance,
escape value 5: RLE) likewise read their distance and length bytes
via `SafePPMDecodeChar`. See `unpack30.cpp:103-131`.

### 19.4 PPMd Algorithm Reference

The PPMd variant H algorithm is fully specified in
`PPMD_ALGORITHM_SPECIFICATION.md`. That document covers the complete
context model, symbol encoding/decoding, model update with information
inheritance, SEE (Secondary Escape Estimation), binary context
optimization, rescaling, both 7z and RAR range coder variants, and the
escape-value table referenced by §19.2.

RAR uses the PpmdRAR range coder variant (carry-less, with `Low` and
`Bottom` state variables) rather than the 7z variant. The model logic
is identical.

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

When a filter is triggered (symbol 257 in the main decode loop, or symbol 256
followed by a zero bit in the alternate path), the filter record is read from
the compressed data. The first byte is both a payload-length prefix and a flag
byte:

| Bits | Meaning |
|------|---------|
| 0..2 | Number of bytes in the following VM filter payload. Let `n = (firstByte & 7) + 1`; if `n == 7`, read one extra byte and use `extra + 7`; if `n == 8`, read a 16-bit value and use that. |
| 3 (`0x08`) | User global data follows. |
| 4 (`0x10`) | Register-init mask and register values follow. |
| 5 (`0x20`) | Block length follows; otherwise reuse the previous length for this stored program. |
| 6 (`0x40`) | Add 258 to the decoded block start. This is a compact common-case offset adjustment, not a different coordinate system. |
| 7 (`0x80`) | Explicit program number follows; otherwise reuse the last program number. |

The following VM filter payload is then parsed as a bitstream:

| Field | Encoding | Description |
|-------|----------|-------------|
| Program number | RARVM number | If `0x80` is set: `0` clears all stored programs, otherwise the stored-program index is `value - 1`. If `0x80` is clear, reuse the previous program number. A new program is indicated when the selected index equals the current stored-program count. |
| Block start | RARVM number | Offset from the current LZ window position; add 258 if first-byte flag `0x40` is set. |
| Block length | RARVM number | Size of filtered region, present only if flag `0x20` is set. New programs must provide it; reuses may omit it to keep the previous length. |
| Register init | bit mask + RARVM numbers | Present only if flag `0x10` is set. The mask is 7 bits for R0..R6; a RARVM number follows for each set bit. |
| Bytecode | RARVM number + bytes | Present only for a new stored program. Length must be 1..65535 bytes, followed by that many raw bytecode bytes. |
| Global data | RARVM number + bytes | Present only if flag `0x08` is set. Appended after the 64-byte system global area. Standard filters do not need it. |

The RARVM number format reads:

| Prefix | Payload | Decoded value |
|--------|---------|---------------|
| `00` | 4 bits  | 0..15 |
| `01` | 8 bits  | 16..255 if the byte is at least 16; otherwise `0xFFFFFF00 | (byte << 4) | next4bits` for small negative constants used by generic VM bytecode. |
| `10` | 16 bits | 0..65535 |
| `11` | 32 bits | 0..0xFFFFFFFF |

For encoder-side filter records, all sizes, offsets, program numbers, channel
counts, and image widths are non-negative, so the `01`/`byte < 16` negative
constant form is not used.

### 20.3 Standard Filter Programs

Rather than executing arbitrary bytecode, all known clean-room implementations
(libarchive, 7-Zip, refinery) recognize standard filter programs by CRC32
fingerprints of their bytecode and use hardcoded native implementations. No
known RAR archive in the wild uses non-standard RARVM programs.

Known filter fingerprints (CRC32 of bytecode):

| Length | CRC32        | Filter   | Purpose |
|-------:|:-------------|:---------|:--------|
|     53 | `0xAD576887` | E8       | x86 CALL (0xE8) address translation. |
|     57 | `0x3CD7E57E` | E8E9     | x86 CALL+JMP (0xE8/0xE9) address translation. |
|    120 | `0x3769893F` | ITANIUM  | Itanium bundle branch address translation. |
|     29 | `0x0E06077D` | DELTA    | Multi-channel delta encoding. Channels in R0. |
|    149 | `0x1C2C5DC8` | RGB      | RGB image delta (width in R0, posR in R1). |
|    216 | `0xBC85E701` | AUDIO    | Audio delta (channels in R0). |

Both the CRC32 **and** the byte length must match for fingerprint recognition.
Reference behavior is confirmed by independent RARVM readers, including
`_refs/7zip/CPP/7zip/Compress/Rar3Vm.cpp`.

For standard filters, bytecode identity selects the native transform and runtime
parameters are supplied through the invocation state:

| Filter | Runtime parameters |
|--------|--------------------|
| E8 / E8E9 | No register overrides. `R4` is the block length and `R6` is supplied by the decoder as the output file position when the filter executes. |
| ITANIUM | Same as E8/E8E9: block length in `R4`, output position in `R6`. |
| DELTA | Register override `R0 = channel count`; block length is still `R4`. |
| RGB | Register overrides `R0 = scanline width`, `R1 = posR`; block length is still `R4`. |
| AUDIO | Register override `R0 = channel count`; block length is still `R4`. |

`R3` is initialized to the system-global offset, `R5` is the stored program's
execution count, and `R7` is initialized by the VM to the memory size. Encoders
should not emit overrides for `R3`, `R4`, `R5`, `R6`, or `R7` for the stock
filters; doing so either duplicates decoder-supplied state or risks changing
the transform.

The E8, E8E9, and DELTA filter algorithms are functionally equivalent to the
fixed filter types in RAR 5.0 (see RAR5 spec Section 12). ARM (BL) is RAR 5.0-
only. RGB, AUDIO, and ITANIUM are RAR 3.x-only and were dropped in RAR 5.0.

### 20.4 RARVM Bytecode Specification

The complete VM bytecode and execution semantics are specified in
`RARVM_SPECIFICATION.md`. RARVM was removed in RAR 5.0 in favour of fixed filter
types. All ordinary RAR 3.x/4.x archives created by official WinRAR use only the
standard filters listed above, so a reader that handles the six standard
fingerprints covers normal archives. A generic RARVM interpreter is needed only
for non-standard or hand-crafted RAR 3.x filter programs.

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
