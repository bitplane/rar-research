# RARVM Filter Capture Fixtures

Generated with RAR 3.00, RAR 3.93, and RAR 4.20 command-line `Rar.exe` under
Wine.

Generator command pattern:

```text
rar a -m5 -s- -mt1 -ep -cfg- -idcdp <archive> <source>
```

RAR 3.00 predates `-mt1` and uses only `-idp`, so its command pattern is:

```text
rar a -m5 -s- -ep -cfg- -idp <archive> <source>
```

The archives are single-file, non-solid RAR 2.9/3.x streams intended for
capturing RARVM standard-filter bytecode definitions. They are not the final
blob constants; use an instrumented RAR 3.x decoder to log VM program bytes
from these archives, then verify the captured length and CRC32 against the
standard-filter fingerprint table.

Use `archives/` as the primary RAR 3.93 capture set. Use `archives-rar300/` as
the earliest RARVM-era cross-check set, and `archives-rar420/` as the later RAR
4.x cross-check set.

| Archive | Source | RAR technical method | Purpose |
|---------|--------|----------------------|---------|
| `archives/x86_e8_rar393.rar` | `sources/x86_e8_stream.bin` | `m5c`, version 2.9 | Dense x86 CALL-only stream; observed to trigger DELTA, not E8 |
| `archives/x86_e8e9_rar393.rar` | `sources/x86_e8e9_stream.bin` | `m5c`, version 2.9 | Dense x86 CALL/JMP stream; observed to trigger DELTA, not E8E9 |
| `archives/delta_4ch_rar393.rar` | `sources/delta_4ch_ramp.bin` | `m5d`, version 2.9 | Four-channel byte ramp for DELTA capture |
| `archives/rgb_gradient_rar393.rar` | `sources/rgb_gradient_24bit.bmp` | `m5c`, version 2.9 | 24-bit BMP gradient for RGB/delta-family capture |
| `archives/audio_stereo_rar393.rar` | `sources/audio_stereo_pcm.wav` | `m5e`, version 2.9 | Stereo 16-bit PCM WAV for AUDIO capture |
| `archives/itanium_synthetic_rar393.rar` | `sources/itanium_synthetic_bundles.bin` | `m5f`, version 2.9 | Synthetic IA-64-like bundle stream; auto mode triggers DELTA, forced mode captures ITANIUM |

RAR 3.00 generated the same source set with the same method labels:

| Archive | RAR technical method | Packed size |
|---------|----------------------|------------:|
| `archives-rar300/x86_e8_rar300.rar` | `m5c`, version 2.9 | 467 |
| `archives-rar300/x86_e8e9_rar300.rar` | `m5c`, version 2.9 | 506 |
| `archives-rar300/delta_4ch_rar300.rar` | `m5d`, version 2.9 | 718 |
| `archives-rar300/rgb_gradient_rar300.rar` | `m5c`, version 2.9 | 815 |
| `archives-rar300/audio_stereo_rar300.rar` | `m5e`, version 2.9 | 15688 |
| `archives-rar300/itanium_synthetic_rar300.rar` | `m5f`, version 2.9 | 2976 |

RAR 4.20 generated the same source set with the same method labels:

| Archive | RAR technical method | Packed size |
|---------|----------------------|------------:|
| `archives-rar420/x86_e8_rar420.rar` | `m5c`, version 2.9 | 655 |
| `archives-rar420/x86_e8e9_rar420.rar` | `m5c`, version 2.9 | 636 |
| `archives-rar420/delta_4ch_rar420.rar` | `m5d`, version 2.9 | 519 |
| `archives-rar420/rgb_gradient_rar420.rar` | `m5c`, version 2.9 | 552 |
| `archives-rar420/audio_stereo_rar420.rar` | `m5e`, version 2.9 | 29047 |
| `archives-rar420/itanium_synthetic_rar420.rar` | `m5f`, version 2.9 | 1354 |

RAR 3.00, RAR 3.93, and RAR 4.20 `rar t` validate their respective archives.
7-Zip 25.01 can list their headers, but this build reports
`Unsupported Method` when extracting these method-5 RAR 3.x streams, so use RAR
or an instrumented public decoder for validation.

The committed archives naturally captured DELTA across all three RAR versions
and AUDIO under RAR 4.20. E8 and E8E9 were captured from a local RAR 3.93
executable archive under `_refs/rarvm-local/`. RGB and ITANIUM were captured
with RAR 3.93 advanced-module forcing (`-mcC+` and `-mcD- -mcI+`). See
`captured-blobs.md` for the final constants and capture-source table.

## SHA-256

```text
a8b0c328d34bd22585b0332fb7e5deb8b76f1318497a6ac6dbbe98903420129c  sources/audio_stereo_pcm.wav
6cfed042719f3b10399ac389b340993f4653b498a9ce2a0c89e13724f5b9ccf8  sources/delta_4ch_ramp.bin
0b345c7a7c5978dbbbf79c5cc7fcc5b2092ed5dc018cff0d626bd740481c98d4  sources/itanium_synthetic_bundles.bin
211a7a298a4b9eb8b72f5e8a052782f865d4fb8788920c578744e9b512fd3c68  sources/rgb_gradient_24bit.bmp
7efff592ab2da266eae9d09d5805dd2820fbb5794f6c7d3b5d48278cf83ba0ba  sources/x86_e8_stream.bin
5bf15ef0f2df7c777610ad9813200527325a0e60e2d7339388e0023923b7acf8  sources/x86_e8e9_stream.bin
f808b26feedfecb5e660e7a453887c3eba7b0c9e5774dc533f8cbd9270b983c9  archives/audio_stereo_rar393.rar
57407687a2e31ab1300a74ecb62ca1479b0cd073105ea7d15738d348e1f97c55  archives/delta_4ch_rar393.rar
1949e00f6474ec7d36334a30353a5b97fe573a4f23d55c45d0bca26c2d23862a  archives/itanium_synthetic_rar393.rar
f0e5c362537f9e26e3868a65fc0f1c6ab3231e9bc7c83cdc227dbc8a7fa5f966  archives/rgb_gradient_rar393.rar
45835967a1adc4a54780274decb53797716c97f3c93f7e6bf38467d66571836b  archives/x86_e8_rar393.rar
211055e6313688b1f0e5d6bdf0293c022bf9236b4347254892833c37dd957861  archives/x86_e8e9_rar393.rar
62df7b593a465181b157af53035c5e8bc108e0ad275786eea31e078f5360970b  archives-rar300/audio_stereo_rar300.rar
c2a4b7a7acd8c83670ad2aada27b6e6519dab925a574dea26224cb0b0fe1045b  archives-rar300/delta_4ch_rar300.rar
4287ed06a7aab1563c1c0359f222192211af69447cc6c59a48c74dfd883ae6c1  archives-rar300/itanium_synthetic_rar300.rar
4ebe1f4d500f4b2aa092fe2f8718776a01b5afd8c9e001669159dc617988a6d3  archives-rar300/rgb_gradient_rar300.rar
96d63e59c5534a15530e7c6799f31723f4207abffcbd1a4cd65a4ab237589261  archives-rar300/x86_e8_rar300.rar
8458579840e24ade4423e93790088e9ca7887b829c274faf243c29ecb91b2ca2  archives-rar300/x86_e8e9_rar300.rar
208f2bc9591501e0af7254e433d2cb0cef5f4100d2b4c52dc63321ed754af6b1  archives-rar420/audio_stereo_rar420.rar
a8d179a5de00e91ee9b9760379b01d6b0b3ed150f8341bfd5be2bed6b95f2d46  archives-rar420/delta_4ch_rar420.rar
f02e38bb229d4f577c2eabc7e5f360080ee85eda53cf5cf7a976384657722b43  archives-rar420/itanium_synthetic_rar420.rar
70a5807c7f47c61ffc79992ce6b3c0801ea5f92913f6b49ab9c60bcd067df121  archives-rar420/rgb_gradient_rar420.rar
4c65b854cba2094448b58d4a3daf77b15a52f986ba181eb75ab6840b7042abd8  archives-rar420/x86_e8_rar420.rar
cce24e2742e0fe208f987b44471664f53ad433c0027a761275316237c86b473e  archives-rar420/x86_e8e9_rar420.rar
```
