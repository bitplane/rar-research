#!/usr/bin/env python3
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path("/home/gaz/src/tmp/rar")
RARS = Path("/home/gaz/src/tmp/rars/target/debug/rars")
RAR1402 = ROOT / "fixtures/1.402/.rar1402-bin/RAR.EXE"
WORK = ROOT / "tmp/rars-dosbox-compat"


def run(cmd: list[str], cwd: Path | None = None) -> None:
    print("+", " ".join(map(str, cmd)))
    subprocess.run(cmd, cwd=cwd, check=True)


def make_inputs() -> None:
    (WORK / "SRC").mkdir(parents=True, exist_ok=True)
    (WORK / "SRC/HELLO.TXT").write_bytes(b"hello from rars\r\n")
    (WORK / "SRC/TINY.TXT").write_bytes(b"tiny payload over sixteen\r\n")
    (WORK / "SRC/SHORT.TXT").write_bytes(b"abcabcabcabcabcabcabcabcabcabcabcabc\r\n")
    long_prefix = bytearray()
    while len(long_prefix) < 300:
        long_prefix.append((len(long_prefix) * 73 + 19) & 0xFF)
    (WORK / "SRC/LONG.BIN").write_bytes(bytes(long_prefix) + bytes(long_prefix[:32]))
    (WORK / "SRC/FIRST.TXT").write_bytes(b"first member primes adaptive state\r\n")
    (WORK / "SRC/SECOND.TXT").write_bytes(b"second member follows adaptive state\r\n")
    (WORK / "SRC/VOLUME.BIN").write_bytes(b"abcdefghijklmnopqrstuvwxyz0123456789")
    (WORK / "SRC/EMPTY.BIN").write_bytes(b"")
    (WORK / "SRC/DIR").mkdir()


def create_archives() -> list[dict[str, str]]:
    out = WORK / "OUT"
    out.mkdir()
    cases = [
        {"name": "STORE", "archive": "STORE.RAR", "password": "", "parts": ["STORE.RAR"]},
        {"name": "LITERAL", "archive": "LITERAL.RAR", "password": "", "parts": ["LITERAL.RAR"]},
        {"name": "SHORTLZ", "archive": "SHORTLZ.RAR", "password": "", "parts": ["SHORTLZ.RAR"]},
        {"name": "LONGLZ", "archive": "LONGLZ.RAR", "password": "", "parts": ["LONGLZ.RAR"]},
        {"name": "PSTORE", "archive": "PSTORE.RAR", "password": "pass", "parts": ["PSTORE.RAR"]},
        {"name": "PCOMP", "archive": "PCOMP.RAR", "password": "pass", "parts": ["PCOMP.RAR"]},
        {"name": "SOLID", "archive": "SOLID.RAR", "password": "", "parts": ["SOLID.RAR"]},
        {"name": "COMMENT", "archive": "COMMENT.RAR", "password": "", "parts": ["COMMENT.RAR"]},
        {"name": "EMPTYC", "archive": "EMPTYC.RAR", "password": "", "parts": ["EMPTYC.RAR"]},
        {"name": "EMPTYS", "archive": "EMPTYS.RAR", "password": "", "parts": ["EMPTYS.RAR"]},
        {"name": "MULTI", "archive": "MULTI.RAR", "password": "", "parts": ["MULTI.RAR"]},
        {"name": "DIRSTORE", "archive": "DIRSTORE.RAR", "password": "", "parts": ["DIRSTORE.RAR"]},
        {"name": "DIRCOMP", "archive": "DIRCOMP.RAR", "password": "", "parts": ["DIRCOMP.RAR"]},
        {"name": "VSTORE", "archive": "VSTORE.RAR", "password": "", "parts": ["VSTORE.RAR", "VSTORE.R00", "VSTORE.R01", "VSTORE.R02"]},
        {"name": "VCOMP", "archive": "VCOMP.RAR", "password": "", "parts": ["VCOMP.RAR", "VCOMP.R00"]},
    ]

    run([str(RARS), "a", "--format", "rar14", "--store", str(out / "STORE.RAR"), str(WORK / "SRC/HELLO.TXT")])
    run([str(RARS), "a", "--format", "rar14", str(out / "LITERAL.RAR"), str(WORK / "SRC/TINY.TXT")])
    run([str(RARS), "a", "--format", "rar14", str(out / "SHORTLZ.RAR"), str(WORK / "SRC/SHORT.TXT")])
    run([str(RARS), "a", "--format", "rar14", str(out / "LONGLZ.RAR"), str(WORK / "SRC/LONG.BIN")])
    run([str(RARS), "a", "--format", "rar14", "--store", "--password", "pass", str(out / "PSTORE.RAR"), str(WORK / "SRC/HELLO.TXT")])
    run([str(RARS), "a", "--format", "rar14", "--password", "pass", str(out / "PCOMP.RAR"), str(WORK / "SRC/TINY.TXT")])
    run([str(RARS), "a", "--format", "rar14", "--solid", str(out / "SOLID.RAR"), str(WORK / "SRC/FIRST.TXT"), str(WORK / "SRC/SECOND.TXT")])
    run([str(RARS), "a", "--format", "rar14", "--comment", "archive note", "--file-comment", "file note", str(out / "COMMENT.RAR"), str(WORK / "SRC/TINY.TXT")])
    run([str(RARS), "a", "--format", "rar14", str(out / "EMPTYC.RAR"), str(WORK / "SRC/EMPTY.BIN")])
    run([str(RARS), "a", "--format", "rar14", "--store", str(out / "EMPTYS.RAR"), str(WORK / "SRC/EMPTY.BIN")])
    run([str(RARS), "a", "--format", "rar14", str(out / "MULTI.RAR"), str(WORK / "SRC/HELLO.TXT"), str(WORK / "SRC/TINY.TXT")])
    run([str(RARS), "a", "--format", "rar14", "--store", str(out / "DIRSTORE.RAR"), str(WORK / "SRC/DIR")])
    run([str(RARS), "a", "--format", "rar14", str(out / "DIRCOMP.RAR"), str(WORK / "SRC/DIR")])
    run([str(RARS), "a", "--format", "rar14", "--store", "--volume-size", "10", str(out / "VSTORE.RAR"), str(WORK / "SRC/VOLUME.BIN")])
    run([str(RARS), "a", "--format", "rar14", "--volume-size", "8", str(out / "VCOMP.RAR"), str(WORK / "SRC/SHORT.TXT")])
    return cases


def run_dosbox(cases: list[dict[str, str]]) -> None:
    dos = WORK / "DOS"
    dos.mkdir()
    shutil.copyfile(RAR1402, dos / "RAR.EXE")
    for path in (WORK / "OUT").iterdir():
        shutil.copyfile(path, dos / path.name.upper())

    lines = ["@echo off", "echo RARS DOSBOX COMPAT > RESULTS.TXT"]
    for case in cases:
        archive = case["archive"]
        password = case["password"]
        pass_arg = f" -p{password}" if password else ""
        lines.append(f"rar t{pass_arg} {archive}")
        lines.append(f"if errorlevel 1 echo FAIL {case['name']} >> RESULTS.TXT")
        lines.append(f"if not errorlevel 1 echo PASS {case['name']} >> RESULTS.TXT")
    lines.append("exit")
    (dos / "GO.BAT").write_text("\r\n".join(lines) + "\r\n")

    env = os.environ.copy()
    env["SDL_VIDEODRIVER"] = "dummy"
    env["SDL_AUDIODRIVER"] = "dummy"
    cmd = [
        "dosbox-x", "-silent", "-exit", "-nogui", "-nomenu",
        "-time-limit", "30",
        "-c", f"mount c {dos}",
        "-c", "c:",
        "-c", "go.bat",
        "-c", "exit",
    ]
    subprocess.run(cmd, capture_output=True, timeout=45, env=env, check=False)


def main() -> None:
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    make_inputs()
    cases = create_archives()
    run_dosbox(cases)
    print((WORK / "DOS/RESULTS.TXT").read_text(errors="replace"))
    print(f"work dir: {WORK}")


if __name__ == "__main__":
    main()
