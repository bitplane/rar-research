# RAR 5.0 inline recovery-record fixtures

Six archives generated with WinRAR 7.21 to pin down the inline RR
data-area layout. See `doc/INTEGRITY_WRITE_SIDE.md §4.6` for the
spec text these fixtures support.

## Generation

Each archive is `wine`-driven from `_refs/wineprefixes/winrar721`:

```sh
export WINEPREFIX="$(realpath _refs/wineprefixes/winrar721)"
RAR721="$WINEPREFIX/drive_c/Program Files/WinRAR/Rar.exe"

# Same uncompressible 64 KiB random input, varying recovery percent
python3 -c "import os; open('rand64.bin','wb').write(os.urandom(65536))"
wine "$RAR721" a -m0 -rr5  -y rar721_rr5_64k.rar  rand64.bin
wine "$RAR721" a -m0 -rr20 -y rar721_rr20_64k.rar rand64.bin
wine "$RAR721" a -m0 -rr50 -y rar721_rr50_64k.rar rand64.bin

# Same recovery percent (-rr10), varying input size
python3 -c "import os; open('rand16.bin','wb').write(os.urandom(16384))"
python3 -c "import os; open('rand128.bin','wb').write(os.urandom(131072))"
wine "$RAR721" a -m0 -rr10 -y rar721_rr10_16k.rar  rand16.bin
wine "$RAR721" a -m0 -rr10 -y rar721_rr10_64k.rar  rand64.bin
wine "$RAR721" a -m0 -rr10 -y rar721_rr10_128k.rar rand128.bin
```

The random input is regenerated each time, so re-running won't produce
byte-identical fixtures — the `{RB}` magic, layout, and shape of the
parity payload reproduce, but the parity bytes themselves change with
the protected data.

## What these fixtures pin

The six archives exercise two orthogonal axes:

- **Same input, varying recovery percent** (`rr{5,10,20,50}_64k`) —
  the protected archive is the same, so any parity-payload structure
  derived purely from the data stays constant; only `NR` (recovery
  shard count) and the parity bytes change. NR observed: 3 / 6 / 13
  / 32 for 5% / 10% / 20% / 50%.
- **Same percent, varying input size** (`rr10_{16k,64k,128k}`) — the
  recovery percent is the same, so any structure derived from
  `recovery_percent` alone stays constant; `shard_size` and
  protected-byte-count change. `shard_size` observed: 1182 / 1604 /
  2122 for 16k / 64k / 128k inputs; NR observed: 1 / 6 / 12 (roughly
  doubling with input size at this fixed percent).

The 64-KiB rr10 fixture sits at the intersection of both axes, so
it's shared between them.

The structural finding is that the inline RR is `NR` self-contained
`{RB}` chunks back-to-back inside the RR service header's data area;
each chunk is `shard_size` bytes long. `(NR, shard_size, header_size)`
are derivable from `(rec_pct, archive_size)` via the formula in
`doc/INTEGRITY_WRITE_SIDE.md §4.6.2`, and the formula matches
predicted == observed for every row of `expected/MANIFEST.tsv` below.

## Manifest entries

Each row records the file-name, total file size, and the
`(rec_pct, archive_size, NR, shard_size, header_size)` tuple. The
verifier (`scripts/verify-fixtures.py`) confirms that the encoder
formula in §4.6.2 predicts these `(NR, shard_size, header_size)`
values from `(rec_pct, archive_size)`, that the file contains
exactly `NR` `{RB}` markers, and that adjacent markers are exactly
`shard_size` bytes apart. The `archive_size` is the byte count of
the archive prefix preceding the RR service header — i.e. the file
offset of the `HEAD_SERVICE` byte that begins the RR record, **not**
the byte offset of the `{RB}` marker and **not** the original input
file size.
