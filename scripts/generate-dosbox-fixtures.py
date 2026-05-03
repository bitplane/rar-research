"""Generate RAR 1.402 + RAR 2.50 fixtures via DOSBox-X.

Covers two distinct gaps from `doc/IMPLEMENTATION_GAPS.md`:

1. **RAR 1.402 edge cases** — empty file, multi-file, >64 KiB window-wrap,
   solid, multi-volume, comment, directory entry, SFX with RSFX marker.
   Uses the extracted `RAR.EXE` from RAR1_402.EXE (1994-03-20).

2. **Unpack20 multimedia switch contrast** — RAR 2.50 `-m5 -mm` inputs that
   exercise the Unpack20 container path. The current committed `AUDIO.RAR`
   starts with table-read peek `0x0040`, so it is an LZ block, not proof of
   the audio predictor path. Uses the DOS RAR.EXE from the rar250.exe SFX
   (extracted earlier under `research/re/rar250/`).

DOSBox-X is invoked with `-time-limit` (clean self-termination, no SIGTERM
popup) and an `exit` line in the autoexec.  All commands run from a
batch file so DOSBox shell parsing of redirects is uniform.
"""

from __future__ import annotations

import os
import random
import shutil
import struct
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

RAR140_EXE = None  # extracted lazily from RAR1_402.EXE
RAR250_EXE = REPO_ROOT / "research" / "re" / "rar250" / "bin" / "extracted" / "RAR.EXE"
RAR1_402_SFX = REPO_ROOT / "_refs" / "rarbins" / "RAR1_402.EXE"
WINEPREFIX_602 = REPO_ROOT / "_refs" / "wineprefixes" / "winrar602"
RAR_602 = WINEPREFIX_602 / "drive_c" / "Program Files (x86)" / "WinRAR" / "Rar.exe"


def run_dosbox(work_dir: Path, batch: str, time_limit: int = 15) -> subprocess.CompletedProcess:
    """Run a DOS batch script under DOSBox-X. Cleans up on its own via -time-limit."""
    bat_path = work_dir / "GO.BAT"
    bat_path.write_text(batch)
    env = os.environ.copy()
    env["SDL_VIDEODRIVER"] = "dummy"
    env["SDL_AUDIODRIVER"] = "dummy"
    cmd = [
        "dosbox-x", "-silent", "-exit", "-nogui", "-nomenu",
        "-time-limit", str(time_limit),
        "-c", f"mount c {work_dir}",
        "-c", "c:",
        "-c", "go.bat",
        "-c", "exit",
    ]
    return subprocess.run(cmd, capture_output=True, timeout=time_limit + 10, env=env)


def extract_rar1402_payload() -> Path:
    """Use wine winrar 6.02 to unpack the RAR1_402.EXE SFX once.
    Returns the path to the extracted RAR.EXE (RAR 1.402 binary).
    """
    target_dir = REPO_ROOT / "fixtures" / "1.402" / ".rar1402-bin"
    rar_exe = target_dir / "RAR.EXE"
    if rar_exe.exists():
        return rar_exe
    target_dir.mkdir(parents=True, exist_ok=True)
    sfx_copy = target_dir / "RAR1_402.EXE"
    shutil.copyfile(RAR1_402_SFX, sfx_copy)
    env = os.environ.copy()
    env["WINEPREFIX"] = str(WINEPREFIX_602)
    env["WINEDEBUG"] = "-all"
    subprocess.run(
        ["wine", str(RAR_602), "x", "-y", "RAR1_402.EXE"],
        cwd=target_dir, env=env, capture_output=True, timeout=30,
    )
    sfx_copy.unlink()
    if not rar_exe.exists():
        raise SystemExit(f"failed to extract RAR.EXE from {RAR1_402_SFX}")
    return rar_exe


# -------------------------------------------------------------- RAR 1.402

def gen_rar1402_fixtures(rar140_exe: Path):
    out_dir = REPO_ROOT / "fixtures" / "1.402"
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / ".work"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir()

    # Stage RAR.EXE
    shutil.copyfile(rar140_exe, work / "RAR.EXE")

    # Stage source files
    (work / "HELLO.TXT").write_bytes(b"Hello, RAR 1.402 fixture world.\r\n")
    (work / "TINY.TXT").write_bytes(b"AAAAAAAA\r\n")
    (work / "EMPTY.BIN").write_bytes(b"")
    (work / "SECRET.TXT").write_bytes(b"Stored encrypted fixture.\r\n")
    (work / "COMMENT.TXT").write_bytes(b"This is the archive comment.\r\n")

    # 80 KB compressible text → exceeds the 64 KB Unpack15 window; the
    # decoder must wrap the window mid-stream.
    rng = random.Random(0)
    words = [b"lorem", b"ipsum", b"dolor", b"sit", b"amet", b"consectetur",
             b"adipiscing", b"elit", b"sed", b"do", b"eiusmod", b"tempor"]
    big_text = bytearray()
    while len(big_text) < 80 * 1024:
        big_text += rng.choice(words) + b" "
    (work / "BIG80K.TXT").write_bytes(bytes(big_text[:80 * 1024]))

    # 96 KB compressible text. With a 2 KB volume limit the compressed
    # Unpack15 bitstream spans multiple old-style volumes.
    rng_c = random.Random(42)
    c_words = [b"alpha", b"beta", b"gamma", b"delta", b"epsilon",
               b"zeta", b"eta", b"theta"]
    cmulti = bytearray()
    while len(cmulti) < 96 * 1024:
        cmulti += rng_c.choice(c_words) + b" "
    (work / "CMULTI.TXT").write_bytes(bytes(cmulti[:96 * 1024]))

    # Repeating binary pattern: triggers many short matches → exercises the
    # Buf60 toggle path in Unpack15 (RAR13_FORMAT_SPECIFICATION.md §6.13).
    pattern = bytes(range(0, 256)) * 32  # 8 KB of cycling bytes
    (work / "REPEATB.BIN").write_bytes(pattern)

    # Random 64 KB payload — incompressible, so -v<small> forces a split into
    # multiple volumes regardless of compression ratio.
    rng_r = random.Random(1)
    (work / "RANDOM.BIN").write_bytes(bytes(rng_r.randint(0, 255) for _ in range(64 * 1024)))

    # A subdirectory with a file inside, for the directory-entry fixture.
    (work / "SUBDIR").mkdir()
    (work / "SUBDIR" / "INNER.TXT").write_bytes(b"Inside subdir.\r\n")

    # Build a single batch script that runs every fixture in one DOSBox session
    # (saves startup overhead, ~0.3 s per call).
    batch = "@echo off\r\n"
    cases = [
        # name, switches, inputs
        ("EMPTY.RAR",     "a -m0",       "EMPTY.BIN"),
        ("MULTIFIL.RAR",  "a",           "HELLO.TXT TINY.TXT"),
        ("BIG80K.RAR",    "a -m3",       "BIG80K.TXT"),
        ("REPEATB.RAR",   "a -m3",       "REPEATB.BIN"),
        ("SOLID.RAR",     "a -s -m3",    "HELLO.TXT TINY.TXT BIG80K.TXT"),
        # -v<size>K: 20K per volume × incompressible 64KB input = 4 vols
        # (unit suffix is required — bare -v20000 was treated as something other than bytes)
        ("MULTIVOL.RAR",  "a -v20K -m0", "RANDOM.BIN"),
        ("CMULTIV.RAR",   "a -v2K -m3",  "CMULTI.TXT"),
        ("WITHDIR.RAR",   "a -r -m3",    "SUBDIR"),
        ("STOREPWD.RAR",  "a -m0 -ppassword", "SECRET.TXT"),
    ]
    for name, switches, inputs in cases:
        batch += f"rar {switches} {name} {inputs}\r\n"

    # SFX archive: rar's `s` command converts an existing archive
    batch += "rar a -m3 SFXSRC.RAR HELLO.TXT\r\n"
    batch += "rar s SFXSRC.RAR\r\n"      # produces SFXSRC.EXE

    # Comment via `c` command takes an input file prefixed with '='.
    batch += "rar a -m0 COMMENT.RAR HELLO.TXT\r\n"
    batch += "rar c COMMENT.RAR =COMMENT.TXT\r\n"

    result = run_dosbox(work, batch, time_limit=20)
    if result.returncode != 0 and result.returncode != 124:
        # rc=124 is OK if -time-limit fired
        print(f"  rar 1.402 batch rc={result.returncode}")

    # File comments (`cf`) enter RAR's interactive editor and finish with F10.
    # DOSBox-X AUTOTYPE queues the editor input after `rar cf` starts. Headless
    # DOSBox-X 2025.02.01 can exit with SIGSEGV after this path, but the archive
    # is complete and deterministic enough for fixture generation.
    (work / "HELLO.TXT").write_bytes(b"Hello, file comment fixture.\r\n")
    fcomment_batch = (
        "@echo off\r\n"
        "rar a -m0 FCOMM.RAR HELLO.TXT\r\n"
        "autotype -w 1 -p 0.1 F C O M enter f10\r\n"
        "rar cf FCOMM.RAR HELLO.TXT\r\n"
    )
    fcomment_result = run_dosbox(work, fcomment_batch, time_limit=20)
    if fcomment_result.returncode not in (0, 124, -11):
        print(f"  rar 1.402 file-comment batch rc={fcomment_result.returncode}")

    # Collect produced fixtures
    produced = []
    for name in ["EMPTY.RAR", "MULTIFIL.RAR", "BIG80K.RAR", "REPEATB.RAR",
                 "SOLID.RAR", "MULTIVOL.RAR", "CMULTIV.RAR", "WITHDIR.RAR",
                 "STOREPWD.RAR", "SFXSRC.EXE", "COMMENT.RAR", "FCOMM.RAR"]:
        src = work / name
        if src.exists() and src.stat().st_size > 0:
            shutil.copyfile(src, out_dir / name)
            produced.append(name)
    # Multi-volume parts (RAR 1.x uses .RNN naming, uppercase from DOS)
    for src in sorted(list(work.glob("MULTIVOL.R[0-9][0-9]")) +
                      list(work.glob("MULTIVOL.r[0-9][0-9]"))):
        shutil.copyfile(src, out_dir / src.name)
        produced.append(src.name)
    for src in sorted(list(work.glob("CMULTIV.R[0-9][0-9]")) +
                      list(work.glob("CMULTIV.r[0-9][0-9]"))):
        shutil.copyfile(src, out_dir / src.name)
        produced.append(src.name)

    expected_dir = out_dir / "expected"
    expected_dir.mkdir(exist_ok=True)
    shutil.copyfile(work / "CMULTI.TXT", expected_dir / "CMULTI.TXT")

    return produced


# ------------------------------------------------------------ RAR 2.50 Unpack20

def gen_rar250_fixtures():
    out_dir = REPO_ROOT / "fixtures" / "2.50"
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / ".work"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir()

    if not RAR250_EXE.exists():
        raise SystemExit(f"missing RAR 2.50 binary: {RAR250_EXE}")

    shutil.copyfile(RAR250_EXE, work / "RAR.EXE")

    # Synthesize 32 KB of 16-bit PCM-style data (smooth multi-byte signal):
    # two channels (L/R), each sample is a slowly-varying sine-ish wave.
    import math
    pcm = bytearray()
    for i in range(8192):
        # Left = ramp, Right = inverse ramp, both 16-bit little-endian
        L = int(20000 * math.sin(i * 2 * math.pi / 256)) & 0xFFFF
        R = int(15000 * math.sin(i * 2 * math.pi / 384 + 1)) & 0xFFFF
        pcm += struct.pack("<HH", L, R)
    (work / "PCM_LR.WAV").write_bytes(bytes(pcm))

    # Plain text input as a contrast (LZ-only; multimedia mode should not win).
    (work / "PLAIN.TXT").write_bytes(b"Hello text not audio.\r\n" * 100)

    # Multi-file input for solid-state coverage. The files deliberately share
    # phrases so the second member can refer back into the first member's
    # dictionary when -s is enabled.
    shared = (
        b"RAR 2.50 solid dictionary carry-over line with repeated tokens "
        b"alpha beta gamma delta.\r\n"
    )
    (work / "SOLID1.TXT").write_bytes(shared * 180)
    (work / "SOLID2.TXT").write_bytes(
        (shared * 90)
        + (b"second member unique tail after shared history.\r\n" * 120)
    )

    # Larger non-solid LZ input. This exercises long-running Unpack20 state and
    # table refreshes without engaging audio mode.
    chunk = bytearray()
    for i in range(4096):
        chunk.extend(f"{i:04x}: unpack20 block refresh fixture ".encode("ascii"))
        chunk.extend(bytes([(i * 17) & 0xff, (i * 31) & 0xff, 13, 10]))
    (work / "BIGLZ.BIN").write_bytes(bytes(chunk))

    batch = (
        "@echo off\r\n"
        # -m5 = max compression, -mm = ask the encoder to test multimedia mode.
        "rar a -m5 -mm AUDIO.RAR PCM_LR.WAV\r\n"
        # contrast: same options on text input (encoder should still detect non-audio)
        "rar a -m5 -mm AUTOREJ.RAR PLAIN.TXT\r\n"
        "rar a -m5 -s SOLID.RAR SOLID1.TXT SOLID2.TXT\r\n"
        "rar a -m5 BIGLZ.RAR BIGLZ.BIN\r\n"
    )
    result = run_dosbox(work, batch, time_limit=15)
    if result.returncode != 0 and result.returncode != 124:
        print(f"  rar 2.50 batch rc={result.returncode}")

    produced = []
    for name in ["AUDIO.RAR", "AUTOREJ.RAR", "SOLID.RAR", "BIGLZ.RAR"]:
        src = work / name
        if src.exists() and src.stat().st_size > 0:
            shutil.copyfile(src, out_dir / name)
            produced.append(name)
    return produced


def main():
    rar140_exe = extract_rar1402_payload()
    print(f"RAR 1.402 binary at {rar140_exe}")

    print("Generating RAR 1.402 fixtures…")
    p1 = gen_rar1402_fixtures(rar140_exe)
    for name in p1:
        size = (REPO_ROOT / "fixtures" / "1.402" / name).stat().st_size
        print(f"  {name:20}  {size:>8}")

    print("Generating RAR 2.50 Unpack20 fixtures…")
    p2 = gen_rar250_fixtures()
    for name in p2:
        size = (REPO_ROOT / "fixtures" / "2.50" / name).stat().st_size
        print(f"  {name:20}  {size:>8}")


if __name__ == "__main__":
    main()
