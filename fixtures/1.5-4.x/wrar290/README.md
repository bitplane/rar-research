# WinRAR 2.90 HEAD3_SIGN shape fixture

`wrar290_head3_sign_patched.rar` is a 276-byte RAR archive
containing a `HEAD3_SIGN` block (HEAD_TYPE = `0x79`), produced by
WinRAR 2.90 with a registration patch applied. The signature is
**degenerate** (the patch lets the writer run with a BSS-zero
registration buffer, so `FUN_004070a0` operates on zeros and the
`HASH1` bytes are not a "real" signature) — but the wire layout
of the block is byte-identical to what a registered build would
produce, which is what §10.9.1 needs for clean-room reader
verification.

## Generation

```sh
cd research/re/wrar290
python3 scripts/patch_force_registered.py     # produces bin/Rar.regpatched.exe
mkdir -p logs
echo "hello, signing world" > logs/testinput.txt
cd logs && wine ../bin/Rar.regpatched.exe a -av test.rar testinput.txt
cp test.rar /home/gaz/src/tmp/rar/fixtures/1.5-4.x/wrar290/wrar290_head3_sign_patched.rar
```

`research/re/wrar290/scripts/patch_force_registered.py` bypasses
six registration gates so the writer at `FUN_004286a8` runs
end-to-end. Patch strategy and full call-tree map in
`research/re/wrar290/notes.md`.

## What this fixture pins

`scripts/verify-fixtures.py` parses the archive against
`doc/RAR15_40_FORMAT_SPECIFICATION.md §10.9.1` and asserts:

- Block layout: 7-byte standard prefix + 4-byte `ARCFILE_DTIME` +
  2 × `NAME_SIZE` + body.
- Body math: `HEAD_SIZE - 15 == NAME1_SIZE + NAME2_SIZE + 0xa7`.
- `HEAD_FLAGS == 0x4000` (old-version-delete flag).
- `NAME1` == archive name (= `"test.rar"` in this fixture).
- `HASH*_LEN + HASH*_BYTES + PADDING` sums to the expected
  per-spec layout.
- `HEAD_CRC` matches `zlib.crc32(13-byte fixed prefix) & 0xFFFF`.

`HASH1_LEN == 61` in this fixture (matches the spec's prediction
for the `FUN_004070a0` signature transform output: 2-digit prefix
+ 60 hex chars × 2 components / 2 hex per byte ≈ 61 bytes).
`HASH2_LEN == HASH3_LEN == 0` because the corresponding BSS hex
buffers are zero-filled in the patched-but-unregistered run.

## Caveats

- The signature in `HASH1` is not a valid registered-user
  signature; it's a deterministic transform of the BSS-zero
  registration buffer.
- `NAME2` is empty for the same reason. A real registered build
  would populate it with the registered-creator string.
- The fixture is a SHAPE confirmation only; it isn't a "wild"
  archive and isn't byte-identical to anything a real registered
  WinRAR 2.90 would produce, beyond the structural fields.
