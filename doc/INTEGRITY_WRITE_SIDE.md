# RAR Integrity Primitives — Write-Side Specification

This document covers the encoder-side integrity primitives in RAR:

1. **BLAKE2sp** hashing for RAR 5.0 file-content digests
2. **Reed–Solomon recovery records** embedded inline in RAR archives
3. **Reed–Solomon recovery volumes** (`.rev` files) for RAR 3.x and 5.0
4. Differences between the 8-bit (`RSCoder`) and 16-bit (`RSCoder16`) Reed–
   Solomon codecs

CRC32 is already fully specified in `CRC32_SPECIFICATION.md`. AES-based MAC
conversion for encrypted file hashes is covered in
`ENCRYPTION_WRITE_SIDE.md` §5.3.

References:
- Public BLAKE2sp test vectors and reference implementation
- Public reader behavior for 8-bit Reed-Solomon recovery data
- Public reader behavior for 16-bit Reed-Solomon recovery data
- Public reader behavior for RAR 3.x `.rev` files
- Public reader behavior for RAR 5.0 `.rev` files
- Public reader behavior for recovery-volume dispatch

## 0. Integrity primitive availability by version

RAR's integrity story grew over time. What an encoder must emit depends
on the target version:

| Primitive | RAR 1.3/1.4 | RAR 1.5–4.x | RAR 5.0 | RAR 7.0 |
|-----------|:-----------:|:-----------:|:-------:|:-------:|
| File-data checksum | 16-bit rolling sum+rotate | CRC32 | CRC32 or BLAKE2sp (selectable) | CRC32 or BLAKE2sp |
| Header CRC | **none** | CRC16 (§8.1) | CRC32 (§8.2) | CRC32 |
| Reed–Solomon recovery record | — | `RSCoder` (§3) | `RSCoder16` (§4) | `RSCoder16` |
| `.rev` recovery volumes | — | RAR 3.x+ only (§3.5) | §4.7 | §4.7 |
| BLAKE2sp content hash | — | — | §1 | §1 |

**RAR 1.3 is deliberately minimal.** A compliant encoder emits only the
16-bit file checksum field described in §11 of
`RAR13_FORMAT_SPECIFICATION.md`; this is a rolling 16-bit sum+rotate over the
uncompressed plaintext bytes, not low-16 CRC32. RAR 1.3 must **not** emit header
CRCs (there is no field for them in the RAR 1.3 block layout), recovery records,
or BLAKE2sp. Modernising a RAR 1.3 archive therefore means re-emitting it as
RAR 2.0 or newer — see the
recommendation in `RAR13_FORMAT_SPECIFICATION.md` §6.16.10.

The remainder of this document targets RAR 1.5 onwards. Sections §1–§7
cover BLAKE2sp and the Reed–Solomon codecs; §8 covers the block-header
CRC rules.

---

## 1. BLAKE2sp (RAR 5.0+)

BLAKE2sp is the 8-way parallel variant of BLAKE2s, producing a 32-byte
(256-bit) digest. The design is standard (Samuel Neves, 2012, public
domain — see https://blake2.net). RAR 5.0 invokes it as a drop-in hash
function; no RAR-specific twist.

### 1.1 State layout

```
struct blake2sp_state:
    blake2s_state R           # root state (folds 8 leaf digests)
    blake2s_state S[8]        # 8 leaf states (hash data in parallel)
    uint8 buf[8 * 64]         # 512-byte input buffer (8 leaves × 64 byte block)
    size_t buflen             # bytes currently in buf
```

### 1.2 Init

```
blake2s_init_param(R, /*node_offset=*/0, /*depth=*/1, /*is_root=*/true)
for i in 0..7:
    blake2s_init_param(S[i], /*node_offset=*/i, /*depth=*/0, /*is_root=*/false)
R.last_node     = true      # root is always last
S[7].last_node  = true      # leaf 7 is the last leaf
buf      = zero
buflen   = 0
```

The `node_offset`, `depth`, and `is_root` parameters enter the BLAKE2s
initialization vector per the BLAKE2 tree-hashing mode in the BLAKE2
specification §2.10. `compatible RAR reader`'s `blake2s_init_param` packs these into the
`h[]` array exactly as the standard requires.

#### 1.2.1 Full BLAKE2 parameter block

The 32-byte BLAKE2 parameter block — required when configuring a
generic BLAKE2s library to interoperate with RAR's BLAKE2sp variant —
is fully constrained. Verified against `_refs/unrar/blake2s.cpp`
(`blake2s_init_param`, lines 58-72) where the IV-XOR pattern
`h[0] ^= 0x02080020`, `h[3] ^= 0x20000000 | (node_depth << 16)` decodes
to the byte layout below:

| Field             | Width | Value (RAR's BLAKE2sp) |
|-------------------|------:|-----------------------|
| `digest_length`   | 1     | `0x20` (32 bytes — full BLAKE2s output) |
| `key_length`      | 1     | `0x00` (no key — RAR doesn't keyed-hash) |
| `fanout`          | 1     | `0x08` (8-way parallelism) |
| `max_depth`       | 1     | `0x02` (root + leaves) |
| `leaf_length`     | 4     | `0x00000000` (unbounded leaf chunk) |
| `node_offset`     | 6     | `0..7` for leaves, `0` for root |
| `node_depth`      | 1     | `0` for leaves, `1` for root |
| `inner_length`    | 1     | `0x20` (each leaf produces 32-byte inner digest) |
| `salt`            | 8     | zero |
| `personalization` | 8     | zero |

The `last_node` flag is **separate from** the parameter block (it lives
in the per-state runtime field, not in the IV-XOR). RAR sets it on:
- the root state (`R`), unconditionally
- the eighth (final) leaf state (`S[7]`), unconditionally

It is **not** propagated to leaves 0..6 even when the input ends
mid-block on those leaves. Verified against `blake2sp_init` in
`_refs/unrar/blake2sp.cpp` (lines 16-28).

A generic BLAKE2s library that exposes a `blake2s_init_param` taking a
full parameter struct can produce bit-identical output by populating
the table above and toggling `last_node` per the rules.

### 1.3 Update

Input bytes are distributed round-robin across the 8 leaves in 64-byte
chunks: bytes 0..63 → leaf 0, 64..127 → leaf 1, ..., 448..511 → leaf 7,
512..575 → leaf 0 again, etc.

```
def update(S, in_bytes):
    # 1. Fill the internal buf to 512 bytes if currently partial
    left = S.buflen
    fill = 512 - left
    if left > 0 and len(in_bytes) >= fill:
        S.buf[left:512] = in_bytes[:fill]
        # Feed all 8 leaves with their respective 64-byte blocks
        for i in 0..7:
            blake2s_update(S.S[i], S.buf[i*64:(i+1)*64], 64)
        in_bytes  = in_bytes[fill:]
        left = 0

    # 2. Feed full 512-byte groups directly (no buffering)
    while len(in_bytes) >= 512:
        for i in 0..7:
            blake2s_update(S.S[i], in_bytes[i*64:(i+1)*64], 64)
        in_bytes = in_bytes[512:]

    # 3. Stash the tail in buf
    S.buf[left:left+len(in_bytes)] = in_bytes
    S.buflen = left + len(in_bytes)
```

### 1.4 Final

```
def final(S) -> 32 bytes:
    hash = [ [0]*32 for _ in range(8) ]
    for i in 0..7:
        # Feed any tail bytes destined for leaf i
        lower = i * 64
        if S.buflen > lower:
            tail_len = min(S.buflen - lower, 64)
            blake2s_update(S.S[i], S.buf[lower:lower+tail_len], tail_len)
        blake2s_final(S.S[i], hash[i])

    for i in 0..7:
        blake2s_update(S.R, hash[i], 32)

    return blake2s_final(S.R)
```

### 1.5 Encoder vs decoder

Hashing is **symmetric** — the same function writes and verifies. There's
no separate "encoder" for a hash; the encoder just feeds the plaintext
file bytes through `update` as it writes them and stores the final digest
in the File Hash extra record (type 0x02, hash type 0x00 = BLAKE2sp) of
the RAR 5.0 file header.

If the file or service data area has a RAR5 encryption extra record with the
`HashMAC` flag set, the digest is subsequently converted to a MAC as described
in `ENCRYPTION_WRITE_SIDE.md` §5.3 before being written to the header.
Archive-wide header encryption alone does not change the digest value.

### 1.6 SIMD path

Some readers provide an SSE-accelerated `blake2s_compress`
replacement that speeds up each leaf's compression function. It is a
drop-in replacement for the reference scalar version and produces
bit-identical output. Optional — the scalar path is plenty fast for most
single-file hashing.

---

## 2. Reed–Solomon fundamentals shared by both variants

Both RAR RS codecs are systematic linear codes over a Galois field:
given `ND` data units, produce `NR` parity units such that any `ND` of
the combined `ND+NR` units can reconstruct the original `ND` data units.
The difference is the **symbol size** and the **encoder construction**:

| Variant   | Field     | Polynomial         | Max shards   | Used by              |
|-----------|-----------|--------------------|--------------|----------------------|
| RSCoder   | GF(2^8)   | `0x11D`            | 255 total    | RAR 2.x/3.x recovery |
| RSCoder16 | GF(2^16)  | `0x1100B`          | 65535 total  | RAR 5.0 recovery     |

Both use **Cauchy-style** or **BCH-style** generators and both are
systematic (data bytes pass through unchanged; parity is appended).

---

## 3. RSCoder (8-bit GF(2^8), RAR 2.x/3.x)

a public RAR reader — ~160 lines, readable from top to bottom. Classic
LFSR-based encoder for a BCH-style Reed–Solomon code with generator
polynomial `g(x) = (x - α¹)(x - α²)...(x - α^N)` where α is the
primitive element of `GF(2^8)` under the field-generator polynomial
`x^8 + x^4 + x^3 + x^2 + 1` (i.e. `0x11D`).

### 3.1 Initialization

```
def gf_init():
    # Precompute log/exp tables for fast GF(2^8) multiplication.
    gfLog = [0] * 256
    gfExp = [0] * 510
    j = 1
    for i in range(255):
        gfLog[j] = i
        gfExp[i] = j
        j <<= 1
        if j > 255:
            j ^= 0x11D
    for i in range(255, 510):
        gfExp[i] = gfExp[i - 255]      # doubled table avoids overflow check

def init(par_size):                     # par_size = number of parity symbols = NR
    gf_init()
    pn_init()                            # builds the generator polynomial GXPol[]
```

The doubled `gfExp` table is a common optimization: it lets
`gfMult(a, b) = gfExp[gfLog[a] + gfLog[b]]` work without a modulo-255
check, because the sum never exceeds `510`.

### 3.2 Generator polynomial

```
def pn_init(par_size):
    # g(x) starts at 1
    p2 = [1] + [0] * par_size
    for i in range(1, par_size + 1):
        # p1 = x + α^i
        p1 = [gfExp[i], 1] + [0] * (par_size - 1)
        GXPol = pn_mult(p1, p2)          # p2 * p1
        p2 = GXPol
    # GXPol is now g(x) of degree par_size
```

`pn_mult` is a straightforward polynomial multiplication over `GF(2^8)`
(add-by-XOR, multiply-by-gfMult).

### 3.3 Systematic encoder (LFSR)

The write side is `RSCoder::Encode`:

```
def encode(data, data_size, par_size) -> parity_bytes:
    shift = [0] * (par_size + 1)
    for i in range(data_size):
        d = data[i] ^ shift[par_size - 1]
        for j in range(par_size - 1, 0, -1):
            shift[j] = shift[j - 1] ^ gfMult(GXPol[j], d)
        shift[0] = gfMult(GXPol[0], d)
    parity = [0] * par_size
    for i in range(par_size):
        parity[i] = shift[par_size - i - 1]
    return parity
```

This is a classic Galois-field LFSR: each input byte is XOR-ed with the
top of the register, then the register shifts with feedback taps from
`GXPol[]`. After consuming all `data_size` data bytes, the register
contains the `par_size` parity bytes (in reverse order — the final output
loop unreverses them).

**Complexity:** `O(data_size × par_size)` multiplications. For RAR's
usage with ~5% recovery record (~12 parity bytes per 256-byte block),
this is cheap.

### 3.4 Inline recovery record layout (RAR 2.x/3.x archive)

The recovery record is a block of header type `0x78`
(`HEAD3_PROTECT`). Its presence is advertised by the `MHD_PROTECT` bit
(`0x0040`) in the main archive header's flags
(`arcread.cpp:236`).

**Header layout (26 bytes, `SIZEOF_PROTECTHEAD` in `headers.hpp:13`):**

| Offset | Field         | Type    | Description |
|--------|---------------|---------|-------------|
| +0     | `HEAD_CRC`    | uint16  | CRC16 over bytes +2 through end of header. |
| +2     | `HEAD_TYPE`   | uint8   | `0x78`. |
| +3     | `HEAD_FLAGS`  | uint16  | Block flags. Always `0xC000` in observed RAR 2.50 fixtures (`LONG_BLOCK` + the `0x4000` bit, the latter signalling "skip if unknown" semantics in the RAR 2.x flag space). |
| +5     | `HEAD_SIZE`   | uint16  | Header size (always 26). |
| +7     | `ADD_SIZE`    | uint32  | Byte count of the data area that follows (`ProtectHeader::DataSize`, `arcread.cpp:468`). Equal to `TotalBlocks * 2 + RecSectors * 512` — see "Reconstructed data-area layout" below. |
| +11    | `Version`     | uint8   | RAR-format version of the protected archive (i.e., `UNP_VER`-style: `0x14 = 20` for RAR 2.0/2.50 inputs). The 2002-era public spec text claimed "always 1", but observed RAR 2.50 fixtures write `0x14` here. |
| +12    | `RecSectors`  | uint16  | `N` — number of 512-byte **parity** sectors emitted (i.e., the `-rrN` value with N ≤ 8 in RAR 2.50). |
| +14    | `TotalBlocks` | uint32  | Number of full 512-byte **data** sectors covered by this record — `floor(protected_byte_count / 512)`. Trailing 0..511 bytes are **not** protected and don't appear in either the tag table or the parity. Confirmed by `fixtures/1.5-4.x/rar250_protect_head_rr1.rar` and `…_rr5.rar` (both have a 102971-byte preceding archive → 201 full sectors + 59 unprotected trailing bytes). |
| +18    | `Mark[8]`     | 8 bytes | The literal ASCII `"Protect!"` (`50 72 6f 74 65 63 74 21`). Not random / not zero — both RAR 2.50 fixtures committed in `fixtures/1.5-4.x/` write the same eight bytes. Public readers don't interpret; an encoder targeting compatibility should write the same constant. |

**What public readers tell us, and what they don't.** Public readers
read the header and advance past `DataSize` bytes — they never
actually **decode** an inline recovery record (recovery from a
PROTECT_HEAD is in WinRAR's proprietary encoder path). The header
layout above is fully verified against committed fixtures
(`fixtures/1.5-4.x/rar250_protect_head_rr1.rar` /
`…_rr5.rar`). The data-area layout below is verified for the
sector-tag region (sizes, layout, last-sector tag differs) but the
exact 16-bit per-sector tag algorithm and the byte-level placement
of RS parity remain partially reconstructed from the `.rev` format
(which public readers do decode).

Empirical boundary from available binaries:

- RAR 2.50 (registered shareware path) writes `PROTECT_HEAD` (`0x78`)
  when `-rrN` is given (`N=1..8`). The two committed fixtures pin
  `HEAD_FLAGS = 0xC000`, `HEAD_SIZE = 26`,
  `ADD_SIZE = TotalBlocks*2 + RecSectors*512`,
  `Version = 0x14` (`UNP_VER` of the protected archive),
  `Mark[8] = "Protect!"`. This contradicts the older
  "no available generated fixture exercises this format" claim — fixtures now
  exist.
- RAR 3.00 and RAR 4.20 `-rrN` produce a RAR 3.x `NEWSUB_HEAD` named
  `RR`, **not** `PROTECT_HEAD`. Tiny one-file `-rr10` archive yields
  `HEAD_TYPE = 0x7a`, `HEAD_FLAGS = 0xc000`, `HEAD_SIZE = 54`,
  `ADD_SIZE = 514`, `Name = "RR"` in both versions (offset 106 in
  3.00, 111 in 4.20).

**Data-area layout (RAR 2.50, fully verified against fixtures):**

```
sector_tags    : TotalBlocks × 2   # 16-bit tag per protected sector
parity_sectors : RecSectors × 512  # interleaved XOR parity (NOT RS!)
```

So `ADD_SIZE = TotalBlocks*2 + RecSectors*512`. The protected data
itself is **not copied** into the recovery record — only the parity
slots and per-sector integrity tags are. (Unlike the `.rev` file
format, which keeps data and parity in separate volumes.)

#### Per-sector tag

```python
tag = ~zlib.crc32(sector_bytes) & 0xFFFF
```

i.e. the **low 16 bits of CRC32's running state** (before the final
XOR-with-`0xFFFFFFFF` finalization). Equivalently: bitwise-NOT of
the standard CRC32, then take the low 16 bits. Same internal-CRC
convention as the modern AV body's inverted-CRC32 field at `+0x1B`
(see `RAR15_40_FORMAT_SPECIFICATION.md §10.4`) — the encoder calls
`update_crc32_dispatch(0xFFFF, 0xFFFF, sector, ds, 0x200)` and stores
the returned uint16 directly without inverting.

Only **full 512-byte sectors** are protected — `TotalBlocks` is
`floor(protected_bytes / 512)`, and any trailing 0..511 bytes are
silently unprotected (recovery cannot restore them). This is a
RAR 2.50 quirk; the RAR 3.x+ `.rev` codec zero-pads to a full sector
boundary instead.

Verified against four fixtures (604/604 sector tags match):
`fixtures/1.5-4.x/rar250_protect_head_rr1.rar` (TotalBlocks=201),
`…_rr5.rar` (TotalBlocks=201), and two further test variants.

#### Parity sectors — interleaved XOR (not Reed–Solomon!)

Despite living in the same toolchain as the `.rev` Reed–Solomon
codec, the inline `PROTECT_HEAD` parity is **simple XOR with
round-robin sector assignment**:

```python
parity = [bytearray(512) for _ in range(RecSectors)]
for i, sector in enumerate(data_sectors):   # last sector zero-padded
    slot = i % RecSectors
    for c in range(512):
        parity[slot][c] ^= sector[c]
```

Equivalently: `parity[k] = XOR of {data_sectors[k], data_sectors[k+RecSectors], data_sectors[k+2·RecSectors], …}`.

This is what the RAR 2.50 `RAR.EXE` encoder loop at `0x1ec9:d9b2`
literally does — confirmed by reading the decompilation and
byte-validating all 6 parity sectors across the `-rr1` and `-rr5`
fixtures (they all match).

**Recovery semantics.** Because each parity slot covers a single
stride-`RecSectors` subset of the data, the decoder can recover:

- Up to **`RecSectors` corrupted sectors total**, *if and only if* no
  two of them fall in the same group (i.e., `index mod RecSectors`
  must be distinct). For example, with `-rr5` you can recover any 5
  losses provided no two are 5 apart.
- A run of up to `RecSectors` **contiguous** missing sectors —
  always recoverable, since contiguous indices have distinct
  `mod RecSectors` values.

This is much weaker than the GF(256) Reed–Solomon scheme used in
RAR 3.x+ `.rev` files. The 2002-era public claim that PROTECT_HEAD
shares the `RSCoder` path is **incorrect** — the inline format is a
simpler interleaved-XOR scheme that predates the proper RS
machinery. The RS column-mode recipe documented earlier in this
section applies to `.rev` files (§3.5), not PROTECT_HEAD.

```python
def encode_recovery_record(archive_bytes, rec_sectors):
    # rec_sectors == N from -rrN  (1..8 in RAR 2.50)
    P = rec_sectors
    total_blocks = len(archive_bytes) // 512   # floor — partial trailing bytes are NOT protected
    # (any trailing 0..511 bytes are silently unprotected; recovery can't restore them)

    # Per-sector tags (low 16 bits of CRC32 running state)
    sector_tags = bytearray(total_blocks * 2)
    for i in range(total_blocks):
        sector = archive_bytes[i*512 : (i+1)*512]
        tag = (~zlib.crc32(sector)) & 0xFFFF
        sector_tags[i*2:i*2+2] = struct.pack('<H', tag)

    # Interleaved XOR parity (round-robin slot assignment)
    parity = [bytearray(512) for _ in range(P)]
    for i in range(total_blocks):
        slot = i % P
        for c in range(512):
            parity[slot][c] ^= archive_bytes[i*512 + c]
    parity_blob = b''.join(bytes(p) for p in parity)

    emit_block_header(type=0x78, head_flags=0xC000,
                      head_size=26,
                      add_size=total_blocks*2 + P*512)
    emit_uint8(unp_ver_of_archive)         # Version (e.g. 0x14 for RAR 2.0)
    emit_uint16_le(P)                       # RecSectors
    emit_uint32_le(total_blocks)            # TotalBlocks
    emit_bytes(b"Protect!")                 # Mark[8]
    emit_bytes(sector_tags)                 # TotalBlocks × 2 bytes
    emit_bytes(parity_blob)                 # RecSectors × 512 bytes
```

End-to-end byte-exact reproduction of all four committed fixtures
verifies this recipe.

**Trap:** the RS codec is byte-oriented, but RAR processes 512-byte
sectors as "rows" and each byte offset within the sector as an
independent "column". A recovery record of `N=100, P=10` protects
against losing any 10 full sectors — not against losing 10 individual
bytes scattered around. The column-independence property is why this
works: each byte offset has its own parity stream, and if a whole
sector is zero-filled, it marks all 512 columns as missing at the same
row index.

**Limit:** the 8-bit field means `N + P ≤ 255`. Archives larger than
`255 × 512 = ~128 KB` cannot fit in a single record. RAR 2.x/3.x
handles this by emitting **multiple** recovery records, each covering a
`~128 KB` chunk of the archive. The encoder partitions the archive
into chunks and runs the RS encode per chunk.

### 3.5 RAR 3.x `.rev` files (separate recovery volumes)

`recvol3.cpp` uses the same `RSCoder` to produce standalone recovery
volumes. The model: N data volume files + P `.rev` parity files. The RS
encode operates byte-by-byte across all `N + P` volumes, treating the
same byte offset in each file as one RS symbol.

```
def make_rev_files(volume_files, num_rev):
    N = len(volume_files)
    P = num_rev
    rs = RSCoder(par_size=P)

    rev_files = [open(name + f'.rev{i}', 'wb') for i in range(P)]
    while True:
        byte_column = [f.read(1) for f in volume_files]
        if any(b == b'' for b in byte_column):
            break
        data = bytes(b[0] for b in byte_column)
        parity = rs.encode(data, N)
        for i in range(P):
            rev_files[i].write(parity[i:i+1])
```

(The real implementation buffers 64 MB across all files for throughput;
`TotalBufferSize = 0x4000000` at `recvol3.cpp:2`, divided by total file
count.)

**File naming.** Two conventions coexist:

- **Old-style (RAR 3.0):** `basename_V_R_F.rev` where `V` = total
  volume count, `R` = recovery volume count, `F` = file index (1-based
  within the recovery set). Example: `arc_5_3_1.rev` is the first of
  three recovery files protecting a 5-volume archive.
- **New-style (RAR 3.10+):** `basename.partNN.revMM` (or similar,
  `recvol3.cpp:65-78` autodetects). The index fields are stored in a
  trailer instead of the name.

New archives should use new-style names. compatible RAR reader verifies old-style
names by parsing digit groups out of the filename; new-style names are
verified by the trailer CRC.

**Per-.rev file trailer (new-style only).** The last 7 bytes of each
new-style recovery volume carry index metadata plus a CRC32 checksum
(`recvol3.cpp:173-186`):

```
offset  size  content
-7      1     P[2] - 1    # F (file index within the set, 1..255)
-6      1     P[1] - 1    # R (RecVolNumber, count of recovery volumes)
-5      1     P[0] - 1    # V (FileNumber, count of data volumes)
-4      4     CRC32 over bytes [0 .. length-4) of the .rev file
```

Each index field is stored as `value - 1` so that the maximum 255 fits
in a single byte. The reader loads `P[2-i] = byte + 1` (loop variable
`i = 0..2` at `recvol3.cpp:175-176`), then validates
`P[1] + P[2] ≤ 255` and `P[0] + P[2] − 1 ≤ 255` to confirm the RS
codeword length stays within the 8-bit field.

These 7 bytes are the same 7 bytes that the end-of-archive header
`EARC_REVSPACE` flag (`0x0004`) reserves inside the last data volume's
`.rar`. When compatible RAR reader reconstructs a lost data volume, it zero-fills that
trailer region (`recvol3.cpp:428-432`) because the parity data does
not cover it.

Old-style `.rev` files have no trailer and no CRC32 — compatible RAR reader still
accepts them but cannot detect corruption in the recovery data itself.

---

## 4. RSCoder16 (16-bit GF(2^16), RAR 5.0)

a public RAR reader — ~420 lines. Completely different construction:
uses a **Cauchy matrix** rather than a BCH generator polynomial. The
symbol size is 16 bits and the field is `GF(2^16)` under polynomial
`x^16 + x^12 + x^3 + x + 1` (`0x1100B`).

### 4.1 Field init

```
def gf_init():
    gfSize = 65536
    gfExp = [0] * (4 * gfSize + 1)
    gfLog = [0] * (gfSize + 1)
    e = 1
    for l in range(gfSize):
        gfLog[e] = l
        gfExp[l]          = e
        gfExp[l + gfSize] = e
        e <<= 1
        if e > gfSize:
            e ^= 0x1100B
    # Sentinel: log(0) points into the zero region so mul-by-zero works
    # without an explicit check.
    gfLog[0] = 2 * gfSize
    for i in range(2 * gfSize, 4 * gfSize + 1):
        gfExp[i] = 0
```

`gfExp` is quadrupled (`4 * gfSize + 1` entries) because two `gfLog`
values can each be up to `gfSize`, and their sum can be up to `2 *
gfSize`. Plus a zero-overflow sentinel region at the end. Memory:
`(4*65536 + 1) * sizeof(uint)` ≈ 1 MB for `gfExp`. Substantial, but
allocated once.

### 4.2 Cauchy encoder matrix

```
def make_encoder_matrix(nd, nr):
    # NR × ND matrix; MX[I][J] = 1 / ((I + ND) XOR J) in GF(2^16)
    mx = [[0] * nd for _ in range(nr)]
    for i in range(nr):
        for j in range(nd):
            mx[i][j] = gf_inv(gf_add(i + nd, j))     # gf_add is XOR
    return mx
```

A Cauchy matrix `C[i][j] = 1 / (x_i + y_j)` for distinct `x_i, y_j` is
**always** non-singular, which is exactly the MDS property we need: any
ND-row submatrix is invertible, so any ND of the ND+NR shards recover
the originals.

### 4.3 Systematic encoding

For each byte offset within the data shards, the encoder computes:

```
for i in 0..NR-1:
    parity_shard[i][offset] = XOR over j of gf_mul(MX[i][j], data_shard[j][offset])
```

Applied across all 16-bit words (each shard is a byte buffer, processed
2 bytes at a time).

a public RAR reader ships an SSE accelerator for this inner loop using
the James Plank / Kevin Greenan / Ethan Miller technique (cited in the
source header): split each 16-bit multiplication into two 8-bit table
lookups via the PSHUFB instruction. For a clean-room encoder, the scalar
`gf_mul` is sufficient; SSE is a ~5x speedup on large recovery records.

### 4.4 Decoder matrix (informational)

When decoding, the encoder provides a `ValidFlags[ND+NR]` array
indicating which shards are intact. `MakeDecoderMatrix` populates MX
with Cauchy rows corresponding to the **surviving parity shards**, then
`InvertDecoderMatrix` Gauss–Jordans the resulting ND × ND matrix in
place. This matrix is applied to the valid shards to reconstruct the
missing data shards. No encoder needs this path.

### 4.5 Limits and advantages over the 8-bit variant

- `ND + NR ≤ 65535` vs 255 for the 8-bit variant — supports much larger
  archives and/or much higher recovery ratios.
- RAR 5.0 removed the `NR ≤ ND` restriction in 2021 (`rs16.cpp:99-100`),
  allowing recovery ratios above 100%. An encoder can produce e.g. 150%
  recovery (`NR = 1.5 × ND`) to survive more simultaneous losses.
- Shards can be arbitrary sizes (limited by chunking, ~64 MB buffer in
  compatible RAR reader) as opposed to the fixed 512-byte sectors of RSCoder.

### 4.6 RAR 5.0 recovery record inline format

The inline RR is a RAR 5.0 service header named `"RR"`
(`SUBHEAD_TYPE_RR` in `_refs/unrar/headers.hpp:121`). What's verifiable
from public source:

- Reader behaviour: unrar locates the RR via the `MainHead.Locator`
  extra field (`RROffset`) when present, else by scanning the block
  stream for `HEAD_SERVICE` with name "RR"
  (`_refs/unrar/arcread.cpp:80-93`).
- Service-header `SubData`: stores the **recovery-percent** value only
  (single byte ≤ RAR 6.02, vint from RAR 6.10+ supporting up to 1000%).
  See `arcread.cpp:908-916`. This is metadata the reader displays,
  *not* the parity payload.
- Parity payload: lives in the service header's data area, sized by
  the standard RAR 5.0 service-header `DataSize` field. Its internal
  field structure is **not parsed by unrar** — the inline RR is
  read-only metadata for unrar, which does not perform inline-RR
  repair. (Repair from `.rev` files is implemented; see §4.7.)

#### Service-header data area

The RR service header's `DataSize` is exactly `NR * shard_size` bytes.
There is no preamble or trailer in the data area beyond the `NR` shards
themselves. A reader determines the inline-RR extent from the service
header's `DataSize` field — never by scanning to EOF. Verified across
all six fixtures
(`fixtures/5.0/rr_inline/rar721_rr*_*k.rar`, WinRAR 7.21).

The data area contains `NR` parity shards back-to-back, each
`shard_size` bytes long. Each shard is a self-contained `{RB}` chunk
with the layout described in §4.6.1. A reader locates the shards by
indexing into the service header's data area at multiples of
`shard_size` — **not** by scanning the archive globally for `{RB}`
bytes. RS parity bytes are uniformly distributed, so they may
incidentally contain the byte sequence `7B 52 42 7D`; in the current
fixture set the only observed occurrences happen to be the shard
magics, but no future fixture is required to preserve that property.

##### 4.6.1 Per-shard chunk layout

Each shard is a 16-byte fixed prefix, followed by a structured
header (a flat sequence of fixed-width little-endian fields, no vint
encoding), followed by the parity payload. The fixed-prefix fields
are:

| Chunk offset | Type   | Field         | Notes |
| -----------: | ------ | ------------- | ----- |
| `+0x00..+0x03` | bytes  | `{RB}` magic  | Literal `7B 52 42 7D` (= LE32 `0x7D42527B`). |
| `+0x04..+0x0b` | uint64 LE | per-shard `CRC-64/XZ` | ECMA-182 reflected polynomial `0xC96C5795D7870F42`, initial state `0xFFFFFFFFFFFFFFFF`, final XOR `0xFFFFFFFFFFFFFFFF` — i.e. the standard Linux/XZ CRC64. Covers chunk bytes from `+0x0c` onwards (`total_size + header + parity`, = `shard_size − 12` bytes). Verified against all 67 shards across the six fixtures. |
| `+0x0c..+0x0f` | uint32 LE | `total_size` | Equal to `shard_size`. The encoder's structured-header builder reserves the first 4 bytes for this field and writes it before serialization. |
| `+0x10..+0x13` | uint32 LE | `header_size` | Byte length of the chunk header (fixed prefix + structured header fields). Equals `D*8 + 0x48`, where `D` is the data-shard count (see §4.6.2). |
| `+0x14..+(header_size − 1)` | bytes | structured header fields | Sequence of fixed-width little-endian fields written by `FUN_00438da0` in WinRAR 6.02. Wire-format layout in §4.6.1.1 below. A clean-room reader does not need to parse this stream — `total_size` and `header_size` from the fixed prefix are sufficient to skip past it to the parity bytes — but a clean-room byte-identical encoder does. |
| `+header_size..+(shard_size − 1)` | bytes | parity payload | `(shard_size − header_size)` bytes of GF(2^16) Reed-Solomon parity computed over the protected archive. The byte count equals `group_count` (see §4.6.2). |

Adjacent shards in the data area are exactly `shard_size` bytes apart;
shard `i` starts at offset `i * shard_size` from the start of the data
area.

##### 4.6.1.1 Structured header fields (chunk +0x14 to +header_size)

These fields are emitted by `FUN_00438da0` (WinRAR 6.02) as a flat
sequence of fixed-width little-endian writes, no vint encoding. Field
positions and types are stable across the entire fixture set; field
semantics for the four marked "encoder-internal" entries have not
been traced end-to-end and are not needed by a clean-room reader.

| Chunk offset | Type   | Field | Notes |
| -----------: | ------ | ----- | ----- |
| `+0x14` | u8 | `version_a` | Constant `0x01` in every fixture (writer emits literal `1`). |
| `+0x15` | u8 | `version_b` | Constant `0x01` in every fixture (writer emits literal `1`). |
| `+0x16..+0x1d` | u64 LE | `chunk_position` | Byte position of this chunk within the encoder's parity buffer. For inline RR (one chunk per parity shard) it is `0` in every fixture. Multi-chunk shards (only seen in `.rev` files in the current tree, not inline RR) would have this advance by `0x4000` per chunk. |
| `+0x1e..+0x21` | u32 LE | `chunk_data_extent` | Encoder-internal byte position of the last data shard's parity contribution within the parity buffer (encoder reads `state[D−1].int[8]`). Treat as encoder-internal — values 913 / 947 / 919 observed for 64K / 16K / 128K archives, doesn't decompose cleanly to a public quantity. |
| `+0x22..+0x29` | u64 LE | `protected_archive_size` | Equal to the `archive_size` input to the formula in §4.6.2 — byte count of the archive prefix preceding the RR service header. |
| `+0x2a..+0x31` | u64 LE | `group_count` | Equal to the `group_count` derived in §4.6.2. (= per-shard parity payload byte count.) |
| `+0x32..+0x39` | u64 LE | `shard_size` | Equal to the `shard_size` derived in §4.6.2. (Repeats `total_size` from `+0x0c`, here as a u64.) |
| `+0x3a..+0x3b` | u16 LE | `D` | Data-shard count; equal to the `D` derived in §4.6.2. |
| `+0x3c..+0x3d` | u16 LE | `NR` | Parity-shard count; equal to the number of `{RB}` chunks in the data area. |
| `+0x3e..+0x3f` | u16 LE | `shard_index` | 0-based index of this parity shard. Strictly increases `0..NR-1` across the chunks. |
| `+0x40..+(0x40 + D*8 − 1)` | D × u64 LE | `data_shard_state[]` | An array of D 64-bit values describing the encoder's running CRC-like state per data shard, for the chunk indexed by `chunk_position / 0x4000`. **Identical across all NR parity shards of a given inline-RR record** (verified across all 13 shards of `rar721_rr20_64k.rar`), because all parity for an inline RR fits in one 64-KB chunk, so the chunk-index used to read this array is always 0. |
| `+(0x40 + D*8)..+(0x47 + D*8)` | u64 LE | `final_state` | Encoder-internal final 64-bit state value (writer reads `state[0x16..0x17]`). Varies per parity shard in our fixture set; treat as encoder-internal until matched against a known computation (likely a CRC of the parity payload or a function thereof). |

The structured header ends at chunk offset `0x40 + D*8 + 8 = D*8 +
0x48`, which equals `header_size` from the fixed prefix at `+0x10`.

##### 4.6.2 (NR, shard_size) formula — WinRAR 6.02 observed encoder

The encoder derives `(NR, shard_size, header_size, group_count)` from
the user's recovery percent and the byte count of the archive prefix
preceding the RR service header. Decoded from WinRAR 6.02 `Rar.exe`
(`research/re/winrar602/`, function at VA `0x004399e0`) and verified
against all six fixtures with predicted == observed for every value.

This formula describes the **WinRAR 6.02** encoder path. RAR 6.10+
extended the recovery-percent metadata field in `SubData` to a vint
supporting values up to 1000% (see `arcread.cpp:908-916`); whether
the same encoder formula clamps to 100 in the 6.10+ path or scales
through the full range is currently untested — there's no
`-rr1000` (or similar) fixture in the tree to compare against. A
clean-room reader doesn't need this distinction (the on-disk shard
layout is governed by `total_size` and `header_size` from the chunk
prefix); a clean-room encoder targeting byte-identical 6.10+/7.x
output should treat the clamp as version-conditional and reverify
against a fresh fixture before committing.

Define:

- `archive_size` = byte count of the archive prefix **before the RR
  service header**. This is the position at which the encoder starts
  emitting the RR service header, *not* the original input file size
  and *not* the byte offset of the `{RB}` marker. (For
  `rar721_rr5_64k.rar` it is `65681` — file offset of the
  `HEAD_SERVICE` byte that begins the RR service header.)
- `rec_pct` = user-supplied recovery percent. WinRAR 6.02 clamps
  this to `[0, 100]` before applying the formula; later versions
  may differ (see paragraph above).

```python
def compute_inline_rr_dims(rec_pct: int, archive_size: int):
    pct = max(0, min(100, rec_pct))
    if archive_size >= 200 * 1024:
        D = 200
    else:
        D = max(1, (archive_size + 1023) // 1024)        # ceil to 1 KiB
    NR = (2 * pct * D) // 200                             # = floor(pct * D / 100)
    if NR > D:
        NR = D
    if NR == 0 and archive_size < 200 * 1024:
        NR = 1
    group_count = (archive_size + D - 1) // D             # ceil(archive_size / D)
    group_count += group_count & 1                        # round up to even
    scale_factor = max(1, (group_count + 0xFFFF) // 0x10000)
    header_size = (D * 8 + 0x48) * scale_factor
    shard_size = header_size + group_count
    return NR, shard_size, header_size, group_count, D
```

Where:

- `D` is the **data-shard count per group**. It is derived from the
  archive size in 1 KiB units, capped at 200. Not "sectors" in the
  RAR 2.x PROTECT_HEAD sense (those are 512-byte units; see §3.4 for
  the legacy scheme). `D` here is purely a count.
- `NR` is the **parity-shard count** = number of `{RB}` chunks emitted.
- `group_count` is the **per-shard parity payload byte count**. It is
  not a sector count.
- `scale_factor` handles archives large enough that `group_count`
  exceeds 65535. For the fixture set it is always 1.
- `header_size = (D*8 + 0x48) * scale_factor` is the chunk's header
  length in bytes.

Verification table:

| Fixture           | rec_pct | archive_size | D   | NR  | group_count | header_size | shard_size |
| ----------------- | ------: | -----------: | --: | --: | ----------: | ----------: | ---------: |
| `rar721_rr10_16k.rar`  | 10 |  16531 |  17 |  1 |   974 |   208 | 1182 |
| `rar721_rr5_64k.rar`   |  5 |  65681 |  65 |  3 |  1012 |   592 | 1604 |
| `rar721_rr10_64k.rar`  | 10 |  65681 |  65 |  6 |  1012 |   592 | 1604 |
| `rar721_rr20_64k.rar`  | 20 |  65681 |  65 | 13 |  1012 |   592 | 1604 |
| `rar721_rr50_64k.rar`  | 50 |  65681 |  65 | 32 |  1012 |   592 | 1604 |
| `rar721_rr10_128k.rar` | 10 | 131223 | 129 | 12 |  1018 |  1104 | 2122 |

#### Supersedes the prior per-shard-header table

An earlier revision of this section described a 64-byte
"self-describing per-shard header" with fields like `NR` at `+0x34`,
`shard_index` at `+0x36`, `protected_archive_size` at `+0x1A`, and
several "unknown" 16-bit fields. **That table was an artefact of
reading shard 0 as a 64-byte structure starting 8 bytes after `{RB}`.**
The actual layout is the one in §4.6.1 / §4.6.1.1: each `{RB}` is a
self-contained chunk, and the values that earlier looked like
"self-describing" fields land naturally inside the fixed prefix and
the structured header of each chunk once the base offset is shifted
by 8 bytes. The fields are positioned in §4.6.1.1.

#### What's still open

- **Encoder-internal fields in the structured header.** §4.6.1.1
  documents the wire-format positions and types of every field, but
  three of them (`chunk_data_extent`, `data_shard_state[]`,
  `final_state`) are encoder-internal running states whose
  closed-form derivation from `(rec_pct, archive_size, parity_bytes)`
  hasn't been pinned. A clean-room reader doesn't need these (it
  skips past the header via `header_size` and consumes parity from
  `+header_size` onwards). A clean-room byte-identical encoder
  needs them — they're the next continuation of the same Ghidra
  pass, and likely fall out of writing the RS encoder side-by-side
  with WinRAR's.
- **Decoder side.** unrar 7.13 does not perform inline-RR repair (it
  only reads the recovery-percent metadata in `SubData` for display).
  The Reed-Solomon repair direction needs either a different reference
  reader or a clean-room implementation built on top of
  `RSCoder16::Init(D, NR)` followed by RS-decode using shard headers
  as the parity-position labels.

The encoder runs `RSCoder16::Init(D, NR, ValidityFlags=None)` once
(NULL flags selects the encoder path), then streams through the data
shards calling the per-offset inner loop for each of the `NR` parity
outputs.

### 4.7 RAR 5.0 `.rev` files

`_refs/unrar/recvol5.cpp` produces and reads `.rev` files for RAR 5.0
multi-volume archives using the same 16-bit RS codec as the inline RR.
Verified file format (`RecVolumes5::ReadHeader`, lines 439-489):

```
File layout (REV5_SIGN_SIZE = 8, max HeaderSize = 0x100000):
+0    8 bytes   REV5_SIGN = "Rar!\x1aRev"        # magic, distinct from main archive marker
+8    4 bytes   HeaderCRC32                      # ~CRC32(HeaderSize_bytes ∥ Body)
+12   4 bytes   HeaderSize (uint32 LE)           # length of Body in bytes (≤ 0x100000, > 5)
+16   N bytes   Body
+16+N ...       Recovery payload (parity bytes for this .rev volume)
```

Body content (read via `RawRead` after the size fields):

```
uint8   Version        # must == 1; reader rejects other values
uint16  DataCount      # ND: number of data volumes (≤ MaxVolumes = 65535)
uint16  RecCount       # NR: number of .rev files in this set
uint16  RecNum         # this .rev file's index, 0 ≤ RecNum < (DataCount+RecCount)
uint32  RevCRC         # CRC32 of THIS .rev file's payload region
                       # (the bytes after the header, computed when reading)

[On the FIRST .rev file processed (FirstRev=true), the per-data-volume
 metadata table follows; subsequent .rev files include it too but the
 reader only uses the first copy:]
For I in 0..DataCount-1:
    uint64  FileSize   # size of i-th data volume (.partNN.rar)
    uint32  CRC        # CRC32 of i-th data volume
```

`HeaderCRC32` is computed as `~CRC32(HeaderSize_field || body_bytes)` —
i.e. it covers the 4-byte HeaderSize *and* the body but not the
8-byte signature or the CRC field itself. Standard CRC-32 (IEEE 802.3,
reflected) with init `0xFFFFFFFF` and final XOR `0xFFFFFFFF`.

Naming convention follows the data volumes: `archive.partNN.rev`
matching `archive.partNN.rar`. RAR 5.0 supports up to 65535 combined
data + recovery volumes; the encoder picks the digit width up front
(`partNNN` etc.) just like the data volumes.

The maximum protection ratio is therefore far higher than RAR 3.x's
8-bit variant: a 100%-recovery setup with 1000 data volumes needs
1000 `.rev` files, which requires the 16-bit codec.

---

## 5. Encoder recipe summary

### 5.1 Adding a recovery record to a RAR 2.x/3.x archive

1. Close the archive's main data region.
2. Measure total archive size `A`.
3. Compute `N = ceil(A / 512)`, `P = recovery_percent × N / 100`.
4. If `N + P > 255`, split into multiple recovery records covering
   consecutive chunks of the archive.
5. For each chunk, run `RSCoder::Init(P)`, then for each byte offset
   `col ∈ 0..511` run `encode(column_data, N)` to produce `P` parity
   bytes.
6. Emit the recovery record block header followed by `N` data sectors
   and `P` parity sectors.

### 5.2 Adding a recovery record to a RAR 5.0 archive

1. Close the archive's main data region.
2. Decide `ND` (number of data shards) and `NR` (number of parity
   shards). Any value up to 65535 is legal.
3. Decide `shard_size` (typically `archive_size / ND`, padded to a
   multiple of 2 bytes for the 16-bit field).
4. `RSCoder16::Init(ND, NR, ValidityFlags=None)` — encoder mode.
5. Write systematic data shards (pass through archive bytes unchanged).
6. Run the per-offset `MX × data` multiply for each of `NR` parity
   shards.
7. Emit the service header carrying the recovery payload.

### 5.3 Generating `.rev` files

Same approach as §5.1 / §5.2 but with each volume as a shard:

- RAR 3.x: 8-bit codec, max 255 `data+rev` volumes combined.
- RAR 5.0: 16-bit codec, max 65535 combined.

Processing reads all volume files in parallel and writes to `.rev` files
in parallel. Memory-bounded by the chosen buffer size (compatible RAR reader uses 64 MB
total across all files).

---

## 6. Test oracle

1. Encode a known input with a known recovery ratio. Verify the decoder
   in a public RAR reader can reconstruct the data after
   zeroing out up to `NR` shards.
2. For `RSCoder`: verify `g(x)` is a degree-`par_size` polynomial with
   roots at `α¹, α², ..., α^par_size`.
3. For `RSCoder16`: verify the encoder matrix is non-singular by checking
   that a random ND-row submatrix inverts successfully.
4. For BLAKE2sp: use the official BLAKE2sp test vectors from
   https://blake2.net — the reference implementation in
   a public RAR reader is test-vector-conformant.

---

## 7. Recommendation

- **BLAKE2sp** is the only mandatory item for a RAR 5.0 encoder. It
  replaces CRC32 in the File Hash extra record when the user requests
  stronger hashing. Use the public-domain BLAKE2sp reference implementation.
- **Recovery records** are entirely **optional** from a correctness
  standpoint — archives without them are valid and widely used. Skip on
  a first implementation. Add later if users request resilience against
  media damage.
- **`.rev` files** are even rarer than inline recovery records and
  almost never used on modern storage. Defer indefinitely.
- **RSCoder (8-bit)** is only needed for RAR 2.x/3.x output. If your
  encoder targets RAR 5.0+ only, you never touch it.
- **RSCoder16 (16-bit)** is only needed if your encoder emits recovery
  records for RAR 5.0. Otherwise skip.

Minimum viable integrity stack for a modern RAR 5.0 encoder: **CRC32 +
BLAKE2sp**, no recovery records. This covers 99.9% of real-world use
cases.

---

## 8. Block header CRC ranges (per-block-type rules)

Every RAR block header starts with its own CRC field. **Which bytes that
CRC covers** depends on the format version (RAR 2.x vs RAR 5.0) and, in
RAR 2.x, on per-block-type quirks. An encoder must emit exactly the CRC
the decoder will compute or the archive fails integrity checks at open
time.

References: public reader behavior for the CRC helpers and CRC call sites.

### 8.1 RAR 2.x — `GetCRC15` (CRC16)

```c
uint RawRead::GetCRC15(bool ProcessedOnly) {
    if (DataSize <= 2) return 0;
    uint HeaderCRC = CRC32(0xffffffff, &Data[2],
                           (ProcessedOnly ? ReadPos : DataSize) - 2);
    return ~HeaderCRC & 0xffff;
}
```

Despite the name "CRC16," the algorithm is **CRC32 with the top 16 bits
discarded**. The encoder computes a normal CRC32 and stores only the
low 16 bits (as `~crc & 0xFFFF`, equivalent to inverting at the end and
masking).

**CRC range:** bytes `[2, end)` where `end` is one of:

- `DataSize` (the full header byte range as it sits on disk), for nearly
  all block types, or
- `ReadPos` (the number of bytes the header parser actually consumed),
  when the decoder passed `ProcessedOnly=true`.

The first 2 bytes are always excluded because they **are** the HeadCRC
field itself.

**Per-block-type behavior:**

| Block type             | `ProcessedOnly` | CRC range                          |
|------------------------|-----------------|------------------------------------|
| `HEAD3_MARK` (marker)  | n/a             | no CRC field — skip                |
| `HEAD3_MAIN`           | false           | `[2, DataSize)`                    |
| `HEAD3_FILE` (no comment) | false        | `[2, DataSize)`                    |
| `HEAD3_FILE` (CommentInHeader) | **true** | `[2, ReadPos)` — excludes comment |
| `HEAD3_CMT`            | false           | `[2, DataSize)`                    |
| `HEAD3_AV`             | —               | decoder does **not** verify        |
| `HEAD3_SIGN`           | —               | decoder does **not** verify        |
| `HEAD3_SERVICE`        | false           | `[2, DataSize)`                    |
| `HEAD3_OLDSERVICE` / `UO_HEAD` | —       | CRC covers string fields that are **not** in `HeadSize` — see §8.1.1 |
| `HEAD3_NEWSUB`         | false           | `[2, DataSize)`                    |
| `HEAD3_ENDARC`         | false           | `[2, DataSize)`                    |

`DataSize` in the table means the byte range that lives on disk for the
header block: `HeadSize` for fixed-layout blocks; `HeadSize + ADD_SIZE
(HighPackSize<<32 | PackSize)` never applies — the data region is
**not** part of the header CRC, only the header bytes are.

The `CommentInHeader` carve-out (`arcread.cpp:430-431`) triggers when the
file header sets `LHD_COMMENT` (bit `0x0008`) and an embedded comment
follows the standard file-header fields. The decoder parses the fixed
fields, stops at `ReadPos`, and CRCs only what it parsed — the trailing
comment bytes are **not** protected by the header CRC, they have their
own CRC inside the comment subrecord. The encoder must match: compute
CRC over the non-comment portion only.

`HEAD3_AV` and `HEAD3_SIGN` have broken/inconsistent CRCs in practice
(`arcread.cpp:520-522`). The decoder tolerates this. An encoder emitting
these block types should compute them normally over `[2, DataSize)` —
the decoder won't check, but matching the natural rule is harmless.

#### 8.1.1 Old Unix owners subrecord quirk

`HEAD3_OLDSERVICE` with subtype `UO_HEAD` predates the service-header
redesign. Its on-disk layout has the user-name and group-name strings
**after** the bytes counted in `HeadSize`. The decoder's comment says:
"Old Unix owners header didn't include string fields into header size,
but included them into CRC, so it couldn't be verified with generic
approach here."

Encoder rule: write `HeadSize = base record size` (not including the
two trailing strings), but compute the header CRC over **base record +
both strings**. The CRC excludes only the first 2 bytes (the HeadCRC
field itself).

In practice, modern encoders should emit the newer `HEAD3_SERVICE`
record instead of `HEAD3_OLDSERVICE` for Unix ownership metadata and
avoid this wart entirely.

### 8.2 RAR 5.0 — `GetCRC50` (CRC32)

```c
uint RawRead::GetCRC50() {
    if (DataSize <= 4) return 0xffffffff;
    return CRC32(0xffffffff, &Data[4], DataSize-4) ^ 0xffffffff;
}
```

Standard CRC32 (IEEE 802.3 polynomial `0xEDB88320`, initial value
`0xFFFFFFFF`, final XOR `0xFFFFFFFF`) — same algorithm as file-data CRC.

**CRC range:** bytes `[4, DataSize)` for **every** block type. The first
4 bytes are the HeadCRC field. `DataSize` is the full on-disk size of
the header block, which equals `4 + sizeof(HeadSize vint) + HeadSize`.
In other words: after the 4-byte CRC, the CRC covers the HeadSize vint
and everything that follows it (HeadType, HeadFlags, ExtraSize,
DataSize, body, extras), up to but **not** including any file data
payload that follows the block.

No `ProcessedOnly` variant exists in RAR 5.0. The rule is uniform across
`HEAD_CRYPT`, `HEAD_MAIN`, `HEAD_FILE`, `HEAD_SERVICE`, and `HEAD_ENDARC`.

**Key encoder consequences:**

1. **Padding for encrypted headers is excluded from the CRC.** Under
   archive-wide header encryption (§6 of `ENCRYPTION_WRITE_SIDE.md`), the
   plaintext header is zero-padded to a 16-byte boundary before AES-CBC
   encryption, but `HeadSize` reflects the **unpadded** length and the
   CRC is computed over the unpadded plaintext. The decoder reads
   `HeadSize` first, trims the buffer, and CRCs `[4, HeadSize+4+vintSize)`
   — which happens to be `DataSize` in the `RawRead` at that moment
   because the buffer was sized from `HeadSize` exactly.
2. **Extra records are covered.** Everything inside the "Extra Area" is
   part of `HeadSize` and therefore part of the CRC. An encoder emitting
   Locator, File Encryption, File Hash, Quick Open, Recovery, or any
   other extra record does **not** need to update the CRC separately —
   just include those bytes in the header buffer before computing CRC
   over the whole range.
3. **File data is not covered.** The `DataSize` vint inside a file
   header describes a payload that follows the header block. The
   file-data payload has its own CRC32 / BLAKE2sp inside the File Hash
   extra record. The header's HeadCRC covers only the header.
4. **Header CRC after encryption.** When writing an encrypted header,
   compute HeadCRC over plaintext first, **then** zero-pad and encrypt.
   The decoder decrypts first, **then** verifies CRC. Doing it in the
   wrong order gives a CRC over ciphertext that the decoder will reject.

### 8.3 Encoder-side checklist

For RAR 2.x (CRC16):

1. Build the complete header byte buffer (HeadCRC field bytes set to
   zero as a placeholder).
2. Decide `ProcessedOnly`: true only for `HEAD3_FILE` with
   `LHD_COMMENT`. False for everything else.
3. Compute `CRC32(0xFFFFFFFF, buf[2..end])` where `end` is the
   processed-only cutoff or `buf.size()`.
4. Store `~crc & 0xFFFF` as a uint16 at offset 0 (little-endian).

For RAR 5.0 (CRC32):

1. Build the complete header byte buffer including all extra records,
   HeadCRC field set to zero placeholder.
2. `HeadSize` vint must reflect the **final** header size (excluding
   the 4-byte CRC and the `HeadSize` vint bytes themselves) before
   computing CRC — i.e., emit `HeadSize` in its correct form first.
3. Compute `CRC32(buf[4..end]) ^ 0xFFFFFFFF` where `end = buf.size()`.
4. Store the 32-bit result as a uint32 at offset 0 (little-endian).
5. If the header will be encrypted under archive-wide HEAD_CRYPT, do
   this **before** padding and encryption.

### 8.4 Test oracle

For each block type, build a minimal valid header, compute the CRC with
the rules above, and feed the resulting bytes to the compatible RAR reader decoder. A
correct encoder passes the decoder's CRC check silently; an encoder that
gets the range wrong produces a "Broken header" warning for every block
it emits. Good early-development sanity check: emit one archive per
block type and count how many "broken header" messages compatible RAR reader prints.
