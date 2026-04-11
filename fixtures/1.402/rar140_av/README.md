# RAR 1.40 AV (legacy `'0'` format) shape fixtures

Two paired fixtures from the RAR 1.40 DOS encoder, captured under
DOSBox-X with the registration patch applied:

- `rar140_noav_baseline.rar` (45 bytes) — produced by the
  **original** `bin/RAR.EXE` 1.40 binary with `rar a noav.rar in.txt`
  (no `-av`). No MHD_AV bit, no AV payload. The control case.
- `rar140_av_patched.rar` (91 bytes) — produced by the
  registration-patched binary with `rarp a -av av.rar in.txt`.
  MHD_AV bit (= `0x20`) set in the main-header Flags byte; AV
  payload appended to the main header.

## What this pins

The pair establishes that **RAR 1.4 archives carry the AV inline
in the main header** (no separate `HEAD3_AV` block — that came in
RAR 1.5+). The 4-byte `RE~^` marker, 7-byte main-header prefix,
and `HeadSize`-extends-the-main-header semantics are all
documented in `RAR13_FORMAT_SPECIFICATION.md §4`. The AV
extension when MHD_AV is set adds:

- A 16-bit length-prefixed payload inside the main header (length
  = `HeadSize - 7 - 2` bytes).
- The first 6 bytes of the payload are a fixed-constant prefix
  `1a 69 6d 02 da ae` (identical across all observed AV-bearing
  fixtures from this binary).
- The remaining bytes are the cipher output of the legacy `'0'`
  AV transform documented in
  `RAR15_40_FORMAT_SPECIFICATION.md §10.8`. Length is
  cipher-mode-dependent.

## Generation

```sh
cd research/re/rar140
python3 scripts/patch_force_registered.py     # produces bin/RAR.regpatched.EXE
cp bin/RAR.regpatched.EXE bin/RARP.EXE         # 8.3-friendly name for DOSBox
mkdir -p logs
echo "hello, signing world" > logs/in.txt

dosbox-x -silent -exit -nogui \
  -c 'mount c /home/gaz/src/tmp/rar/research/re/rar140' \
  -c 'c:' -c 'cd bin' \
  -c 'rar.exe a c:\logs\noav.rar c:\logs\in.txt' \
  -c 'rarp.exe a -av c:\logs\av.rar c:\logs\in.txt' \
  -c 'exit'

cp research/re/rar140/logs/NOAV.RAR    fixtures/1.402/rar140_av/rar140_noav_baseline.rar
cp research/re/rar140/logs/AV.RAR      fixtures/1.402/rar140_av/rar140_av_patched.rar
```

## Caveats

- The patched binary always emits an AV payload — even when run
  without `-av` — because the registration patch unconditionally
  bypasses the registered-vs-shareware checks. The "patched, no
  -av" case isn't included here because it doesn't add anything
  the `_av_patched.rar` fixture doesn't already cover.
- The signature inside the AV payload is over BSS-zero
  registration data (the patch doesn't inject a registration name)
  — so the cipher output is deterministic but not a "real"
  registered signature. Useful for confirming block layout and
  reader behaviour; not byte-identical to a wild registered
  archive.
- The `RE~^` marker, MHD_AV-on-main-header, and 6-byte fixed
  prefix `1a 69 6d 02 da ae` are observed-stable across runs and
  match what `_refs/unrar/` identifies as RAR 1.4 format
  (verified: `unrar v rar140_av_patched.rar` reports
  `Details: RAR 1.4`).
