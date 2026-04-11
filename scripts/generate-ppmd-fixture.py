"""Generate a single Unpack29 PPMd-mode fixture using RAR 3.00 under wine.

Closes the "Unpack29 PPMd-mode (all current rarvm/ fixtures are LZ-mode)"
gap from `doc/IMPLEMENTATION_GAPS.md`.

The output method byte is `m5b` (vs `m5a` for LZ) — RAR's encoder selects
PPMd when `-mct` (method-by-context-text) is set and the input is
text-like enough to benefit. We feed a deterministic 127 KB lorem-ipsum
stream, which is well into the territory where PPMd wins.
"""

from __future__ import annotations

import os
import random
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO_ROOT / "fixtures" / "ppmd"
SOURCES_DIR = FIXTURE_DIR / "sources"
EXPECTED_DIR = FIXTURE_DIR / "expected"

WINEPREFIX = REPO_ROOT / "_refs" / "wineprefixes" / "winrar300"
RAR_EXE = WINEPREFIX / "drive_c" / "Program Files (x86)" / "WinRAR" / "Rar.exe"


def make_lorem(size_bytes: int, seed: int = 0) -> bytes:
    rng = random.Random(seed)
    words = ["lorem", "ipsum", "dolor", "sit", "amet", "consectetur",
             "adipiscing", "elit", "sed", "do", "eiusmod", "tempor",
             "incididunt", "ut", "labore", "et", "dolore", "magna",
             "aliqua", "enim", "ad", "minim", "veniam", "quis"]
    out: list[str] = []
    while sum(len(w) + 1 for w in out) < size_bytes:
        out.append(rng.choice(words))
    return (" ".join(out)).encode("ascii")[:size_bytes]


def run_rar(args, cwd):
    env = os.environ.copy()
    env["WINEPREFIX"] = str(WINEPREFIX)
    cmd = ["wine", str(RAR_EXE)] + args
    return subprocess.run(cmd, cwd=cwd, env=env, timeout=60)


def write_rar_stdout(args, cwd, out_path: Path):
    env = os.environ.copy()
    env["WINEPREFIX"] = str(WINEPREFIX)
    cmd = ["wine", str(RAR_EXE)] + args
    with out_path.open("w") as out:
        return subprocess.run(cmd, cwd=cwd, env=env, stdout=out, stderr=subprocess.STDOUT, timeout=60)


def make_escape_text(size_bytes: int) -> bytes:
    phrase = b"escape-char literal path alpha\x02beta gamma\x02delta\r\n"
    return (phrase * ((size_bytes // len(phrase)) + 1))[:size_bytes]


def make_binary_control(size_bytes: int) -> bytes:
    rng = random.Random(29)
    return bytes(rng.randrange(0, 256) for _ in range(size_bytes))


def write_archive(work: Path, archive: str, files: list[str], switches: list[str]) -> None:
    (work / archive).unlink(missing_ok=True)
    args = ["a", *switches, "-ep", "-cfg-", "-idp", "-o+", archive, *files]
    result = run_rar(args, work)
    if result.returncode != 0:
        raise SystemExit(
            f"rar failed for {archive}: rc={result.returncode}"
        )
    if not (work / archive).exists():
        raise SystemExit(f"archive not produced at {work / archive}")


def main():
    if not RAR_EXE.exists():
        raise SystemExit(f"Rar.exe not found at {RAR_EXE}")

    SOURCES_DIR.mkdir(parents=True, exist_ok=True)
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    EXPECTED_DIR.mkdir(parents=True, exist_ok=True)

    lorem_path = SOURCES_DIR / "lorem_127k.txt"
    lorem_path.write_bytes(make_lorem(127 * 1024))
    lorem_a_path = SOURCES_DIR / "solid_lorem_a.txt"
    lorem_b_path = SOURCES_DIR / "solid_lorem_b.txt"
    esc_path = SOURCES_DIR / "escape_64k.bin"
    binary_path = SOURCES_DIR / "binary_64k.bin"
    lorem_a_path.write_bytes(make_lorem(96 * 1024, seed=1))
    lorem_b_path.write_bytes(make_lorem(96 * 1024, seed=2))
    esc_path.write_bytes(make_escape_text(64 * 1024))
    binary_path.write_bytes(make_binary_control(64 * 1024))

    work = FIXTURE_DIR / ".work"
    work.mkdir(exist_ok=True)
    for path in [lorem_path, lorem_a_path, lorem_b_path, esc_path, binary_path]:
        shutil.copyfile(path, work / path.name)

    archives = [
        (
            "ppmd_lorem_rar300.rar",
            ["lorem_127k.txt"],
            ["-m5", "-mct"],
            "Unpack29 PPMd-mode (-m5 -mct, method 'm5b'), 127 KB lorem ipsum input.",
        ),
        (
            "ppmd_escape_rar300.rar",
            ["escape_64k.bin"],
            ["-m5", "-mct"],
            "Unpack29 PPMd-mode with repeated literal 0x02 bytes to pin escape-character handling.",
        ),
        (
            "ppmd_solid_rar300.rar",
            ["solid_lorem_a.txt", "solid_lorem_b.txt"],
            ["-m5", "-mct", "-s"],
            "Solid Unpack29 PPMd archive with two text members to pin model/state reuse.",
        ),
        (
            "ppmd_mixed_rar300.rar",
            ["lorem_127k.txt", "binary_64k.bin"],
            ["-m5", "-mct"],
            "Mixed text and binary archive generated with -mct to look for PPMd/LZ transitions.",
        ),
    ]

    manifest = []
    for archive, files, switches, note in archives:
        write_archive(work, archive, files, switches)
        shutil.copyfile(work / archive, FIXTURE_DIR / archive)
        result = write_rar_stdout(["lt", archive], work, EXPECTED_DIR / f"{archive}.lt.txt")
        if result.returncode != 0:
            raise SystemExit(f"rar lt failed for {archive}: rc={result.returncode}")
        size = (FIXTURE_DIR / archive).stat().st_size
        manifest.append(f"{archive}\t{size}\t{note}\n")

    (EXPECTED_DIR / "MANIFEST.tsv").write_text("".join(manifest))

    for archive, _, _, _ in archives:
        size = (FIXTURE_DIR / archive).stat().st_size
        print(f"wrote {FIXTURE_DIR / archive} ({size} bytes)")


if __name__ == "__main__":
    main()
