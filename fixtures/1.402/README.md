# RAR 1.402 Fixtures

Generated with RAR 1.402 (1994-03-20 Eugene Roshal beta) under DOSBox-X.

These archives use the pre-RAR-1.5 `RE~^` container with `UnpVer = 13`
(Unpack15 codec). The first three are the original committed fixtures
(single `README` payload across stored / compressed / encrypted variants);
the rest were added 2026-04-27 to cover edge cases identified by the
clean-room codec audit.

## Generation

```sh
python3 scripts/generate-dosbox-fixtures.py
```

The generator extracts `RAR.EXE` from `_refs/rarbins/RAR1_402.EXE` (an SFX
wrapper around the binary) using `wine winrar 6.02`, then runs everything
through DOSBox-X with `-time-limit` for clean termination. The extracted
binary lives in `.rar1402-bin/` (gitignored).

## Original fixtures

Container metadata (shared across all three):

- Signature: `RE~^`
- Main header size: 7 bytes
- Main flags: `0x80` observed from RAR 1.402 output
- One file entry: `README`
- Unpacked size: 2016 bytes
- File checksum field: `0xe079` (RAR 1.3 rolling sum+rotate checksum)
- File header size: 27 bytes
- File attributes: `0x20`
- `UnpVer`: `2`

| Archive | Packed size | File flags | Method | Notes |
|---------|-------------|------------|--------|-------|
| `README_store.rar` | 2016 | `0x00` | `0` | Stored file, no compression |
| `README.RAR` | 1078 | `0x00` | `3` | Normal compression |
| `README_password=password.rar` | 1078 | `0x04` | `3` | Same compressed entry encrypted with password `password` |

`expected/README` is the extracted payload copied from the stored archive
data. Use it as the byte-for-byte comparison target when decoding the
compressed and password-protected variants.

## Edge-case fixtures (2026-04-27)

| Archive | Switches | Spec exercise |
|---|---|---|
| `EMPTY.RAR` | `a -m0` (empty file) | `DataSize == 0` path; `FileHead.UnpSize == 0`. |
| `MULTIFIL.RAR` | `a` (HELLO + TINY) | Two file headers in sequence; reader's `SeekToNext` + per-block `HeadSize` walk. |
| `BIG80K.RAR` | `a -m3` (80 KB lorem-ipsum) | Input exceeds the 64 KiB Unpack15 sliding window → decoder must wrap `UnpPtr & 0xFFFF` mid-stream (`RAR13_FORMAT_SPECIFICATION.md` §6.10). |
| `REPEATB.RAR` | `a -m3` (256-byte cycling pattern × 32) | High-density short matches → likely exercises the `Buf60` toggle path (rep-match length 10 with `DecodeNum == 0xFF`; `RAR13` §6.13). |
| `SOLID.RAR` | `a -s -m3` (3 inputs) | Solid mode: `MainHead` carries `MHD_SOLID (0x0008)`; per-file LZ state carries across boundaries. |
| `MULTIVOL.RAR` + `.R00`/`.R01`/`.R02` | `a -v20K -m0 RANDOM.BIN` (64 KB random) | 4-volume archive (vol 1 = `.RAR`, vols 2–4 = `.R00`/`.R01`/`.R02` per RAR 1.x old-style naming). `MainHead` carries `MHD_VOLUME`; payload split mid-stream across volumes. |
| `CMULTIV.RAR` + `.R00`..`.R06` | `a -v2K -m3 CMULTI.TXT` (96 KB generated text) | 8-volume archive with one compressed Unpack15 bitstream split across old-style volumes. `expected/CMULTI.TXT` is the byte-for-byte extraction target; the final file checksum is `0x87cd`. |
| `WITHDIR.RAR` | `a -r -m3 SUBDIR` | Directory entry: `FileAttr & 0x10` set; the inner file `INNER.TXT` follows. |
| `STOREPWD.RAR` | `a -m0 -ppassword SECRET.TXT` | Stored encrypted file. `LHD_PASSWORD` is set, method is store (`0`), password is `password`, plaintext is `Stored encrypted fixture.\r\n`. |
| `SFXSRC.EXE` | `a SFXSRC.RAR` then `s SFXSRC.RAR` | Self-extracting archive. `RSFX` marker at absolute offset 28 (verified at gen time: bytes `5253 4658`). Required by readers per `READ_SIDE_OVERVIEW.md` §2.2 to distinguish a real RAR 1.4 SFX from arbitrary executables containing the short signature. |
| `COMMENT.RAR` | `a -m0`, then `c COMMENT.RAR =COMMENT.TXT` | Archive-level comment. `MainHead` carries `MHD_COMMENT (0x02)` and `MHD_PACK_COMMENT (0x10)`; `HeadSize == 43`, so 36 bytes of comment metadata/data are embedded in the main-header extension before the first file header. Decodes to `This is the archive comment.\r\n`. |
| `FCOMM.RAR` | `a -m0`, then interactive `cf FCOMM.RAR HELLO.TXT` with DOSBox-X `autotype ... F C O M enter f10` | File-level comment. File header carries `LHD_COMMENT (0x08)`; `HeadSize == 38`, so 8 bytes of comment metadata/data follow the 9-byte filename before file data. Decodes to `FCOM\r\n`. |

## Coverage gaps not generated

- **StMode trigger**: requires a specific Huffman-decode burst pattern
  (`NumHuf >= 16` per `unpack15.cpp:373`); not deterministically producible
  from input data alone.
## SHA-256

Current committed fixture hashes:

```text
e5692692645c18be15326273997fbee0fb95cccaf13a93f7557bb8469d44c23a  README.RAR
126fba938887b0ce9439aeef41d78ecf10089b6434596ac9f4e02a5c1e32306c  README_store.rar
0ba5ffc4db66a739a91dd8423edf488cb9ae936442a036eae6d83e93f0db403d  README_password=password.rar
e70e00c521ee53176d194cfc66d2c284e340d50c07667776071b220ed956570e  expected/README
69bfe4a0df27f95deaffd6ea937427d00b43d3257b3204a839f3996b28a8fc0b  COMMENT.RAR
db470414d97b8d7a3213eaf5957b139e01621012ede09eda1565342709aa1191  FCOMM.RAR
a53d5c28cd5b18a3bb438ea5895c663c04336ade7ea1d0e6dff7f018af8026cd  STOREPWD.RAR
4703cc0b4babed7c37779b1bdd3369d6827288a79ef7aadb9f4e77ccb9944456  CMULTIV.RAR
b765e2569137222dfe4d4ba8211864d1d22f09b0d2e1285c5861044ddcba55fb  CMULTIV.R00
5ee03f70306aef63815a8f1de3fbe840157aca569d3b738b45e6e63eae956dd6  CMULTIV.R01
8eb91047a7bdd63bca86aca7200eb8f8aa72ac3a2ded4bf0c501b24783846d42  CMULTIV.R02
d4fb136515a3b92ff9d37cd192212c11bb8f26d5c4c1fe8a997d149825e75830  CMULTIV.R03
b9c6e6f5c13e7af40e2ac38ba92ab0a2deaec01767f397862dcfe0f952b77308  CMULTIV.R04
4783a08c6613cebdd19373637c0a73fae0f38a6aeb75f23c48377cc62328aab2  CMULTIV.R05
1eebd7cf8f732bfd84569fe029b001b37212c800cd79640620a859e693917f52  CMULTIV.R06
fadffe4534bea5bb600c9ea799d88b0123409db4be2e4171c66c0b697eab6007  expected/CMULTI.TXT
```

Some edge-case fixtures hash differently if regenerated because RAR.EXE
1.402 stamps the file mtime field at archive time. The checked-in hashes
above are the authoritative values for the committed files; sizes are stable
per the table above.
