# RAR Archive-Level Write Side

This document covers the archive-level mechanics that an encoder must
handle beyond the LZ/Huffman/filter/encryption/integrity primitives:

1. **Solid mode** dictionary and model persistence rules across files
2. **Multi-volume** split algorithm — cut points, continuation state, header
   rewriting
3. **Service header** write procedures (comments, Quick Open, recovery,
   file system metadata)
4. **Quick Open** cache contents selection
5. **Symlink / hardlink / high-precision time / extended attribute** write
   encoding

References:
- a public RAR reader — `UnpInitData` reset rules (lines 206–240)
- a public RAR reader — `NextVolumeName` naming conventions
- a public RAR reader / `arcread.cpp` — header framing
- a public RAR reader — Quick Open cache structure
- a public RAR reader / `filcreat.cpp` — file creation and link
  resolution

---

## 0. Write order and cross-reference dependencies

Scattered through this document and the format specs are a number of
"X must be written before Y" and "X's bytes point at Y's position"
rules. An encoder that emits blocks in the wrong order, or backpatches
pointers at the wrong time, produces archives that verify per-block
but fail as a whole. This section collects the constraints in one
place so an implementer can sequence the writer correctly.

### 0.1 Block emit order (linear archive, RAR 5.0)

```
  [SFX stub]                       (optional; precedes marker — §5.6)
  MARK_HEAD                        (magic bytes — 8 bytes for RAR 5.0)
  MAIN_HEAD                        (archive flags + locator extra)
  [encryption header]              (if archive-encrypted; see ENCRYPTION_WRITE_SIDE.md §4)
  [CMT service header]             (optional archive comment; typically here)
  [file header 1] [data] [ACL?] [STM?] ...
  [file header 2] [data] ...
  ...
  [QO service header]              (optional; backpatched into MAIN_HEAD locator)
  [RR service header]              (optional; backpatched into MAIN_HEAD locator)
  END_OF_ARCHIVE (type 5)
```

For RAR 2.x/3.x/4.x the skeleton is the same, with `MARK_HEAD` 7 bytes,
`END_OF_ARCHIVE` type `0x7B`, and any PROTECT_HEAD (0x78) recovery
blocks in place of the RR service header.

### 0.2 Cross-reference dependency graph

Each edge `A → B` means "A's bytes contain an offset/CRC/size that
refers to B's bytes". Resolving the edge requires knowing B's position
at the time A's bytes are finalized — either write B first, or write
A with a placeholder and backpatch.

```
                     ┌──────────────────────────────────┐
                     │           MAIN_HEAD              │
                     │  ┌─────────────────────────┐     │
                     │  │ MHEXTRA_LOCATOR extra   │─────┼──► QO service header offset
                     │  │                         │─────┼──► RR service header offset
                     │  └─────────────────────────┘     │
                     └──────────┬───────────────────────┘
                                │                    ┌──► every file header
                                ▼                    │
                     ┌──────────────────┐            │
                     │   QO service     │────────────┘  (Quick Open cache contains
                     │   header payload │                 copies of file header blobs
                     │                  │                 + their archive offsets)
                     └──────────────────┘
                                                    ┌──► archive bytes being protected
                                                    │
                     ┌──────────────────┐           │
                     │   RR service     │───────────┘  (Reed-Solomon parity computed
                     │   header payload │                over archive content; any
                     │                  │                edit to earlier bytes
                     └──────────────────┘                invalidates RR)

                     ┌──────────────────┐
                     │  END_OF_ARCHIVE  │──► archive data CRC32 (if EARC_DATACRC)
                     └──────────────────┘        covers all preceding data
```

The two backpatch edges (`MHEXTRA_LOCATOR → QO`, `MHEXTRA_LOCATOR →
RR`) are the tricky ones. Both QO and RR are emitted near the end of
the archive, but their offsets must appear in the main header at the
beginning. Three workable strategies:

1. **Reserve and backpatch.** Write `MAIN_HEAD` with the locator
   record sized for both offsets and filled with zeros. Emit the rest
   of the archive. When emitting QO and RR, record their offsets.
   Seek back to the locator, overwrite the offset fields, and
   recompute `MAIN_HEAD`'s header CRC32.
2. **Two-pass.** Run the full archive emission once to memory, record
   all offsets, then emit the final bytes with real locator values.
   Slower and memory-hungry but avoids seekback.
3. **Skip QO/RR.** Locator is optional. If the encoder does not emit
   Quick Open or a recovery record, `MHEXTRA_LOCATOR` can be absent or
   have both offset fields set to zero (which compatible RAR reader interprets as
   "reserved space was not enough"; `arcread.cpp:1014, 1020`).

### 0.3 Ordering rules (must-before / must-after)

| Must happen before | Must happen after | Why |
|---|---|---|
| MARK_HEAD | every other header | Magic bytes identify the format. |
| MAIN_HEAD | every file / service / end block | Main header carries archive flags the decoder needs. |
| Encryption header | any encrypted file / service header | Carries archive-wide salt, IV, KDF count. |
| File header | its data area | Decoder allocates based on unpacked size. |
| File header | ACL / STM service headers for that file | Service headers attach to the preceding file (§3.2). |
| First-file-in-solid-group header | subsequent solid-group file headers | Solid flag must be clear on the first, set on the rest. |
| QO's referenced file headers | QO service header | QO's payload includes copies of the file header bytes and their archive positions — they must be known when QO is emitted (§4.3). |
| RR's protected archive bytes | RR service header | RR parity is computed over the bytes; they must be final when RR is emitted. |
| Every data block that contributes | END_OF_ARCHIVE | End marker's optional DataCRC covers the whole archive (§2.6). |
| Locator backpatch | MAIN_HEAD CRC finalization | CRC32 covers the backpatched bytes; the backpatch must happen before the header CRC is written, or the CRC must be recomputed and rewritten. |

### 0.4 Combined solid + multi-volume write order

When both solid mode and multi-volume splitting are active, the two
constraints compose without conflict because they act on different
boundaries: solid mode persists LZ state across *file* boundaries,
multi-volume splitting preserves archive-level headers across *volume*
boundaries. The encoder sequences:

1. Choose a solid group (an ordered list of files sharing LZ state).
2. Within the group, emit files in order. The LZ state and Huffman
   tables carry across file headers (§1.1).
3. When the current volume reaches its size cap, stop mid-stream,
   emit `FHD_SPLIT_AFTER`, write `END_OF_ARCHIVE` for this volume
   (with `EARC_NEXT_VOLUME`), start the next volume with `MARK_HEAD`
   + `MAIN_HEAD` + continuation file header (`FHD_SPLIT_BEFORE`).
4. LZ state, Huffman tables, and the solid-group-level model state
   persist across the volume boundary (§2.5); the decoder's
   `UnpInitData(Solid=true)` call treats volume boundaries the same
   as file boundaries for state purposes.
5. Filters reset at every file boundary regardless of volume (§1.1).

An encoder that volume-splits **within** a single file (any split
where the outer `FHD_SPLIT_*` flags apply) inherits solid-mode's
"don't reset LZ state" rule even if the archive isn't nominally solid,
because the file's data stream simply continues in the next volume.

---

## 1. Solid mode — state persistence across file boundaries

### 1.1 What carries over and what resets

The canonical source is `Unpack::UnpInitData(bool Solid)` at
a public RAR reader, the entry point shared by **Unpack50
(RAR 5.0) and Unpack70 (RAR 7.0)** — they both dispatch through the same
init. Reading the unconditional vs the `if (!Solid)` branches produces
this table:

| State element | Resets on non-solid | Carries over in solid | Notes |
|---|:---:|:---:|---|
| LZ window bytes | ✓ | ✓ carries | |
| Repeat distances `OldDist[0..3]` / `_reps[0..3]` | ✓ | ✓ carries | |
| `LastDist`, `LastLength` / `_lastLen` | ✓ | ✓ carries | |
| `OldDistPtr` | ✓ | ✓ carries | |
| `WrPtr` / `UnpPtr` / `PrevPtr` | ✓ | ✓ carries | |
| Huffman tables (`BlockTables`, `TablesRead5`) | ✓ | ✓ carries | Unpack50/70 only. |
| Bit input state | ✓ | ✓ (new stream, but fresh bit buffer) | |
| `FirstWinDone` | ✓ | ✓ carries | Unpack50/70 only. |
| `WriteBorder` | ✓ | ✓ carries | Unpack50/70 only. |
| **Filters** | ✓ | **✓ reset always** | All codec generations. |
| `BlockHeader` | ✓ | ✓ reset always | |

**The one surprise:** filters reset at every file boundary, even in solid
mode. From `unpack.cpp:224`:
```
// Filters never share several solid files, so we can safely reset them
// even in solid archive.
InitFilters();
```

This means that if the encoder emits a filter at the end of file N, the
decoder discards it before starting file N+1. The encoder should
therefore never emit a filter whose region spans a file boundary — the
filter block must live entirely within a single file's LZ stream.

**Unpack50 vs Unpack70.** The two codecs share `UnpInitData` verbatim and
operate on the same state fields. The only differences are the Distance
table size (64 → 80 symbols) and the dictionary encoding (fraction bits
active), both of which are per-file, not per-solid-group. Solid grouping
across a v0/v1 boundary therefore carries the LZ state cleanly, provided
the encoder does not change the dictionary size within the group
(otherwise the window buffer must be re-allocated, which implicitly
forces `Solid = false`).

**Unpack15 / Unpack20 / Unpack29 (RAR 1.5-4.x).** These older codecs have
separate initialization paths. The broad rules are the same: the LZ window,
repeat distances, and (for Unpack29) PPMd model context persist across solid
files; filters and per-block Huffman tables reset. The state fields map
differently:

| Codec | State that carries in solid mode |
|-------|----------------------------------|
| Unpack15 | `ChSet`, `NToPl`, `Place` arrays (adaptive Huffman); window |
| Unpack20 | Huffman tables (`MD`, `LD`, `DD`, `RD`, `BD`); window; `LastDist`/`LastLength`; audio filter state `AudioV` |
| Unpack29 | All Unpack20 state; PPMd model (`ModelOrder`, SubAlloc, `FoundExcaped`); RARVM filter list; v29-specific `UnpBlockType` (LZ / PPM) |

A RAR 2.x/3.x/4.x encoder must match the source code in the matching
`unpackNN.cpp` file; the RAR 5.0 state list above is **not** a substitute.
Filter reset-per-file is the one rule that holds across every codec
generation.

### 1.2 Encoder rule: emit in file order, never reset mid-stream

For a solid-mode multi-file encoder:

```
init LZ state (window fresh, reps invalid, _lastLen = 0, Huffman tables absent)
for each file in the solid group, in emission order:
    emit file header with solid_flag = true (except the first file)
    continue the LZ stream into the new file's data
    (window, reps, Huffman tables persist unchanged)
    before emitting any filter that straddles the boundary, split it
    into two filters — one per file
    after emitting the file's last byte:
        InitFilters()      # match decoder's filter reset
```

The first file in a solid group must have `solid_flag = false` in its
file header's compression info field. Every subsequent file must have
`solid_flag = true`. Service headers must not set the solid flag and do
not participate in the regular-file solid chain.

The archive's main header also advertises that solid grouping is present:
RAR 2.x/3.x/4.x use `MHD_SOLID`; RAR 5.0 uses main-archive flag `0x0004`.
Those archive-level flags are summary metadata. The actual RAR 5.0
continuation decision for each file is still the file header Compression
Information bit 6, gated by archive order: only a regular file after a
previous regular file can be a valid solid continuation.

### 1.3 The RAR 5.0 `table_present` flag interaction

In RAR 5.0's block header (§11.2 of the RAR 5.0 spec), bit 7 of the
flags byte is "table present". An encoder emitting the first LZ block of
a solid-continuation file has three choices:

- `table_present = 0`: reuse the previous block's Huffman tables. Cheap
  (~50 bytes saved) but only valid if the statistics haven't shifted.
- `table_present = 1` with full 20-symbol level table: new tables. Safe.
- `table_present = 1` with delta-zero lengths: degenerate case — emits
  a new table identical to the previous one. Pointless but valid.

A practical encoder picks `table_present = 0` when the file is small
(< 32 KB) and highly similar to the previous one; otherwise emits new
tables. There is no downside to always emitting new tables except a
small size overhead.

### 1.4 The "first file without solid flag" wedge

`unpack.cpp:38-40` has a defensive comment about the first unpacked file
being flagged solid-true by mistake:

```
// It prevents crash if first unpacked file has the wrong "true" Solid flag,
// so first DoUnpack call is made with the wrong "true" Solid value later.
```

The encoder must ensure the first file in the solid group is `solid =
false` — corrupting this invariant produces an archive that compatible RAR reader
rejects. This is particularly easy to get wrong when rebuilding an
archive from mixed solid/non-solid sources.

### 1.5 Cost and benefit

Solid mode gains 10–40% compression on groups of similar files (source
trees, log collections, repeated file types) because the LZ dictionary
warms up across files. It loses on random-extract use cases because the
decoder must decompress every preceding file to reach a target. A good
encoder default: group files ≤ 1 MB each into solid groups of ≤ 64 MB
total; emit each file > 64 MB as its own non-solid record.

### 1.6 Reader-side obligations (informational)

The encoder rules above imply corresponding reader obligations. A
conforming reader extracting file N from a solid group must:

1. **Decompress every preceding file in the group in order.** The LZ
   window, repeat-distance ring, and Huffman tables from files 0..N-1
   are inputs to decoding file N. There is no way to jump directly to
   file N — even if the file header's data offset is known, the
   decompressor state is not.

2. **Not reset LZ state between solid files.** Mirror the write-side
   table in §1.1: keep window, reps, `LastDist`/`LastLength`, Huffman
   tables, `FirstWinDone`, `WriteBorder`. Only reset filters
   (`unpack.cpp:224` — `InitFilters()` runs per file).

3. **Honour `solid_flag` on file entry, not on block entry.** The flag
   lives in the file header's compression-info field. Blocks within a
   solid continuation file may freely re-emit Huffman tables
   (§1.3) — that's a block-level decision, not a state reset.

4. **Reject the first-file-solid-true wedge.** The defensive code at
   `unpack.cpp:38-40` tolerates a malformed first file marked
   `solid = true`, but a fresh implementation should either treat the
   first file's solid flag as false regardless or reject the archive.
   Silently accepting it masks encoder bugs.

5. **Seek via Quick Open only for listing.** The QO cache (§4) carries
   file-header copies but not LZ state, so it's a read-only shortcut
   for `rar l` and metadata queries. Any extraction path must still
   decompress linearly from the start of the solid group.

**Partial extraction cost.** To extract only file N from a K-file solid
group, a reader must decompress files 0..N — discarding the output of
0..N-1. In practice, the UI should warn when a user requests a single
file from a large solid archive; libraries should document the cost.

**Solid group boundaries.** A group begins at the first file with
`solid_flag = false` and ends at the file immediately before the next
`solid_flag = false`. A non-solid archive is semantically equivalent to
a sequence of one-file solid groups. RAR 3.x+ solid archives set the
`MHD_SOLID` flag in the main archive header as an advisory hint; the
authoritative per-file signal is still the compression-info bit.

---

## 2. Multi-volume split algorithm

### 2.1 Continuation flags

RAR supports splitting a file's data across two or more volumes. Each
half is represented by a separate file header with a continuation flag:

| RAR version | Flag for "split before" (prev volume continues here) | Flag for "split after" (continues in next volume) |
|---|---|---|
| 2.x/3.x/4.x | `LHD_SPLIT_BEFORE` (`0x0001` in file header flags) | `LHD_SPLIT_AFTER` (`0x0002`) |
| 5.0 | Common header flag bit 0 (`0x0001` — "data continues from previous volume") | Common header flag bit 1 (`0x0002` — "data continues in next volume") |

Both flags appear in the **file header** (and identically in service
headers), not the block header. A single file's data can span 3+ volumes
by emitting three file headers: volume 1 with `SplitAfter`, volume 2 with
both `SplitBefore` and `SplitAfter`, volume 3 with `SplitBefore`.

### 2.2 Split point selection

The encoder picks where to cut based on a volume size budget. The
algorithm:

```
for each source file to archive:
    emit file header (without split flags yet)
    while bytes_remaining_in_file > 0:
        chunk = min(bytes_remaining_in_file, volume_bytes_remaining)
        emit LZ/PPMd/stored compressed data for this chunk
        bytes_remaining_in_file -= chunk
        volume_bytes_remaining  -= chunk
        if bytes_remaining_in_file > 0:
            # Split — finish this volume, start a new one
            patch the file header to set SplitAfter flag
            finalize volume (emit end-of-archive marker if applicable)
            open next volume file
            emit archive signature + main archive header + Multi-volume marker
            emit new file header with SplitBefore flag
```

Key observations:

- **The cut point is arbitrary at byte granularity.** The compressed
  stream simply stops at any byte boundary and resumes at the same byte
  position in the next volume. No padding, no realignment, no flushing.
  The decoder reassembles the compressed stream as if it were continuous.

  This applies uniformly across codecs:

  - **LZ-mode (Unpack15/20/29/50/70):** the bit-stream cursor (`InAddr`
    + `InBit` in `_refs/unrar/getbits.cpp`) carries across; the next
    bit is read from the next volume's first compressed byte at the
    appropriate bit offset.
  - **PPMd-mode (Unpack29):** the range coder state (`Low`, `Range`,
    `Code`) persists in the decompressor object across the volume
    boundary; only the underlying byte source switches. A cut may land
    mid-symbol — the range coder's next byte fetch will simply happen
    from the new volume.
  - **AES-CBC encrypted streams:** must respect the 16-byte block
    boundary at the cut (see "Encrypted data" below).

  Volume boundaries are not a codec event; they are a transport-layer
  byte-stream split.
- **The file header is duplicated** across volumes. Each copy carries
  the split flags and the portion of the file data that lives in that
  volume. The header itself is *not* split — only the payload.
- **The end-of-archive marker** in a non-final volume is optional in RAR
  2.x/3.x (the reader detects split-after and keeps reading the next
  volume). In RAR 5.0, the end marker uses bit `0x0001` of its own flags
  to signal "not the last volume" (spec §9).
- **The main archive header** in a non-first volume must still be
  present and must carry the `MHD_VOLUME` flag. The `MHD_FIRSTVOLUME`
  flag (RAR 3.0+) is only set on volume 1.

### 2.3 The first-volume-only marker

RAR 3.0+ adds `MHD_FIRSTVOLUME` (`0x0100` in 2.x/3.x main header flags,
or a dedicated bit in RAR 5.0 main header) to identify volume 1
unambiguously. This lets decoders auto-detect volume 1 when given any
volume. An encoder should always set it on volume 1 and never on other
volumes. Legacy RAR 2.x archives lack this flag and use filename
convention (`.rar` = first, `.rNN` = subsequent) instead.

### 2.4 Volume naming conventions

```
Old scheme (RAR 2.x default):
    volume 1: archive.rar
    volume 2: archive.r00
    volume 3: archive.r01
    ...
    volume 100: archive.r98
    (can only go to r99 — 100 volumes max)

New scheme (RAR 3.0+ default):
    volume 1: archive.part01.rar (or part1, part001 etc.)
    volume 2: archive.part02.rar
    ...
    (up to 99/999/... depending on digit count in volume 1's name)
```

a public RAR reader handles both schemes. The digit-
count in the first volume's filename determines how many volumes the
scheme supports before overflowing; the encoder should pick enough digits
up front (e.g. `partNNN` for expected 100+ volumes). The archive header
flag `0x0010` (MHD_NEWNUMBERING) indicates new-scheme names.

### 2.5 Encoder-side state management across volumes

A volume boundary is a **transport-layer** split, not a codec event. The
compressed byte stream is continuous across the boundary — the encoder
writes it into volume N until the size limit, closes that volume with a
split-tagged end-of-archive marker, opens volume N+1, emits marker +
main header + file header + continuation flags, then keeps writing
bytes. The same rules apply to every codec generation (Unpack15 through
Unpack70): the decoder's UnpInitData is **not** called at a volume
boundary, only at a new file entering the codec without a continuation
flag.

When splitting a file across volumes N and N+1:

- **Decompressor state:** LZ window, repeat distances, Huffman tables,
  PPMd model (RAR 2.9+), adaptive Huffman arrays (RAR 1.3–2.0), filter
  list — everything that §1.1 lists as "carries in solid mode" — also
  carries across volume boundaries, unchanged. The volume boundary is
  invisible to the codec. This holds for every RAR version:

  | Version | State that crosses the boundary |
  |---------|--------------------------------|
  | RAR 1.3, 1.5 (Unpack15) | 64 KB window, `ChSet`/`NToPl`/`Place`, `LastDist`/`LastLength`, `Nhfb`/`Nlzb` |
  | RAR 2.0 (Unpack20) | Window, Huffman tables, `LastDist`/`LastLength`, audio filter state |
  | RAR 2.9/3.x/4.x (Unpack29) | All Unpack20 state + PPMd model + RARVM filter list |
  | RAR 5.0 (Unpack50) | Window, `_reps[0..3]`, `_lastLen`, `BlockTables`, `FirstWinDone` |
  | RAR 7.0 (Unpack70) | Same as Unpack50 (80-symbol distance table persists) |

- **Block framing:** The encoder may start a new block at the volume
  boundary, or it may continue a block across. The decoder handles
  both. Starting a new block is simpler because block headers are
  byte-aligned (RAR 5.0) or naturally re-syncable (RAR 2.x/3.x).
  Unpack15 has no explicit block structure at all — the stream ends
  only at file boundary, so cross-volume splits there are always
  mid-stream.
- **Service headers** (comments, recovery) attached to split files can
  themselves be split. They follow the same split-flag pattern.
- **Encrypted data:** AES-CBC can cross volume boundaries because the IV
  and key are already in the file header and persist in RAM. The
  16-byte block alignment must be maintained — the encoder must not
  emit a partial 16-byte block at the volume cut. RAR 1.3 XOR and
  RAR 1.5 CRC-XOR stream ciphers are also stream-continuous across
  volume boundaries; they have no block alignment to maintain.

### 2.6 End-of-archive marker placement

Every RAR volume ends with an end-of-archive marker block. In RAR 5.0
this block's flag bit `0x0001` means "not last volume" (so the decoder
knows to look for a next volume), and bit `0x0002` means "volume number
present" followed by a vint volume index. For RAR 2.x/3.x, the end
marker is `ENDARC_HEAD` (type `0x7B`) with optional volume fields.

The encoder must write the end marker **after** the last header/block of
the volume, including any trailing service headers. It must **not**
write it before the last file's split-after data.

---

## 3. Service header write procedures

Service headers are file-like records that carry metadata rather than
user file content. Common types (§10 of the RAR 5.0 spec):

| Name | Purpose |
|---|---|
| `CMT` | Archive comment. |
| `QO`  | Quick Open cache. |
| `RR`  | Recovery record. |
| `ACL` | Windows ACL / POSIX ACL data. |
| `STM` | Windows alternate data streams. |
| `AV`  | Authenticity verification (legacy). |

### 3.1 Write procedure

```
for each service header:
    1. build payload bytes (per service type)
    2. build service header (type = HEAD_SERVICE) with:
         - name field = service type string (e.g. "CMT", "QO")
         - packed size = compressed_payload_size
         - unpacked size = raw_payload_size
         - compression method = usually store (m0) for small, LZ for large
         - extra area records if applicable (hash, encryption)
    3. emit header block
    4. emit compressed payload
```

Service headers are structurally identical to file headers — they even
carry compression info, file hash, and encryption records. Only the
header type and the "name" convention differ: a file header's name is a
user-visible path; a service header's name is a well-known token from
the `SUBHEAD_TYPE_*` set in `headers5.hpp`.

### 3.2 Placement rules

Each rule below is marked **strict** (wire-protocol-required; readers will
mis-decode if violated) or **convention** (encoder choice; readers handle
any ordering).

- **CMT** — *convention*: typically the first service header in the archive,
  before any file headers. Multiple CMT records are legal (but only the first
  is user-visible in most readers). Wire format does not constrain position.
- **QO** — *strict for fast listing*: the decoder seeks to near-end-of-archive
  to find QO (`READ_SIDE_OVERVIEW.md` §5). A QO record placed mid-archive will
  not be discovered by the quick-open shortcut and the listing will fall back
  to a full walk. The header itself remains parseable in any position; only
  the optimization is lost.
- **RR** — *convention*: after all file headers but before QO. RR payload
  contains the Reed–Solomon parity over the preceding archive bytes
  (see `INTEGRITY_WRITE_SIDE.md`). Position only matters because the RR
  record itself can't protect bytes written after it.
- **ACL / STM** — *strict*: must immediately follow the file header they
  describe. An ACL header with name "ACL" after a file header for `foo.txt`
  adds an ACL to `foo.txt`. The two headers are linked by proximity, not by
  a cross-reference field — a reader attaches each ACL/STM to the most
  recent file header.
- **AV** — *convention*: anywhere; legacy. The `MainHead.PosAV` pointer (when
  present) gives the explicit position; otherwise the reader scans for block
  type `0x76`.

Verified against `_refs/unrar/arcread.cpp` block-walk invariants. The only
strict ordering rules are (a) ACL/STM-attaches-to-preceding-file-header and
(b) QO-must-be-near-end-for-fast-listing.

### 3.3 Encryption

Service headers can be encrypted just like file headers, using the same
per-file AES setup. The encoder applies the same file-hash MAC
conversion (`ENCRYPTION_WRITE_SIDE.md` §5.3) for encrypted service
payloads with hashes.

### 3.4 Archive comment write path

The archive comment is a free-form text blob associated with the
archive as a whole (not with any individual file). Three wire
representations exist across the format's history — an encoder
chooses based on the target format version:

| Format version | Representation | Block / name |
|----------------|----------------|--------------|
| RAR 1.4 (RARFMT14) | Inline after main header, length-prefixed | — |
| RAR 2.x/2.9 | `HEAD3_CMT` subblock (`0x75`), embedded inside the main archive header | `CommentHeader` struct (13 bytes + data) |
| RAR 3.0+ (RARFMT15) | Service header with name `CMT` | `HEAD_SERVICE`, `SUBHEAD_TYPE_CMT` |
| RAR 5.0 (RARFMT50) | Service header with name `CMT`, UTF-8 payload | `HEAD_SERVICE` type 3, name "CMT" |

Modern encoders should emit the RAR 5.0 form for `RARFMT50` archives
and the RAR 3.0 form for `RARFMT15`. The RAR 1.4 and RAR 2.9 forms
are legacy and should not be produced by a new encoder.

**Size limit.** compatible RAR reader defines `MAXCMTSIZE = 0x40000` (256 KB;
`rardefs.hpp:26`). This is the unpacked size bound — compressed size
is whatever the chosen compression method produces. An encoder that
exceeds 256 KB may still produce a readable archive in compatible RAR reader (the
limit is enforced at creation/display time in WinRAR, not at read
time in compatible RAR reader), but the comment may be truncated by archivers that
respect the limit. Keep comments below 256 KB.

**Text encoding.**

- **RAR 5.0:** UTF-8, no trailing zero (`arccmt.cpp:154-155`, reader
  uses `UtfToWide`). An encoder converts native wide strings to UTF-8
  and writes the raw byte sequence.
- **RAR 3.0+ (RARFMT15) service header:** two variants, selected by
  the `SUBHEAD_FLAGS_CMT_UNICODE` bit (`0x0001`) in the service
  header's `SubFlags` field (`arccmt.cpp:157`):
  - Flag clear: OEM codepage bytes (typically CP437 or the host
    default). On read, compatible RAR reader converts OEM → UTF-16 via
    `CharToWide`.
  - Flag set: "raw wide" — comment bytes are the source wide-char
    representation converted back via `RawToWide`. In practice this
    is UTF-16LE padded to the comment's byte length. Use sparingly;
    UTF-8 through the RAR 5.0 path is cleaner.
- **RAR 2.9 `HEAD3_CMT` block:** OEM codepage (no Unicode flag).
- **RAR 1.4 inline:** OEM codepage.

**Compression method.** The CMT subblock / service header's compressed
data area can be stored (`0x30`) or any LZ/PPMd method (`0x31..0x35`).
Decoder uses a fixed **64 KB window** (`CmtUnpack.Init(0x10000, false)`
at `arccmt.cpp:88`) regardless of what the archive's main files use —
an encoder must not emit a comment compressed with a larger
dictionary. Practical rule:

- If the raw comment is < 1 KB: store (`0x30`). Compression overhead
  often exceeds the saving.
- If > 1 KB: Normal method (`0x33`) with a 64 KB window is the
  conventional choice.

**Per-version header field layout — RAR 2.9 / 3.x `HEAD3_CMT`**
(`CommentHeader`, `headers.hpp:319-325`, 13-byte header + data):

| Offset | Field     | Type   | Description |
|--------|-----------|--------|-------------|
| +0     | HEAD_CRC  | uint16 | CRC16 (low 16 bits of CRC32). |
| +2     | HEAD_TYPE | uint8  | `0x75`. |
| +3     | HEAD_FLAGS | uint16 | Block flags. |
| +5     | HEAD_SIZE | uint16 | Total block size (13 + payload bytes). |
| +7     | UnpSize   | uint16 | Uncompressed comment length. Max 0xFFFF (64 KB inline); archives > 64 KB use the service-header form. |
| +9     | UnpVer    | uint8  | Minimum decoder version (15–current). |
| +10    | Method    | uint8  | Compression method `0x30..0x35`. |
| +11    | CommCRC   | uint16 | Low 16 bits of `~CRC32(0xFFFFFFFF, comment_bytes) & 0xFFFF` (`arccmt.cpp:126, 92`). Computed over the uncompressed comment for stored, or unpacked bytes for compressed. |

The `UnpSize` field is a `uint16`, capping the RAR 2.9-style inline
comment at 64 KB. Archives needing a larger comment must use the
service-header form (RAR 3.0+), where `UnpSize` is a vint and the
`MAXCMTSIZE = 256 KB` limit is the binding constraint.

**Placement.** Conventionally emitted before any file headers, right
after the main archive header. compatible RAR reader's `SearchSubBlock` scans from
the archive start (`arccmt.cpp:38-39`) so placement is not strictly
required to be first, but readers often short-circuit after the main
header.

**CRC for RAR 5.0 service-header form.** The service header itself
has a `Header CRC32` like any other RAR 5.0 block. The payload CRC is
carried by the optional `FHEXTRA_HASH` (type `0x02`, BLAKE2sp) or the
common `FHFL_CRC32` flag. The legacy low-16-bit `CommCRC` field does
not exist in RAR 5.0 — validation is via BLAKE2sp or the file-level
CRC32.

**Encoder recipe (RAR 5.0):**

```
comment_utf8 = comment_str.encode('utf-8')
assert len(comment_utf8) <= 0x40000

method = 0x30 if len(comment_utf8) < 1024 else 0x33

# Build service header
service_header = build_service_header(
    name = "CMT",
    unpacked_size = len(comment_utf8),
    compression = method,
    extra_area = [
        # Optional: attach a BLAKE2sp hash for integrity
        FHEXTRA_HASH(blake2sp(comment_utf8)),
    ],
)

# Compress payload with a 64 KB dictionary
payload = compress(comment_utf8, method, window=0x10000) if method != 0x30 else comment_utf8

emit(service_header)
emit(payload)
```

### 3.5 Archive comment read path

Mirror of §3.4 from the reader's perspective.

**Discovery.**

- **RAR 5.0 (RARFMT50):** walk header blocks from the start of the
  archive; stop at the first `HEAD_SERVICE` block whose `Name` field is
  the 3-byte ASCII string `"CMT"` (`arccmt.cpp:142-158`). Readers that
  only need the comment can stop walking after one CMT is found; a
  conforming reader ignores subsequent CMT records.
- **RAR 3.0+ (RARFMT15) service header form:** same as RAR 5.0 but the
  block type is `HEAD_NEWSUB` (`0x7A`) with name `"CMT"`.
- **RAR 2.9 `HEAD3_CMT` subblock (`0x75`):** embedded inside (or
  immediately after) the main archive header. compatible RAR reader's `SearchSubBlock`
  scans from the archive start; a reader can also short-circuit by
  checking the `MHD_COMMENT` flag (`0x0002`) on the main header.
- **RAR 1.4 inline:** length-prefixed comment immediately follows the
  main archive header extension when `MHD_COMMENT` is set. If
  `MHD_PACK_COMMENT` is also set, the extension contains
  `CmtLength`, `UnpCmtLength`, and a fixed-key comment-encrypted
  Unpack15 payload; see `RAR13_FORMAT_SPECIFICATION.md §8`.

A conforming reader tries the forms in priority order for the detected
format version and uses the first match. Only one archive comment is
user-visible even if multiple CMT records exist.

**Decompression.** Regardless of version, compatible RAR reader uses a fixed 64 KB
decoder window (`CmtUnpack.Init(0x10000, false)` at `arccmt.cpp:88`).
If the comment's `UnpVer` indicates a dictionary size larger than 64 KB
the reader must reject the comment (or treat as corrupt) — it cannot
fall back to a larger window without risking out-of-window references.
A stored comment (`method == 0x30`) bypasses decompression entirely.

**Integrity check.**

- **RAR 1.4 inline:** no separate comment CRC is present. Validate by
  respecting the main-header `HeadSize`, the declared comment lengths,
  and the Unpack15 output bound for packed comments.
- **RAR 2.9 inline (`HEAD3_CMT`):** verify the 16-bit `CommCRC`
  against the low 16 bits of `~CRC32(0xFFFFFFFF, unpacked_comment)`.
  Computed over the *uncompressed* comment regardless of method.
- **RAR 3.0+ service header form:** the enclosing service header
  carries the standard block `Header CRC32`; the payload uses whatever
  file-level hash the encoder attached (`FHFL_CRC32` or the
  BLAKE2sp-bearing `FHEXTRA_HASH`). A reader may skip payload CRC
  validation for comments — the cost of a corrupted comment is
  cosmetic, unlike a corrupted file.

**Charset decode.**

| Form | Bytes → text |
|------|--------------|
| RAR 5.0 service | UTF-8 → wide via `UtfToWide` (`arccmt.cpp:154`). Malformed UTF-8 falls back to OEM. |
| RAR 3.0+ service, `SUBHEAD_FLAGS_CMT_UNICODE` clear | OEM → wide via `CharToWide`. |
| RAR 3.0+ service, `SUBHEAD_FLAGS_CMT_UNICODE` set | `RawToWide` — treat bytes as UTF-16LE of the declared length. |
| RAR 2.9 `HEAD3_CMT` | OEM → wide. No Unicode option. |
| RAR 1.4 inline | OEM → wide. |

A reader should expose the comment as a native string (UTF-8 or UTF-16
depending on platform) after charset decode, not the raw bytes. Callers
that care about the original byte form can re-encode.

**Encrypted comments.** When the archive has header encryption
(RAR 5.0 archive encryption header, or RAR 3.x/4.x `MHD_PASSWORD`),
the CMT service header's byte payload is encrypted along with the rest
of the headers. A reader must decrypt the header stream before running
the discovery step above — the CMT name is not visible in ciphertext.
File-level password protection (per-file `-hp` without header
encryption) does not encrypt the archive comment.

**Size cap.** Enforce `MAXCMTSIZE = 0x40000` (256 KB) on the decoded
length. A reader that hits the cap mid-decompression should treat the
comment as corrupt (or truncate and warn); a longer payload likely
indicates decoder misconfiguration or a malicious archive.

**Placement reliance.** Readers must not assume the CMT is immediately
after the main header — some archives interleave files and comments —
but they may use that as a fast path. If a CMT isn't found in the
first N blocks (e.g. N = 8), fall back to a full header walk before
concluding the archive has no comment.

---

## 4. Quick Open cache contents

The Quick Open (QO) service header stores a cached copy of every file
and service header in the archive, compressed, at the end of the
archive. When a decoder is asked to list or locate files, it reads the
QO payload from the tail of the archive instead of walking every block
from the beginning — a huge speedup on archives with thousands of files.

### 4.1 Payload layout

Reader payload framing:

```
For each cached header:
    # Wrapper framing (read by ReadRaw)
    CRC32        : 4 bytes                # CRC50 of the wrapper body below
                                          # (Flags + Offset + HeaderSize + HeaderData)
    BlockSize    : vint                   # length of the wrapper body in bytes

    # Wrapper body (read by ReadNext)
    Flags        : vint                   # reserved, always emit 0
    Offset       : vint                   # backward delta: QOHeaderPos − cachedHeaderPos
                                          # (i.e. distance from the QO service header's
                                          # start back to the cached header's position)
    HeaderSize   : vint                   # size of the cached header in bytes
                                          # MUST be ≤ MAX_HEADER_SIZE_RAR5 (0x200000);
                                          # larger values abort the decoder
    HeaderData   : HeaderSize bytes       # literal copy of the original header
```

`CRC32` is the `CRC50` variant (§CRC32_SPECIFICATION.md) computed over the
wrapper body only. `BlockSize` covers the body bytes (Flags through
HeaderData) — it does not include the CRC32 field or the BlockSize vint
itself.

The payload (concatenation of all wrappers) is the `UnpSize` of the QO
service header. The decoder streams it 64 KB at a time
(`MaxBufSize = 0x10000` in `qopen.hpp:33`), so a single wrapper larger
than ~64 KB − 256 bytes forces a reshuffle but is otherwise legal; the
real ceiling is the `MAX_HEADER_SIZE_RAR5` check on `HeaderSize`.

### 4.2 What to include

The QO cache should contain every header whose seek would be expensive —
in practice, that's every file and service header in the archive.
Excluded:

- Data-region blocks (compressed file contents). These are *not*
  headers; they're payload.
- The end-of-archive marker itself. QO always precedes it.
- QO itself (obviously — infinite recursion).

### 4.3 Encoder recipe

Build the payload in two passes because the `Offset` field is measured
relative to the final QO service header position, which you only know
once the payload size is fixed:

```
def build_qo_payload(all_headers, qo_header_abs_pos):
    out = bytearray()
    for hdr in all_headers:
        if hdr.type not in (HEAD_FILE, HEAD_SERVICE):
            continue
        if hdr.type == HEAD_SERVICE and hdr.name == 'QO':
            continue
        body = (
            encode_vint(0)                                  # Flags
            + encode_vint(qo_header_abs_pos - hdr.file_offset)  # backward delta
            + encode_vint(len(hdr.raw_bytes))
            + hdr.raw_bytes
        )
        wrapper = (
            rar_crc50(body).to_bytes(4, 'little')           # CRC of body only
            + encode_vint(len(body))                        # BlockSize = len(body)
            + body
        )
        out += wrapper
    return out
```

First pass: emit all file/service headers, track their absolute
positions, compute the total payload size with a placeholder QO position.
Second pass: knowing the QO service header's final position, build the
payload with the correct backward deltas and compress (store or LZ).
Finally emit the QO as a service header named `QO`.

### 4.4 Size-benefit tradeoff

QO adds overhead proportional to the total header size. For an archive
with N files, QO roughly doubles the header region (one copy inline + one
copy at end). On archives with a few small files, this is wasteful. On
archives with thousands of files, it's invaluable — the decoder avoids
N full header-walk disk seeks.

Heuristic: emit QO only if `total_files > 64` or
`total_header_bytes > 4 KB`. Otherwise skip.

### 4.5 Trap: QO must be updateable in place

When an encoder appends a new file to an existing archive (the `u` or
`a` operation), it must:

1. Rewrite the QO service header to include the new file's cached
   header.
2. Preserve the end-of-archive marker's position relative to QO.
3. Not invalidate prior offsets — they're still valid because we only
   *append*.

An encoder that naively appends a file without updating QO produces an
archive where QO points at stale header positions, confusing the
decoder. The safest option on update: strip the old QO, append the new
file, regenerate QO from scratch, re-emit the end-of-archive marker.

---

## 5. File system metadata encoding

### 5.1 Symlinks (RAR 5.0 File System Redirection Record)

Symlinks are encoded via the File System Redirection record (extra area
record type `0x05`, §8 of the RAR 5.0 spec):

```
Redirection Record type 0x05:
    Type       : vint    # FSREDIR_* value (see table below)
    Flags      : vint    # bit 0: "redirect target is a directory"
    NameLength : vint    # byte count of the target name UTF-8 string
    Name       : NameLength bytes    # target path (UTF-8)
```

`FSREDIR_*` values:

| Value | Meaning |
|---|---|
| 0x0001 | `FSREDIR_UNIXSYMLINK` — POSIX symlink |
| 0x0002 | `FSREDIR_WINSYMLINK` — Windows symbolic link |
| 0x0003 | `FSREDIR_JUNCTION` — Windows junction point |
| 0x0004 | `FSREDIR_HARDLINK` — hard link |
| 0x0005 | `FSREDIR_FILECOPY` — reference to another file's content (deduplication) |

The file header carrying this extra record should have `PackedSize = 0`
(no data payload) for all types except `FILECOPY`, which points at
another file within the same archive by name and still has its own
separate hash.

### 5.2 Hardlinks

Encoded as `FSREDIR_HARDLINK` with the target filename in the Name
field. The decoder creates a hard link to the named file after
extraction. The encoder must emit the target file **before** the
hardlink file in archive order — otherwise the target doesn't exist yet
at link creation time.

### 5.3 High-precision times (File Time Record, type 0x03)

```
File Time Record type 0x03:
    Flags : vint
        bit 0: modification time present
        bit 1: creation time present
        bit 2: access time present
        bit 3: Unix time format (else Windows FILETIME)
        bit 4: nanosecond precision (only with Unix time)
    modification_time : uint32 or uint64     (if bit 0)
    creation_time     : uint32 or uint64     (if bit 1)
    access_time       : uint32 or uint64     (if bit 2)
```

For Unix-format times without nanoseconds, each time is a `uint32`
(seconds since epoch). With nanoseconds, each is a `uint64` (nanoseconds
since epoch). For Windows FILETIME, each is a `uint64` (100-nanosecond
ticks since 1601-01-01 UTC).

Encoder rule: pick the format that matches the source filesystem. On
Unix, prefer Unix nanosecond format; on Windows, prefer Windows FILETIME.
Mixing formats across files in the same archive is legal but confusing.

### 5.4 Windows ACLs (service header `"ACL"`)

The ACL service header carries a Windows `SECURITY_DESCRIPTOR` in
self-relative form, written verbatim to the target file via Win32
`SetFileSecurity()` at extract time. Verified against
`_refs/unrar/win32acl.cpp` (`ExtractACL20`, `ExtractACL`).

**RAR 2.x wire format** (`HEAD3_NEWSUB = 0x7a`, `EAHeader` extending
`SubBlockHeader`, body bytes after the standard 7-byte block header):

| Offset | Field    | Type    | Notes |
|--------|----------|---------|-------|
| +0     | UnpSize  | uint32  | Unpacked SECURITY_DESCRIPTOR size in bytes. |
| +4     | UnpVer   | uint8   | Unpack codec version (must be ≤ `VER_PACK`). |
| +5     | Method   | uint8   | Compression method, must be `0x31..0x35` (no stored mode for ACLs). |
| +6     | EACRC    | uint32  | CRC32 of the unpacked SECURITY_DESCRIPTOR. |
| +10    | DataSize | bytes   | Inherited from the standard block header; compressed payload follows. |

After decompression: the bytes are a Windows
`SECURITY_DESCRIPTOR` self-relative blob. The reader passes them
unchanged to `SetFileSecurity` with `OWNER | GROUP | DACL` security
information (plus `SACL` if the user has `SeSecurityPrivilege`). The
RAR format does not parse the SECURITY_DESCRIPTOR internals — the
encoder stores the bytes from `GetFileSecurity` and the decoder writes
them back via `SetFileSecurity`.

**RAR 5.0 wire format**: a service header named `"ACL"` whose data
area carries the same SECURITY_DESCRIPTOR self-relative bytes
(possibly compressed per the standard service-header `Method`). The
preceding file header's owner is the file the ACL applies to (per
ACL/STM placement rule in §3.2).

Encoder responsibility: extract the SECURITY_DESCRIPTOR via
`GetFileSecurity` (Win32) at archive time, store as the service
header's payload. On non-Windows hosts the doc convention is to skip
ACL emission rather than synthesize an equivalent.

### 5.4.1 NTFS Alternate Data Streams (service header `"STM"`)

The STM service header carries the contents of one NTFS Alternate Data
Stream (ADS) attached to the previously-emitted file. Verified against
`_refs/unrar/win32stm.cpp` (`ExtractStreams20`, `ExtractStreams`,
`GetStreamNameNTFS`).

**RAR 2.x wire format** (`HEAD3_NEWSUB = 0x7a`, `StreamHeader` extending
`SubBlockHeader`):

| Offset | Field          | Type            | Notes |
|--------|----------------|-----------------|-------|
| +0     | UnpSize        | uint32          | Unpacked ADS content size. |
| +4     | UnpVer         | uint8           | Unpack codec version. |
| +5     | Method         | uint8           | Must be `0x31..0x35`. |
| +6     | StreamCRC      | uint32          | CRC32 of the unpacked ADS bytes. |
| +10    | StreamNameSize | uint16          | Length of the stream name in bytes (max 260, OEM/native encoding). |
| +12    | StreamName     | StreamNameSize bytes | ADS name including the leading `:` (e.g. `:Zone.Identifier`). Slashes (`\` or `/`) are not allowed. |
| +12+SNS| DataSize       | bytes           | Inherited from block header; compressed ADS contents. |

After decompression: raw bytes of the ADS, written to
`host_file_path + StreamName` via Win32 `CreateFile`. The combined
path forms a colon-suffixed NTFS path (e.g. `C:\file.txt:Zone.Identifier`).

**RAR 5.0 wire format**: a service header named `"STM"` whose
**Sub-data extra-area field** carries the stream name (UTF-8 in RAR 5.0,
raw OEM in RAR 2.x — see `GetStreamNameNTFS` in `win32stm.cpp:218`),
and whose data area carries the ADS content. The preceding file
header is the host file.

**Security rule (`IsNtfsProhibitedStream`).** Reader must reject
any stream name containing more than one colon. This blocks shenanigans
like:

- `:file::$DATA` (the `$DATA` type would let an attacker hide content
  inside the host file's main data stream rather than a separate ADS)
- `:Zone.Identifier:$DATA` (would overwrite the Mark-of-the-Web by
  routing to the main `Zone.Identifier` stream content rather than as
  an alternate stream — ZDI-CAN-23156)

Encoders must therefore emit single-colon stream names only (e.g.
`:Zone.Identifier`, never `:Zone.Identifier:$DATA`). The implicit type
is always `:$DATA`.

### 5.4.2 Other extended attributes

POSIX-style extended attributes have no standardized RAR service-header
name in current public source. Vendor-specific tokens are tolerated by
readers (skipped via `HeadSize` per §10) but interoperability across
encoders is not guaranteed. An encoder that needs to round-trip POSIX
xattrs cannot use a defined RAR service header for them.

### 5.5 Unix owner record (type 0x06)

```
Unix Owner Record type 0x06:
    Flags : vint
        bit 0: owner name present
        bit 1: group name present
        bit 2: numeric owner ID present
        bit 3: numeric group ID present
    [owner name]         : vint length + UTF-8 bytes (if bit 0)
    [group name]         : vint length + UTF-8 bytes (if bit 1)
    [uid]                : vint (if bit 2)
    [gid]                : vint (if bit 3)
```

The encoder should emit both the name and the numeric ID — the decoder
prefers the name (for portability) but falls back to the numeric ID when
the name doesn't exist on the target system.

---

## 5.6 Self-extracting (SFX) stub

A RAR archive can be prefixed with an executable loader so that running
the file extracts the archive payload. The decoder side is straight-
forward: a reader checks the first
few bytes, and if they don't match a RAR signature, scans up to
`MAXSFXSIZE` bytes looking for a valid signature and records the offset
as `SFXSize`.

### 5.6.1 Wire format

```
[stub bytes, 0 .. SFXSize)        ← executable loader (opaque)
[RAR signature]                   ← SFXSize is the offset of this byte
[archive body as usual]
```

The stub is **opaque** to the archive format. The decoder never parses
the stub — it just scans for a signature and uses the offset. An
encoder produces an SFX archive by concatenating:

1. A stub binary (Windows PE, Linux ELF, etc.) of its choice.
2. The full normal RAR archive (starting with the 7- or 8-byte
   signature marker).

No additional metadata is written to tie the two together; the stub
knows how to find the archive because its own code does the same
signature search the decoder does.

### 5.6.2 Maximum stub size

```
#define MAXSFXSIZE 0x400000    // 4 MiB  (rardefs.hpp:24)
```

The decoder reads `MAXSFXSIZE - 16` bytes from the start of the file
and scans for a signature. **A stub larger than 4 MiB causes the
archive body to be unreachable** — the decoder will report "not a RAR
archive" because its scan window doesn't extend that far.

Practical stubs from WinRAR are 100–300 KiB (Default.SFX, Zip.SFX,
WinCon.SFX). A clean-room encoder that ships its own stub should stay
well under 1 MiB to leave headroom and match decoder expectations.

### 5.6.3 Signature search offsets

The decoder scans byte-by-byte for a byte `0x52` ("R") at any offset in
`[0, MAXSFXSIZE - 16)`, then tests whether the following bytes form a
valid signature. The first match wins.

**False-match hazard.** If the stub binary happens to contain the
literal bytes `52 61 72 21 1A 07 00` or `52 61 72 21 1A 07 01 00`
**before** the real archive start, the decoder will mis-identify the
archive start at that earlier offset and fail to parse (because the
bytes following the fake signature won't form a valid main header).

In practice, PE and ELF binaries rarely contain this sequence by
accident — `52 61 72 21` is "Rar!" in ASCII, which is unusual as a
code or constant. **But**: stubs that bundle a sample RAR archive as a
resource (for testing or branding), or stubs that embed the signature
as a literal string for their own archive-detection logic, will
trigger a false match.

Encoder rule: **scan the stub bytes for any occurrence of the target
signature bytes before concatenating.** If found, either patch the
stub to XOR-obfuscate its own copies of the signature, or reject the
stub. The simplest production-quality check:

```python
def validate_sfx_stub(stub_bytes: bytes, rar_format: str) -> None:
    if rar_format == "RAR15":
        sig = b"\x52\x61\x72\x21\x1A\x07\x00"
    elif rar_format == "RAR50":
        sig = b"\x52\x61\x72\x21\x1A\x07\x01\x00"
    else:
        raise ValueError(rar_format)

    if sig in stub_bytes:
        raise ValueError(
            f"stub contains embedded {rar_format} signature at "
            f"offset {stub_bytes.find(sig)} — will confuse decoder")

    if len(stub_bytes) >= 0x400000 - 16:
        raise ValueError("stub exceeds MAXSFXSIZE scan window")
```

### 5.6.4 RAR 1.4 special case

`archive.cpp:161-166` contains a carve-out for the RAR 1.4 signature
(`52 45 7E 5E`, "RE~^", only 4 bytes — short enough to collide with
random data). The decoder requires that the 4-byte RAR 1.4 signature
be preceded by the literal ASCII bytes `RSFX` at **file offset 28**.
If those aren't present, the match is rejected and scanning continues.

A clean-room encoder producing RAR 1.4 SFX output must:

1. Emit a stub whose byte at offsets 28..31 is `RSFX` (0x52 0x53
   0x46 0x58).
2. Place the RAR 1.4 signature somewhere after offset 28 but within
   `MAXSFXSIZE`.

For RAR 1.5 and RAR 5.0 formats the `RSFX` marker is **not** required
— the 7- or 8-byte signature is long enough to be false-match
resistant. A modern encoder should not emit RAR 1.4 at all (legacy
format, no round-trip value), so in practice `RSFX` is a historical
curiosity only.

Empirical SFX stub notes from the local historical binaries:

- `_refs/rarbins/rar250.exe` is a RAR 2.50 SFX package. Its RAR marker starts
  at offset `29489`, and the file has `RSFX` at absolute offset 28.
- Extracting that package with historical UnRAR 3.00 yields `IDOS.SFX` of size
  `29489`; `IDOS.SFX` also has `RSFX` at absolute offset 28.
- `fixtures/1.54/readme.EXE` has a RAR 1.5 marker at offset `7259` and also
  has `RSFX` at absolute offset 28, but the RAR 1.5 reader path does not require
  this marker.
- A RAR 3.00 DOS SFX generated with `-sfxdos.sfx` has its RAR marker at offset
  `93816` and does **not** contain `RSFX` at offset 28; this is valid because
  the RSFX gate is specific to RAR 1.4-style SFX detection.

### 5.6.5 Encoder recipe

```
1. Select target stub binary (PE for Windows targets, ELF for Linux).
2. Validate:
   - stub_size < MAXSFXSIZE - 16
   - no embedded RAR signature false-matches (see §5.6.3)
   - for RAR 1.4 only: bytes 28..31 == b"RSFX"
3. Write stub bytes to output.
4. Write normal RAR archive starting at offset SFXSize = len(stub):
   - Marker
   - [HEAD_CRYPT if header-encrypted]
   - Main header (archive flags, etc.)
   - File headers + payloads
   - Recovery record
   - End of archive
5. Set the output file's execute bit on Unix
   (`os.chmod(path, 0o755)`).
```

Nothing inside the archive body needs to know it's an SFX — main
header offsets are still measured from the marker, not from file
offset 0. The `SFXSize` value is computed on read by the decoder and
is purely an implementation detail of the scan.

### 5.6.6 Source of stub binaries

**No open-source RAR distribution ships a clean-room SFX stub.**
WinRAR's SFX binaries (Default.SFX, Zip.SFX, WinCon.SFX, rarsfxwin,
rarsfxcon) are proprietary Windows executables distributed only with
the commercial WinRAR package. Options for an encoder:

1. **Re-use a WinRAR stub verbatim.** Legal for redistribution under
   the WinRAR license for end-user-created SFX archives, but check
   the license terms for your use case. Simplest to implement: the
   encoder ships a blob and prepends it.
2. **Write a minimal clean-room stub.** A stub's job is: read its own
   executable, find the RAR signature, hand the offset to an embedded
   compatible RAR reader decoder, unpack to a user-chosen directory. On Linux this is
   ~200 lines of C around libarchive or a clean-room compatible RAR reader. On
   Windows it needs a PE file and a GUI for directory selection.
3. **Skip SFX entirely.** An encoder that only produces `.rar` files
   can ignore this whole section — users who want SFX can re-pack
   with WinRAR afterwards. This is the recommended path for a
   clean-room encoder targeting correctness rather than feature
   parity.

### 5.6.7 Interaction with other archive features

- **Multi-volume archives.** Only the **first volume** (part001) carries
  the SFX stub. Subsequent volumes start with a plain marker. The stub
  is responsible for finding and opening follow-up volumes by
  filename pattern.
- **Recovery records.** The recovery record protects the **archive
  portion** only, not the stub bytes. Damage to the stub is undetected
  by the recovery code. This is a legitimate attack surface: an
  adversary can swap the stub for a malicious binary and the archive
  recovery will happily confirm the (unchanged) payload is intact.
  Users opening SFX archives from untrusted sources should treat them
  as executables, not archives.
- **Header encryption.** Compatible but redundant: the stub is
  unencrypted regardless. An attacker with the SFX file can see the
  stub's code, read the HEAD_CRYPT salt/PswCheck, and attempt a
  password attack without executing anything.
- **Archive comment.** Stubs often display the archive comment during
  extraction. The main header comment is stored as a service record
  (§3 of this doc) and the stub reads it via an embedded mini-parser.

---

## 6. Implementation priority

For a minimal working RAR 5.0 encoder:

**Must have:**
- Solid mode state persistence (§1)
- Basic file header emission (§8 of RAR 5.0 spec)
- End-of-archive marker

**Should have:**
- High-precision times (§5.3)
- Multi-volume splitting (§2) — essential if targeting large archives
- QO cache (§4) — for usability on archives with > 100 files

**Nice to have:**
- Symlinks (§5.1)
- Hardlinks (§5.2) with correct emission order
- Service headers for comments and recovery (§3)

**Skip unless requested:**
- ACLs, xattrs, Windows ADS (§3, §5.4)
- Unix owner records (§5.5)
- AV authenticity headers (legacy)

---

## 7. Test oracle

1. **Round-trip test.** For each feature above, encode a test archive
   and verify `reference decoder ` extracts it byte-exact (for data) and
   metadata-exact (for links, times, attributes).
2. **Solid mode correctness.** Verify that a solid archive of 100 small
   similar files compresses smaller than 100 non-solid archives of the
   same files combined, and that random-access extraction (using
   `rar e archive.rar file50.txt`) correctly walks through files 1–49
   first.
3. **Multi-volume correctness.** Split an archive into 3+ volumes and
   verify:
   - Deleting any middle volume produces a clean "missing volume" error.
   - The RR service header (if present) can recover a lost sector
     within a volume.
4. **QO consistency.** Verify that a QO-enabled archive produces
   identical file listings whether read via full header walk
   (forced by deleting QO) or via QO shortcut.
