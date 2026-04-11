# RAR Encryption — Write-Side Specification

The format specs document the **decryption** side of every RAR encryption
version (`RAR13_FORMAT_SPECIFICATION.md` §7,
`RAR15_40_FORMAT_SPECIFICATION.md` §14.3–14.6,
`RAR5_FORMAT_SPECIFICATION.md` Appendix C). This document fills the
encoder-side gap: how to **write** encrypted streams, and what symmetry
breaks exist between encrypt and decrypt for each version.

Every RAR encryption cipher from 1.3 through 7.0 falls into one of three
patterns on the write side:

1. **Self-inverse stream cipher** — same routine encrypts and decrypts
   (RAR 1.5).
2. **Additive/subtractive stream cipher** — encrypt is the arithmetic
   inverse of decrypt (RAR 1.3).
3. **Block cipher with separate encrypt/decrypt paths** — standard symmetric
   encryption (RAR 2.0 Feistel, RAR 3.x/4.x AES-128-CBC, RAR 5.0+ AES-256-CBC).

References: public RAR reader behavior plus the existing format specs for
the decoder side.

---

## 1. RAR 1.3 (CRYPT_RAR13)

Key derivation: 3 bytes from the password. Decryption is a position-dependent
**subtraction** stream (`*Data -= Key[0]`), so encryption is the matching
**addition**. The keystream update is identical.

### Encrypt

```
def encrypt13(data):
    for i in range(len(data)):
        Key[1] = (Key[1] + Key[2]) & 0xFF
        Key[0] = (Key[0] + Key[1]) & 0xFF
        data[i] = (data[i] + Key[0]) & 0xFF
```

Compared to `Decrypt13` in a public RAR reader, only the operator
on line 46 flips from `-=` to `+=`. The keystream advance (`Key[1] += Key[2]`
then `Key[0] += Key[1]`) is identical because it depends only on the key
state, not the data byte.

### Key init

`SetKey13` for archive payload encryption; `SetCmt13Encryption` (fixed key
`{0, 7, 77}`) for comment encryption. The write side uses the same
initializers.

Security: none. 3-byte key, trivial to break. Include for archive-level
compatibility only.

---

## 2. RAR 1.5 (CRYPT_RAR15)

A pure XOR stream cipher: `*Data ^= keystream_byte`. XOR is **self-inverse**
— the encryption routine is literally the decryption routine, unchanged.

### Key init

Verified against `_refs/unrar/crypt1.cpp` (`SetKey15`). Uses the IEEE 802.3
CRC32 of the password as a seed, plus per-byte mixing of the password against
the same CRC32 lookup table.

```
def set_key_15(password):
    psw_crc = crc32_ieee(password)              # standard reflected CRC32
    Key = [0, 0, 0, 0]                          # four 16-bit words
    Key[0] =  psw_crc        & 0xFFFF
    Key[1] = (psw_crc >> 16) & 0xFFFF
    Key[2] = 0
    Key[3] = 0
    for P in password:
        Key[2] ^= (P ^ CRCTab[P]) & 0xFFFF
        Key[3]  = (Key[3] + (P + (CRCTab[P] >> 16))) & 0xFFFF
    return Key
```

`CRCTab[i]` is the standard 256-entry IEEE 802.3 CRC32 table (poly
`0xEDB88320`); both shifted bytes (`& 0xFFFF` and `>> 16`) are taken from the
same 32-bit table entry.

### Keystream + encrypt

```
def crypt15(Key, data):
    for i in range(len(data)):
        Key[0] = (Key[0] + 0x1234) & 0xFFFF
        idx    = (Key[0] & 0x1FE) >> 1          # even byte index 0..127
        Key[1] = (Key[1] ^ (CRCTab[idx]      & 0xFFFF)) & 0xFFFF
        Key[2] = (Key[2] - ((CRCTab[idx] >> 16) & 0xFFFF)) & 0xFFFF
        Key[0] = (Key[0] ^ Key[2]) & 0xFFFF
        Key[3] = rotr16(Key[3], 1) ^ Key[1]
        Key[3] = rotr16(Key[3], 1)
        Key[0] = (Key[0] ^ Key[3]) & 0xFFFF
        data[i] ^= (Key[0] >> 8) & 0xFF
```

Where `rotr16(x, 1) = ((x >> 1) | (x << 15)) & 0xFFFF`.

Encrypt and decrypt are the same routine. Verified against `Crypt15` in
`_refs/unrar/crypt1.cpp`.

Security: also broken (CRC-32-derived keystream), but less trivially than
RAR 1.3. Do not use for anything real.

---

## 3. RAR 2.0 (CRYPT_RAR20, Feistel)

32-round Feistel-style block cipher on 16-byte blocks. State carried
across blocks: four 32-bit key words `Key20[0..3]` and a 256-byte
substitution table `SubstTable20[]`. Verified against
`_refs/unrar/crypt2.cpp` (`SetKey20`, `EncryptBlock20`,
`DecryptBlock20`, `UpdKeys20`, `Swap20`).

### 3.1 Initial key constants

```
Key20 = [0xD3A3B879, 0x3F6D12F7, 0x7515A235, 0xA4E7F123]
```

Reset to these four hardcoded values at every `SetKey20` call (i.e.,
once per archive open). Verified at `crypt2.cpp:30-33`.

### 3.2 SubstTable20 initial contents

The 256-byte permutation `InitSubstTable20[]` is reproduced in full at
`crypt2.cpp:9-26`. Encoder/decoder must use the same array verbatim.

### 3.3 Round function

`substLong(t)` applies `SubstTable20` byte-by-byte to a 32-bit word:

```
substLong(t) = SubstTable20[t & 0xff]
             | (SubstTable20[(t >> 8)  & 0xff] << 8)
             | (SubstTable20[(t >> 16) & 0xff] << 16)
             | (SubstTable20[(t >> 24) & 0xff] << 24)
```

Per-round transform (encrypt path; decrypt iterates `I = 31 down to 0`
with the same body):

```
def round(I, A, B, C, D):
    T  = (C + rotl32(D, 11)) ^ Key20[I & 3]
    TA = A ^ substLong(T)
    T  = (D ^ rotl32(C, 17)) + Key20[I & 3]
    TB = B ^ substLong(T)
    return (C, D, TA, TB)        # rotate (A, B, C, D) → (C, D, TA, TB)
```

`rotl32(x, n)` is a 32-bit rotate-left with truncation modulo 2^32.

### 3.4 Block transform

```
def encrypt_block_20(buf):
    A = read_u32_le(buf, 0)  ^ Key20[0]
    B = read_u32_le(buf, 4)  ^ Key20[1]
    C = read_u32_le(buf, 8)  ^ Key20[2]
    D = read_u32_le(buf, 12) ^ Key20[3]
    for I in 0..31:
        A, B, C, D = round(I, A, B, C, D)
    write_u32_le(buf, 0,  C ^ Key20[0])    # note: output order is C, D, A, B
    write_u32_le(buf, 4,  D ^ Key20[1])
    write_u32_le(buf, 8,  A ^ Key20[2])
    write_u32_le(buf, 12, B ^ Key20[3])
    upd_keys_20(buf)                       # buf now holds ciphertext

def decrypt_block_20(buf):
    A = read_u32_le(buf, 0)  ^ Key20[0]
    B = read_u32_le(buf, 4)  ^ Key20[1]
    C = read_u32_le(buf, 8)  ^ Key20[2]
    D = read_u32_le(buf, 12) ^ Key20[3]
    saved = bytes(buf[0:16])               # snapshot ciphertext BEFORE overwriting
    for I in 31..0:                        # reverse round order
        A, B, C, D = round(I, A, B, C, D)
    write_u32_le(buf, 0,  C ^ Key20[0])
    write_u32_le(buf, 4,  D ^ Key20[1])
    write_u32_le(buf, 8,  A ^ Key20[2])
    write_u32_le(buf, 12, B ^ Key20[3])
    upd_keys_20(saved)                     # update with the ciphertext (saved)
```

### 3.5 Inter-block key update

```
def upd_keys_20(buf16):
    for I in 0, 4, 8, 12:        # four 4-byte groups
        Key20[0] ^= CRCTab[buf16[I]]
        Key20[1] ^= CRCTab[buf16[I+1]]
        Key20[2] ^= CRCTab[buf16[I+2]]
        Key20[3] ^= CRCTab[buf16[I+3]]
```

`CRCTab[]` is the standard 256-entry IEEE 802.3 CRC32 table
(poly `0xEDB88320`).

**Symmetry trap.** Both directions feed `upd_keys_20` with the
**ciphertext** of the block — the encoder uses the in-place output
buffer (which holds ciphertext after the writeback) and the decoder
uses the saved copy of its input (which is the same ciphertext). This
is what keeps the key state synchronized between encryptor and
decryptor. An implementation that feeds plaintext to `upd_keys_20` on
either side will desync after the first block. Verified at
`crypt2.cpp:84` (`UpdKeys20(Buf)` after encrypt writeback) and
`crypt2.cpp:108` (`UpdKeys20(InBuf)` with the saved input).

### 3.6 Key derivation from password

```
def set_key_20(password):
    Key20 = [0xD3A3B879, 0x3F6D12F7, 0x7515A235, 0xA4E7F123]   # reset
    SubstTable20 = list(InitSubstTable20)                       # reset

    for J in 0..255:
        for I in 0, 2, 4, ... up to PswLength-2:                # step 2
            N1 = CRCTab[(password[I]   - J) & 0xff] & 0xff
            N2 = CRCTab[(password[I+1] + J) & 0xff] & 0xff
            K = 1
            while N1 != N2:
                Swap(SubstTable20[N1], SubstTable20[(N1 + I + K) & 0xff])
                N1 = (N1 + 1) & 0xff
                K += 1

    psw_padded = password + b'\x00' * ((-len(password)) % 16)
    for offset in 0, 16, ..., len(psw_padded) - 16:
        encrypt_block_20(psw_padded[offset:offset+16])     # mutates Key20 + SubstTable20
```

The two-step shuffle: a CRCTab-driven swap pass mixes the password
into `SubstTable20`, then encrypting the zero-padded password
finalizes the cipher state by exercising both `Key20` evolution (via
`upd_keys_20`) and the round structure on real data. Both encoder and
decoder run the **same** routine — direction during key setup is
always encrypt.

The shuffle's `K=1; N1 != N2; ...` inner loop has no fixed iteration
count: it can stall until `N1` happens to equal `N2`. For some
password byte combinations this could be many iterations; for
identical pairs (`Password[I] == Password[I+1]`, `J=0`) it terminates
immediately. The behaviour is deterministic but bounded only
empirically. Encoders should not rely on a specific iteration count.

Security: proprietary Feistel, broken in the early 2000s, faster than
brute force. Do not use for anything real.

---

## 4. RAR 3.x / 4.x (CRYPT_RAR30) — AES-128 CBC

**AES-128**, not 256. 16-byte AES key plus 16-byte AES-CBC IV are
derived together from a 262144-round SHA-1 chain over
`utf16le(password) || salt[0..7]`. Verified against
`_refs/unrar/crypt3.cpp` (`SetKey30`) and
`_refs/unrar/sha1.cpp` (`sha1_process_rar29`, `sha1_done`).

### 4.1 KDF inputs

```
RawPsw = utf16le(password)            # WideToRaw, no NUL terminator
if salt is not None:
    RawPsw += salt[0..7]              # 8-byte salt if present
RawLength = len(RawPsw)
```

Empty password is legal. `WideToRaw` writes each wide character as 2
little-endian bytes — i.e. UTF-16-LE without BOM.

### 4.2 The 262144-round inner loop

```
HashRounds = 0x40000        # 262144

c = sha1_init()
AESInit = bytearray(16)
for I in 0..HashRounds-1:                              # I = 0, 1, ..., 262143
    sha1_process_rar29(c, RawPsw)                      # see §4.3 — NOT vanilla SHA-1
    PswNum = bytes([ I & 0xff, (I >> 8) & 0xff, (I >> 16) & 0xff ])
    sha1_process(c, PswNum)                            # 3-byte LE iteration counter
    if I % (HashRounds // 16) == 0:                    # at I = 0, 16384, 32768, ..., 245760
        snapshot = clone(c)                            # COPY context
        digest5  = sha1_done(snapshot)                 # finalize copy; original c untouched
        AESInit[I // (HashRounds // 16)] = digest5[4] & 0xff   # low byte of word 4 = byte 19 of 20-byte SHA-1 output

# Final AES key = the 16 bytes of digest words 0..3 in **little-endian** byte order
final_digest = sha1_done(c)
AESKey = bytearray(16)
for I in 0..3:
    for J in 0..3:
        AESKey[I*4 + J] = (final_digest[I] >> (J * 8)) & 0xff
```

Key facts an implementer needs:

- **IV byte sample positions:** `I = 0, 0x4000, 0x8000, ..., 0x3C000`
  (16 samples at 16384-iteration intervals starting from 0). The
  **last** sample is at iteration 245760, *not* 262144 — the final
  16384 iterations contribute only to the final AES key.
- **Snapshot semantics:** `sha1_process_rar29` runs unmodified after a
  snapshot; `sha1_done` is called on a *copy* of the context so the
  running chain is undisturbed. Equivalent: clone state[5] + count +
  buffer, finalize the clone, discard the clone.
- **IV byte source:** `(byte)digest[4]` in unrar takes the low 8 bits
  of the fifth state word. Since `sha1_done` writes
  `digest[i] = state[i]` natively (`sha1.cpp:197-198`) and SHA-1 outputs
  state words in big-endian standard byte order, this corresponds to
  **byte 19 of the 20-byte standard SHA-1 digest** (the
  least-significant byte of the last word).
- **AES key byte order:** the 16 key bytes are extracted from
  `digest[0..3]` in **little-endian** order per word: `AESKey[0]` =
  low byte of `digest[0]`, `AESKey[3]` = high byte of `digest[0]`,
  etc. This differs from the IV sample's "low byte of word 4" —
  encoder/decoder must use the same byte-extraction or the AES key
  will be wrong.

### 4.3 `sha1_process_rar29` — the RAR-specific SHA-1 quirk

**This is the single biggest pitfall for clean-room implementers.**
RAR 3.x's KDF does *not* use vanilla SHA-1 for the password hashing
loop. Verified at `_refs/unrar/sha1.cpp:146-164`:

```c
void sha1_process_rar29(sha1_context *context, const unsigned char *data, size_t len)
{
    /* ... feed `data` into context like normal SHA-1 ... BUT: */
    for ( ; i + 63 < len; i += 64) {
        SHA1Transform(context->state, workspace, data+i, false);
        for (uint k = 0; k < 16; k++)
            RawPut4(workspace[k], (void*)(data+i+k*4));   /* WRITE BACK */
    }
}
```

After processing each 64-byte block of the input buffer, the
SHA-1 message-schedule words (`workspace[0..15]`) are **written back
into the input buffer** in little-endian, replacing the original
bytes. On the next loop iteration of the KDF (`I+1`), the modified
buffer is hashed again — so the password+salt buffer is destructively
mutated by the chain.

Implications:

- A clean-room SHA-1 library will **not** produce the right digest. The
  KDF must use a SHA-1 variant that writes back the message schedule.
- The mutation only kicks in for inputs spanning multiple 64-byte
  blocks. For UTF-16-LE passwords up to 27 wide chars (54 bytes) +
  8-byte salt = 62 bytes, the mutation is inert. For longer passwords
  (≥ 28 wide chars), the mutation is active and the result depends on
  the exact buffer layout each iteration.
- The encoder and decoder both use this variant. The KDF cache (§4.5)
  is what makes this fast in practice — the 262144-round loop is run
  once per (password, salt) pair.

This quirk was retroactively documented as RAR 3.x-specific (hence the
`_rar29` suffix — RAR 2.9 was the first version to use it). RAR 5.0
moved to standard PBKDF2-HMAC-SHA-256 (see §5).

### 4.4 Direction-dependent step

The KDF is symmetric. The only direction-dependent step is AES-CBC
initialization:

```
rin.Init(Encrypt, AESKey, 128, AESInit)
```

`Encrypt = true` configures the AES state machine for forward rounds
rather than reverse rounds. The key schedule is built once and reused.

### 4.5 KDF cache

`KDF3Cache` keys on `(password, salt-presence, salt[0..7])` and
caches `(AESKey, AESInit)`. Encoder and decoder both populate it. See
`crypt3.cpp:6-16` for the lookup and `:55-61` for the insert. Cache
size is fixed; entries cycle FIFO-by-position via `KDF3CachePos`.

### 4.6 Write-side encryption

Standard AES-128-CBC encrypt with the derived key and IV. The encoder
must zero-pad each encrypted region to a 16-byte boundary (the format
uses zero padding, not PKCS#7). The plaintext size is implicit — the
decoder knows how many plaintext bytes to extract from context (file
size in the header, or header size for encrypted headers).

### 4.7 Trap: salt reuse across headers

Each encrypted file header carries its own 8-byte salt. The encoder
**must** generate a fresh random salt for every file header, and must
**not** reuse the same salt across multiple files — salt reuse lets an
attacker detect common prefixes, *and* the §4.5 KDF cache means a
re-used salt yields a cache hit on the receiver: identical salt across
files implies identical `(AESKey, AESInit)`, which is catastrophic.
Always generate fresh salt; the read-side cache becomes harmless when
no two files share salt.

Security: AES-128-CBC with a strong KDF is cryptographically sound. The
262144-iteration SHA-1 chain (with the `_rar29` quirk) is weaker than
modern PBKDF2-HMAC-SHA-256 but still adequate against casual attacks.
No known break as of 2024.

---

## 5. RAR 5.0+ (CRYPT_RAR50) — AES-256 CBC with PBKDF2-HMAC-SHA256

Modern cryptography. AES-256 key + 16-byte IV, key derived via PBKDF2 with
HMAC-SHA256, `2^Lg2Cnt` iterations (typically `Lg2Cnt = 15`, giving 32768
iterations). Plus two auxiliary values derived from the same PBKDF2 chain
by continuing past the key:

- **HashKey** (V1) = PBKDF2 at `count + 16` iterations. Used as the HMAC
  key for file-content hashes (CRC32 is HMAC'd into a MAC, BLAKE2sp is
  HMAC'd via HMAC-SHA256 over the digest).
- **PswCheckValue** (V2) = PBKDF2 at `count + 32` iterations. XOR-folded
  into 8 bytes to become the `PswCheck` stored in the encryption header.

The spec's Appendix C is terse but correct; this section fills in the
missing encoder detail.

### 5.1 PBKDF2 chain layout

The function `pbkdf2(Pwd, Salt, Key, V1, V2, Count)` runs **one** PBKDF2
chain and taps it at three points:

```
U1 = HMAC(Pwd, Salt || 0x00000001)       # iteration 1; computed once
Fn = U1                                  # accumulator initialised once

for I in [Count-1, 16, 16]:              # three output segments share Fn and U1
    for J in range(I):
        U2 = HMAC(Pwd, U1)
        U1 = U2
        Fn ^= U1
    write Fn to (Key, V1, V2)[current segment]
```

`Fn` and `U1` are **not** re-initialised between segments — the chain
continues, and each tap is the running XOR of every `Ui` produced so far.
So:
- `Key`   = Fn after `1 + (Count-1) = Count` iterations.
- `V1`    = Fn after `Count + 16` iterations.
- `V2`    = Fn after `Count + 32` iterations.

Verified against `_refs/unrar/crypt5.cpp` (`pbkdf2`).

**Performance note.** For the same password, the HMAC inner/outer SHA-256
states are the same, so they can be computed once and reused across all
`HMAC(Pwd, *)` calls. An encoder should do the same; 32768 iterations x 2
SHA-256 blocks/iter is 65K block hashes per file, and caching the inner/outer
prep eliminates half of them.

### 5.2 Encoder write recipe

```
def encrypt_file_rar50(password_utf8, plaintext):
    salt = os.urandom(16)
    iv   = os.urandom(16)
    lg2_count = 15           # typical; encoder choice

    key_32, hash_key, psw_check_32 = pbkdf2(
        password_utf8, salt, count = 1 << lg2_count)

    # Password check value is the XOR fold of psw_check_32 into 8 bytes:
    psw_check_8 = bytes(
        psw_check_32[i] ^ psw_check_32[i + 8] ^ psw_check_32[i + 16] ^ psw_check_32[i + 24]
        for i in range(8)
    )
    ciphertext = aes256_cbc_encrypt(plaintext, key_32, iv,
                                    pad = ZERO_PAD_TO_16)

    # Emit encryption header extra record (§8 record type 0x01 of RAR 5.0 spec):
    emit_vint(0)                  # version = 0 (AES-256)
    emit_byte(lg2_count)           # KDF count (binary log)
    emit_bytes(salt)               # 16 bytes
    emit_bytes(iv)                 # 16 bytes (only if `is_file_record`; archive
                                  #          encryption header has its own salt layout)
    if emit_check_value:
        emit_bytes(psw_check_8)    # 8 bytes
        emit_bytes(checksum(psw_check_8))  # 4-byte CRC of the check value
    emit_bytes(ciphertext)
```

The check value stored in the header is the 8-byte folded `psw_check_8`
followed by its own 4-byte checksum (CRC32 over the 8 bytes, per the RAR
5.0 spec §6 / §8). The full 12-byte field lets the decoder verify the
password without decrypting any content.

### 5.3 File hash MAC conversion

When a file or service data area has a RAR5 encryption extra record with flag
`0x0002` (`HashMAC` / `UseMAC`) set, its file-content hashes are not written
raw. They are converted to MACs using that record's `HashKey` (the V1 output of
PBKDF2):

- **CRC32:** serialize the 4-byte CRC in **little-endian** order, compute
  `HMAC-SHA256(HashKey, serialized_crc)`, then XOR-fold the 32-byte digest
  back into a 32-bit integer:
  ```
  result = 0
  for i = 0..31:
      result ^= Digest[i] << ((i & 3) * 8)
  ```
  Equivalent restatement: XOR the SHA-256 digest with itself in
  4-byte strides to produce a 4-byte folded MAC, then load those 4
  bytes as a little-endian `uint32`. The resulting value replaces the
  original `CRC32` field in the file header's `DataCRC32` slot.
- **BLAKE2sp:** compute `HMAC-SHA256(HashKey, BLAKE2sp_digest_32_bytes)`;
  the 32-byte HMAC output **replaces** the BLAKE2sp digest in the
  File Hash Record (type `0x02`). No folding — both HMAC-SHA256 and
  BLAKE2sp produce 32-byte outputs.

An encoder that sets the encryption record's `HashMAC` flag **must** apply this
MAC conversion or the decoder will reject the archive. Files without a per-file
or per-service encryption extra record, and encrypted records without `HashMAC`,
use raw CRC32 / BLAKE2sp. Archive-wide header encryption (`HEAD_CRYPT`) alone
does not trigger file-hash MAC conversion; it only encrypts the headers that
carry those hash fields.

For split files, non-final parts can store a CRC32 of the packed bytes in that
volume. That packed-part CRC is checked raw; RAR5 does not apply HMAC conversion
to it.

**Which hash types need MAC conversion.** Only the RAR5 CRC32 and BLAKE2sp
file-content hash forms need conversion. `HASH_NONE` has nothing to convert;
RAR 1.4's pre-CRC32 checksum predates RAR5 AES encryption, so it never
co-occurs with this encryption extra record. An encoder need not handle a fifth
hash type; any future extension would require a parallel decoder update.

**Folding formula verification.** The XOR-fold formula is exact:
byte index `i` contributes to the output byte at position `i & 3` with
no shift other than the within-byte placement. Because 32 is a
multiple of 4, each output byte receives exactly 8 input bytes XORed
together. The distribution is uniform — no output byte is favored —
so a brute-force attacker who tries to invert the fold faces
`2^(32 - 32) = 1` candidate HMAC output per fold output only in the
statistical average; in practice, recovering the full 256-bit HMAC
from the 32-bit fold is computationally infeasible.

### 5.4 Trap: NULL IV means "check password only"

The RAR5 KDF can be used in a "password check only" mode: derive the key,
HashKey, and PswCheck values without initializing AES-CBC with an IV. This is
used when the caller only wants to produce a PswCheck value for archive-level
password verification, not to encrypt any bytes. An encoder implementing
archive encryption headers should follow the same pattern: call the KDF with
`InitV = None` to get the PswCheck, and only pass a real IV when actually
encrypting content.

### 5.5 Trap: KDF cache correctness

KDF outputs may be cached, but the cache key **must** include all three of
password, salt, and lg2_count. An encoder that caches on password alone will
produce correct-looking output for the first file and silently wrong output for
any file whose salt differs. Since salts are per-file, a
naive cache is worse than no cache.

### 5.6 Security

AES-256-CBC + PBKDF2-HMAC-SHA256 with 32K+ iterations is well-studied and
sound against known attacks. The per-file salt and IV give forward secrecy
between files. The password check value is XOR-folded, which destroys
enough information that a brute-force attacker still needs to run the
full PBKDF2 chain per candidate password — the check value is not a
shortcut.

**Iteration-count limits.** The on-disk `lg2_count` byte is constrained:

| Limit | Value | Iterations | Source |
|-------|-------|-----------|--------|
| `CRYPT5_KDF_LG2_COUNT` (compatible RAR reader default) | 15 | 32,768 | `_refs/unrar/crypt.hpp:19` |
| `CRYPT5_KDF_LG2_COUNT_MAX` (decoder hard maximum) | 24 | 16,777,216 | `_refs/unrar/crypt.hpp:20` (`SetKey50` rejects anything above this) |

**Encoder policy:**

- **Floor:** emit `lg2_count >= 15`. Lower values are accepted by current
  readers but provide insufficient brute-force resistance.
- **Default:** `lg2_count = 15` for compatibility; `lg2_count = 16` (65K
  iterations) to stay ahead of cheap GPU-based offline attacks.
- **Ceiling:** `lg2_count <= 24`. Anything above is rejected by every
  reader sourced from compatible RAR reader.

**Decoder policy:**

- Reject `lg2_count > 24` (matches reference reader behaviour).
- Optionally reject `lg2_count < 10` (1024 iterations) as evidence of a
  hostile or broken encoder; standard archives are always >= 15.

AES-256-CBC + PBKDF2-HMAC-SHA256 with 32K+ iterations is well-studied and
sound against known attacks. The per-file salt and IV give forward secrecy
between files. The password check value is XOR-folded, which destroys
enough information that a brute-force attacker still needs to run the
full PBKDF2 chain per candidate password — the check value is not a
shortcut.

---

## 6. Archive-wide header encryption (HEAD_CRYPT)

The sections above cover **per-file** encryption — each file record carries its
own salt/IV and the rest of the archive (main header, file headers, service
headers) is written in the clear. RAR also supports **archive-wide** header
encryption, where every block after the marker is encrypted and the entire
archive structure is opaque to anyone without the password. This is the
`-hp<pwd>` switch in WinRAR.

Two wire-format eras:

- **RAR 3.x/4.x (CRYPT_RAR30 headers):** archive flag `MHD_PASSWORD` (`0x0080`)
  in the main header signals "all headers after the main header are
  encrypted." Each subsequent block is prefixed with an 8-byte salt; AES-128
  CBC encrypts the fixed-size block bytes in place. No IV field — the AES
  state is fresh per block (CBC chain reset, implicit zero IV, because the
  key was just re-derived).
- **RAR 5.0+ (CRYPT_RAR50 headers):** a dedicated `HEAD_CRYPT` block (type 4)
  follows the marker and carries the archive-wide salt, Lg2Count, and
  PswCheck. Every subsequent header is prefixed with a **per-block 16-byte
  IV**, then AES-256-CBC encrypted and zero-padded to 16 bytes. The key is
  derived **once** from the HEAD_CRYPT salt and reused across every header.

### 6.1 RAR 5.0 wire format

```
[marker 8 bytes]
[HEAD_CRYPT block]                     ← unencrypted, carries Salt16+Lg2Cnt+PswCheck
[IV16][AES-CBC(MainHead,       pad=0)]
[IV16][AES-CBC(FileHead1,      pad=0)]
[IV16][AES-CBC(FileHead2,      pad=0)]
...
[IV16][AES-CBC(EndArcHead,     pad=0)]
```

File **data** (compressed payload following each FileHead) is **not** covered
by header encryption — it uses per-file encryption as documented in §5 if the
file also has its own encryption extra record. Archive-wide header encryption
and per-file data encryption are orthogonal; `-hp` typically implies both
(every file gets its own encryption record) but the mechanisms are separate.

The HEAD_CRYPT block itself is identical to a per-file encryption extra record
in layout (§5.2) but written as a top-level block with `HeaderType = 4`. The
Salt field in HEAD_CRYPT is the **archive-wide** salt; it is not reused in any
per-file encryption record.

### 6.2 RAR 5.0 encoder recipe

```python
def write_encrypted_headers_50(out, password_utf8, headers, lg2_count=15):
    # Derive once from the archive-wide salt. IV is unused for derivation;
    # pass None / NULL to skip the rin.Init step — see §5.4.
    archive_salt = os.urandom(16)
    key_32, _hash_key, psw_check_8 = pbkdf2_derive(
        password_utf8, archive_salt, count=1 << lg2_count)
    psw_check_12 = psw_check_8 + crc32_le(psw_check_8).to_bytes(4, "little")

    # 1. Write the marker.
    out.write(RAR50_MARKER)

    # 2. Emit the HEAD_CRYPT block in the clear.
    emit_head_crypt(out,
                    version=0,                 # AES-256
                    flags=0x0001,              # password check present
                    lg2_count=lg2_count,
                    salt=archive_salt,
                    check=psw_check_12)

    # 3. For every subsequent header, prefix a fresh IV and AES-CBC encrypt.
    for hdr_bytes in headers:
        # hdr_bytes already contains its final HeadSize and plaintext HeadCRC.
        iv = os.urandom(16)
        padded = hdr_bytes + b"\x00" * (-len(hdr_bytes) % 16)
        ct = aes256_cbc_encrypt(padded, key_32, iv)
        out.write(iv)
        out.write(ct)
```

Notes:

- **CBC chain resets per block.** Each header starts with a fresh random IV,
  so ciphertext from one header does not chain into the next. This is
  essential because the decoder reads headers independently (seeking around
  for Quick Open, locator lookups, etc.) and must be able to decrypt any
  header in isolation given the archive-wide key.
- **Zero padding, not PKCS#7.** The header's own `Header Size` vint tells the
  decoder how many bytes follow from `Header Type` through the end of the Extra
  Area. The complete plaintext header length is
  `4 + sizeof(HeaderSizeVint) + HeaderSize`. Padding bytes after that complete
  plaintext header are ignored. On disk, an encrypted header occupies
  `16 + align16(complete_plaintext_header_length)` bytes: 16 bytes of IV plus
  the padded AES-CBC ciphertext.
- **Header CRC32 covers plaintext.** The `HeadCRC` field inside each header
  is computed over the plaintext header bytes (everything after HeadCRC) —
  not over the ciphertext. The decoder CRCs after decrypting. The HEAD_CRYPT
  block itself has a normal plaintext CRC.
- **Backpatch before encryption.** Any placeholder inside the header
  (`HeadSize`, `DataSize`, locator offsets, optional `DataCRC32`, etc.) must be
  finalized in the plaintext buffer before computing `HeadCRC` and before
  encrypting. If a file's packed size is not known before compression, reserve
  the final encrypted-header footprint on disk, stream the data, then seek back
  and overwrite that reserved region with the final `[IV16][ciphertext]`.
- **No separate check-value path.** Unlike per-file encryption where the
  PswCheck is optional, archive header encryption essentially requires it —
  without it, a wrong password silently produces garbage headers that fail
  CRC. Set the encryption flag `0x0001` and emit the 12-byte check value.
- **Lg2Count is global.** All headers share one KDF output. An encoder can
  compute the key once at archive-open time.

### 6.3 The NULL-IV "password check only" variant

The RAR5 key setup has a password-check-only variant: if no IV is supplied,
derive the key, HashKey, and PswCheck values without initializing AES-CBC. This
is used by the encoder when it wants to compute PswCheck (for writing the
HEAD_CRYPT block or a per-file encryption extra record) **without** actually
encrypting any bytes yet. It's a pure KDF invocation.

An encoder should use this path for:

1. Computing `PswCheck` for the HEAD_CRYPT block before any real IV exists.
2. Validating a user-supplied password against an existing HEAD_CRYPT block
   (round-trip the check value) before attempting a full decrypt.

The actual per-block AES-CBC encrypt then uses the derived key bytes and each
block's IV. An encoder should cache the derived key bytes and call the AES
primitive directly per block, skipping the KDF entirely on subsequent headers.

### 6.4 RAR 3.x/4.x archive-wide header encryption

Flagged by the `MHD_PASSWORD` (`0x0080`) bit in the main archive header's
flags field. When set, **every block after the main header** is preceded by
an 8-byte salt and encrypted with AES-128-CBC. Same per-block structure as
§6.1 but with a different prefix size:

```
[marker 7 bytes]
[MainHead ... MHD_PASSWORD flag set, ENCRYPTVER, HighPosAv, PosAv ...]
[Salt8][AES128-CBC(FileHead1,  pad=0)]
[Salt8][AES128-CBC(FileHead2,  pad=0)]
...
```

The main header itself is **not** encrypted in this scheme — it has to be
readable in the clear so the decoder can see the `MHD_PASSWORD` flag. Only
block headers **after** the main header are encrypted.

Encoder subtleties:

- **Re-key per block vs. reuse.** The decoder calls `SetCryptKeys` with each
  block's 8-byte salt (`arcread.cpp:163`), which re-runs the 262144-iteration
  SHA-1 KDF every time. This is catastrophically slow unless the encoder
  writes the **same salt for every block** — then the `KDF3Cache` hits and
  the KDF runs exactly once per archive. **An encoder targeting RAR 3.x
  header encryption should reuse a single archive-wide salt across every
  encrypted header.** This is the opposite of per-file encryption (§4
  "Trap: salt reuse across headers"), where fresh salt is a **security**
  requirement — but archive-header encryption uses the archive salt only
  as a key-derivation input, and per-block freshness buys nothing because
  an attacker who can see one header can see them all.
- **Implicit zero IV.** Each block resets CBC state (no IV field on disk),
  so any header modification flips at most `ceil(HeadSize/16)` ciphertext
  blocks in that header — same tamper-locality as RAR 5.0's explicit IV.
- **EncryptVer field.** The RAR 3.x main header reports `ENCRYPTVER` (one
  byte) telling the decoder which crypt version to use. Set to 20 for
  AES-128 (the only RAR 3.x+ value).
- **Comment and service headers.** `HEAD3_CMT`, `HEAD3_SERVICE`, and any
  other non-MAIN blocks are all encrypted under this scheme.

Recommendation: if the target is bit-for-bit interop with WinRAR's `-hp`
output, inspect a sample archive to confirm whether WinRAR uses one salt or
many. For a clean-room encoder, one-salt-per-archive is both correct and
much faster.

#### 6.4.1 Password check for RAR 3.x/4.x (no explicit check field)

Unlike RAR 5.0's HEAD_CRYPT block (§6.3), RAR 3.x/4.x has **no explicit
`PswCheck` field**. A decoder cannot distinguish a wrong password from a
correct one until it has decrypted at least one full block and validated
its `HEAD_CRC` (CRC16, low 16 bits of CRC32 over the cleartext header).

**Implicit check procedure (decoder / "check only" flow):**

```
given password, archive bytes:
    read marker (7 bytes, clear)
    read main header (clear)
    if not (MainFlags & MHD_PASSWORD): return "archive is not encrypted"

    read first post-main block's 8-byte salt (clear)
    key, iv = KDF_RAR3(password, salt)          # 262144 SHA-1 iterations
    ciphertext = read next HeadSize bytes       # HeadSize unknown yet;
                                                # read AES block-by-block
    plaintext  = AES128_CBC_decrypt(key, iv, ciphertext)
    computed   = CRC16(plaintext[2:])           # exclude HEAD_CRC field itself
    stored     = u16_le(plaintext[0:2])
    return stored == computed
```

The first challenge: `HeadSize` lives inside the encrypted header. A
reader typically decrypts one AES block (16 bytes) — enough to cover
`HEAD_CRC (2) || HEAD_TYPE (1) || HEAD_FLAGS (2) || HEAD_SIZE (2)` —
then reads the rest based on `HEAD_SIZE`. The CRC check uses every
byte after offset +2 in the cleartext.

**Tamper locality.** A wrong password flips every plaintext byte after
the first AES block (CBC error propagation is self-correcting after one
block), so the CRC check has ~1/65536 false-positive probability per
header. Readers that want higher confidence can require two consecutive
headers to pass CRC before declaring the password correct — the usual
approach in batch-mode tools.

**Per-file encryption (`-p`, no header encryption).** When the archive
does not use `MHD_PASSWORD` but individual files are encrypted (file
header flag `LHD_PASSWORD = 0x0004`), the file header carries an
`FHD_SALT = 0x0400` flag and an 8-byte salt prefix. Password verification
works the same way but against the decrypted *file data*'s CRC (the
`FILE_CRC` field in the cleartext file header). A reader can check the
password without decompressing by reading the first AES block of the
encrypted data stream — but can only validate it after the full stream
is decrypted and CRC'd.

**Why there's no explicit check in 3.x/4.x.** The format predates the
"offer password-check without full decrypt" UX pattern. When WinRAR
needs to verify a password for a RAR 3.x archive it decrypts the first
encrypted header and tests the CRC. This is effectively the same cost
as a full password-check round in RAR 5.0 (both dominated by the
262144-iteration KDF), just without the dedicated check field.

**Encoder implications.** A writer targeting RAR 3.x/4.x header
encryption does not emit a password-check field. The first encrypted
header's `HEAD_CRC` *is* the check value; a reader validates it after
decryption. This means:

- Encoders **must** set `HEAD_CRC` correctly on every encrypted block
  (just like unencrypted blocks — the CRC rules don't change).
- There is no "fast-fail" path for a reader — the full KDF runs before
  the first CRC check.
- Password-strength advice differs from RAR 5.0: without an explicit
  check field, offline-guess attackers are bottlenecked by the KDF
  exactly as with a correct decrypt, so the effective attack cost is
  identical to RAR 5.0 at the same iteration count (262144 SHA-1 vs
  PBKDF2-HMAC-SHA256 — comparable per-guess cost, different hash).

### 6.5 Testing header encryption

Round-trip test plan:

1. Build an archive with one or more headers encrypted under a known password.
2. Feed it to the compatible RAR reader decoder and verify every header parses.
3. Flip a single ciphertext byte in one header — verify the decoder reports
   a CRC mismatch on exactly that header and continues to read others.
4. Flip the archive-wide salt in the HEAD_CRYPT block — verify the decoder
   fails at password-check time, before touching any encrypted header.
5. Build a RAR 5.0 archive with `lg2_count = 15` and verify against a
   vintage `rar` binary (`rar x` with the right password).

---

## 7. Cross-version summary

| Version | Cipher         | Key | IV | KDF iterations | Status | Encode path differs? |
|---|---|---|---|---|---|---|
| RAR 1.3 | Byte-add stream | 3 bytes | — | trivial | broken | Yes: `+=` vs `-=` |
| RAR 1.5 | XOR stream | 8 bytes | — | CRC32 | broken | No: self-inverse |
| RAR 2.0 | Feistel (proprietary) | 16 bytes | — | 256-entry shuffle + Feistel | broken | Yes: reverse round order + `UpdateKeys` input |
| RAR 3.x/4.x | AES-128-CBC | 16 bytes | 16 bytes | 262144× SHA-1 | sound | No: AES standard |
| RAR 5.0+ | AES-256-CBC | 32 bytes | 16 bytes | `1<<Lg2Cnt` × PBKDF2-HMAC-SHA256 | sound | No: AES standard |

For legacy versions (1.3–2.0), the format docs already cover the decode
path completely and the encode path is either trivially symmetric or
requires only the sign/order flips noted in §1, §3. For modern versions
(3.x onward), the encode path uses standard AES + standard KDFs — an
encoder drops in OpenSSL or libtomcrypt and wires up the correct key
sizes and PBKDF2 chain lengths.

---

## 8. WinRAR parity item: IV/salt generation policy across solid archives

The encoder must generate fresh random salt and IV for each encrypted
record. The question that is **not** answered by any of our reference
sources is whether and how the WinRAR encoder reuses or derives IVs
across files in a solid encrypted archive — specifically, does a solid
archive use one IV for the entire compressed stream, or one per file? This
is tracked as a WinRAR parity-only item in `IMPLEMENTATION_GAPS.md` because it
affects byte-identical compatibility with the official `rar` binary.

For a clean-room encoder: generate a fresh 16-byte random IV **per
encrypted region**, and a fresh 16-byte random salt **per file header**.
This is always safe regardless of the official encoder's behavior — it's
equivalent to or stronger than any solid-mode IV sharing scheme. The only
potential downside is that an archive opened with the clean-room encoder
won't be bit-identical to one produced by WinRAR from the same input, but
both archives will decrypt correctly with either decoder.

---

## 9. Test oracle

For each encryption version:

1. Encrypt a known plaintext with a known password.
2. Run a compatible RAR reader decoder against the ciphertext.
3. Assert byte-exact plaintext recovery.
4. For RAR 5.0, additionally:
   - Verify the emitted `PswCheck` XOR-fold matches the decoder's expectation.
   - Verify the `HashMAC` conversion produces the same MAC on both sides for a
     known CRC32 / BLAKE2sp test vector.
5. Cross-check against a vintage `rar` binary: compress the same input with
   the same password and compare the decrypt outputs (not the ciphertexts,
   which will differ because of random salt/IV).

A RAR 5.0 encoder that passes these PBKDF2-HMAC-SHA256 vectors has its base
KDF primitive correct:

- `("password", "salt", 1)` → `12 0f b6 cf fc f8 b3 2c 43 e7 22 52 56 c4 f8 37 a8 65 48 c9 2c cc 35 48 08 05 98 7c b7 0b e1 7b`
- `("password", "salt", 4096)` → `c5 e4 78 d5 92 88 c8 41 aa 53 0d b6 84 5c 4c 8d 96 28 93 a0 01 ce 4e 11 a4 96 38 73 aa 98 13 4a`

These are standard PBKDF2-HMAC-SHA256 test vectors — if an
implementation uses a third-party PBKDF2 it should already pass them.
