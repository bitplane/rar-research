# Captured RARVM Standard Filter Blobs

Captured with an instrumented 7-Zip 25.01 RAR3 decoder from RAR 3.93-created
archives. Each blob includes the leading XOR checksum byte and has `xor(blob) ==
0`.

```python
RAR3_STANDARD_FILTER_BYTECODE = {
    "E8": bytes.fromhex(
        "971b012807069808000000d13a101592ec50cb9920b925f0291915530312"
        "ae511035592b6004156d4066ab023449043602523e9700"
    ),  # 53 bytes, CRC32 0xAD576887
    "E8E9": bytes.fromhex(
        "841b0128111069808000000d13a101c689d280ac9762855cc905c92f8148c"
        "8aa981895728881aac95b0020ab6a03355811a24821b01291f4b8"
    ),  # 57 bytes, CRC32 0x3CD7E57E
    "ITANIUM": bytes.fromhex(
        "469e08080c0c00000e0e08080000080800006c115a04ac0cc4cc5c08184"
        "62408f9a0442512124585990c1400262558999003381a08dc02300c4ed"
        "11d89a1e2d0551133608c5a2306de0618007ffffc4dcc1917b306c444b"
        "2325a44c4a601f424888338ccc4110987a6e04602b22403e2a032548352c5b170"
    ),  # 120 bytes, CRC32 0x3769893F
    "DELTA": bytes.fromhex(
        "2f019a4180ec27482f09766dd3ea415b5944e8175ce16c914c4e3f7700"
    ),  # 29 bytes, CRC32 0x0E06077D
    "RGB": bytes.fromhex(
        "c5019a4195c9a64dba4b140af49b804c0015a6a807262ac9c48b8662320"
        "f8664240666711998cc433331990066883330ccd10e980b3334400cd14"
        "666199a28cc4980b3334500cd18666199a30cc8980b3334604cd10668a"
        "520626688334628050f320c4cd14668c50041e48fc8855e027cc9268183"
        "b09dc2de9c78acd668b40e71dbb249386e022a2c412b109882490314f4e19700"
    ),  # 149 bytes, CRC32 0x1C2C5DC8
    "AUDIO": bytes.fromhex(
        "47019a4195e5720dc26482749324b14006d8384400a8013411dca1ba0199"
        "0cc4033119a4066622604d9a400d668e60d030401826c1c8f6e62613789"
        "208e850bc5a07c6e9f520a9a0ed3733473966907019a39bcf258380c1"
        "bd30166e233493811609b050183b4dc84c059b88c528e0769390980b37"
        "118a59c480424843a947ee43346047d44a0dbbd359a486ee05094026c93"
        "42476a0306a20ea022004a041509e503fe6e128944601bd8b40f068113"
        "6c9a1923811419ca89510ee50662b00209511040262ac668c6aca2640b"
        "2671b4b26cc648a6271a2b8"
    ),  # 216 bytes, CRC32 0xBC85E701
}
```

## Capture Sources

| Filter | Capture log | Source archive |
|--------|-------------|----------------|
| E8 | `capture-logs/local-rar393-exe.jsonl` | `_refs/rarvm-local/rar393_exe.rar` |
| E8E9 | `capture-logs/local-rar393-exe.jsonl` | `_refs/rarvm-local/rar393_exe.rar` |
| ITANIUM | `capture-logs/local-itanium-forced-no-delta-rar393.jsonl` | `_refs/rarvm-local/itanium_forced_no_delta_rar393.rar` |
| DELTA | `capture-logs/rar300.jsonl`, `capture-logs/rar393.jsonl`, `capture-logs/rar420.jsonl` | committed RARVM fixture archives |
| RGB | `capture-logs/local-forced-rar393.jsonl` | `_refs/rarvm-local/rgb_forced_rar393.rar` |
| AUDIO | `capture-logs/rar420.jsonl` | `archives-rar420/audio_stereo_rar420.rar` |

The `_refs/rarvm-local/` archives are local capture-only inputs and are not
intended for git. The durable artifact is the captured bytecode above plus the
JSONL capture logs.
