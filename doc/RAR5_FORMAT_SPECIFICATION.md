# RAR 5.0 Archive Format Specification

**Independent documentation derived from publicly available sources:**
- RARLAB's published technote (https://www.rarlab.com/technote.htm)
- 7-Zip source code (LGPL, independently written by Igor Pavlov)

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
- Maximum encoding: 10 bytes for a 64-bit integer. A compliant decoder stops
  accumulating at shift ≥ 64, so bytes 11+ produce undefined results and
  should never be emitted by an encoder.
- Leading `0x80` bytes (zero with continuation set) are **not** valid at the
  start of a value, but **trailing** `0x80`-then-zero patterns are legal:
  encoders sometimes reserve N bytes for a future vint by pre-emitting
  `0x80, 0x80, ..., 0x80, 0x00`, which decodes to `0` — and then backpatch
  the real value before finalizing the archive. See §7 (Locator record) for
  the canonical use case.
- **Zero-offset sentinel.** Fields that carry a vint offset use the value
  `0` as a "not present / reserved space was insufficient" sentinel when
  the encoder pre-allocated space for the field but never wrote it.
- **Decoder overflow rules.** Compatible readers follow these practical
  rules:
  - Uses `Result += (byte & 0x7f) << Shift` (addition, not OR — same
    result for non-overlapping bit fields but subtly different intent).
  - Stops at `Shift >= 64` to avoid C++ undefined behavior on shift overflow.
  - Returns `0` when the vint runs past the data boundary — callers are
    expected to check the byte offset against the buffer length **before**
    interpreting the return value as a valid integer.
- **Encoder consistency.** Always emit the minimum-length vint for a given
  value (no superfluous leading-zero-with-continuation bytes) **except** in
  the reserved-space backpatch scenario. Most clean-room decoders tolerate
  over-long encodings, but producing them wastes space and can trip
  archive-size assertions in stricter readers.

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
| Check Value       | 12 bytes | Password verification. Present if flag `0x0001` set. First 8 bytes are the folded PBKDF2 password-check value, last 4 bytes are a checksum of those 8 bytes. |

When this header is present, every subsequent header is preceded by a 16-byte
AES-256 IV, followed by encrypted header data padded to a 16-byte boundary.
Those subsequent headers are decrypted before their normal Header CRC32 is
checked; the CRC covers the unpadded plaintext header bytes.

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
| Quick Open Offset    | vint | Byte distance from the start of the main archive header block to the Quick Open service block. Present if `0x0001` flag. Zero means "reserved but unset" (see below). |
| Recovery Record Offset | vint | Byte distance from the start of the main archive header block to the recovery record. Present if `0x0002` flag. Zero means "reserved but unset". |

**Offset base.** Both offsets are relative to the byte position of the
**main archive header block**,
not to the archive start. An encoder computes `offset = qo_block_pos -
main_header_pos` at finalization time.

**Backpatch pattern.** The Locator record is the canonical user of vint
space reservation: an encoder emits the main header with the Flags field
set and the offset fields pre-allocated as padding (`0x80, 0x80, ..., 0x00`),
writes the rest of the archive until QO / RR block positions are known,
then seeks back and overwrites the pre-allocated vint bytes with the real
offset. If the reserved space turns out to be too small for the final
offset value, the encoder writes **zero** (a single `0x00` byte) to signal
"unset" — decoders treat this as "Locator not usable for this block, fall
back to a full walk".

**Encoder rule.** Pre-allocate at least 5 bytes (enough for an offset up to
2^35 ≈ 34 GB) for each offset field. Archives beyond that range are
exceptional; for those, pre-allocate 10 bytes to cover the full 64-bit
range. Under-reserving and falling back to zero is correct but defeats
the point of the Locator record.

### Main header emit recipe

Field order an encoder writes for the RAR 5.0 main archive header.
All vint fields use the minimum encoding by default; the
`MHEXTRA_LOCATOR` offsets are the exception — see §7 Locator above for
reservation / backpatch rules. Steps marked **(backpatch)** require
seekback after later data is known.

```
  START = current archive position           # needed for locator offsets
  emit uint32     Header CRC32               # placeholder 0, (backpatch)
  emit vint       Header Size                # placeholder (backpatch after final size known)
  emit vint       Header Type = 1            # main archive header
  emit vint       Header Flags               # OR of common header flags
                                             #   0x0001 if Extra Area present (QO/RR/Metadata)
  if Header Flags & 0x0001:
    emit vint     Extra Area Size            # placeholder (backpatch)
  emit vint       Archive Flags              # OR of archive flags (§7)
  if Archive Flags & 0x0002:                 # volume, not first
    emit vint     Volume Number              # 1-based volume index
  if Header Flags & 0x0001:
    # Extra Area records, each prefixed with size + type vints
    emit_extra_locator()                     # MHEXTRA_LOCATOR (type 0x01)
                                             # QO/RR offsets backpatched later
    emit_extra_metadata_if_any()             # MHEXTRA_METADATA (type 0x02)
    # Extra Area Size (backpatch) = sum of emitted extra records' byte lengths
  # Header Size (backpatch) = all bytes from Header Type start to end of Extra Area
  # Header CRC32 (backpatch) = CRC32 of all bytes from Header Size start to end
```

**Deriving Header Flags (common, not archive):**

- `0x0001` (Extra area present): set if any main-header extra records
  are emitted (typically for multi-volume archives, archives with
  recovery, or archives with metadata).
- `0x0002` (Data area present): **never** set on main header — main
  header has no data area.
- `0x0004` (Skip if unknown when updating): WinRAR 7.x sets this on
  the main header when emitting `MHEXTRA_METADATA` (`-ams`) records,
  so older RAR tools that don't recognise the extras drop the whole
  block during an update rather than mishandle it. Set it whenever
  the header carries records an older reader could misinterpret;
  leave it clear otherwise. Verified against
  `fixtures/7.0/ams_archive_name_rar721.rar` (header flags `0x05`).
- `0x0008` (Split-before — continuation from previous volume):
  never set on main header.
- `0x0010` (Split-after — continues in next volume): never set on
  main header.
- `0x0020` (Child block — depends on preceding file): never set on
  main header.
- `0x0040` (Preserve child block when host modified): never set on
  main header.

**Deriving Archive Flags:**

| Flag     | Set when |
|----------|----------|
| `0x0001` | Archive is a volume of a multi-volume set. |
| `0x0002` | This is not the first volume (so `Volume Number` follows). |
| `0x0004` | At least two files share LZ state (solid). Must agree with per-file solid flags in Compression Information. |
| `0x0008` | At least one `RR` service header is present. |
| `0x0010` | Archive is marked locked. |

`FIRSTVOLUME` equivalence in RAR 5.0 is "volume with flag `0x0001` set
AND `0x0002` clear" — there is no separate `FIRSTVOLUME` bit.

**Field-size pre-reservation for backpatch:** `Header Size` and
`Extra Area Size` are typically small (< 128 → 1 byte vint) but
encoders that dynamically build the Extra Area should pre-reserve at
least 2 bytes for each length field and emit a **non-minimal vint**
when the final value fits in fewer bytes (§11.12.2 covers non-minimal
vint encoding in detail). Under-reserving forces a full archive
shift, which is almost always wasteful.

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
| Data CRC32             | uint32 | CRC32 of unpacked data. For split files (except last part): CRC32 of packed data in this volume. Present if file flag `0x0004`. If the file/service encryption extra record sets `HashMAC`, the final unpacked-data CRC is stored as the key-dependent MAC described in `ENCRYPTION_WRITE_SIDE.md` §5.3; packed-part CRCs remain raw. |
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
| `0x0008` | Unpacked size unknown. The `Unpacked Size` field is still present, but the decoder does not use it as the output limit. |

### Attributes Field (encoder rules)

The `Attributes` vint stores OS-specific file attributes. Layout
depends on the `Host OS` field (`0x0000` Windows, `0x0001` Unix). An
encoder must pick a single host OS per file and use the matching
layout; readers pass the attribute bytes to the extraction host for
interpretation.

**Windows (`Host OS = 0x0000`):**

Store the Windows `FILE_ATTRIBUTE_*` bitmask as a vint. In practice the
value fits in one byte (most files use `0x20` or `0x10 | children`).
The canonical bits are the same as RAR 3.x Windows attrs — see
`RAR15_40_FORMAT_SPECIFICATION.md` §8. The directory bit `0x10` must
be set on directories, in addition to the `File Flags` directory bit.

**Unix (`Host OS = 0x0001`):**

Store the POSIX `st_mode` value as a vint. The low 16 bits carry the
file-type and permission bits using the same layout as RAR 3.x Unix
attrs (§8 of `RAR15_40_FORMAT_SPECIFICATION.md`). Typical values:
`0x81A4` for a regular file with mode 0644, `0x41ED` for a directory
with mode 0755.

#### Directory signaling

Unlike RAR 3.x, directories are detected via the `File Flags` bit
`0x0001` (`FHFL_DIRECTORY`) — not via the window
field in compression information. Encoder rules:

1. Set `File Flags & 0x0001 = 1`.
2. Set `Attributes` to the platform-appropriate directory value (`0x10`
   on Windows, `0x4000 | perms` on Unix) for correct attribute
   restoration.
3. Omit the data area (`Header Flags & 0x0002` cleared) — directories
   have no compressed content.

#### Symlinks, junctions, hardlinks

RAR 5.0 moved filesystem redirections out of the `Attributes` field and
into the File System Redirection extra record (type `0x05`, §8 below).
An encoder emitting a symlink does **not** set `(Attributes & 0xF000)
== 0xA000` as RAR 3.x required — that encoding was Unix-host
conventional but is not the canonical RAR 5.0 form. Instead:

1. Set `Attributes` to a regular-file mode (e.g., `0x81A4`). Symlink
   permission bits have no effect on extraction; readers recreate the
   link with the OS default.
2. Omit the data area (symlinks have no payload in RAR 5.0).
3. Add an extra record of type `0x05` carrying the target path. See
   the File System Redirection Record table below.
4. Do not set `FHFL_DIRECTORY` — a symlink is its own entry type, even
   if the link target is a directory. If the target is a directory, set
   the redirection record's `Flags & 0x0001` (`FHEXTRA_REDIR_DIR`)
   instead.

The redirection types from `FSREDIR_*` (`headers.hpp:110`):

| vint   | Constant              | Notes |
|--------|-----------------------|-------|
| `0x01` | `FSREDIR_UNIXSYMLINK` | Unix symlink (`symlink(2)`). |
| `0x02` | `FSREDIR_WINSYMLINK`  | Windows symlink (`CreateSymbolicLink`). |
| `0x03` | `FSREDIR_JUNCTION`    | NTFS junction (directory reparse point). |
| `0x04` | `FSREDIR_HARDLINK`    | Hardlink to a prior file in the archive. |
| `0x05` | `FSREDIR_FILECOPY`    | Copy of a prior file's data (saves space for duplicates). |

For `FSREDIR_HARDLINK` and `FSREDIR_FILECOPY`, the `Name` field names a
file **already extracted from the archive**, not a filesystem path. The
encoder must emit the source file earlier in the archive stream. Resolution
rules verified against `_refs/unrar/extract.cpp:830-855` and
`ExtractFileCopy` (extract.cpp:1088):

- **String comparison is verbatim**, byte-for-byte after slash conversion.
  No case-folding, no Unicode normalization. A reference to `Foo/bar.txt`
  does *not* match an entry stored as `foo/bar.txt`, even on
  case-insensitive host filesystems. Encoders must emit the reference
  exactly as the source entry's `Name` field appears.
- **Slash normalization is one-directional**: backslashes in `RedirName`
  are converted to forward slashes during parse (`SlashToNative`), so
  encoders may use either form on the wire and the reader will accept
  both. Encoder convention since RAR 5.10 is forward-slash internally.
- **The source must be extracted to disk by the time the link is
  processed.** If the source was skipped (filter, error, user choice) the
  link extraction fails. There is no in-archive "virtual reference"
  fallback; the link is a filesystem operation against the on-disk source.
- **Cross-volume references work** as long as the source-file's data has
  been written before the link record is processed. Solid-mode and
  multi-volume splits don't affect this — they only affect the order in
  which the bytes hit disk, which is already serialized by the read-side
  walk.
- **`FSREDIR_FILECOPY` may use temporary-source caching** (`RefList` in
  extract.cpp): if the source was extracted to a temp file (because the
  user requested no permanent extraction) the file copy can rename or
  copy from that temp. This is an optimization, not a wire-format effect.
- **`DirTarget` flag (`0x0001`)** is informational for symlinks/junctions
  only; hardlinks and file-copies are always file-to-file regardless.

#### Unix ownership preservation

Numeric UID/GID and user/group name strings are carried in the Unix
Owner extra record (type `0x06`, §8). `Attributes` alone cannot
preserve ownership — an encoder that wants `chown`-equivalent
round-tripping must emit a `0x06` record.

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

| Name | Constant              | Purpose |
|------|-----------------------|---------|
| CMT  | `SUBHEAD_TYPE_CMT`    | Archive comment |
| QO   | `SUBHEAD_TYPE_QOPEN`  | Quick open data |
| ACL  | `SUBHEAD_TYPE_ACL`    | NTFS security descriptor (Windows ACL) |
| STM  | `SUBHEAD_TYPE_STREAM` | NTFS alternate data stream |
| RR   | `SUBHEAD_TYPE_RR`     | Recovery record |

RAR 3.x additionally defined `UOW` (Unix owner), `AV` (authenticity
verification), and `EA2` (OS/2
extended attributes) as service types. These are **not used in RAR
5.0** — Unix ownership moved to a per-file extra record
(`FHEXTRA_UOWNER`, type `0x06`), and AV / EA2 were dropped. An encoder
writing RAR 5.0 should not emit service headers with those names.

#### Per-service payload encoding

Service headers reuse the file-header layout (§8 above). The following
table summarizes what each service carries, where:

| Service | Data area (compressed payload) | `FHEXTRA_SUBDATA` (type `0x07`) | Extraction path |
|---------|--------------------------------|----------------------------------|-----------------|
| `CMT`   | UTF-8 comment bytes; compressed or stored via normal `Compression Information`. | — | Reader scans for a `CMT` service header and decompresses the data area. |
| `QO`    | Quick-open index (serialized file-header copies with offsets). See `ARCHIVE_LEVEL_WRITE_SIDE.md`. | — | Decoder reads data area via the main-header locator pointer (`MHEXTRA_LOCATOR_QLIST`). |
| `ACL`   | NTFS `SECURITY_DESCRIPTOR` as bytes, attached to the **preceding** file entry. | — | Reader restores ACL metadata for the target file. |
| `STM`   | Alternate-data-stream contents; stream name stored in the service header's `Name` field as `:StreamName:$DATA`. | — | Reader restores the named alternate stream. |
| `RR`    | Reed-Solomon parity bytes (see `INTEGRITY_WRITE_SIDE.md`). | Single vint: recovery percent (1 byte through RAR 6.02, vint since 6.10). | Reader uses the main-header locator pointer (`MHEXTRA_LOCATOR_RR`) if present, else scans for the `RR` service. |

ACL and STM are **per-file** metadata: the encoder must emit them
immediately after the file they describe, and the file name in the
service header's `Name` field should match (or be a stream-name suffix
of) the target file. CMT, QO, and RR are **archive-wide** and are
typically emitted near the end of the archive (QO and RR must be
locatable via the main-header locator extra record — see
`MHEXTRA_LOCATOR` below).

#### Extra Area records valid inside service headers

Compatible readers parse the same extra-record types in both `HEAD_FILE`
and `HEAD_SERVICE` blocks. Not all are semantically meaningful on a
service. Encoder rules:

| Type   | On `HEAD_FILE` | On `HEAD_SERVICE` | Notes |
|--------|----------------|-------------------|-------|
| `0x01` `FHEXTRA_CRYPT`   | ✅ | ✅ | Required if the service's data area is encrypted. |
| `0x02` `FHEXTRA_HASH`    | ✅ | ✅ | BLAKE2sp of the service data area. Recommended for CMT, QO, ACL, STM. |
| `0x03` `FHEXTRA_HTIME`   | ✅ | rare | Service headers rarely carry high-precision times; encoder may omit. |
| `0x04` `FHEXTRA_VERSION` | ✅ | ❌ | File-only. Services have no file-version semantics. |
| `0x05` `FHEXTRA_REDIR`   | ✅ | ❌ | File-only. A service header is not a redirectable filesystem entry. |
| `0x06` `FHEXTRA_UOWNER`  | ✅ | ❌ | File-only. Service headers have no ownership. |
| `0x07` `FHEXTRA_SUBDATA` | ❌ | ✅ | Service-only. Carries service-specific parameters (e.g., RR percent). |

The "valid on HEAD_SERVICE" column reflects reader behavior plus semantic
meaning. A decoder should not crash on a misplaced record, but an encoder
that writes, say, `FHEXTRA_REDIR` on a service header produces a
nonsensical archive: services are not extracted as filesystem links.

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
| Flags        | vint     | `0x0001`: password check data present. `0x0002`: `HashMAC` / tweaked checksums (CRC32 and BLAKE2sp fields are stored as key-dependent MACs). |
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
| Hash Data | 32 bytes | BLAKE2sp hash (for type 0x00), or its `HashMAC` conversion if the enclosing file/service encryption record sets flag `0x0002`. |

For split files (except last part): hash of packed data in current volume.
For non-split files and last parts: hash of unpacked data.

#### File Time Record (type 0x03)

| Field             | Type            | Present when |
|-------------------|-----------------|--------------|
| Size              | vint            | always |
| Type              | vint            | always (= 0x03) |
| Flags             | vint            | always |
| mtime             | uint32 or uint64 | flag `0x0002` set |
| ctime             | uint32 or uint64 | flag `0x0004` set |
| atime             | uint32 or uint64 | flag `0x0008` set |
| mtime nanoseconds | uint32          | flags `0x0001` + `0x0002` + `0x0010` all set |
| ctime nanoseconds | uint32          | flags `0x0001` + `0x0004` + `0x0010` all set |
| atime nanoseconds | uint32          | flags `0x0001` + `0x0008` + `0x0010` all set |

**Time flags:**

| Flag     | Meaning |
|----------|---------|
| `0x0001` | Unix `time_t` format (else Windows FILETIME). |
| `0x0002` | Modification time present. |
| `0x0004` | Creation time present. |
| `0x0008` | Last access time present. |
| `0x0010` | Nanosecond precision (Unix time only — flag `0x0001` must also be set). |

**Width per timestamp** (verified against `_refs/unrar/arcread.cpp:1128-1158`):

| `0x0001` (Unix) | `0x0010` (Nanos) | Per-timestamp encoding |
|:---:|:---:|:---|
| 0 | 0 | uint64 (Windows FILETIME, 100-ns ticks since 1601-01-01 UTC) |
| 0 | 1 | uint64 (Windows FILETIME — nanosecond flag without Unix flag is **ignored**) |
| 1 | 0 | uint32 (Unix `time_t` seconds since 1970-01-01 UTC) |
| 1 | 1 | uint32 seconds **followed by** uint32 nanoseconds (low 30 bits used; values ≥ 1,000,000,000 are clamped/rejected) |

Nanosecond fields appear in the same per-time order (mtime, ctime, atime) and
**only after** all the seconds fields, not interleaved. So the on-disk layout
when all three times + nanoseconds are present is: `mtime_sec ctime_sec
atime_sec mtime_ns ctime_ns atime_ns`.

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
| Header Flags       | vint   | Common header flags (§4). |
| End of Archive Flags | vint | See below. |

**End of Archive Flags (RAR 5.0):**

| Bit | Name | Meaning |
|---|---|---|
| `0x0001` | `EHFL_NEXTVOLUME` | Archive is a volume and is **not** the last in the set. |

Only bit `0x0001` is defined for RAR 5.0. The legacy `DATACRC`,
`REVSPACE`, and `VOLNUMBER` flags that existed in RAR 2.x/3.x
(`RAR15_40_FORMAT_SPECIFICATION.md` §7) are **not** used in RAR 5.0.
Volume numbering in RAR 5.0 is carried on the main header, not the end
marker.

RAR does not read anything past this header. An encoder must emit exactly
one end-of-archive header per volume, as the last block of the volume,
after all file headers, service headers, and recovery records.

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

Data is uncompressed (method = 0). Contains an array of wrapper records, one
per cached header:

| Field        | Type           | Description |
|--------------|----------------|-------------|
| CRC32        | uint32         | CRC32 (CRC50 variant) of the wrapper body that follows (Flags + Offset + HeaderSize + HeaderData). |
| BlockSize    | vint           | Length of the wrapper body in bytes. Max encoding width 3 bytes. |
| Flags        | vint           | Currently 0. |
| Offset       | vint           | **Backward** delta from the start of this QO service header back to the cached header's archive position: `Offset = QOHeaderPos - cachedHeaderPos`. Offsets within the payload are monotonically *decreasing* in iteration order (cached headers appear in archive order, so they sit progressively further behind QO). |
| HeaderSize   | vint           | Size of the cached header in bytes. Must be ≤ `MAX_HEADER_SIZE_RAR5` (0x200000). |
| HeaderData   | HeaderSize bytes | Verbatim copy of the original cached header. |

Verified against `_refs/unrar/qopen.cpp` (`ReadRaw` / `ReadNext`) and
described from the writer's perspective in `ARCHIVE_LEVEL_WRITE_SIDE.md` §4.1.

**Security note:** Use the same access pattern (quick open vs. direct) for both
displaying and extracting files. Divergence could allow showing one filename while
extracting different content.

---

## 11. Compression Algorithm

RAR5 uses an LZ-based compression scheme with Huffman coding. The compressed data
stream is organized into blocks, each containing Huffman tables followed by
compressed symbols.

### 11.1 Decoder Properties

The decoder receives 2 bytes of properties (7-Zip `Rar5Decoder::SetProperties`
API — not the wire format; see §8 "Compression Information" for the
on-disk CompInfo vint):

| Byte | Field |
|------|-------|
| 0    | Dictionary size exponent (power). |
| 1    | Bits 3-7: dictionary size fraction. Bit 0: solid flag. Bit 1: algorithm version (0 = Unpack50, 1 = Unpack70). |

Dictionary size = `(fraction + 32) << (power + 12)` bytes.

The minimum effective window size is 256 KB (`1 << 18`).

**Unpack70 vs Unpack50 — the only wire-format difference is the
distance-alphabet size.** Verified against `_refs/unrar/unpack.cpp:184`
(`ExtraDist = (Method == VER_PACK7)`) and `_refs/unrar/compress.hpp:24-29`:

| Algorithm | Distance alphabet | Concatenated table size | Max dictionary |
|-----------|------------------|------------------------|----------------|
| Unpack50 (algo 0) | `DCB = 64` slots | `HUFF_TABLE_SIZEB = NC + DCB + RC + LDC` | 4 GiB |
| Unpack70 (algo 1) | `DCX = 80` slots | `HUFF_TABLE_SIZEX = NC + DCX + RC + LDC` | 1 TiB |

Everything else — the block grammar, the Main alphabet, the level table
(§11.3), match-length decoding (§11.5), the distance encoding formula
(§11.6), the length-bonus table (§11.7), the repeat-distance buffer
(§11.8), the filter set (§12) — is identical. A single decoder
implementation switches on the algorithm bit only when reading the
Distance code-length section of the level table and when interpreting
distance slots ≥ 64.

For very large dictionaries (>~256 MiB) where a single contiguous buffer
allocation may fail, unrar uses a `FragmentedWindow`
(`_refs/unrar/unpack50frag.cpp`) that splits the window across multiple
heap blocks. This is purely an allocation strategy — the wire format
sees a flat circular window in either case.

### 11.2 Compressed Block Structure

Each compressed block begins with a header read from the byte-aligned bitstream:

| Field      | Size             | Description |
|------------|------------------|-------------|
| Flags      | 1 byte           | See below. |
| Checksum   | 1 byte           | XOR checksum: `checksum ^ flags ^ blocksize_bytes...` must equal `0x5A`. |
| Block Size | 1-3 bytes        | Little-endian byte count for the payload following this header. Number of size bytes determined by `(flags >> 3) & 3` (0 = 1 byte, 1 = 2 bytes, 2 = 3 bytes; value 3 is invalid). |

**Flag bits:**

| Bits  | Meaning |
|-------|---------|
| 0-2   | Payload end bit position inside the final stored byte: `flags_low3 = (payload_bits - 1) & 7`. The decoder computes `blockSizeBits7 = (flags & 7) + 1`, carries value 8 into the byte count, then masks to `0..7`. |
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
`N + 2` entries, starting at the current table position, are zero (run-length
of zeros). A following count of zero means the current entry is literal bit
length 15.

This level table is used to Huffman-decode the bit lengths for the four main tables.

#### Main Tables

The level decoder is used to read code lengths for all four tables concatenated:

| Table          | Size (symbols)    | Purpose |
|----------------|-------------------|---------|
| Main           | 306             | Literals + match length slots + control symbols. |
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
- 17: repeat previous bit length. Run count = `11 + read_bits(7)` (i.e., 11-138 times).
- 18: set to zero. Run count = `3 + read_bits(3)` (i.e., 3-10 times).
- 19: set to zero. Run count = `11 + read_bits(7)` (i.e., 11-138 times).

Symbols 16 and 17 are invalid before any literal bit length has been decoded,
because there is no previous value to repeat. Encoders should split longer runs
into the fixed level symbols above; the count is not encoded by adding to the
symbol number.

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

**End-of-block / end-of-stream signalling.** Unlike DEFLATE, the Main alphabet
has **no end-of-block symbol**. The decoder consumes symbols until it has
read all the bits announced by the block header (`Block Size` bytes, with
sub-byte precision from the bit-position field in the flags). On reaching
that boundary the decoder either reads the next block header (if the current
block did not have the "last block in file" flag set) or stops (if it did).
Verified against `_refs/unrar/unpack50.cpp` (`Unpack5` main loop, lines 14-47).
A reader must therefore track `BlockBitSize` exactly — overshooting by even
one symbol will mis-align all subsequent block headers.

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

Four repeat distances are maintained (`OldDist[0..3]`), initially set to the
all-ones sentinel `(size_t)-1` (`0xFFFFFFFF` on 32-bit, `0xFFFFFFFFFFFFFFFF`
on 64-bit). This is larger than any valid distance for any supported
dictionary size, so referencing an uninitialized slot via symbols 258-261
before a real match has been recorded yields an out-of-window read that the
decoder must reject. `LastLength` (used by symbol 257) is initially `0`, which
causes symbol 257 to be ignored until a match has been emitted. Verified
against `_refs/unrar/unpack.cpp` (`UnpInitData`).

When a new match is used (sym >= 262), the distances shift right and the new distance
enters at position 0.

When a repeat match is used (sym 258-261), the selected distance moves to position 0
and the others shift to fill the gap.

### 11.9 Solid Mode

In solid mode, the dictionary window, repeat distances, `_lastLen`, and current
Huffman tables carry over from the previous file's decompression. Pending filter
state does not carry: filter queues and filter-overlap tracking reset at every
file boundary, including solid continuations.

The archive stores the solid flag in the file header Compression Information
field (bit 6). The decoder property byte used by the internal Unpack50/70 codec
sets bit 0 only when this file is a valid solid continuation. Service headers do
not participate in the file solid chain, and the first file in a solid group must
be written with the solid flag clear.

When starting a non-solid file (or the first file in a solid group), the window
position is reset, repeat distances are reset to invalid, `_lastLen` is cleared,
and the Huffman tables are marked absent.

### 11.10 Dictionary Size Limits

The maximum dictionary size depends on the platform pointer size:
- 32-bit: up to 2^31 bytes (2 GB).
- 64-bit: up to 2^40 bytes (1 TB).

### 11.11 Encoder (RAR 5.0 LZ compressor)

RAR 5.0 is structurally the cleanest RAR LZ format to encode: byte-aligned
block headers with a simple XOR checksum, four standalone Huffman tables
built per-block, length encoded directly into the Main symbol (no ambiguous
"repeat vs new" symbol overload like RAR 3.x's 256), and a small fixed set
of hardcoded filters instead of the RAR 3.x RARVM. A RAR 5.0 encoder that
shares its match finder (`LZ_MATCH_FINDING.md`) and Huffman construction
(`HUFFMAN_CONSTRUCTION.md`) with a RAR 3.x encoder is only a few hundred
lines of version-specific logic.

Primary reference: `_refs/7zip/CPP/7zip/Compress/Rar5Decoder.cpp` (~2060
lines, independent reader). Filter encoding parameters are trivial and
covered in §11.11.8 below.

#### 11.11.1 Encoder pipeline

```
for each block in the file (at encoder-chosen boundaries):
    1. run match finder over block_size input bytes → token stream
    2. count Main(306), Distance(64/80), Length(44), Align(16) frequencies
    3. build four Huffman tables via HUFFMAN_CONSTRUCTION §3, maxLen = 15
    4. RLE-pack the concatenated lens[] against last-block baseline (§11.3)
    5. write block header (1 byte flags + 1 byte checksum + 1-3 size bytes)
    6. emit 20-symbol level Huffman (raw 4-bit lengths + RLE level codes)
    7. emit payload tokens as canonical codes
    8. stash lens[] for next block's baseline
```

Block size is an encoder choice bounded by the 24-bit stored byte-count field:
at most `2^24 - 1` bytes of encoded payload. In practice 64–256 KB of *input*
per block balances table overhead vs local adaptation.

#### 11.11.2 Block header (byte-aligned!)

This is the one place where RAR 5.0 is byte-aligned rather than bit-packed.
Before writing the header, the encoder must flush its bit buffer to a byte
boundary (pad trailing bits with zero). Then write:

```
assert payload_bit_size > 0
payload_bytes = (payload_bit_size + 7) >> 3
numSizeBytes = 0 if payload_bytes <= 0xff else
               1 if payload_bytes <= 0xffff else 2
flags_low3 = (payload_bit_size - 1) & 7
flags    = flags_low3                  # bits 0..2: final-byte bit position
         | (numSizeBytes << 3)          # bits 3..4: 0..2 (size is 1..3 bytes)
         | (last_block ? 0x40 : 0)      # bit 6
         | (tables_present ? 0x80 : 0)  # bit 7
block_size_bytes = encode_le(payload_bytes, numSizeBytes + 1)
checksum = 0x5A ^ flags
for b in block_size_bytes: checksum ^= b
emit byte: flags
emit byte: checksum
emit bytes: block_size_bytes
```

Where `payload_bit_size` is the total number of payload bits that will follow
(level table + RLE-packed lens + token stream), `payload_bytes` is the rounded-up
stored byte count, and `flags_low3` preserves the exact final bit position.

**Chicken-and-egg.** The header contains the size of its own payload, but
the payload can only be sized after encoding it. Two resolutions:

1. **Two-pass:** encode the block into a scratch buffer, measure bits, write
   header with the known size, copy the scratch buffer out.
2. **Reserve max header:** always emit `numSizeBytes = 2` (3-byte size, max
   24 bits of payload size), backpatch after payload. Wastes 0–2 bytes per
   block but allows single-pass encoding.

Pass 2 matches common decoder-side buffering. Pass 1 is cleaner for small
blocks.

**Checksum gotcha.** The XOR includes the flags byte and all size bytes, but
**not** the checksum byte itself. The decoder validates
`0x5A ^ flags ^ size_bytes... ^ checksum == 0`, which is equivalent to
`checksum = 0x5A ^ flags ^ size_bytes...`. Easy to get wrong if you XOR in
the checksum byte during construction.

#### 11.11.3 Huffman table emission

The 20-symbol level table encodes the concatenated code lengths of Main
(306) + Distance (64 or 80) + Align (16) + Length (44) = 430 (v6) or 446
(v7) lengths. Emission is identical to RAR 3.x §18.8.2 step 4 — see
`HUFFMAN_CONSTRUCTION.md` §5.3 for the RLE symbol table.

The level alphabet is fixed at 20 symbols. Symbols 16 and 17 repeat the
previous decoded length with short and long counts; symbols 18 and 19 emit
short and long zero runs. See §11.3 for the exact extra-bit counts. Do not
encode the run count by adding it to the symbol number.

#### 11.11.4 LZ token selection

Main table alphabet (306 symbols):

| Match / literal | Main symbol | Extra fields |
|---|---|---|
| Literal byte `b` | `b` (0..255) | — |
| Filter trigger | 256 | Filter type + params (§11.11.8) |
| Last-length match (uses `rep0` and `_lastLen`) | 257 | — |
| Repeat distance `k` (k ∈ 0..3) | 258 + k | Length slot via Length table |
| New match | 262 + length_slot | Raw length extra bits + Distance table slot + distance extras |

The Main table carries both literals and the **length slot** for new matches
— so the encoder must pick the length slot, look up its base length via
§11.5, and encode the raw extra bits after the Main code:

```
# New match of length `len` (len ≥ 2), distance `dist`:
len_enc = len - 2
if len_enc < 8:
    len_slot = len_enc
    len_extra_bits = 0
    len_extra = 0
else:
    numBits = floor(log2(len_enc)) - 2        # inverse of slot >> 2 - 1
    len_slot = (numBits + 1) * 4 + ((len_enc >> numBits) & 3)
    len_extra_bits = numBits
    len_extra = len_enc & ((1 << numBits) - 1)
main_symbol = 262 + len_slot
```

For repeat matches (symbols 258–261), the length is encoded through the
**Length table** (44 symbols) using the same slot formula, *not* baked into
the Main symbol. Encoder emits:

```
emit Main code for 258 + rep_index
emit Length code for len_slot
emit raw extra bits as above
```

Symbol 257 (last-length match) has no extras at all — it's a 1-symbol way
to express "exactly the previous `(len, dist)` pair again". The encoder
should emit it whenever the best-match at the current position is the
previous `_lastLen` bytes from `rep0`; this is the cheapest token in the
entire alphabet and worth explicit check during parsing.

#### 11.11.5 Distance encoding

For a chosen `dist ≥ 1`, compute `dist_enc = dist - 1`, then:

```
if dist_enc < 4:
    dist_slot = dist_enc
    num_extras = 0
else:
    numBits = floor(log2(dist_enc)) - 1
    dist_slot = (numBits + 1) * 2 + ((dist_enc >> numBits) & 1)
    num_extras = numBits
extras = dist_enc - slot_base(dist_slot)      # low num_extras bits
```

The slot base is `(2 | (dist_slot & 1)) << numBits` where
`numBits = (dist_slot - 2) >> 1`.

Then emit:

```
emit Distance code for dist_slot
if num_extras < 4 or align_mode_inactive:
    emit raw bits(extras, num_extras)
else:
    # Split into high-bits raw + low-4-bits via Align Huffman
    high = extras >> 4
    low  = extras & 0xF
    emit raw bits(high, num_extras - 4)
    emit Align code for low
```

**Align mode.** Active when any Align code length differs from 4 (i.e. the
encoder is actually using a biased distribution over low nibbles). An
encoder that never biases can set all 16 Align lengths to 4, which makes
the decoder go through the direct-bit path and skips the Align table
lookup. Biased Align coding wins ~1–3% on pointer-aligned binaries (same
optimization as RAR 3.x's LowOffset).

When align mode is active, every distance with `num_extras ≥ 4` *must*
split. Half-splitting some distances and not others is not representable —
the decoder's align-mode flag is block-global.

#### 11.11.6 Length bonus inverse (§11.7)

The decoder adds `{0,0,0,0,0,0,0,1,1,1,1,1,2,2,2,2,2,3,3,3,...}[numBits]`
to match length after distance decoding. The encoder must **subtract** this
bonus from the actual match length *before* computing the length slot,
otherwise the slot is off by 1–3.

```
actual_len = match finder output
numBits = distance_numBits(dist)
encoder_len = actual_len - length_bonus[numBits]
# proceed to encode encoder_len via §11.11.4
```

Same-structure trap as RAR 2.0 §16.11.2 and RAR 3.x §18.8.4, but the bonus
table is indexed by `numBits`, not by distance thresholds. Easy to miss
because the RAR 3.x bonus looks similar at a glance.

#### 11.11.7 Repeat distance handling

Identical to RAR 3.x §18.8.6: rotate on rep match, shift on new match.

```
on new match (dist, len):
    _reps[3] = _reps[2]
    _reps[2] = _reps[1]
    _reps[1] = _reps[0]
    _reps[0] = dist
    _lastLen = len

on repeat match (symbol 258 + k):
    selected = _reps[k]
    remove _reps[k], insert at position 0
    _lastLen = len

on symbol 257 (last-length):
    # reuses _reps[0] and _lastLen unchanged
    pass
```

`_lastLen` is set by every match and read only by symbol 257. An encoder
that fails to update `_lastLen` on rep matches silently corrupts future
symbol-257 emissions.

#### 11.11.8 Filter emission (RAR 5.0 hardcoded filters)

Unlike RAR 3.x RARVM bytecode, RAR 5.0 filters are a fixed enum with tiny
parameter blocks. See §12 of this spec for the parameter layout. The
encoder decides *when* to emit a filter — see the Filter Detection
Heuristics items in `IMPLEMENTATION_GAPS.md` — and emits:

```
emit Main code for symbol 256
emit ReadUInt32-format filter_offset (relative to current window position)
emit ReadUInt32-format filter_length (how many bytes the filter covers)
emit 3 bits: filter_type   # 0=DELTA 1=E8 2=E8E9 3=ARM
if filter_type == DELTA:
    emit 5 bits: channel_count - 1
```

A filter-agnostic encoder can skip filter emission entirely — RAR 5.0's
decoder handles a filter-free stream correctly. Loss is 5–15% on x86
binaries and 20–30% on multi-byte PCM-like data.

#### 11.11.9 Solid mode across files

When the solid flag is set on a file, the encoder:

- Does **not** reset `_reps[0..3]`, `_lastLen`, the window, or Huffman
  tables at the file boundary.
- Does reset pending filter state at the file boundary; RAR5 filters cannot
  span several solid files.
- May emit the first block of the new file with `table_present = 0` if the
  previous block's tables are still accurate for the new file's statistics.
  In practice, re-emitting tables at each file boundary is simpler and only
  costs 100–500 bytes per file.

The solid flag appears in the file header compression info bit 6; the matching
codec property bit is derived from that flag only for valid file continuations.
Encoders must write the first file of a solid group with bit 6 clear, set bit 6
only on subsequent regular files in the group, and emit the files in one
continuous compressed stream without gaps. Service headers must not be marked
solid.
See the Solid Archives item in `IMPLEMENTATION_GAPS.md`.

#### 11.11.10 Algorithm version 0 (Unpack50) vs version 1 (Unpack70)

RAR 7.0 did not introduce a new archive format — the signature, block
framing, extra-record scheme, and encryption are all unchanged from RAR
5.0. The single on-wire difference is the per-file **algorithm version**
stored in bits 0–5 of the Compression Information vint (§8):

| Algorithm version | Emitted by | Decoder | Distance table | Dict encoding | Dict max |
|-------------------|------------|---------|----------------|---------------|----------|
| 0 | RAR 5.0 / 6.x | Unpack50 | 64 symbols | power only (bits 10–14) | `128 KB << power`, ≤ 4 GB (power ≤ 15) |
| 1 | RAR 7.0+ | Unpack70 | 80 symbols | power + 5-bit fraction (bits 15–19) | up to 1 TB (power ≤ 23) |

Archive-level flags do **not** carry a "RAR 7.0" marker. The encoder
chooses per file and the decoder switches codepaths based on the file's
CompInfo bits 0–5. An archive may contain a mix of v0 and v1 files —
most commonly when appending new files to an existing v0 solid stream.

##### Version selection rule

```
def select_algo_version(file):
    # v1 is required for any of these:
    if file.dict_size > 4 * GB:           return 1
    if file.dict_size not in power_of_2:  return 1   # needs fraction
    if file.target_decoder == "RAR 7.0+": return 1   # user-forced
    # Otherwise v0 — widest compatibility (any RAR 5.0+ decoder).
    return 0
```

##### Encoding the dictionary size (CompInfo bits 10–19)

```
# Decompose requested window size into base power + fractional offset.
# Dictionary size (v1) = (128 KB << power) + (128 KB << power) * fraction / 32
base_power = floor(log2(dict_size / 128 KB))          # bits 10–14
base_size  = 128 KB << base_power
fraction   = round((dict_size - base_size) * 32 / base_size)   # bits 15–19
assert 0 <= fraction <= 31

# v0 archives MUST emit fraction = 0 and power ≤ 15.
# v1 archives MAY emit any (power, fraction) pair with power ≤ 23.
```

The v0 decoder interprets bits 15–19 as reserved zero; an encoder
targeting v0 readers must not set them.

##### Appending v1 files to a v0 solid stream — bit 20 (RAR5_COMPAT)

When an encoder extends an existing v0 solid archive with a new file
and the cumulative dictionary now exceeds what the v0 encoding can
express (e.g. a non-power-of-2 size after a dictionary bump), it emits
the new file with **algorithm version 0 in bits 0–5** but **bit 20
(`FCI_RAR5_COMPAT`) set**. The decoder then:

- Runs the Unpack50 algorithm (64-symbol Distance table, no new
  opcodes).
- Reads the dictionary size using the v1 encoding (bits 10–19 with
  fraction), allowing a size that doesn't round to a v0 power-of-2.

Clean-room encoders emitting fresh archives never need bit 20 — it
exists purely for in-place append compatibility. Leave it clear when
generating archives from scratch.

##### Distance table extension (Unpack70 only)

For v1 blocks the Distance alphabet grows from 64 to 80 codes. The
slot-to-distance formula in §11.6 is unchanged — slot 79 just plugs
into the same `numBits = (slot >> 1) - 1` and
`base = ((2 | (slot & 1)) << numBits) + 1` expressions, which produce
larger values at the high slots without any special case. Concretely:

| Slot | numBits | decoded distance range |
|------|---------|------------------------|
| 63   | 30      | `0xC0000001` .. `0x100000000` (3 GB + 1 .. 4 GB) |
| 64   | 31      | `0x100000001` .. `0x180000000` (4 GB + 1 .. 6 GB) |
| 65   | 31      | `0x180000001` .. `0x200000000` (6 GB + 1 .. 8 GB) |
| 66   | 32      | `0x200000001` .. `0x300000000` (8 GB + 1 .. 12 GB) |
| 67   | 32      | `0x300000001` .. `0x400000000` (12 GB + 1 .. 16 GB) |
| 68   | 33      | `0x400000001` .. `0x600000000` (16 GB + 1 .. 24 GB) |
| 69   | 33      | `0x600000001` .. `0x800000000` (24 GB + 1 .. 32 GB) |
| 70   | 34      | `0x800000001` .. `0xC00000000` (32 GB + 1 .. 48 GB) |
| 71   | 34      | `0xC00000001` .. `0x1000000000` (48 GB + 1 .. 64 GB) |
| 72   | 35      | `0x1000000001` .. `0x1800000000` (64 GB + 1 .. 96 GB) |
| 73   | 35      | `0x1800000001` .. `0x2000000000` (96 GB + 1 .. 128 GB) |
| 74   | 36      | `0x2000000001` .. `0x3000000000` (128 GB + 1 .. 192 GB) |
| 75   | 36      | `0x3000000001` .. `0x4000000000` (192 GB + 1 .. 256 GB) |
| 76   | 37      | `0x4000000001` .. `0x6000000000` (256 GB + 1 .. 384 GB) |
| 77   | 37      | `0x6000000001` .. `0x8000000000` (384 GB + 1 .. 512 GB) |
| 78   | 38      | `0x8000000001` .. `0xC000000000` (512 GB + 1 .. 768 GB) |
| 79   | 38      | `0xC000000001` .. `0x10000000000` (768 GB + 1 .. 1 TB) |

Worked example — encoding distance `0xC000000042` (≈ 768 GB + 66):

```
d = 0xC000000042
# dist_enc = d − 1
dist_enc = 0xC000000041
# numBits = floor(log2(dist_enc)) − 1 = 39 − 1 = 38
# Wait: bit 39 of 0xC000000041 is set (bit 39 = 0x8000000000),
# so floor(log2) = 39 and numBits = 39 − 1 = 38.  Correct.
slot    = (numBits + 1) * 2 + ((dist_enc >> numBits) & 1)
        = 39 * 2 + ((0xC000000041 >> 38) & 1)
        = 78 + 1 = 79                                   # slot 79
# The 38 extra bits split into 4 low bits (LowDist / Align code) and
# 34 high bits written straight to the main bitstream.
extras  = dist_enc − ((2 | (slot & 1)) << numBits)
        = 0xC000000041 − 0xC000000000
        = 0x41                                          # 65
high_bits = extras >> 4   = 0x4         # 34 bits, written into bitstream
align     = extras & 0xF  = 0x1         # LowDist, decoded via Align Huffman
```

Other encoder-side changes vs Unpack50:

- **Huffman alphabet sizes** in the Decoder-Property table: the Distance
  Huffman block carries 80 code lengths instead of 64. Level/Main/Align/
  Length-Remainder tables are unchanged.
- **Length bonus** (§11.7) is indexed by `numBits`. The published table
  has 40 entries and saturates at `3` from index 17 onward, so it covers
  Unpack70's max `numBits = 38` unchanged. Encoders that prefer a
  slot-indexed bonus table (one lookup per token instead of two) size
  it at 80 entries and fill slots 64–79 with `3` — equivalent to "append
  16 entries of value 3" when growing a 64-entry slot-indexed table.
- **No changes** to the slot formula, length alphabet, rep-distance
  buffer, filter opcodes, block framing, or EOF semantics.

##### Wire-format parity

| Field | v0 value | v1 value |
|-------|----------|----------|
| CompInfo bits 0–5 | `0` | `1` |
| CompInfo bits 10–14 (dict power) | ≤ 15 | ≤ 23 |
| CompInfo bits 15–19 (dict fraction) | 0 | 0–31 |
| CompInfo bit 20 (RAR5_COMPAT) | 0 | 0 (normal) / 1 (legacy append) |
| Distance table | 64 symbols | 80 symbols |
| Block flags, level table, Main/Length/Align tables | identical | identical |

See Appendix D for the historical summary.

#### 11.11.11 Test oracle

1. Round-trip every encoded block through at least one independent RAR 5.0
   decoder and assert byte-exact recovery.
2. Verify Kraft equality on all four Huffman tables (Main, Distance,
   Length, Align).
3. Test all token types: pure literals, run of rep0 (symbol 257 heavy),
   rep-dist rotation sequence, align-mode on/off, large-distance split,
   last-block flag, multi-block file, v6 and v7 output.
4. Cross-check block header checksums with the decoder's XOR validation.
5. Verify `_lastLen` / `_reps[]` state after every token type matches the
   decoder's shadow.

#### 11.11.12 Ratio tuning knobs

- **Block size** — 64–256 KB input per block is the sweet spot.
- **Align mode** — enable for binary data, disable for text.
- **Filter emission** — worth implementing even a naïve E8E9 detector
  (scan for `0xE8` / `0xE9` byte frequency above baseline) for a quick 5%
  win on executables.
- **Symbol 257 utilization** — explicit check in the parser for
  `current_match == (_lastLen, rep0)` wins ~1–2% on repetitive data.
- **Match finder tuning** — identical to §18.8.9 but with RAR 5.0's much
  larger dictionary raising the payoff of a higher `cutValue`.

### 11.12 Streaming encode with unknown file size

A common real-world use case: compress data from stdin, a network socket,
or a growing log where the total size is not known when the file header
must be written. RAR 5.0 supports unknown **unpacked** size, but the outer
header still needs the packed data-area size so readers can skip to the next
header and bound the compressed input.

1. The **unpacked-size-unknown flag** (`0x0008` in File Flags) — tells the
   decoder not to pass `Unpacked Size` as the output limit to the Unpack50/70
   codec. The compressed stream still ends at its normal RAR5 LZ last-block
   marker, within the packed byte count from `Data Size`.
2. The **vint reservation + backpatch pattern** (§1 Data Types) — pre-
   emit fixed-byte-width placeholders, write the payload, then overwrite the
   placeholders with the actual values in **non-minimal** vint encodings of
   the same byte width.

Together these let an encoder start writing a file header before the compressed
payload is known — provided the output is seekable and the header CRC can be
recomputed after backpatching. Streaming to a non-seekable sink still requires
knowing sizes in advance or buffering the compressed output first.

#### 11.12.1 Which fields need reservation

For a file header with unknown sizes, the encoder must reserve backpatch
space for:

| Field          | Purpose                                   | Max width |
|----------------|-------------------------------------------|-----------|
| Header Size    | Total header length (minus CRC+vint)      | 3 bytes (limit: `MAX_HEADER_SIZE_RAR5` = 2 MB) |
| Data Size      | Packed file size / data-area length (common flag `0x0002`) | 10 bytes  |
| Unpacked Size  | **Not reserved — set flag `0x0008` instead** | — |
| Data CRC32     | Fixed 4 bytes if file flag `0x0004` is emitted | 4 bytes   |

`Header Size` reservation is **mandatory** for streaming because every
other reservation inflates it. A 3-byte vint encodes up to 2 MiB, which
comfortably covers the 2 MB `MAX_HEADER_SIZE_RAR5` limit — reserve 3
bytes unconditionally and you never need to shift the rest of the
header. For `Data Size`, reserve the full 10 bytes (u64 max). `Data Size`
is still required for a file data area; the unknown-size flag does not make the
packed payload self-delimiting at the archive-header layer.

**Why not reserve Unpacked Size the same way?** File flag `0x0008`
("unpacked size unknown") is specifically designed for this case. Setting it
lets the encoder emit a minimal 1-byte placeholder for `Unpacked Size` (value
0) and never backpatch. The decoder still parses the field, but passes no
output-size limit to the codec when the flag is set. Use the flag; it saves 9
bytes of reservation per file.

**`Data CRC32`** is optional, controlled by file flag `0x0004`. If emitted, it
is a fixed 4-byte field, not a vint, so reservation is trivial: emit
`0x00 0x00 0x00 0x00`, compute CRC as you stream the unpacked data, overwrite
in place at finalisation.

#### 11.12.2 Non-minimal vint encoding

To backpatch a vint into a pre-reserved byte count `N`, encode the value
in exactly `N` bytes:

```python
def encode_vint_fixed_width(value, num_bytes):
    """Encode `value` as a non-minimal vint of exactly `num_bytes` bytes."""
    assert 1 <= num_bytes <= 10
    out = bytearray(num_bytes)
    for i in range(num_bytes - 1):
        out[i] = (value & 0x7F) | 0x80  # low 7 bits + continuation
        value >>= 7
    out[num_bytes - 1] = value & 0x7F    # terminator (no continuation)
    if value >> 7:
        raise ValueError(f"value too large for {num_bytes}-byte vint")
    return bytes(out)
```

Sanity checks:

- The minimal encoding of `0` is one byte (`0x00`); the 10-byte
  non-minimal encoding is `80 80 80 80 80 80 80 80 80 00`. Both decode
  identically under `RawRead::GetV`.
- For any value ≤ `2^((num_bytes-1)*7) - 1`, the last byte will be
  zero-extended by the high-bit groups above. For larger values, the
  last byte carries real payload. Both forms are legal on the wire as
  long as `num_bytes ≤ 10`.
- The decoder is tolerant of non-minimal encodings by construction
  (`rawread.cpp:114-127` — it just accumulates 7-bit groups until it
  sees a byte without the continuation bit).

#### 11.12.3 Write recipe

Assuming a seekable output sink:

```
 1. Buffer the fixed part of the header in memory:
    - HeadCRC placeholder (4 bytes zero)
    - HeadSize placeholder (3-byte vint, value zero)
    - HeaderType + HeaderFlags (vints, known at this point)
    - ExtraSize (vint, known after laying out extras)
    - DataSize placeholder (10-byte vint, value zero)      ← packed data size
    - FileFlags (include 0x0008 for unknown-unpacked-size)
    - UnpackedSize (1-byte vint, value zero)               ← sentinel
    - Attributes, mtime, optional DataCRC32 (4-byte placeholder if emitted),
      Compression Info, HostOS, NameLength, Name, Extra Area

 2. Compute HeadSize as the byte count of steps (3..N) above — the
    sum of HeaderType through end-of-ExtraArea. Write it back to
    the HeadSize placeholder as a 3-byte non-minimal vint.

 3. Write the fixed-part buffer to disk. Remember:
    - fileStart = offset of HeadCRC on disk
    - dataSizeOffset = offset of the 10-byte DataSize placeholder
    - dataCrcOffset  = offset of the 4-byte DataCRC32 placeholder, if emitted
    - headerEndOffset = current file position

 4. Stream-compress the file data, writing compressed bytes directly
    to disk. Track:
    - packedBytesWritten (running total)
    - dataCrc (running CRC32 over unpacked bytes, if emitting DataCRC32)

 5. At EOF:
    a. Seek to dataSizeOffset.
    b. Write encode_vint_fixed_width(packedBytesWritten, 10).
    c. If DataCRC32 was emitted, seek to dataCrcOffset.
    d. If DataCRC32 was emitted, write dataCrc as little-endian uint32.

 6. Recompute HeadCRC32 over the now-patched header bytes
    (`[fileStart+4, headerEndOffset)` — skip the first 4 bytes which
    are the CRC field itself).

 7. Seek to fileStart. Write HeadCRC as little-endian uint32.

 8. Seek to the end of the file data (headerEndOffset +
    packedBytesWritten). Continue with the next archive header/block.
```

Step 6 is the easy-to-miss one: the header CRC covers the `DataSize` field and
the `DataCRC32` field if present, so it **must** be recomputed after the
backpatch, not before. The cost is reading back `HeadSize` bytes from disk,
which is trivial since the header is at most 2 MB and almost always a few
hundred bytes.

#### 11.12.4 Non-seekable sinks

If the output cannot seek (e.g., writing to a pipe), streaming encode with
unknown packed size is **not possible** in a single pass — the header CRC
depends on the sizes. Options:

1. **Buffer the compressed output in memory** until EOF, then write the
   header with known sizes followed by the buffered payload. Fine for
   files up to a few MB; unbounded for long streams.
2. **Buffer to a temp file** on disk, then splice the temp file onto
   the output sink after writing the completed header. Standard Unix
   trick; trades disk I/O for memory.
3. **Use multi-volume output** and emit each volume as its own
   seekable file, closing and finalising it before the next volume
   opens. The unknown-total-size case then reduces to unknown-per-volume
   sizes, which is bounded by the volume size limit.

The multi-volume approach composes cleanly with the reservation pattern
above: each volume is a seekable unit, backpatched independently, and
the split-file flags (`FHD_SPLIT_BEFORE` / `FHD_SPLIT_AFTER` in RAR 5.0
common flags, see §4) handle the cross-volume continuity.

#### 11.12.5 Interaction with archive-wide header encryption

If the archive uses HEAD_CRYPT (`ENCRYPTION_WRITE_SIDE.md` §6), the
streaming-encode backpatch happens on **plaintext** header bytes before
encryption. Since the encrypted file header must appear before the packed data,
a seekable writer reserves the encrypted header's final on-disk footprint first:

```
 1. Build the plaintext header with fixed-width placeholders as in §11.12.3.
 2. Compute the final padded plaintext length:
      encrypted_header_size = 16 + align16(len(plaintext_header))
    where the 16-byte prefix is the per-header IV.
 3. Write encrypted_header_size placeholder bytes to the output and remember
    encryptedHeaderOffset.
 4. Stream-compress the file data after that reserved region.
 5. Backpatch DataSize and optional DataCRC32 in the plaintext header buffer.
 6. Recompute Header CRC32 over the final plaintext header.
 7. Zero-pad the final plaintext header to a 16-byte boundary.
 8. Generate a fresh random IV16 and AES-256-CBC encrypt the padded plaintext.
 9. Seek to encryptedHeaderOffset and overwrite the placeholder with
    [IV16][ciphertext].
10. Seek back to the end of the file data and continue with the next archive
    header/block.
```

This forces the encoder to keep the full plaintext header buffer in memory until
the data stream finishes, because the CRC-then-encrypt dependency is sequential.
In practice headers are small, so this is a non-issue. On a non-seekable output
sink, unknown packed size under header encryption still requires buffering the
compressed payload first.

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
| Channels    | 5 bits   | Stored as `channels - 1`, giving 1-32 channels. Only read if type is DELTA. |

The `ReadUInt32` function reads a compact bitstream integer, not a RAR5 `vint`:
2 bits select the byte count (0=1 byte, 1=2 bytes, 2=3 bytes, 3=4 bytes), then
that many 8-bit bytes follow least-significant byte first.

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
