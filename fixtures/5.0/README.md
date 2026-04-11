# RAR 5.0 Fixtures

Generated with WinRAR 6.02 (`Rar.exe`, June 2021) under wine 10.0 from the
prefix at `_refs/wineprefixes/winrar602/`. WinRAR 6.x emits the RAR 5.0 wire
format (`Rar!\x1a\x07\x01`), not RAR 7.0 — that's a separate codec gap (see
`doc/IMPLEMENTATION_GAPS.md` "RAR 7.0 / Unpack70").

Reproducible: run `python3 scripts/generate-rar5-fixtures.py` from the repo
root. Source files in `sources/` are deterministic (constants or seed-N PRNG
output); each fixture is one `Rar.exe a` invocation with the switches listed
below. `expected/` holds the verbatim `Rar.exe lta` output for each fixture
plus a `MANIFEST.tsv` with sizes and descriptions.

## Coverage by category

### Codec basics

| Fixture | Switches | Notes |
|---------|----------|-------|
| `stored.rar` | `-m0 -ma5` | Stored only, default CRC32 hash. |
| `stored_blake2.rar` | `-m0 -ma5 -htb` | Stored only, BLAKE2sp hash (File Hash extra record type 0x02, hash type 0x00). |
| `m1_fastest.rar` | `-m1 -ma5 -htb` | Fastest compression on 64 KB lorem-ipsum text. |
| `m3_default.rar` | `-m3 -ma5 -htb` | Default compression. |
| `m5_max.rar` | `-m5 -ma5 -htb` | Maximum compression. |

### Dictionary size (CompInfo bitfield variation)

| Fixture | Switches | Notes |
|---------|----------|-------|
| `dict_128k.rar` | `-md128k` on 512 KB input | CompInfo encodes the explicit small dict. |
| `dict_1m.rar` | `-md1m` on 512 KB input | Larger requested dict than input → RAR clamps to input size. Anything `-md4m`+ on this input also clamps to 1M and produces an identical `dict_*.rar` modulo timestamp; not committed. |

### Encryption

| Fixture | Switches | Password | Notes |
|---------|----------|----------|-------|
| `password_aes.rar` | `-p`, `-htb` | `password` | AES-256-CBC + PBKDF2-HMAC-SHA-256, 32K iterations. BLAKE2sp digest is HashMAC-converted (see `ENCRYPTION_WRITE_SIDE.md` §5.3). |
| `password_crc32.rar` | `-p`, `-htc` | `password` | Same but with CRC32 hash (also MAC-converted). |
| `header_encrypted.rar` | `-hp` | `password` | Whole-archive header encryption. Listing without password fails with "Program aborted"; this is correct behavior for `HEAD_CRYPT`-protected archives. |

### Service headers

| Fixture | Switches | Service headers present |
|---------|----------|------------------------|
| `with_comment.rar` | `-zcomment.txt` | `CMT` |
| `with_recovery.rar` | `-rr10` | `RR` (10% Reed–Solomon parity, RSCoder16) |
| `with_quickopen.rar` | `-qo+` | `QO` |
| `with_all_services.rar` | `-zcomment.txt -rr5 -qo+` | `CMT`, `QO`, `RR` together — also exercises the `MHEXTRA_LOCATOR` extra record in the main header. |

### Multi-file / solid / multi-volume

| Fixture | Switches | Notes |
|---------|----------|-------|
| `multifile.rar` | (3 inputs, non-solid) | `hello.txt` + `tiny.txt` + `random_4k.bin`. |
| `solid.rar` | `-s` | Two files in one solid group. |
| `multivol.part1.rar` + `.part2.rar` + `.part3.rar` | `-v2k` | 4 KB input split across 3 volumes (~2 KB each). Exercises `LHD_SPLIT_BEFORE` / `LHD_SPLIT_AFTER` flags and end-of-archive marker `EARC_NEXTVOLUME` bit. |
| `multivol_rev.part{1..5}.rar` + `multivol_rev.part{1,2}.rev` | `-v4k -rv2 -m0` | 16 KB input split across 5 data volumes + 2 recovery volumes. The `.rev` files use `REV5_SIGN = "Rar!\x1aRev"` (`5261 7221 1a52 6576`) and follow the layout in `INTEGRITY_WRITE_SIDE.md` §4.7. |

### Filter triggers (RAR 5.0 hardcoded enum)

`-m5` is required to engage filter detection. The encoder auto-detects filter
applicability based on input contents.

| Fixture | Source | Filter type expected |
|---------|--------|---------------------|
| `filter_arm.rar` | `arm_synthetic.bin` (4 KB of synthetic `cond=AL` BL instructions) | ARM (type 3) |
| `filter_e8.rar` | `x86_e8_stream.bin` (reused from `fixtures/rarvm/sources/`) | E8 (type 1) |
| `filter_e8e9.rar` | `x86_e8e9_stream.bin` | E8E9 (type 2) |
| `filter_delta.rar` | `delta_4ch_ramp.bin` | DELTA (type 0) |

To verify a fixture actually triggered the intended filter, examine the
unpacked block stream — RAR 6.02's `lta` output doesn't surface filter
selection. An instrumented decoder is needed for confirmation; until one
exists, treat these as "filter trigger candidates" rather than confirmed
captures.

### Edge cases

| Fixture | Notes |
|---------|-------|
| `empty_file.rar` | Single zero-byte file. Exercises the `DataSize == 0` path. |

## Coverage gaps (not generated)

- **Symlinks / hardlinks / file-copies / junctions** (`-ol`, `-oh`, `-oi`):
  wine on Linux has uneven Win32 symlink support and the resulting fixture
  tends to capture wine's quirks rather than WinRAR's intended encoding. Skip
  until generated on a real Windows host or the encoder is rewritten with
  explicit FHEXTRA_REDIR records (see `RAR5_FORMAT_SPECIFICATION.md` §8 and
  `ARCHIVE_LEVEL_WRITE_SIDE.md` §5.1–§5.2).
- **High-precision File Time variants** (Unix-time + nanos, separate ctime /
  atime in different combinations): `Rar.exe -ts` controls which time fields
  are stored. The wine filesystem doesn't preserve nanosecond timestamps so
  the resulting archive lacks the `0x10` flag exercise. Need a Linux-host
  filesystem with nanosecond support and direct `utimensat()`-set timestamps
  before archive generation.
- **>4 GiB dictionary** (Unpack70 `DCX = 80` distance alphabet): requires (a)
  WinRAR 7.x for the Unpack70 encoder, and (b) ≥4 GiB of input data so the
  encoder doesn't clamp the dict. Both unavailable.
- ~~**Recovery volumes (`.rev` files)**~~ — covered by `multivol_rev.part*.rev`.

## Verification

`expected/MANIFEST.tsv` lists every committed `.rar` file with its size and
description. `expected/<fixture>.lta.txt` is the verbatim `Rar.exe lta` output
captured at generation time; a reader implementation can match its own
listing against this for byte-for-byte parity (modulo the WinRAR banner and
the date-formatted mtime line, which depends on the host's locale).
