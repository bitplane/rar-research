# RAR 2.50 Unpack20 Fixtures

Generated with RAR 2.50 (`research/re/rar250/bin/extracted/RAR.EXE`) under
DOSBox-X. These fixtures cover the RAR 2.x Unpack20 decoder paths that are
distinct from the older Unpack15 format.

The RAR 2.x audio mode (also called "multimedia mode" by WinRAR) is enabled
per-block via bit 15 of the table-read peek word — **not** by `UnpVer`
(`UnpVer = 26` is "files >2 GiB", not "audio"; see
`RAR15_40_FORMAT_SPECIFICATION.md` §1 and §16.3).

## Generation

```sh
python3 scripts/generate-dosbox-fixtures.py
```

The generator stages the RAR 2.50 binary into a DOSBox-X work dir and runs
contrasting archive operations for audio, plain LZ, solid state, and a larger
LZ stream.

## Files

| Archive | Switches | Source | Spec exercise |
|---|---|---|---|
| `AUDIO.RAR` | `a -m5 -mm PCM_LR.WAV` | 32 KB synthetic 16-bit stereo-like PCM (sine waves L=20000·sin(i·2π/256), R=15000·sin(i·2π/384+1)) | `-mm` (multimedia) instructs the encoder to test audio mode, but this fixture's first table-read peek word is `0x0040`, so bit 15 is clear and the member decodes through normal Unpack20 LZ. Keep it as a multimedia-switch contrast fixture, not as proof of the audio predictor path. |
| `AUTOREJ.RAR` | `a -m5 -mm PLAIN.TXT` | "Hello text not audio.\r\n" × 100 (2300 bytes of repeating text) | Same `-mm` switch but the encoder rejects audio mode for this text input (LZ wins) and emits a plain LZ block. Useful contrast: same encoder, same flags, different per-block path. Compresses 2300 → 54 bytes via LZ. |
| `SOLID.RAR` | `a -m5 -s SOLID.RAR SOLID1.TXT SOLID2.TXT` | Two text members sharing repeated phrases | Solid Unpack20 state carry-over. The archive main header is solid and the second file is marked `LHD_SOLID`, so the decoder must preserve the dictionary and codec state between members. |
| `BIGLZ.RAR` | `a -m5 BIGLZ.RAR BIGLZ.BIN` | Deterministic 160 KB mixed text/binary stream | Longer LZ decode coverage for Unpack20 table refresh and distance/history handling without audio mode. |

## Reader test

A reader implementing `RAR15_40_FORMAT_SPECIFICATION.md` §17 should:

1. Read `AUDIO.RAR`'s file header (UnpVer 20, method 0x35 = `-m5`).
2. Begin Unpack20 decode of the data area.
3. At the first table-read, peek 16 bits and find `0x0040`. Bit 15 is clear,
   so this fixture is an LZ block even though it was generated with `-mm`.
4. Verify the output matches the original 32 KB PCM-like payload.

`AUTOREJ.RAR`'s decode path goes through the regular LZ branch (bit 15 = 0
at the table-read peek), which exercises the same §16 framing without the
§17 audio path.

The code test suite also promotes the external `unpack20_audio_text.rar`
sample from the junrar corpus. Despite the WAV-named first member, its first
table-read peek is `0x2221`, so bit 15 is also clear there.

## Coverage gaps not generated

- **Vintage-encoder true audio blocks, Channels = 1..4**: would need
  synthesizing inputs whose encoder actually sets bit 15 in the table-read
  peek word. Local probes with mono, 3-channel, and 4-channel WAV-shaped data
  plus `-mm`/`-mmf` still selected normal LZ blocks. `rars` has synthetic
  one-channel audio coverage at codec level and synthetic in-memory RAR 2.0
  archive coverage for channel counts 1, 2, 3, and 4, but these are not RAR
  2.50-authored fixtures.
  `scripts/find-rar20-audio-candidates.py` scanned the local external corpus,
  spec fixtures, promoted crate fixtures, and old numbered volumes (517
  archive/volume files total, excluding hidden scratch directories) and did not
  find a clean standalone vintage audio block. Some members have bit 15 set in
  the first two raw data bytes,
  but those are not proof of audio mode unless they are at a fresh table-read
  boundary after any required decryption and split/solid continuation state.
  For example, `SOLID.RAR` member 2 starts with raw peek `0xdfbe`; it is a
  solid continuation (`LHD_SOLID`) and decodes correctly as normal Unpack20
  state carry-over, not as a new audio block.
  Re-run the scan with:

  ```sh
  ./scripts/find-rar20-audio-candidates.py \
    /home/gaz/src/tmp/rar-test-data \
    fixtures \
    /home/gaz/src/tmp/rars/crates/rars-format/tests/fixtures
  ```

  To force a specific vintage channel count an encoder may need a lower-level
  switch or direct bitstream construction not yet identified.
- **Audio block immediately following / followed by an LZ block in the
  same file**: would test the per-block transition logic. Needs a mixed
  input or specific encoder flag combinations not yet identified.
