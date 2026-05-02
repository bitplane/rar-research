# RAR Test Vectors

Known-good input/output pairs for every primitive in the spec set.
Pointed at by each doc's "Test oracle" section so writers don't have
to assemble vectors twice.

Scope: per-primitive byte vectors **plus** a standard round-trip
strategy. Full micro-archive samples (compressed → decompressed)
belong in the test suite binary fixtures, not here.

## Strategy: round-trip as the default oracle

Most RAR primitives are stateful enough that stand-alone input/output
vectors either cover the trivial cases (empty input) or are fragile
under implementation variance (e.g. two encoders producing different
but both-valid Huffman trees for the same frequency distribution).
The default oracle across the spec set is therefore a **round-trip**:

```
plaintext -> our_encoder -> bytes -> reference_decoder -> plaintext'
assert plaintext == plaintext'
```

Where a stand-alone byte vector exists (CRC32 of "123456789", PBKDF2
output for "password"/"salt"), this doc lists it. Where it doesn't,
the entry points at the spec section that documents the round-trip
procedure for that primitive.

## 1. CRC32 (IEEE 802.3, reflected)

Spec: `CRC32_SPECIFICATION.md`. Same algorithm for every RAR version;
only the storage width (16 vs 32 bits) changes.

| Input | CRC32 | CRC16 (low 16) |
|-------|-------|----------------|
| `""` (empty) | `0x00000000` | `0x0000` |
| `"123456789"` | `0xCBF43926` | `0x3926` |
| `"test"` | `0xD87F7E0C` | `0x7E0C` |
| `"Rar!"` | `0x5835B208` | `0xB208` |
| `"a"` | `0xE8B7BE43` | `0xBE43` |
| `"abc"` | `0x352441C2` | `0x41C2` |
| 256 × `0x00` | `0xA51B25FB` | `0x25FB` |
| 256 × `0xFF` | `0x8A9136AA` | `0x36AA` |

Values are little-endian when stored in RAR headers (e.g. `FILE_CRC`).

## 2. BLAKE2sp (RAR 5.0 File Hash, hash type 0x00)

Spec: `INTEGRITY_WRITE_SIDE.md` §1. 8-way parallel BLAKE2s, 32-byte
output, no key, no personalization.

Primary oracle: the official BLAKE2sp test vectors at
[blake2.net](https://blake2.net).

Quick sanity vectors:

| Input | BLAKE2sp (hex) |
|-------|----------------|
| `""` (empty) | `dd0e891776933f43c7d032b08a917e25741f8aa9a12c12e1cac8801500f2ca4f` |
| `"abc"` | `34d6cf42076a96db8e19a872d5e15ce11e24a6c058bef71e40d5cd20e4ad43ad` |
| 1 MiB of `0x00` | covered by blake2.net's "long" vectors |

Encoders should run BLAKE2sp over the **uncompressed** file data, not
the compressed stream. If the file/service encryption extra record sets
`HashMAC`, the hash is HMAC'd via the conversion in §5 below; the
raw-BLAKE2sp vector above is not what ends up on disk for those records.

## 3. AES-256 CBC (RAR 5.0 encryption primitive)

Spec: `ENCRYPTION_WRITE_SIDE.md` §5. AES-256 in CBC mode, PKCS#7
padding, explicit IV stored in the crypt record.

Use the NIST test vectors from SP 800-38A, Appendix F.2.5
(`CBC-AES256.Encrypt`):

```
Key = 603deb1015ca71be2b73aef0857d7781 1f352c073b6108d72d9810a30914dff4
IV  = 000102030405060708090a0b0c0d0e0f
PT  = 6bc1bee22e409f96e93d7e117393172a ae2d8a571e03ac9c9eb76fac45af8e51
       30c81c46a35ce411e5fbc1191a0a52ef f69f2445df4f9b17ad2b417be66c3710
CT  = f58c4c04d6e5f1ba779eabfb5f7bfbd6 9cfc4e967edb808d679f777bc6702c7d
       39f23369a9d9bacfa530e26304231461 b2eb05e2c39be9fcda6c19078c6a9d1b
```

Any AES implementation that passes this passes RAR 5.0's cipher
requirements. RAR 3.x/4.x use the same AES primitive at 128-bit key
size; the same NIST appendix provides `CBC-AES128` vectors.

## 4. PBKDF2-HMAC-SHA256 (RAR 5.0 KDF)

Spec: `ENCRYPTION_WRITE_SIDE.md` §5.1. Use standard PBKDF2-HMAC-SHA256
sanity vectors before testing the RAR-specific continuation values:

| Password | Salt | Iterations | dkLen | Output (hex) |
|----------|------|-----------:|------:|--------------|
| `"password"` | `"salt"` | 1 | 32 | `120fb6cffcf8b32c43e7225256c4f837a86548c92ccc35480805987cb70be17b` |
| `"password"` | `"salt"` | 4096 | 32 | `c5e478d59288c841aa530db6845c4c8d962893a001ce4e11a4963873aa98134a` |

An implementation using a third-party PBKDF2-HMAC-SHA256 already
passes. Pass these before wiring into the RAR-specific chain
(§5.1 in the spec for how the chain extends these to produce
Key ∥ IV ∥ PswCheckValue).

## 5. RAR 5.0 HashMAC conversion (HMAC fold)

Spec: `ENCRYPTION_WRITE_SIDE.md` §5.3. When the file/service encryption extra
record sets `HashMAC`, a raw CRC32 or BLAKE2sp hash and the KDF-derived HMAC
key produce the on-disk MAC.

Round-trip oracle only — no stand-alone vector. Procedure:

```
plaintext      = <chosen file data>
hash_raw       = CRC32(plaintext)  or  BLAKE2sp(plaintext)
hmac_key       = derived from password via the §5.1 chain
hash_onwire    = HashMAC(hash_raw, hmac_key)
```

Feed the archive into compatible RAR reader and confirm it reports "checksum OK". The
XOR-fold for CRC32 is specified in §5.3; cross-check by running the
compatible RAR reader decoder against our encoder's output.

## 6. RAR 3.x/4.x KDF (SHA-1, 262144 iterations)

Spec: `ENCRYPTION_WRITE_SIDE.md` §4. Input: UTF-16LE password + 8-byte
salt. Output: 16-byte AES-128 key + 16-byte IV.

No stand-alone RFC vectors exist — the iteration count and the hash
feeding are RAR-specific. Round-trip procedure:

1. Build an archive with header encryption using a known password
   and a known (constant) salt.
2. Decrypt the first post-main header with our implementation.
3. Verify HEAD_CRC of the plaintext matches.
4. Cross-check against a reference implementation by feeding it the same
   password/salt and comparing derived key+IV.

## 7. RAR 2.0 Feistel cipher

Spec: `ENCRYPTION_WRITE_SIDE.md` §3. The three traps (plaintext vs
ciphertext for UpdateKeys, round order, key-setup shuffle) are the
main sources of breakage; a round-trip that doesn't produce
byte-exact ciphertext identical to a public RAR reader's output
indicates one of the three is wrong.

Round-trip procedure: encrypt a known 16-byte block with a known password, feed
the ciphertext to a compatible RAR 2.0 decoder, and assert byte-exact plaintext
recovery.

## 8. RAR 1.5 encryption

Spec: `ENCRYPTION_WRITE_SIDE.md` §2. Trivial XOR-with-keystream; the
keystream derives from the password's CRC32. Round-trip oracle
suffices — the cipher is weak enough that divergence is self-evident
(corrupt output) rather than silent.

## 9. RAR 1.3 encryption

Spec: `RAR13_FORMAT_SPECIFICATION.md` §7 and `ENCRYPTION_WRITE_SIDE.md` §1.
Subtractive 3-byte stream cipher: a 3-byte key is derived from the password
(`Key[0]+=P`, `Key[1]^=P`, `Key[2]=rotl8(Key[2]+P,1)`) and then per-byte output
is `B - Key[0]` after `Key[1]+=Key[2]; Key[0]+=Key[1]`. Round-trip against
`_refs/unrar/crypt1.cpp` (`SetKey13` / `Decrypt13`) or
`_refs/XADMaster/XADRAR13CryptHandle.m`.

### 9.1 Packed-comment fixed-key vector

Spec: `RAR13_FORMAT_SPECIFICATION.md` §7.3 and §8. RAR 1.4 archive
comments with `MHD_PACK_COMMENT` use a fixed initial key
`Key13 = [0, 7, 77]` regardless of password.

Vector from `fixtures/1.402/COMMENT.RAR` (validated by
`scripts/verify-packed-comment.py`):

| Field | Value |
|-------|-------|
| Encrypted packed payload (32 bytes) | `79 da 20 65 4f 71 0f 5d 05 71 e8 be 5e 71 a0 bd d8 1e 04 37 20 dc 1a 54 cc cb e6 05 5b 68 c9 90` |
| Decrypt13 output (32 bytes, Unpack15 input) | `25 e5 3d 47 a9 f6 72 51 3d a0 c1 f4 a4 7a 1f 65 5c 31 59 81 12 29 75 70 5c 82 77 23 b9 b9 c0 e0` |
| Unpack15 decoded text (30 bytes) | `This is the archive comment.\r\n` |

## 10. Huffman construction

Spec: `HUFFMAN_CONSTRUCTION.md` §7. Round-trip only — two encoders
can produce different valid codes for the same frequency histogram
(tie-breaking depends on sort stability), so byte-exact comparison
against an external reference isn't meaningful.

Canonical self-test:

```
freqs = random histogram over N symbols
lens  = Huffman_Generate(freqs, max_len=15)
assert sum(2^(-l) for l in lens) <= 1.0     # Kraft inequality
codes = canonical_codes(lens)
for sym in all_input:
    assert decode(encode(sym)) == sym
```

If the resulting tree round-trips every input symbol and satisfies
Kraft, the construction is correct regardless of whether it matches
any specific encoder's output byte-for-byte.

### 10.1 Unpack15 adaptive-Huffman

The Unpack15 codec (RAR 1.3 and RAR 1.5 compressed method) is **not**
covered by the §10 canonical self-test: it uses an adaptive-Huffman
construction with `CorrHuff` frequency correction, StMode literal-run
interrupts, and MTF-encoded symbol positions (§6.5–§6.14 of
`RAR13_FORMAT_SPECIFICATION.md`). The encoder's state machine differs
from canonical Huffman enough that a round-trip through any other
Huffman implementation doesn't exercise it.

**Encoder round-trip oracle**:

```
plaintext -> our_Unpack15_encoder -> compressed_bytes
compressed_bytes -> RAR 1.5-compatible decoder -> plaintext'
assert plaintext == plaintext'
```

**Historical fixtures.** `fixtures/1.54/` contains WinRAR 1.54 generated
archives for the shared RAR 1.3/1.5 adaptive-Huffman + LZ codec:

- `readme_154_normal.rar`: single `README.md`, compressed, `UNP_VER = 15`.
- `readme_154_store_solid.rar`: same payload with solid archive flag set
  (still compressed method `0x33`; not a stored-method fixture).
- `readme.EXE`: self-extracting RAR 1.5 archive.
- `doc_154_best.rar`: multi-file documentation corpus.
- `random.rar`, `random.r00`, `random.r01`: split multi-volume archive.

These fixtures use the RAR 1.5 `Rar!` container. They validate Unpack15 and
RAR 1.5 archive framing. `fixtures/1.402/` covers the older RAR 1.3/1.4
`RE~^` container with single-file stored, compressed, and encrypted `README`
entries (`UnpVer = 2`; methods `0` and `3`; encrypted fixture password
`password`). Generator binaries are kept out of git under `_refs/rarbins/` and
mirrored at https://archive.org/details/old_winrar_binaries.

Use these fixtures as the positive decoder oracle for Unpack15. Encoder
correctness is still validated by decoder round-trip and by matching the
documented `InitHuff` state tables; byte-identical output is not expected
because WinRAR's token-selection heuristics are not public.

**RAR 1.3 encryption** (§9 above) does not have this fixture problem —
the cipher is trivial enough that `_refs/XADMaster/XADRAR13CryptHandle.m`
round-trip suffices.

## 11. PPMd variant H

Spec: `PPMD_ALGORITHM_SPECIFICATION.md` §13.5. The range coder and
the model update rules compound; a single wrong update silently
degrades compression without producing invalid output.

Round-trip oracle:

1. Encode known input with our encoder at order O and memory M.
2. Decode via a PPMd-capable RAR reader using the same order/memory.
3. Assert byte-exact plaintext recovery.

Supplementary: compare compressed output size against 7-Zip's
`_refs/7zip/Ppmd7Encoder.c` on the same input. Equal bit-length
within ±1% indicates the model is tracking correctly; large
divergence indicates a rescale or SEE bug.

## 12. Filters (RARVM bytecode and RAR 5.0 hardcoded enum)

Spec: `FILTER_TRANSFORMS.md` §10. Forward transforms are pure
functions — stand-alone vectors work well here.

### E8 (x86 CALL)

```
input (5 bytes at stream offset 0):
    E8 00 00 00 00         # call $+5
after forward transform (addr 0x00000005 encoded):
    E8 05 00 00 00
```

For a file-size transform, the actual replacement is `addr +
CurrentPos mod file_size_for_filter`; the test above assumes
`CurrentPos=0` and `file_size_for_filter=0`.

### E8E9 (x86 CALL + JMP)

Same as E8 but also matches the `E9` opcode. Vector: replace the
leading byte above with `E9` and the same transform applies.

### DELTA (channel=1)

```
input:  10 15 14 17 20
output: F0 FB 01 FD F7         # previous minus each byte (prev starts at 0)
```

Sign convention matches `FILTER_TRANSFORMS.md §4` and the inverse in
`_refs/unrar/unpack50.cpp` (`DstData[DestPos] = (PrevByte -= Data[SrcPos++])`).

### ARM (BL)

BL instructions are 4-byte little-endian with the top byte in range
`0xEB..0xEB` (conditional BL) or the unconditional forms. Vector:

```
input (4 bytes, ARM BL to +0x10):
    04 00 00 EB
output (PC-relative converted to absolute, low 24 bits replaced):
    depends on filter position
```

Full ARM vector requires the filter position as context — see §5 of
FILTER_TRANSFORMS for the formula.

### RAR 3.x RARVM bytecode blobs

The six stock RAR 3.x VM filter programs are captured in
`fixtures/rarvm/captured-blobs.md`, with JSONL capture logs under
`fixtures/rarvm/capture-logs/`.

Verification oracle for each captured blob:

```
assert len(blob) == expected_length
assert crc32(blob) == expected_crc32
assert xor_all_bytes(blob) == 0
```

Expected fingerprints:

| Filter | Length | CRC32 |
|--------|-------:|:------|
| E8 | 53 | `0xAD576887` |
| E8E9 | 57 | `0x3CD7E57E` |
| ITANIUM | 120 | `0x3769893F` |
| DELTA | 29 | `0x0E06077D` |
| RGB | 149 | `0x1C2C5DC8` |
| AUDIO | 216 | `0xBC85E701` |

The committed RARVM fixture matrix naturally captures DELTA across RAR
3.00/3.93/4.20 and AUDIO under RAR 4.20. E8/E8E9, RGB, and ITANIUM are
captured from local `_refs/rarvm-local/` source archives with the resulting
logs committed; the local source archives themselves are not required because
the durable artifact is the bytecode plus capture logs.

## 13. Reed–Solomon (RSCoder, 8-bit GF(2^8))

Spec: `INTEGRITY_WRITE_SIDE.md` §3. Round-trip only: encode K data
shards into K+NR total shards, zero any NR shards, decode and assert
recovery.

Canonical self-test: use `K=6`, `NR=3` (the default compatible RAR reader config),
random input, zero shards `[0, 3, 5]`, assert `decode == input`.

## 14. Reed–Solomon (RSCoder16, 16-bit GF(2^16))

Spec: `INTEGRITY_WRITE_SIDE.md` §4. Same round-trip pattern as §13
with 16-bit symbols. Additionally: verify the Cauchy encoder matrix
is non-singular by inverting a random ND-row submatrix.

## 15. Block header CRC ranges

Spec: `INTEGRITY_WRITE_SIDE.md` §8. Per-block-type rules for which
bytes feed into `HEAD_CRC`. No stand-alone vectors possible (depends
on block content); the oracle is "compatible RAR reader accepts our archive".

Cross-check: assemble a block, run our CRC over the documented byte
range, compare against what `GetCRC15` / `GetCRC50` compute in
a public RAR reader / `arcread.cpp`.

## 16. Archive round-trip test matrix

The minimum oracle for an encoder/decoder pair, per format version:

| Format | Scenario | Oracle |
|--------|----------|--------|
| RAR 1.3 container | single file, stored | Decode `fixtures/1.402/README_store.rar`; bytes and rolling sum+rotate checksum match `fixtures/1.402/expected/README` |
| RAR 1.3 container | single file, stored encrypted | Decode `fixtures/1.402/STOREPWD.RAR` with password `password`; plaintext is `Stored encrypted fixture.\r\n`; rolling sum+rotate checksum is `0x4423` |
| RAR 1.3 container | single file, compressed | Decode `fixtures/1.402/README.RAR`; bytes and rolling sum+rotate checksum match `fixtures/1.402/expected/README` |
| RAR 1.3 container | single file, encrypted | Decode `fixtures/1.402/README_password=password.rar` with password `password`; bytes and rolling sum+rotate checksum match `fixtures/1.402/expected/README` |
| RAR 1.5 / Unpack15 | single file, compressed | Decode `fixtures/1.54/readme_154_normal.rar`; CRC and bytes match `fixtures/1.54/expected/README.md` |
| RAR 1.5 / Unpack15 | single file, solid archive flag | Decode `fixtures/1.54/readme_154_store_solid.rar`; CRC and bytes match `fixtures/1.54/expected/README.md` |
| RAR 1.5 / Unpack15 | SFX | Decode `fixtures/1.54/readme.EXE` from embedded marker |
| RAR 1.5 / Unpack15 | multi-file corpus | Decode `fixtures/1.54/doc_154_best.rar`; all file CRCs match |
| RAR 1.5 / Unpack15 | multi-volume | Decode `fixtures/1.54/random.rar` + `.r00` + `.r01` |
| RAR 3.x RARVM | stock filter bytecode blobs | Verify `fixtures/rarvm/captured-blobs.md` length + CRC32 + XOR checks |
| RAR 3.x RARVM | historical encoder matrix | Decode all archives under `fixtures/rarvm/archives*/`; extracted bytes match `fixtures/rarvm/sources/` |
| RAR 1.5–4.x | single file, all methods (0x30–0x35) | Our output decodes via public RAR reader |
| RAR 1.5–4.x | multi-file, solid | Our output decodes via public RAR reader; CRCs match per file |
| RAR 1.5–4.x | multi-volume | Our split output joins and extracts via public RAR reader |
| RAR 2.0 | encrypted files (`-p`) | Decode `rarfile/rar202-comment-psw.rar` with password `password`; `FILE1.TXT` is `file1\r\n`, `FILE2.TXT` is `file2\r\n`, and both CRC32 fields match |
| RAR 3.x/4.x | encrypted files (`-p`) | Decode `fixtures/1.5-4.x/rar300/encrypted_per_file_rar300.rar` with password `password`; `hello.txt` is `Hello, RAR 3.x fixture world.\n` and CRC32 is `0xa538535e` |
| RAR 1.5–4.x | encrypted (`-hp`) | Round-trip with header encryption |
| RAR 2.9–4.x | PPMd block (UnpVer = 29, RAR `-mc` switch) | Round-trip of large text input. Method byte (`0x30..0x35`) is compression *level*, not codec selector — PPMd is requested via `-mc<MODE>:<MEM>` independent of method byte. |
| RAR 1.5–4.x | with recovery record | `rar r` can repair after bit-flip in data |
| RAR 5.0 | all methods + dictionary sizes | Our output decodes via public RAR reader |
| RAR 5.0 | all 4 filter types | Round-trip with the filter engaged |
| RAR 5.0 | encrypted | Round-trip against both `-p` and `-hp` modes |
| RAR 5.0 | with Quick Open | `rar l` completes quickly against our archive |
| RAR 5.0 | with RR | `rar r` repairs after bit-flip |

Use `scripts/verify-fixtures.py` for the committed fixture invariants. Without
an extractor it verifies README SHA tables, expected-payload hashes, RARVM
bytecode fingerprints, and capture logs. With historical UnRAR it also extracts
the RAR 1.402 and RAR 1.54 fixtures and compares payload bytes/manifests:

```
scripts/verify-fixtures.py \
  --wine-prefix _refs/wineprefixes/winrar300 \
  --unrar-exe "_refs/wineprefixes/winrar300/drive_c/Program Files (x86)/WinRAR/UnRAR.exe"
```

"Our output decodes via public RAR reader" is strong — it catches any wire-format
divergence. Byte-exact comparison against WinRAR's output is much
harder and usually not achievable (see the parity-only items in
`IMPLEMENTATION_GAPS.md` — WinRAR's compressor parameter tables, filter
heuristics, and IV policy are not public).

Old RAR/WinRAR binaries used for fixture generation are kept out of git under
`_refs/rarbins/`. A public backup of the current set is available at
https://archive.org/details/old_winrar_binaries.

## 17. Fixture verification checklist

Before using the fixture set as an implementation oracle, run these mechanical
checks:

1. Run `scripts/verify-fixtures.py` to verify every SHA-256 listed in each
   fixture README:
   - `fixtures/1.402/README.md`
   - `fixtures/1.54/README.md`
   - `fixtures/rarvm/README.md`
2. Decode all `fixtures/1.402/*.rar` archives and compare extracted bytes
   against `fixtures/1.402/expected/README`; use password `password` for the
   encrypted archive.
3. Decode the RAR 1.54 fixtures:
   - `readme_154_normal.rar`
   - `readme_154_store_solid.rar`
   - `readme.EXE`
   - `doc_154_best.rar`
   - `random.rar` with `random.r00` and `random.r01` present.
4. Compare the extracted `README.md` from `readme_154_normal.rar`,
   `readme_154_store_solid.rar`, and `readme.EXE` against
   `fixtures/1.54/expected/README.md`.
5. For `doc_154_best.rar` and the `random.*` multi-volume set, verify full
   extraction against:
   - `fixtures/1.54/expected/doc_154_best.manifest.tsv`
   - `fixtures/1.54/expected/random.manifest.tsv`
6. For `fixtures/rarvm/captured-blobs.md`, recompute `len`, CRC32, and XOR for
   each byte array and compare against the table in §12.
7. For each `fixtures/rarvm/capture-logs/*.jsonl` entry, verify:
   - `xor_ok == true`
   - `code_size` matches the stock-filter length table
   - `crc32` matches the stock-filter fingerprint table
   - `code_hex` equals the corresponding byte array in
     `fixtures/rarvm/captured-blobs.md`.
8. Decode all archives under `fixtures/rarvm/archives/`,
   `fixtures/rarvm/archives-rar300/`, and `fixtures/rarvm/archives-rar420/`;
   compare each extracted file with the same basename under
   `fixtures/rarvm/sources/`.

These checks are intentionally format-agnostic: they validate fixture integrity
and captured constants before an implementation-specific test harness layers on
round-trip archive generation.

### Public reader compatibility matrix

Observed local reader behavior for the committed fixtures:

| Fixture family | 7-Zip 25.01 | Historical UnRAR 3.00 | Historical UnRAR 4.20 | Notes |
|----------------|-------------|------------------------|------------------------|-------|
| `fixtures/1.402/*.rar` (`RE~^`) | Cannot open | Extracts/tests OK | Extracts/tests OK | Use for RAR 1.3/1.4 container + Unpack15 coverage. |
| `fixtures/1.54/readme_154_normal.rar` | Lists, extraction reports unsupported method | Extracts/tests OK | Extracts/tests OK | Same for `readme_154_store_solid.rar`, `readme.EXE`, and `doc_154_best.rar`. |
| `fixtures/1.54/random.rar` + `.r00`/`.r01` | Lists first volume only for metadata checks | Extracts/tests OK across all volumes | Extracts/tests OK across all volumes | Full expected payload is represented by `expected/random.manifest.tsv`. |
| `fixtures/rarvm/archives*/` | Lists RAR3 archives, extraction reports unsupported method for tested fixtures | Extracts/tests OK for RAR 3.93 sample | Extracts/tests OK for RAR 3.93 sample | These fixtures are primarily for VM bytecode capture and source-payload comparison. |
| RAR 5.0/7.0 writer smoke tests | Useful public reader oracle | Too old | Too old for RAR 5.0/7.0 | Use a modern public reader when RAR5/7 fixture output exists. |

This matrix is a compatibility aid, not a normative part of the file format.
Current 7-Zip is still useful for listing metadata and independent source-code
cross-checks, but it is not the extraction oracle for the historical compressed
fixtures in this repository.

## 18. Fuzzing and negative tests

Beyond the positive round-trip vectors above:

- **Truncated archives**: for every valid archive, test that reading
  the first N bytes for N = 1..len-1 either fails gracefully or
  returns the blocks readable so far. No crashes, no infinite loops.
- **Bit-flipped headers**: flip a random byte in each block header;
  reader must report "broken header" for that block and continue to
  the next (using the block-length field) without crashing.
- **Malicious paths**: see `PATH_SANITIZATION.md` §8 for the attack
  table. Each attack should be covered by a reject-or-rename test.
- **Zip bombs**: a 42-byte archive decompressing to multi-GB data.
  Reader should honour a configurable output-size cap; the cap
  triggers before exhausting memory.

Use `scripts/generate-negative-fixtures.py <out-dir>` to generate deterministic
truncated and bit-flipped copies of representative fixtures. Keep generated
negative archives out of git unless a specific bug requires preserving one as a
regression test.

## 19. Sources

- **NIST SP 800-38A** — AES-CBC test vectors.
- **RFC 6070** — PBKDF2 test vectors.
- **blake2.net** — BLAKE2sp reference vectors.
- **`_refs/7zip`** — independent encoders for PPMd/LzFind/Huffman;
  useful for second-opinion ratio checks.
- **`_refs/XADMaster`** — alternate RAR 1.3/1.5/2.0/3.0 decoders.
