"""Generate RAR 7.0-format fixtures using WinRAR 7.21 beta 1 under wine.

Important context: WinRAR 7.x's *default* output is **bit-identical** to
WinRAR 6.02's for the same inputs (modulo embedded timestamps). Both produce
the RAR 5.0 wire format (`Rar!\\x1a\\x07\\x01`). The "RAR 7" name refers to
two distinct things:

1. **Unpack70** (the extended-distance LZ codec with `DCX = 80` distance
   slots, supporting up to 1 TiB dictionaries). This only engages when the
   on-disk dictionary exceeds 4 GiB, which requires ≥4 GiB of input data.
   Not generated here — see `IMPLEMENTATION_GAPS.md`.
2. **New RAR 7-only switches** that emit new wire-format features without
   requiring huge inputs:
   - `-ams` saves the archive's own filename as a new extra record in the
     main header.
   - `-om[=lst]` propagates Mark-of-the-Web (Windows Zone.Identifier ADS).
     Not generatable from wine on Linux because the source file would need
     a real NTFS ADS attached.
   - `-me<par>` exposes encryption parameters not directly settable in 6.x.

Of those, `-ams` is the only one we can generate here without external
dependencies. The fixture is committed to demonstrate the new extra record
the spec doesn't yet cover.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO_ROOT / "fixtures" / "7.0"
SOURCES_DIR = FIXTURE_DIR / "sources"
EXPECTED_DIR = FIXTURE_DIR / "expected"

WINEPREFIX = REPO_ROOT / "_refs" / "wineprefixes" / "winrar721"
RAR_EXE = WINEPREFIX / "drive_c" / "Program Files" / "WinRAR" / "Rar.exe"


def run_rar(args, cwd):
    env = os.environ.copy()
    env["WINEPREFIX"] = str(WINEPREFIX)
    env["WINEDEBUG"] = "-all"
    cmd = ["wine", str(RAR_EXE)] + args
    result = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, timeout=60)
    result.stdout_text = result.stdout.decode("cp437", errors="replace")
    return result


def main():
    if not RAR_EXE.exists():
        raise SystemExit(f"missing RAR.EXE at {RAR_EXE}")

    SOURCES_DIR.mkdir(parents=True, exist_ok=True)
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    EXPECTED_DIR.mkdir(parents=True, exist_ok=True)

    (SOURCES_DIR / "hello7.txt").write_bytes(b"Hello, RAR 7.21 fixture world.\n")

    work = FIXTURE_DIR / ".work"
    work.mkdir(exist_ok=True)
    for f in work.iterdir():
        if f.is_file():
            f.unlink()
    shutil.copyfile(SOURCES_DIR / "hello7.txt", work / "hello7.txt")

    # Fixture 1: -ams (archive name save) — new in RAR 7
    archive = "ams_archive_name_rar721.rar"
    args = ["a", "-m3", "-ams", "-htb", "-ep", "-cfg-", "-idq", archive, "hello7.txt"]
    result = run_rar(args, work)
    if result.returncode != 0:
        raise SystemExit(f"rar failed: {result.stdout_text[:200]}")
    src = work / archive
    if not src.exists():
        raise SystemExit(f"archive not produced: {src}")
    shutil.copyfile(src, FIXTURE_DIR / archive)
    listing = run_rar(["lta", archive], work).stdout_text
    (EXPECTED_DIR / f"{archive}.lta.txt").write_text(listing)

    # Manifest
    size = (FIXTURE_DIR / archive).stat().st_size
    (EXPECTED_DIR / "MANIFEST.tsv").write_text(
        f"{archive}\t{size}\tWinRAR 7.21 -ams switch: archive's own filename "
        f"stored as a new extra record in the main header. Wire format is "
        f"still RAR 5.0 ('Rar!\\x1a\\x07\\x01'), not Unpack70 — the new "
        f"feature is the extra record, not the codec.\n"
    )

    print(f"wrote {FIXTURE_DIR / archive} ({size} bytes)")


if __name__ == "__main__":
    main()
