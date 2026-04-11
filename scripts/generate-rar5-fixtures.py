"""Generate RAR 5.0 fixtures using WinRAR 6.02 under wine.

Reproducible: source files are deterministic (constants or seed-0 PRNG); each
fixture is one wine invocation with explicit switches. README is regenerated
from this script's `FIXTURES` table.

Mirror of `scripts/generate-negative-fixtures.py` style.
"""

from __future__ import annotations

import os
import random
import shutil
import struct
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO_ROOT / "fixtures" / "5.0"
SOURCES_DIR = FIXTURE_DIR / "sources"
EXPECTED_DIR = FIXTURE_DIR / "expected"

WINEPREFIX = REPO_ROOT / "_refs" / "wineprefixes" / "winrar602"
RAR_EXE = WINEPREFIX / "drive_c" / "Program Files (x86)" / "WinRAR" / "Rar.exe"

# (basename, rar args after the archive name, description)
FIXTURES = [
    # --- A. basic codec ---
    ("stored.rar",          ["a", "-m0", "-ma5", "-ep", "-cfg-", "-idq"], ["hello.txt"],            "Stored only, CRC32 hash, no compression."),
    ("stored_blake2.rar",   ["a", "-m0", "-ma5", "-htb", "-ep", "-cfg-", "-idq"], ["hello.txt"],     "Stored only, BLAKE2sp hash."),
    ("m1_fastest.rar",      ["a", "-m1", "-ma5", "-htb", "-ep", "-cfg-", "-idq"], ["bigtext_64k.bin"], "-m1 fastest compression on 64 KB of text."),
    ("m3_default.rar",      ["a", "-m3", "-ma5", "-htb", "-ep", "-cfg-", "-idq"], ["bigtext_64k.bin"], "-m3 default compression."),
    ("m5_max.rar",          ["a", "-m5", "-ma5", "-htb", "-ep", "-cfg-", "-idq"], ["bigtext_64k.bin"], "-m5 maximum compression."),

    # --- B. dictionary sizes (CompInfo bitfield differences; need payload >= dict to see effect) ---
    ("dict_128k.rar",       ["a", "-m3", "-ma5", "-md128k", "-htb", "-ep", "-cfg-", "-idq"], ["bigtext_512k.bin"], "Dictionary 128 KB (CompInfo encodes the explicit -md value)."),
    ("dict_1m.rar",         ["a", "-m3", "-ma5", "-md1m",   "-htb", "-ep", "-cfg-", "-idq"], ["bigtext_512k.bin"], "Dictionary 1 MB. RAR clamps the requested dict to the input size, so values >1M (-md4m, etc.) round down to 1M for this 512 KB input."),

    # --- C. encryption ---
    ("password_aes.rar",    ["a", "-m3", "-ma5", "-ppassword", "-htb", "-ep", "-cfg-", "-idq"], ["hello.txt"], "Per-file AES-256 encryption (-p), BLAKE2sp HashMAC."),
    ("password_crc32.rar",  ["a", "-m3", "-ma5", "-ppassword", "-htc", "-ep", "-cfg-", "-idq"], ["hello.txt"], "Per-file AES-256 encryption (-p), CRC32 (MAC-converted)."),
    ("header_encrypted.rar",["a", "-m3", "-ma5", "-hppassword", "-htb", "-ep", "-cfg-", "-idq"], ["hello.txt"], "Whole-archive header encryption (-hp)."),

    # --- D. service headers ---
    ("with_comment.rar",    ["a", "-m3", "-ma5", "-zcomment.txt", "-htb", "-ep", "-cfg-", "-idq"], ["hello.txt"], "Archive comment (CMT service header)."),
    ("with_recovery.rar",   ["a", "-m3", "-ma5", "-rr10", "-htb", "-ep", "-cfg-", "-idq"], ["hello.txt"], "10% recovery record (RR service header)."),
    ("with_quickopen.rar",  ["a", "-m3", "-ma5", "-qo+", "-htb", "-ep", "-cfg-", "-idq"], ["hello.txt", "tiny.txt"], "Quick Open cache (QO service header)."),
    ("with_all_services.rar",["a", "-m3", "-ma5", "-zcomment.txt", "-rr5", "-qo+", "-htb", "-ep", "-cfg-", "-idq"], ["hello.txt", "tiny.txt"], "Comment + RR + QO together; exercises Locator extra in main header."),

    # --- E. multi-file / solid / multi-volume ---
    ("multifile.rar",       ["a", "-m3", "-ma5", "-htb", "-ep", "-cfg-", "-idq"], ["hello.txt", "tiny.txt", "random_4k.bin"], "Multiple files, non-solid."),
    ("solid.rar",           ["a", "-m3", "-ma5", "-s", "-htb", "-ep", "-cfg-", "-idq"], ["hello.txt", "tiny.txt"], "Solid archive (-s)."),
    ("multivol.rar",        ["a", "-m3", "-ma5", "-v2k", "-htb", "-ep", "-cfg-", "-idq"], ["random_4k.bin"], "Multi-volume (-v2k → 2 KB volumes; rar emits multivol.part1.rar, multivol.part2.rar, ...)."),
    ("multivol_rev.rar",    ["a", "-m0", "-ma5", "-v4k", "-rv2", "-htb", "-ep", "-cfg-", "-idq"], ["random_16k.bin"], "Multi-volume + recovery volumes (-v4k → 4 KB data volumes, -rv2 → 2 .rev files). Stored mode (-m0) so the volume count is predictable from input size. Exercises REV5_SIGN ('Rar!\\x1aRev') and the .rev file format from INTEGRITY_WRITE_SIDE.md §4.7."),

    # --- F. filter triggers ---
    ("filter_arm.rar",      ["a", "-m5", "-ma5", "-htb", "-ep", "-cfg-", "-idq"], ["arm_synthetic.bin"], "ARM filter trigger (synthetic BL stream)."),
    ("filter_e8.rar",       ["a", "-m5", "-ma5", "-htb", "-ep", "-cfg-", "-idq"], ["x86_e8_stream.bin"], "E8 (x86 CALL) filter trigger."),
    ("filter_e8e9.rar",     ["a", "-m5", "-ma5", "-htb", "-ep", "-cfg-", "-idq"], ["x86_e8e9_stream.bin"], "E8E9 (x86 CALL+JMP) filter trigger."),
    ("filter_delta.rar",    ["a", "-m5", "-ma5", "-htb", "-ep", "-cfg-", "-idq"], ["delta_4ch_ramp.bin"], "DELTA filter trigger (4-channel ramp)."),

    # --- G. edge cases ---
    ("empty_file.rar",      ["a", "-m3", "-ma5", "-htb", "-ep", "-cfg-", "-idq"], ["empty.bin"], "Single empty (zero-byte) file."),
]


def make_sources():
    """Generate deterministic source files in SOURCES_DIR."""
    SOURCES_DIR.mkdir(parents=True, exist_ok=True)

    (SOURCES_DIR / "hello.txt").write_bytes(b"Hello, RAR 5.0 fixture world.\n")
    (SOURCES_DIR / "tiny.txt").write_bytes(b"AAAAAAAA\n")
    (SOURCES_DIR / "comment.txt").write_bytes(b"This is the archive comment.\n")
    (SOURCES_DIR / "empty.bin").write_bytes(b"")

    rng = random.Random(0)
    (SOURCES_DIR / "random_4k.bin").write_bytes(bytes(rng.randint(0, 255) for _ in range(4096)))
    rng_b = random.Random(0)
    (SOURCES_DIR / "random_16k.bin").write_bytes(bytes(rng_b.randint(0, 255) for _ in range(16384)))

    # Reproducible "text" content for dictionary-size tests: lorem-ipsum-ish
    rng2 = random.Random(1)
    words = [b"lorem", b"ipsum", b"dolor", b"sit", b"amet", b"consectetur",
             b"adipiscing", b"elit", b"sed", b"do", b"eiusmod", b"tempor",
             b"incididunt", b"ut", b"labore", b"et", b"dolore", b"magna"]
    bigtext_64k = bytearray()
    while len(bigtext_64k) < 64 * 1024:
        bigtext_64k += rng2.choice(words) + b" "
    (SOURCES_DIR / "bigtext_64k.bin").write_bytes(bytes(bigtext_64k[:64 * 1024]))

    rng3 = random.Random(2)
    bigtext_512k = bytearray()
    while len(bigtext_512k) < 512 * 1024:
        bigtext_512k += rng3.choice(words) + b" "
    (SOURCES_DIR / "bigtext_512k.bin").write_bytes(bytes(bigtext_512k[:512 * 1024]))

    # Synthetic ARM stream: every 4-byte word is `<24-bit imm> 0xEB`
    rng4 = random.Random(3)
    arm = bytearray()
    for _ in range(1024):
        imm = rng4.randint(0, 0xFFFFFF)
        arm += struct.pack("<I", imm | 0xEB000000)
    (SOURCES_DIR / "arm_synthetic.bin").write_bytes(bytes(arm))

    # Reuse the rarvm/sources/ binaries for E8 / E8E9 / DELTA triggers
    rarvm_sources = REPO_ROOT / "fixtures" / "rarvm" / "sources"
    for name in ("x86_e8_stream.bin", "x86_e8e9_stream.bin", "delta_4ch_ramp.bin"):
        src = rarvm_sources / name
        if src.exists():
            shutil.copyfile(src, SOURCES_DIR / name)


def run_rar(args, cwd):
    env = os.environ.copy()
    env["WINEPREFIX"] = str(WINEPREFIX)
    env["WINEDEBUG"] = "-all"
    cmd = ["wine", str(RAR_EXE)] + args
    result = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=60)
    return result


def generate_one(archive, rar_switches, inputs, description):
    """Generate one fixture by running rar.exe inside a temp work dir."""
    work = SOURCES_DIR.parent / ".work"
    work.mkdir(exist_ok=True)

    # Stage source files into work dir
    for inp in inputs + ([Path(rar_switches[2][1:])] if rar_switches and rar_switches[2].startswith("-z") else []):
        inp_name = Path(inp).name
        src = SOURCES_DIR / inp_name
        if not src.exists():
            print(f"  ! missing source: {src}")
            return False
        shutil.copyfile(src, work / inp_name)

    # If a -z<file> switch is present, also stage that file
    for sw in rar_switches:
        if sw.startswith("-z") and len(sw) > 2:
            src = SOURCES_DIR / sw[2:]
            if src.exists():
                shutil.copyfile(src, work / sw[2:])

    # Remove pre-existing archives so rar doesn't append
    for f in work.glob(f"{archive.replace('.part1.rar', '')}*.rar"):
        f.unlink()

    args = rar_switches + [archive] + inputs
    result = run_rar(args, work)
    if result.returncode != 0:
        print(f"  ! rar failed: rc={result.returncode}")
        print(f"    stderr: {result.stderr[:200]}")
        return False

    # For multi-volume archives (rar emits <base>.part1.rar, <base>.part2.rar, …
    # when given <base>.rar as the archive arg with -v) copy ALL parts and
    # any companion .rev recovery volumes.
    base = archive[:-4] if archive.endswith(".rar") else archive
    multivol_parts = sorted(work.glob(f"{base}.part*.rar")) + \
                     sorted(work.glob(f"{base}.part*.rev"))
    if multivol_parts:
        for src in multivol_parts:
            shutil.copyfile(src, FIXTURE_DIR / src.name)
        return True
    src_arc = work / archive
    if not src_arc.exists():
        print(f"  ! archive not produced: {src_arc}")
        return False
    shutil.copyfile(src_arc, FIXTURE_DIR / archive)
    return True


def list_archive(archive):
    """Run `rar lta` and capture technical listing.

    Wine refuses absolute Linux paths through Rar.exe — we must run from inside
    a directory containing the archive, with the archive named relatively.
    For multi-volume archives, list the .part1.rar; rar follows the chain.
    """
    work = SOURCES_DIR.parent / ".work"
    base = archive[:-4] if archive.endswith(".rar") else archive
    multivol_parts = sorted((FIXTURE_DIR).glob(f"{base}.part*.rar"))
    if multivol_parts:
        for src in multivol_parts:
            shutil.copyfile(src, work / src.name)
        result = run_rar(["lta", multivol_parts[0].name], work)
    else:
        archive_path = FIXTURE_DIR / archive
        if not archive_path.exists():
            return None
        shutil.copyfile(archive_path, work / archive)
        result = run_rar(["lta", archive], work)
    return result.stdout


def resolve_fixture_files(archive, fixture_dir):
    """Map a declared archive name to the real files emitted on disk.

    Multi-volume archives don't produce a file matching the placeholder
    `<base>.rar` name passed to rar — they produce `<base>.part1.rar`,
    `<base>.part2.rar`, … (new naming) or `<base>.rar` + `<base>.r00` +
    `<base>.r01` … (old naming, `-vn`). Recovery volumes add
    `<base>.partN.rev`. Return the actual files in deterministic order so
    each manifest row points to a real on-disk artifact.
    """
    base = archive[:-4] if archive.endswith(".rar") else archive
    files = []
    primary = fixture_dir / archive
    if primary.exists():
        files.append(primary)
    files.extend(sorted(fixture_dir.glob(f"{base}.part[0-9]*.rar")))
    files.extend(sorted(fixture_dir.glob(f"{base}.part[0-9]*.rev")))
    files.extend(sorted(fixture_dir.glob(f"{base}.r[0-9][0-9]")))
    return files


def main():
    if not RAR_EXE.exists():
        sys.exit(f"Rar.exe not found at {RAR_EXE}")

    print(f"sources -> {SOURCES_DIR}")
    make_sources()

    print(f"fixtures -> {FIXTURE_DIR}")
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    EXPECTED_DIR.mkdir(parents=True, exist_ok=True)

    succeeded = []
    failed = []
    for archive, switches, inputs, description in FIXTURES:
        print(f"  {archive}")
        if generate_one(archive, switches, inputs, description):
            succeeded.append((archive, switches, inputs, description))
        else:
            failed.append(archive)

    # Capture per-archive `lta` output for the manifest
    manifest_lines = []
    for archive, switches, inputs, description in succeeded:
        listing = list_archive(archive)
        if listing:
            (EXPECTED_DIR / f"{archive}.lta.txt").write_text(listing)
        for real in resolve_fixture_files(archive, FIXTURE_DIR):
            manifest_lines.append(f"{real.name}\t{real.stat().st_size}\t{description}")

    (EXPECTED_DIR / "MANIFEST.tsv").write_text("\n".join(manifest_lines) + "\n")

    print(f"\ndone: {len(succeeded)} ok, {len(failed)} failed")
    if failed:
        for a in failed:
            print(f"  FAIL: {a}")


if __name__ == "__main__":
    main()
