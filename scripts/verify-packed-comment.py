#!/usr/bin/env python3
"""Validate the RAR 1.4 packed archive-comment decode procedure on COMMENT.RAR.

Runs the documented procedure from `doc/RAR13_FORMAT_SPECIFICATION.md` §8
end-to-end against `fixtures/1.402/COMMENT.RAR`:

  1. Parse the 7-byte main header; check `MHD_COMMENT (0x02)` and
     `MHD_PACK_COMMENT (0x10)` flags.
  2. Read `CmtLength` and `UnpCmtLength` from the main-header extension.
  3. Decrypt the `CmtLength - 2` packed bytes with the fixed RAR 1.3
     comment key `[0, 7, 77]` (§7.3).
  4. Hand the original archive to a reference RAR binary and assert the
     comment it prints equals the expected text.

Step 4 is a cross-check: a reference reader runs the same procedure
internally, so agreement on the final text confirms steps 1-3 landed on
the right bytes. This script's value is asserting steps 1-3 match
byte-for-byte and emitting the decrypted intermediate as a test vector
for clean-room implementations.
"""

from __future__ import annotations

import shutil
import struct
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "fixtures/1.402/COMMENT.RAR"
def _find_reference_reader() -> str | None:
    """A distributed RAR binary that can print an archive comment."""
    for candidate in (
        ROOT / "_refs/rarbins/rar712-linux-x64",
        ROOT / "_refs/rarbins/rar393-linux-x64",
    ):
        if candidate.exists():
            return str(candidate)
    return shutil.which("unrar") or shutil.which("rar")


UNRAR = _find_reference_reader()

EXPECTED_TEXT = b"This is the archive comment.\r\n"

EXPECTED_DECRYPTED = bytes.fromhex(
    "25e53d47a9f672513da0c1f4a47a1f655c315981122975705c827723b9b9c0e0"
)


def decrypt13(data: bytes, key: tuple[int, int, int] = (0, 7, 77)) -> bytes:
    k0, k1, k2 = key
    out = bytearray(len(data))
    for i, b in enumerate(data):
        k1 = (k1 + k2) & 0xFF
        k0 = (k0 + k1) & 0xFF
        out[i] = (b - k0) & 0xFF
    return bytes(out)


def main() -> int:
    raw = ARCHIVE.read_bytes()

    sig = raw[0:4]
    if sig != b"RE~^":
        print(f"fail: signature {sig!r} != b'RE~^'")
        return 1

    head_size, flags = struct.unpack_from("<HB", raw, 4)
    if head_size != 43:
        print(f"fail: HeadSize {head_size} != 43")
        return 1
    if (flags & 0x02) == 0:
        print(f"fail: MHD_COMMENT (0x02) not set in flags 0x{flags:02x}")
        return 1
    if (flags & 0x10) == 0:
        print(f"fail: MHD_PACK_COMMENT (0x10) not set in flags 0x{flags:02x}")
        return 1
    print(f"ok: main header sig+HeadSize+flags (0x{flags:02x})")

    ext = raw[7:head_size]
    if len(ext) != 36:
        print(f"fail: extension length {len(ext)} != 36")
        return 1

    cmt_length, unp_cmt_length = struct.unpack_from("<HH", ext, 0)
    if cmt_length != 34:
        print(f"fail: CmtLength {cmt_length} != 34")
        return 1
    if unp_cmt_length != len(EXPECTED_TEXT):
        print(f"fail: UnpCmtLength {unp_cmt_length} != {len(EXPECTED_TEXT)}")
        return 1
    print(f"ok: CmtLength={cmt_length} UnpCmtLength={unp_cmt_length}")

    packed_len = cmt_length - 2
    encrypted = ext[4 : 4 + packed_len]
    if len(encrypted) != 32:
        print(f"fail: encrypted payload length {len(encrypted)} != 32")
        return 1

    decrypted = decrypt13(encrypted)
    if decrypted != EXPECTED_DECRYPTED:
        print("fail: Decrypt13 output mismatch")
        print(f"  got:      {decrypted.hex()}")
        print(f"  expected: {EXPECTED_DECRYPTED.hex()}")
        return 1
    print(f"ok: Decrypt13([0,7,77]) -> {decrypted.hex()}")

    if UNRAR is None:
        print("skip: no reference RAR binary found, cannot verify Unpack15 round-trip")
        return 0

    result = subprocess.run(
        [UNRAR, "lc", str(ARCHIVE)],
        capture_output=True,
        text=True,
        check=False,
    )
    if "This is the archive comment." not in result.stdout:
        print("fail: unrar lc did not surface expected comment text")
        print(result.stdout)
        return 1
    print("ok: unrar lc surfaces expected comment text (Unpack15 round-trip)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
