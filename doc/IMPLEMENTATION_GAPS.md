# RAR Implementation Gaps

Open items for a full RAR compressor + decompressor across all versions
(1.3, 1.5–2.0, 2.9/3.x/4.x, 5.0/7.0).

This file lists **what's still open**, not what's closed. Completed
research and fixture work is intentionally **not** repeated here — the
spec docs in `doc/` are the authority and `git log` is the audit
trail. Each item below points at the spec section that already covers
the closed material plus a concrete hint for the next research step.

This file is structured by **what would unblock each item**, not by
codec/format. Items in the same bucket need the same kind of work.

## Source references

External material lives under `_refs/` (gitignored):

- `_refs/7zip/` — 7-Zip 25.01 (independent decoders + PPMd/Huffman/LzFind
  encoders). Authoritative for second-opinion checks and for encoder-side
  algorithms not exposed by historical RAR binaries.
- `_refs/XADMaster/` — The Unarchiver (Obj-C RAR 1.3-4.x handlers). Useful
  alternate reference, especially for RAR 1.3 (Unpack13).
- `_refs/rarbins/` — old RAR/WinRAR binaries for fixture generation and
  reverse-engineering. Public backup:
  https://archive.org/details/old_winrar_binaries
- `_refs/wineprefixes/` — wine prefixes installed from the binaries above
  (`winrar300/`, `winrar420/`, `winrar602/`, `winrar721/`).

Reverse-engineering work-in-progress lives under `research/re/` (also
gitignored) — one directory per binary, each with its own notes.md
that records the symbolic anchors, the call-tree inventory, and any
attempted-but-blocked verification paths. When closing items, prefer
independent cross-checks: committed fixtures, instrumented captures,
historical binaries, and second-opinion public readers. Cite the
concrete fixture, script, capture log, or binary-analysis note used
for the doc edit.

## Status

Baseline reader/writer specs are wire-format-complete: a clean-room
implementation following the `doc/` set can produce and consume valid
archives across every supported version. The items below are either
non-blocking parity refinements, fixtures we can't generate from current
tooling, or specific records that need either binary analysis or fresh
external material.

## Fixture coverage

Per-version inventory (last updated 2026-05-02):

| Dir | Encoder | Coverage |
|---|---|---|
| `fixtures/1.402/` | RAR 1.402 (DOSBox-X) | Original 3 fixtures (stored / compressed / encrypted README) plus 10 edge cases: empty file, multi-file, stored encrypted file, >64 KiB window-wrap, solid, multi-volume (4 parts, old `.RAR`/`.R0N` naming), directory entry, SFX with `RSFX` marker at offset 28, archive comment, repeating-pattern `Buf60` toggle candidate. |
| `fixtures/1.54/` | WinRAR 1.54 (DOSBox) | Single-file compressed, CRYPT_RAR15 encrypted compressed, solid, multi-file, multi-volume, SFX, plus audio-shaped WAV payloads with Windows long names and DOS 8.3 names. |
| `fixtures/2.02/` | External `rarfile` corpus | Old-format RAR 2.x main-header comment extension boundary plus RAR 2.0 encrypted compressed members using `CRYPT_RAR20`. |
| `fixtures/2.50/` | RAR 2.50 (DOSBox-X) | Unpack20 LZ coverage from `-mm` multimedia-switch input, external audio-shaped inputs that still select LZ, explicit LZ contrast, solid carry-over, and larger LZ streams. No committed vintage-encoder archive fixture currently proves a true audio block; `AUDIO.RAR` starts with table-read peek `0x0040`, and `unpack20_audio_text.rar` starts with `0x2221`, so bit 15 is clear in both. `rars` has synthetic one-channel audio coverage at codec level and synthetic in-memory RAR 2.0 archive coverage for channel counts 1, 2, 3, and 4. |
| `fixtures/1.5-4.x/rar300/` | WinRAR 3.00 (wine) | Per-file `-p` encryption, header `-hp`, comment, recovery record (`HEAD3_NEWSUB "RR"`), multi-volume both old (`.r00`) and new (`.partNN.rar`) naming, solid. |
| `fixtures/1.5-4.x/rar420/` | WinRAR 4.20 (wine) | EXT_TIME nibble groups, `-hp` cross-version. |
| `fixtures/1.5-4.x/third_party/` | External corpora | Focused edge-case oracles with documented provenance. Includes the libarchive mixed encrypted RAR4 fixture, where only member `b.txt` is a positive oracle because historical RAR 3.93 validates it while rejecting later member `d.txt`; junrar and SharpCompress RAR4 encrypted/header-encrypted password fixtures; and the node-unrar-js mixed visible-name fixture used only for metadata, stored-member, and negative password behaviour because the encrypted-member passwords are unknown after local and upstream fixture-source audits. |
| `fixtures/1.5-4.x/` | RAR 2.50 (DOSBox-X) | Two `PROTECT_HEAD` recovery-record fixtures (`rar250_protect_head_rr1.rar`, `…_rr5.rar`) — pin the per-sector tag formula and interleaved-XOR parity in `INTEGRITY_WRITE_SIDE.md §3.4`. |
| `fixtures/1.5-4.x/wrar290/` | WinRAR 2.90 (wine, registration-patched) | One `HEAD3_SIGN` shape fixture (`wrar290_head3_sign_patched.rar`) — pins the §10.9.1 block layout end-to-end. Signature is degenerate (BSS-zero registration buffer); see fixture README. |
| `fixtures/1.402/rar140_av/` | RAR 1.40 DOS (DOSBox-X, registration-patched) | Paired AV shape fixtures (`rar140_noav_baseline.rar`, `rar140_av_patched.rar`) — pin the RAR 1.4 main-header AV-payload layout in `RAR13_FORMAT_SPECIFICATION.md §4.1`. Cipher output is degenerate (BSS-zero registration); see fixture README. |
| `fixtures/rarvm/` | RAR 3.00, 3.93, 4.20 (wine) | All six standard RARVM filter bytecode blobs (E8, E8E9, ITANIUM, DELTA, RGB, AUDIO) captured across 3 encoder versions; bytecode CRC32 fingerprints in `captured-blobs.md`. |
| `fixtures/ppmd/` | RAR 3.00 (wine) | Unpack29 PPMd-mode (method `m5b`, UnpVer 29) on 127 KB lorem-ipsum input. |
| `fixtures/5.0/` | WinRAR 6.02 (wine) | 22 fixtures: codec basics (m0/m1/m3/m5, BLAKE2sp vs CRC32), dictionary sizes, per-file AES-256 + BLAKE2sp HashMAC, header-encrypted, all four service headers (CMT/RR/QO + Locator-extra archive), multi-file, solid, multi-volume + recovery volumes (`.partN.rev` with REV5_SIGN), all four RAR 5.0 filter triggers, empty file. |
| `fixtures/5.0/rr_inline/` | WinRAR 7.21 (wine) | Six inline RR fixtures across two axes (rec_pct 5/10/20/50 at fixed input size; rec_pct 10 at three input sizes). Pin the `(NR, shard_size)` formula in `INTEGRITY_WRITE_SIDE.md §4.6.2`. |
| `fixtures/7.0/` | WinRAR 7.21 beta 1 (wine) | One fixture: `-ams` archive-name-save extra record (new in RAR 7). Default 7.21 output is bit-identical to 6.02. |
| `fixtures/negative/` | derived | 12 corruption variants (trunc-1, trunc-half, bit-flip) across 4 source archives spanning RAR 1.5 / 3.x. |

Fixture-coverage gaps that aren't blocking the spec text live under "Open
items" below.

---

## Open items by unblocking constraint

### A. Needs binary analysis of WinRAR (Ghidra-style RE)

Same toolchain as the `research/re/` directories already in use:
unpack the binary, import to Ghidra (or continue an existing
project), symbol-rename, dynamic capture via DOSBox-X / wine.

- **RAR 5.0 inline-RR encoder-internal running-state fields.**
  Wire layout, formula, CRC, and the verifiable structured-header
  fields are all in `INTEGRITY_WRITE_SIDE.md §4.6.1.1`. Three
  fields remain encoder-internal: `chunk_data_extent` at chunk
  `+0x1e`, `data_shard_state[]` at `+0x40..+(0x40+D*8-1)`, and
  `final_state` at `+(0x40+D*8)..+(0x47+D*8)`. A clean-room reader
  doesn't need them (skips via `header_size`); a clean-room
  byte-identical encoder does. **Hint:** these are RS encoder
  running-state values; closing them most cheaply falls out of
  writing the RS encoder side-by-side with WinRAR's. Anchors are
  named in `research/re/winrar602/symbols.tsv`.

- **RAR 2.x AV body codec (modern format byte `'4'`) — AV framing stage.**
  The decryption stage (§10.8 block cipher) is fully ported and
  empirically validated against ground truth captured under
  DOSBox-X: clean-room Python output matches the binary's runtime
  decryption byte-for-byte (80/80 bytes) for the `rar250_sfx.exe`
  fixture (`research/re/rar250/scripts/decode_av_legacy.py`,
  `patch_dump_full.py`, `patch_dump_init_keys.py`). The generic
  Unpack20 LZ path now exists in `rars-codec`, so the remaining
  blocker is narrower: map the AV-specific body framing/table bytes
  into the Unpack20 reader state so the decrypted 80-byte body
  expands to the captured 305-byte `AVDUMP.BIN`. Earlier notes in
  `research/re/rar250/notes.md` show that feeding `AV_WIRE_BODY.bin`
  directly into standalone experiments does not reproduce the binary
  output, which implies an additional AV wrapper transform or table
  initialization detail still needs to be pinned.

- **RAR 2.x AV legacy format byte `'0'` — wild / real-registered
  fixture.** The cipher cluster (byte-stream and 16-byte-block
  branches, `"awbw"` seed, eight round keys, S-box build, CRC32
  integrity field) is fully reverse-engineered in
  `RAR15_40_FORMAT_SPECIFICATION.md §10.8`, and the **shape**
  of the carrier in RAR 1.4 archives is now confirmed via
  `fixtures/1.402/rar140_av/` (paired baseline + patched
  fixtures from the registration-patched RAR 1.40 encoder under
  DOSBox-X). Carrier layout — AV embedded in the main header
  with `MHD_AV = 0x20` flag, length-prefixed body, fixed 6-byte
  magic `1a 69 6d 02 da ae` — is documented in
  `RAR13_FORMAT_SPECIFICATION.md §4.1`. **Still open:** a
  byte-identical fixture from a real registered build would let
  us verify the cipher output against a known-valid signature;
  needs either a wild signed archive or registration-key material
  + a name-injection extension to the patch.

- **`HEAD3_SIGN` (block type `0x79`) — verifier-side public key.**
  The block's wire layout, the GF(2^15) arithmetic frame
  (primitive polynomial `0x8003`, log/exp tables), the embedded
  constants, and an empirical-confirmation fixture
  (`fixtures/1.5-4.x/wrar290/wrar290_head3_sign_patched.rar`) are
  all covered in `RAR15_40_FORMAT_SPECIFICATION.md §10.9.1` /
  §10.9.1.1. The bit-level signature arithmetic is now traced
  in §10.9.2: a Schnorr-like signature over a binary elliptic
  curve, GF(2^15)^16 coordinate field, integer mod
  `DAT_004304b0` for the (r, s) components. What's still open is
  obtaining the **verifier-side public key** — the embedded
  WinRAR public key derived from the same registration-key
  material. Without it, readers can validate block layout
  (offsets, padding sum 0xa7, length prefixes) but not the
  signature itself, exactly as for any genuine asymmetric scheme.
  Closing this needs either (a) a registered WinRAR build, or
  (b) cross-correlating the `HASH1` outputs of two real signed
  archives (with known archive names) to extract the public key
  via repeated EC group operations.

### B. Needs a real Windows host (wine-on-Linux distorts the encoding)

- **Symlink / hardlink / junction / file-copy redirection records**
  (RAR 5.0, FHEXTRA_REDIR types 1..5): wine's Win32 symlink emulation is
  uneven, so a wine-generated fixture captures wine quirks rather than
  WinRAR's intended encoding.
- **High-precision File Time variants** (Unix time + nanos, separate
  ctime/atime combinations): wine's filesystem layer loses sub-second
  precision before `Rar.exe` sees the source mtime. Needs a Linux host
  with nanosecond timestamps and direct `utimensat()`.
- **Additional `FHD_UNICODE` Form 1 encoder cases** (RAR 1.5–4.x compact
  Unicode filename encoding): third-party fixtures now cover read-side CJK
  compact-name decoding. Wine still makes it hard to generate controlled
  WinRAR-authored Form 1 names for write-side cross-checks because it hands
  cp437-encoded `argv` to `Rar.exe`, dropping CJK and mapping Latin-1 to cp437.
- **MOTW propagation** (`-om` switch, RAR 7): needs an NTFS
  `Zone.Identifier` ADS attached to the source file before archiving.

### C. Needs ≥4 GiB inputs (disk-space-bound)

- **Unpack70 codec engagement** (`DCX = 80` extended-distance alphabet,
  `Algorithm version` bit set in CompInfo): only triggered when the
  encoder selects a >4 GiB dictionary, which only happens with >4 GiB
  input. WinRAR 7.21 is installed; the constraint is purely fixture-size.
- **RAR 1.5–4.x `FHD_LARGE`** (>4 GiB single-file size pair, `HIGH_PACK_SIZE`
  / `HIGH_UNP_SIZE` uint32 pair active): same reason.

### D. Encoder-internal / not deterministically input-driven

These need either custom encoder switches that don't exist or a tailored
input that happens to trigger an internal threshold.

- **RAR 1.402 StMode**: requires a specific Huffman-decode burst pattern
  (`NumHuf >= 16` per `unpack15.cpp:373`).
- **Unpack20 audio with `Channels = 1..4`**: no committed vintage-encoder
  archive fixture currently proves a true audio block. `rars` has synthetic
  one-channel audio coverage at codec level and synthetic in-memory RAR 2.0
  archive coverage for channel counts 1, 2, 3, and 4, but RAR 2.50 `-mm` and
  `-mmf` local probes, including mono/3-channel/4-channel WAV-shaped inputs,
  selected normal LZ blocks.
  `scripts/find-rar20-audio-candidates.py` scanned the local external
  corpus, spec fixtures, promoted crate fixtures, and old numbered volumes
  (517 archive/volume files total, excluding hidden scratch directories) and
  found no clean standalone vintage audio-block fixture. All 36 raw bit-15
  candidates were encrypted members, split continuations, solid continuation
  members, or stored/raw data false positives.
  `fixtures/2.50/SOLID.RAR` deliberately pins one such trap: the
  second member's raw data-start peek is `0xdfbe`, but it is an `LHD_SOLID`
  continuation and not a fresh table-read boundary.
  Forcing vintage channel-count fixtures needs either a custom encoder or an
  input whose autocorrelation reliably prefers the audio predictor.
- **`-me<par>` RAR 7 explicit encryption parameters**: defaults match
  6.02; would need to identify which parameter combinations actually shift
  the on-disk CompInfo bytes.

### E. Gated on broader rars encoder coverage

- **Round-trip oracle** (write → read identity, every fixture): the
  only test that proves an encoder + decoder pair self-consistent. This has
  started for narrow RAR 1.5 store-only, compressed, solid-compressed,
  old-numbered multivolume, old-style archive/file comments, and per-file
  encrypted paths including encrypted split volumes; it still needs
  public-reader oracle fixtures for generated output and later RAR families
  before it can act as a broad format oracle.

---

## WinRAR-parity refinements (never fully closeable from public source)

These affect compression ratio, archive size, listing speed, or
byte-identical matching with official WinRAR output. They do **not** block
valid archive creation. WinRAR's encoder is closed-source; the items
below are documented as clean-room defaults in the corresponding spec
files, with WinRAR's exact choice noted as "implementation-defined."

- **Filter detection heuristics** — when WinRAR applies each RAR 3.x VM-era
  filter and each RAR 5.0 fixed-enum filter. Transform algorithms and
  RARVM blobs are documented; only the selection policy is closed-source.
- **Compressor parameter tables for `-m0..-m5`** — match-finder cut
  values, lazy/optimal parsing choices, block-size choices, LZ-vs-PPMd
  selection tables. `LZ_MATCH_FINDING.md` documents clean-room defaults.
- **Solid-mode reset thresholds** — whether WinRAR forces dictionary /
  model resets inside a solid group, and the thresholds it uses if so.
  Codec state-carry rules are documented in
  `ARCHIVE_LEVEL_WRITE_SIDE.md §1.1`.
- **Solid-mode file emit order within a group** (alphabetical / by
  extension / by mtime). The clean-room default is encoder choice; a
  stable default that affects compression ratio should be chosen and
  documented in `ARCHIVE_LEVEL_WRITE_SIDE.md`.
- **IV/salt generation policy across solid archives** — whether WinRAR
  reuses or regenerates IV/salt per solid group or per file.
  `ENCRYPTION_WRITE_SIDE.md §7` recommends fresh-per-record for
  clean-room output.
- **RAR 2.x multimedia audio channel-count selection** — how WinRAR picks
  audio blocks at all, then `Channels = 1..4`, for a multimedia block. Decoder
  behaviour and the safe exhaustive-search encoder default are documented in
  `RAR15_40_FORMAT_SPECIFICATION.md §16.11.6`.
- **Quick Open writer threshold policy** — when WinRAR decides a RAR 5.0
  Quick Open cache is worth emitting. The cache format is documented in
  `ARCHIVE_LEVEL_WRITE_SIDE.md §4`; §4.4 gives a clean-room heuristic.
