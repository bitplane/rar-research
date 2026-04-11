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
| `AUDIO.RAR` | `a -m5 -mm PCM_LR.WAV` | 32 KB synthetic 16-bit stereo PCM (sine waves L=20000·sin(i·2π/256), R=15000·sin(i·2π/384+1)) | `-mm` (multimedia) instructs the encoder to test audio mode. The encoder selects audio mode when it compresses better than LZ on the input. PCM compresses 32768 → 1938 bytes (5%) under audio mode; LZ alone would be much worse. The compressed block has the audio bit set in the table-read peek word and emits per-channel `MD[0..1]` Huffman tables (channels = 2 for L/R interleave). |
| `AUTOREJ.RAR` | `a -m5 -mm PLAIN.TXT` | "Hello text not audio.\r\n" × 100 (2300 bytes of repeating text) | Same `-mm` switch but the encoder rejects audio mode for this text input (LZ wins) and emits a plain LZ block. Useful contrast: same encoder, same flags, different per-block path. Compresses 2300 → 54 bytes via LZ. |
| `SOLID.RAR` | `a -m5 -s SOLID.RAR SOLID1.TXT SOLID2.TXT` | Two text members sharing repeated phrases | Solid Unpack20 state carry-over. The archive main header is solid and the second file is marked `LHD_SOLID`, so the decoder must preserve the dictionary and codec state between members. |
| `BIGLZ.RAR` | `a -m5 BIGLZ.RAR BIGLZ.BIN` | Deterministic 160 KB mixed text/binary stream | Longer LZ decode coverage for Unpack20 table refresh and distance/history handling without audio mode. |

## Reader test

A reader implementing `RAR15_40_FORMAT_SPECIFICATION.md` §17 should:

1. Read `AUDIO.RAR`'s file header (UnpVer 20, method 0x35 = `-m5`).
2. Begin Unpack20 decode of the data area.
3. At the first table-read, peek 16 bits and find bit 15 = 1
   (audio block).
4. Read the channels nibble from bits 12..13 (= 1, since `Channels =
   ((peek >> 12) & 3) + 1 = 2`).
5. Read 2 × 257 = 514 Huffman code lengths (one MD[] per channel).
6. Decode bytes using `MD[CurChannel]` and run `DecodeAudio` per
   §17.2; alternate channels per §17.3.
7. Verify the output matches the original 32 KB PCM.

`AUTOREJ.RAR`'s decode path goes through the regular LZ branch (bit 15 = 0
at the table-read peek), which exercises the same §16 framing without the
§17 audio path.

## Coverage gaps not generated

- **Channels = 1, 3, 4**: would need synthesizing inputs whose encoder
  picks each count (mono, 24-bit, RGBA-style). The exhaustive-search
  encoder default (`-m5 -mm`) selects 2 for our stereo PCM input. To force
  a specific channel count an encoder needs `-mc<switch>`; RAR 2.50
  doesn't expose that on the command line.
- **Audio block immediately following / followed by an LZ block in the
  same file**: would test the per-block transition logic. Needs a mixed
  input or specific encoder flag combinations not yet identified.
