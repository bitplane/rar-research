# RAR Read Side — Parser Walkthrough

End-to-end companion to the `*_WRITE_SIDE.md` docs. Specifies how a
reader turns a file-on-disk into a stream of extracted files across
all three format generations. Read these together with the wire-format
specs, which are the authoritative source for individual field
layouts; this doc is about the state machine connecting them.

Canonical behavior: public RAR readers and the format specs in this directory.

## 1. Pipeline

```
open(path)
  → detect_archive_format         (§2)
  → walk_headers                  (§3)
      ├─ per-block dispatch       (§4)
      └─ quick-open shortcut      (§5, listing only)
  → per-file extraction           (§6)
      ├─ version dispatch         (§6.1)
      ├─ stream decrypt (if any)  (§6.2)
      ├─ decompress               (§6.3)
      ├─ apply filters            (§6.4)
      └─ verify hash              (§6.5)
  → close
```

Each stage has a version-specific implementation inside a version-
agnostic outer loop. The type-detection stage (§2) is what selects
which `ReadHeaderXX` function feeds the walk.

## 2. Archive type detection

### 2.1 The three signatures

| Format | Bytes | Length | Set at position |
|--------|-------|--------|-----------------|
| RAR 1.3/1.4 | `52 45 7E 5E` (`RE~^`) | 4 | Usually offset 0 |
| RAR 1.5–4.x | `52 61 72 21 1A 07 00` (`Rar!` + `0x1a 0x07 0x00`) | 7 | Usually offset 0; SFX-scan if not |
| RAR 5.0/7.0 | `52 61 72 21 1A 07 01` (`Rar!` + `0x1a 0x07 0x01`) | 7 | Same as 1.5–4.x |

`52` (`'R'`) is the lead byte for all three. A reader fast-paths by
reading the first 7 bytes and checking them against the three forms
via `IsSignature` (`archive.cpp:100-126`). Byte 7 of the `Rar!` form
distinguishes RAR 1.5–4.x (`0x00`) from RAR 5.0 (`0x01`); values
`0x02..0x04` are reserved for a hypothetical `RARFMT_FUTURE` and
should be rejected.

### 2.2 SFX scan

If the first 7 bytes match none of the three signatures, the archive
may be a self-extracting executable with the RAR data appended. Scan
the first `MAXSFXSIZE` bytes (compatible RAR reader: 0x400000 = 4 MiB,
defined in `rardefs.hpp:24`) for any `0x52`
byte and retest `IsSignature` starting at that offset
(`archive.cpp:154-172`).

```
CurPos   = tell()                          # file position where Buffer starts
ReadSize = read(Buffer, MAXSFXSIZE - 16)

for i in 0 .. ReadSize - 1:
    if Buffer[i] != 0x52:                  # 'R' — first byte of every signature
        continue
    type = IsSignature(Buffer + i, ReadSize - i)
    if type == RARFMT_NONE:
        continue

    # RAR 1.4 inside SFX: stub-identification check.
    # Only applied when the match is *not* at file offset 0
    # (i.e. preceded by SFX bytes) AND the 4-byte window at
    # absolute file offset 28 is inside the buffer we just read.
    if type == RARFMT14 and i > 0 and CurPos < 28 and ReadSize > 31:
        D = Buffer[28 - CurPos : 32 - CurPos]
        if D != b"RSFX":
            continue                       # not the official stub — false positive

    SFXSize = CurPos + i                   # absolute offset of RAR data
    seek(SFXSize)
    break
```

**Why absolute offset 28.** The official `RARSFX14.EXE` DOS stub embeds
the ASCII marker `"RSFX"` at fixed file offset 28 (inside its MZ header
region), specifically so RAR readers can distinguish a genuine RAR 1.4
self-extractor from an arbitrary file that happens to contain `Rar!`
bytes later in its body. The check at `archive.cpp:161-166` reads those
four bytes directly by absolute offset — not relative to the signature
match. `28 - CurPos` is simply the buffer-relative index of file
offset 28.

**Gating conditions** (`archive.cpp:161`):

| Condition | Purpose |
|-----------|---------|
| `i > 0` | Only apply when the signature match is *after* some preceding bytes — i.e. an SFX stub is plausibly present. A match at `i == 0` is a bare RAR 1.4 archive and needs no stub marker. |
| `CurPos < 28` | Only apply when the current read covers file offset 28. Later re-reads (e.g. after a partial buffer flush during multi-volume processing) don't retrigger the check. |
| `ReadSize > 31` | Ensures bytes 28..31 are in the buffer — avoids out-of-bounds read on a short file. |

The RSFX check exists because the RAR 1.4 signature (`RE~^`,
4 bytes — see §2.1) is shorter than the 7-byte `Rar!` family and
much easier for arbitrary executable code to collide with. RAR 5.0
and RAR 1.5–4.x signatures are long enough that the check isn't
needed for them.

**Consequence for writers.** Non-WinRAR SFX builders that don't embed
`"RSFX"` at offset 28 will be rejected by compatible RAR reader as "not a RAR
archive" for **RAR 1.4 SFX only**. A clean-room encoder that produces RAR 1.4
SFX archives must either reuse the official stub or
place the `"RSFX"` marker at offset 28 of its own stub. (RAR 1.5+
SFX archives face no such constraint.)

Local empirical checks match this boundary. The RAR 2.50 DOS SFX package
`_refs/rarbins/rar250.exe`, its extracted `IDOS.SFX` module, and the WinRAR
1.54 SFX fixture all contain `RSFX` at offset 28. A RAR 3.00 DOS SFX generated
with `-sfxdos.sfx` does not contain `RSFX` at offset 28 and is still listed as a
valid SFX archive by RAR 3.00, because its embedded archive uses the RAR 1.5+
marker path.

**Scan cap.** `MAXSFXSIZE = 4 MiB` is a pragmatic upper bound on stub
size (see `ARCHIVE_LEVEL_WRITE_SIDE.md` §5.6.2). Archives with a
larger stub will be rejected as "not a RAR archive" — acceptable
because no legitimate stub approaches that size.

### 2.3 RAR 1.4 seek-back

After detecting RAR 1.4 at the archive start, the reader seeks back to
offset 0 before parsing — the `RE~^` marker is part of the archive
header, not a separate block (`archive.cpp:150`). RAR 1.5 and 5.0 do
not seek back because their markers are distinct blocks.

## 3. Header walk

Entry point: `Archive::ReadHeader()` (`arcread.cpp:3-45`). Dispatches
to `ReadHeader14`, `ReadHeader15`, or `ReadHeader50` based on the
detected format. Each returns the number of bytes read (0 on end of
archive or fatal error) and sets `CurHeaderType` to the parsed block
type.

### 3.1 The block iteration loop

```
while (header_size = ReadHeader()) != 0:
    if header_type == HEAD_ENDARC:
        break
    dispatch_on_block_type()
    SeekToNext()              # seek to CurBlockPos + FullHeaderSize
```

`SeekToNext` uses the common `HeadSize` field to skip past the
current block (and its data payload, for block types that have one).
Every block carries a length — a reader walks by length, not by
scanning for block-start markers. This is what makes broken-header
recovery possible: a damaged block's length still navigates the
reader to the next one.

### 3.2 Block-size accounting

For RAR 1.5–4.x, the total block size is:

```
FullHeaderSize(HeadSize) =
    HeadSize                                   # header only
    + DataSize       if (HeadFlags & LHD_LONG_BLOCK)
```

For RAR 5.0:

```
FullHeaderSize = Header_Size + (Data_Size if DATA_PRESENT flag)
```

A reader must honour both paths. An encoder can produce a header-only
block (e.g. end-of-archive marker) or a header-plus-payload block
(file, service).

### 3.3 Broken-block recovery

If a block's CRC fails (`arcread.cpp:515-547`) the reader:

1. Sets `BrokenHeader = true`.
2. Returns the block-size as-read anyway, so the outer loop can
   advance past it.
3. In strict mode (`EnableBroken = false`), the top-level caller
   treats any broken block as a fatal archive error.
4. Exceptions: `HEAD3_AV`, `HEAD3_SIGN`, and the old Unix-owners
   subrecord are exempt from CRC checking (see `RAR15_40 §10.2`
   and the `OldUnixOwners` note in `INTEGRITY_WRITE_SIDE.md §8.1.1`).

A reader that wants to list what's in a partially damaged archive
should run in lenient mode and report both the recoverable blocks
and the count of broken ones.

### 3.4 End-of-archive detection

Three signals mean "stop walking":

- **Explicit HEAD_ENDARC block** (RAR 1.5+). Standard terminator.
  Reader stops on this block type regardless of whether more bytes
  follow in the file (those bytes may be recovery record trailer or
  padding — handled below).
- **EOF** before a HEAD_ENDARC. Legal for RAR 2.x/3.x (older compatible RAR reader
  versions wrote archives without an end marker) but a reader should
  warn; tests with intentional truncation should hit this path.
- **Zero-size read from ReadHeader()**. Indicates EOF or fatal parse
  failure. Either way, walk terminates.

RAR 5.0 always emits HEAD_ENDARC. RAR 1.5–4.x archives built by
WinRAR always do; archives built by older rar command-line tools
may not.

### 3.5 Volume continuation

If the HEAD_ENDARC block has the `EARC_NEXTVOLUME` flag set
(RAR 5.0: `0x0001`; RAR 1.5–4.x: `EARC_NEXT_VOLUME = 0x0001`) the
archive is not complete — the next volume contains the continuation.
Reader opens the next volume (§8) and continues the walk from its
main header.

## 4. Per-block dispatch

After `ReadHeader()` returns, `CurHeaderType` tells the reader what
block it just parsed. Handling varies by type:

| Block type | Action |
|-----------|--------|
| `HEAD_MAIN` | Record archive-wide flags (volume, solid, locked, encrypted, signed). Only one per volume. |
| `HEAD_FILE` | Stage a file for extraction. Record filename, size, method, CRC, flags. |
| `HEAD_SERVICE` (RAR 3.x+) | Look at the name. `CMT` → archive comment; `QO` → quick-open cache; `RR` → recovery record; `ACL`/`STM`/`UO` → metadata for the preceding file. |
| `HEAD3_CMT` (RAR 2.x) | Archive comment (inline subblock). |
| `HEAD3_AV` / `HEAD3_SIGN` | Authenticity info. Skip over (modern readers don't verify; see RAR15_40 §10). |
| `HEAD3_PROTECT` | RAR 2.x recovery record. Skip for extraction; parse for repair. |
| `HEAD3_OLDSERVICE` | Old-style subblock — look at `SubType`: ACL, STM, UO. |
| `HEAD_ENDARC` | End of archive; see §3.4. |
| `HEAD_CRYPT` (RAR 5.0) | Archive-wide header encryption — see §7. |
| Unknown type | Skip via HeadSize; warn if strict mode. |

The important rule: **every unknown block type must be skippable via
its length field**. A reader that doesn't recognize a future block
type should still be able to walk past it and extract the files.

## 5. Quick Open shortcut (RAR 5.0, listing only)

When the user wants to list archive contents (`rar l` equivalent) or
seek to a specific file without scanning every header from offset 0:

```
seek to (end_of_archive - ~1KB)
scan backward for HEAD_SERVICE with name "QO"
if found:
    decompress QO payload
    iterate over cached (header_copy) entries
    report to user without walking the full header stream
```

The reader must still walk-and-verify for extraction — QO is a
metadata cache, not a replacement for the real headers. If a QO
entry references a file offset, the reader seeks there and re-parses
the real file header before streaming data.

Full payload layout is in `ARCHIVE_LEVEL_WRITE_SIDE.md §4`.

**Important QO constraint for solid archives**: QO gives you the
file's position on disk but not the decompressor state needed to
extract it. For extraction from a solid archive, the reader must
decompress every preceding file in the group regardless of QO
(see `ARCHIVE_LEVEL_WRITE_SIDE.md §1.6`).

## 6. Per-file extraction

### 6.1 Version dispatch

The file header's `UnpVer` (RAR 1.5–4.x: `byte`; RAR 5.0: bitfield in
compression-info) selects the decompressor:

| UnpVer | Decoder | Notes |
|--------|---------|-------|
| 13 | Unpack13 | RAR 1.3/1.4. Adaptive Huffman. (Wire byte = `2`; see `RAR13_FORMAT_SPECIFICATION.md` §5.) |
| 15 | Unpack15 | RAR 1.5. Short-distance LZ with Huffman. |
| 20 | Unpack20 | RAR 2.0. Block-structured LZ. |
| 26 | Unpack20 | RAR 2.x compression for files larger than 2 GB. Same codec as `UnpVer = 20`; the higher version number lets older readers reject what they can't size. (Audio mode is per-block via bit 15 of the block header — not UnpVer-gated.) |
| 29 | Unpack29 | RAR 2.9/3.x/4.x. LZ + optional PPMd + RARVM filters. |
| 36 | Unpack29 (extended dict) | RAR 3.0 experimental; treat as 29. |
| 50 | Unpack50 | RAR 5.0. Byte-aligned blocks, hardcoded filters. |
| 70 | Unpack70 | RAR 7.0. Unpack50 with larger dictionaries (up to 1 TiB). |

A reader must implement every version it claims to support — there is
no forward compatibility within a format generation (Unpack29 can't
read Unpack50 streams). The spec files each describe the
corresponding decoder's state machine:

- RAR 1.3: `RAR13_FORMAT_SPECIFICATION.md` §6.
- RAR 1.5/2.0/2.9/3.x: `RAR15_40_FORMAT_SPECIFICATION.md` §15–§18.
- RAR 5.0/7.0: `RAR5_FORMAT_SPECIFICATION.md` §11.

### 6.2 Stream decrypt (if encrypted)

If the file header signals encryption (`LHD_PASSWORD` flag for
RAR 1.5–4.x, or the File Encryption extra record for RAR 5.0):

1. Derive the key from password + salt per the version's KDF
   (`ENCRYPTION_WRITE_SIDE.md` §1–§5).
2. Wrap the compressed-data stream in an AES-CBC / Feistel / XOR
   decrypt layer before handing it to the decompressor.
3. For RAR 5.0: also MAC-convert the file hash (§5.3 of encryption
   spec) and compare against the stored value after extraction.

A reader that is only listing the archive can skip decrypting file
data — but must still decrypt headers if `HEAD_CRYPT` is set
(RAR 5.0) or `MHD_PASSWORD` is set (RAR 3.x/4.x).

### 6.3 Decompress

The decompressor consumes the (possibly decrypted) compressed stream
and produces bytes into the LZ window. For RAR 5.0 that window may
be up to 1 TiB in the RAR 7.0 variant; a reader on a resource-
constrained host should reject archives whose dictionary size exceeds
a configured cap before allocating.

**Solid groups**: in solid mode, the decompressor state carries over
between files in the group. The reader must decompress files
sequentially — no skipping — see `ARCHIVE_LEVEL_WRITE_SIDE.md §1.6`.

### 6.4 Apply filters

If the decompressed stream includes filter blocks (RAR 3.x VM-era or
RAR 5.0 hardcoded enum), the reader:

1. Buffers the decompressed bytes into the filter region.
2. Runs the inverse transform per filter type (`FILTER_TRANSFORMS.md`).
3. Emits the transformed bytes as the final extraction output.

Filters never cross file boundaries (even in solid mode — see §1.1 of
ARCHIVE_LEVEL_WRITE_SIDE); a reader resetting filter state at each
file boundary is correct.

### 6.5 Hash verify

After extraction, the reader computes the file's hash:

- **RAR 1.3**: compute the 16-bit rolling sum+rotate checksum described in
  `RAR13_FORMAT_SPECIFICATION.md` §11 over the final plaintext output bytes
  and compare it to the file-header `FileCRC` field.
- **RAR 1.5–4.x**: full CRC32 compared to `FILE_CRC`.
- **RAR 5.0 with CRC32 flag**: full CRC32 compared to the on-disk
  value (or the MAC'd value if encrypted).
- **RAR 5.0 with BLAKE2sp (File Hash extra record type 0x02, hash
  type 0x00)**: BLAKE2sp(data) compared to the 32-byte hash in the
  extra record (or its HMAC fold if encrypted).

Hash mismatch means "file is corrupt" — report and either discard
the extraction or keep it with a warning, per user policy. Don't
silently accept.

## 7. Encrypted-archive read flow

Two distinct modes (see `ENCRYPTION_WRITE_SIDE.md` §6):

### 7.1 Per-file encryption (`-p` equivalent)

- Individual file headers carry encryption records (RAR 5.0) or the
  `LHD_PASSWORD` flag + salt (RAR 1.5–4.x).
- Archive headers themselves are cleartext — walk normally.
- At extraction time (§6.2) decrypt the file data.
- Password verification happens implicitly via hash check after
  decryption (see `ENCRYPTION_WRITE_SIDE.md §6.4.1` for RAR 3.x/4.x,
  or the explicit `PswCheck` in RAR 5.0 HEAD_CRYPT).

### 7.2 Archive-wide header encryption (`-hp` equivalent)

- RAR 5.0: a HEAD_CRYPT block is the first block after the 8-byte
  signature; all subsequent blocks are encrypted.
- RAR 3.x/4.x: MainHead carries `MHD_PASSWORD`; all blocks after
  MainHead are encrypted (MainHead itself is cleartext).
- Reader must derive the KDF-based key from user password + salt
  **before** it can walk block headers.
- Password verification: RAR 5.0 carries an explicit `PswCheck` field
  in HEAD_CRYPT (validated before any header decryption). RAR 3.x/4.x
  has no such field — verification is implicit via the first decrypted
  header's CRC16 (`ENCRYPTION_WRITE_SIDE.md` §6.4.1).

A reader asked to list an encrypted archive without a password
should report "archive is encrypted" and stop — walking blind won't
reveal block boundaries after the main header.

## 8. Multi-volume reading

### 8.1 Volume detection

A file is a volume if its main header has the `MHD_VOLUME` /
volume-set flag. The first volume additionally has the
`MHD_FIRSTVOLUME` flag (RAR 3.x/5.0 only; RAR 2.x readers must use
filename conventions).

### 8.2 Continuation flags

File headers on volume boundaries carry `LHD_SPLIT_BEFORE` (first
part of a file continued from previous volume) or `LHD_SPLIT_AFTER`
(last part of a file continued into next volume). A reader treats
these as a single logical file spanning multiple physical volumes.

### 8.3 Next-volume discovery

When the reader hits HEAD_ENDARC with `EARC_NEXT_VOLUME`, it needs
to open the next volume. Naming conventions:

- Old-style (pre-RAR 3.0): `foo.rar` → `foo.r00` → `foo.r01` → …
- New-style (`MHD_NEWNUMBERING`): `foo.part01.rar` → `foo.part02.rar` → …
- RAR 5.0 always uses new-style.

The reader derives the next-volume filename and opens it; if it
doesn't exist, report "missing volume" and stop.

### 8.4 State across volumes

For split files (`LHD_SPLIT_BEFORE` / `LHD_SPLIT_AFTER`), the
decompressor state (LZ window, reps, Huffman tables) carries across
the volume boundary — the file is logically continuous even though
the bytes live in different physical files. A reader that restarts
the decompressor at each volume boundary will corrupt split files.

## 9. Streaming reader behaviour

A reader fed bytes incrementally (e.g. over a network socket) can:

1. **Buffer until signature detection succeeds.** Minimum buffer =
   7 bytes; during SFX scan, up to MAXSFXSIZE. A reader that can't
   seek must either buffer MAXSFXSIZE bytes upfront or refuse SFX
   archives.
2. **Process blocks as they complete.** Each block's `HeadSize`
   field is available after the first few bytes; the reader can
   decide whether to parse or skip without reading the rest.
3. **Stream-decompress file payloads.** Decompressor operates on
   arbitrary-sized input chunks; no need to buffer the whole
   compressed stream.
4. **Verify hashes only at end-of-file.** A streaming reader that
   emits uncompressed bytes to the caller before hash verification
   must make clear to the caller that the bytes are unvalidated
   until end-of-stream.

Streaming readers cannot use the Quick Open shortcut (§5) — QO lives
at the end of the archive; seeking there requires random access.
Streaming is always full-walk.

## 10. Error handling

A reader will encounter, at minimum:

| Error | Response |
|-------|----------|
| Unknown signature | Report "not a RAR archive"; don't guess. |
| Broken header CRC | Lenient mode: warn, skip to next block via HeadSize. Strict mode: abort. |
| Unknown block type | Skip via HeadSize; note in log. |
| Unknown UnpVer | Abort — can't extract without the right decoder. |
| Password wrong (explicit PswCheck fails) | Abort with "wrong password". |
| Password wrong (implicit, CRC fails) | Report after the first post-main block CRC fails. |
| Hash mismatch | Report per file; don't silently succeed. |
| Volume missing | Stop at the last readable volume; report per-file extraction status. |
| Truncation mid-block | Report "unexpected EOF at block type X". |
| Dictionary size exceeds cap | Refuse before allocating. |
| Infinite LZ loop | Cap output size (caller-configured) and abort on overshoot. |

A reader that loops, crashes, or silently produces wrong data on any
of these is unacceptable — the archive file is attacker-controlled
and validation is the reader's responsibility.

## 11. Resource and Safety Limits

Archive metadata is untrusted. A reader should enforce limits before allocating
memory, opening output files, or following volume chains. Recommended defaults:

| Limit | Recommended default | Enforcement point |
|-------|---------------------|-------------------|
| SFX marker scan | 4 MiB | Stop scanning for a marker after the cap (§2.3). |
| Header size | Format maximum, but no more than 2 MiB for RAR 5.0 | Reject before buffering the header body. |
| Header count | Caller-configured, e.g. 1 million | Abort archive walk on excess headers. |
| Dictionary size | Caller-configured; never allocate above available memory | Check before initializing Unpack20/29/50/70. |
| Unpacked output per file | Caller-configured | Count produced bytes even when the header says size is unknown. |
| Total unpacked output | Caller-configured | Count across all files and volumes. |
| Volume count | Caller-configured; RAR 2.x old naming naturally caps at 100 | Abort volume discovery after the cap. |
| RARVM execution | 25,000,000 VM instructions | Abort non-standard VM programs that exceed the cap. |
| Archive comment | 256 KiB decoded output | Abort comment decompression at the cap. |
| Path component length | 255 bytes after normalization | Reject before filesystem creation. |

The important distinction is "format maximum" versus "deployment maximum." RAR
5.0/7.0 can describe very large dictionaries and unknown unpacked sizes, but a
resource-constrained reader is still correct if it refuses archives that exceed
its configured policy and reports a clear resource-limit error.

Path handling is also a resource-safety issue. Apply `PATH_SANITIZATION.md`
before creating anything, and prefer race-safe relative file creation from an
already-open extraction-root directory handle.

## 12. Minimum viable reader checklist

For a new implementation, the minimum to read every historically
significant archive:

- [ ] All three signatures detected; SFX scan implemented.
- [ ] RAR 1.4 seek-back logic for in-SFX detection.
- [ ] `ReadHeader14` / `ReadHeader15` / `ReadHeader50` dispatch.
- [ ] Block-length-driven iteration, not pattern-scanning.
- [ ] `HEAD_MAIN` / `HEAD_FILE` / `HEAD_ENDARC` handling for each
      format version.
- [ ] Unknown block skipping via HeadSize.
- [ ] Unpack15/20/29/50 decompressors — skip Unpack13 if RAR 1.3
      archives aren't in scope.
- [ ] Per-file CRC32 verification.
- [ ] BLAKE2sp verification for RAR 5.0.
- [ ] Per-version encryption (at least RAR 5.0 AES-256 CBC +
      PBKDF2 is table-stakes; older variants optional).
- [ ] Solid-mode support (no file-boundary state reset for LZ state).
- [ ] Multi-volume: next-volume discovery + split-file handling.
- [ ] Path sanitization per `PATH_SANITIZATION.md`.
- [ ] Resource caps: max decompressed size, max dictionary, max
      header count per block type, max volume count.
- [ ] Graceful handling of truncation, bit flips, and unknown
      blocks.

Items a modern reader can skip on a first pass:

- RAR 1.3 compression (RAR13 archives are rare; most "RAR 1.3" tools
  output stored-only files).
- AV / SIGN blocks (informational only).
- Recovery records (only needed for `rar r` equivalent).
- `.rev` files (separate recovery volumes — rarely used).
- Generic RARVM bytecode execution (needed only for non-standard RAR 3.x
  filters; `RARVM_SPECIFICATION.md` documents it, while the hardcoded filter
  set in `FILTER_TRANSFORMS.md §9` covers the standard ones).

## 13. Reference map

Per-stage pointers back into the spec set:

| Stage | Spec |
|-------|------|
| Signature / SFX | §2 (this doc), `RAR13` §2, `RAR15_40` §2, `RAR5` §3 |
| Block walk | §3 (this doc), `RAR15_40` §3, `RAR5` §4 |
| Main header | `RAR15_40` §5, `RAR5` §7 |
| File header | `RAR15_40` §6, `RAR5` §8 |
| Service headers | `ARCHIVE_LEVEL_WRITE_SIDE` §3 + §3.5, `RAR5` §10 |
| Compression | `RAR15_40` §15–§19, `RAR5` §11, plus `HUFFMAN_CONSTRUCTION`, `LZ_MATCH_FINDING`, `PPMD_ALGORITHM_SPECIFICATION` |
| Filters | `RAR15_40` §20, `RAR5` §12, `FILTER_TRANSFORMS` |
| Encryption | `ENCRYPTION_WRITE_SIDE` (write side) + §7 here (read flow) |
| Integrity | `INTEGRITY_WRITE_SIDE`, `CRC32_SPECIFICATION` |
| Multi-volume | `ARCHIVE_LEVEL_WRITE_SIDE` §2, §8 (this doc) |
| Solid | `ARCHIVE_LEVEL_WRITE_SIDE` §1 + §1.6 |
| Path security | `PATH_SANITIZATION` |
| Test vectors | `TEST_VECTORS` |
| Gaps | `IMPLEMENTATION_GAPS` |
