"""Generate RAR 1.5–4.x edge-case fixtures using WinRAR 3.00 and 4.20 under wine.

Closes the "RAR 1.5–4.x: header-encrypted (-hp), FHD_LARGE (>4 GiB),
FHD_UNICODE Form 1, EXT_TIME nibble groups" line from
`doc/IMPLEMENTATION_GAPS.md`. We skip the FHD_LARGE case because it requires
a >4 GiB input file; everything else is a routine archive-creation run.

Two prefixes are exercised:
- `winrar300/` — RAR 3.00 (May 2002): -hp, -p, comments, RR, multi-vol,
  solid, FHD_UNICODE.
- `winrar420/` — RAR 4.20 (Jun 2012): -ts switches for high-precision
  EXT_TIME nibble groups; everything else from 3.00 also still works.

Output layout mirrors `fixtures/rarvm/`: one dir per WinRAR version, each
archive named with the version suffix (e.g. `encrypted_rar300.rar`).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_BASE = REPO_ROOT / "fixtures" / "1.5-4.x"
SOURCES_DIR = FIXTURE_BASE / "sources"

PREFIXES = {
    "rar300": REPO_ROOT / "_refs" / "wineprefixes" / "winrar300",
    "rar420": REPO_ROOT / "_refs" / "wineprefixes" / "winrar420",
}

# (version_tag, basename, switches, inputs, description)
FIXTURES = [
    # --- RAR 3.00 ---
    ("rar300", "encrypted_per_file_rar300.rar",
        ["a", "-m3", "-ppassword", "-ep", "-cfg-", "-idp"],
        ["hello.txt"],
        "Per-file AES-128 encryption (-p). Password-derived key+IV per RAR 3.x KDF (262144 SHA-1 iters with the _rar29 quirk; see ENCRYPTION §4)."),
    ("rar300", "header_encrypted_rar300.rar",
        ["a", "-m3", "-hppassword", "-ep", "-cfg-", "-idp"],
        ["hello.txt"],
        "Whole-archive header encryption (-hp). MainHead has MHD_PASSWORD (0x0080); all blocks after the marker are AES-128-CBC encrypted with per-block IV16 prefix."),
    ("rar300", "with_comment_rar300.rar",
        ["a", "-m3", "-zcomment.txt", "-ep", "-cfg-", "-idp"],
        ["hello.txt"],
        "Archive comment via -z. RAR 3.x stores it as HEAD3_NEWSUB (0x7a) with name 'CMT' or as inline HEAD3_CMT depending on encoder flags."),
    ("rar300", "with_recovery_rar300.rar",
        ["a", "-m3", "-rr10", "-ep", "-cfg-", "-idp"],
        ["bigtext_64k.bin"],
        "10% recovery record. RAR 3.x emits HEAD3_NEWSUB 'RR' (NEWSUB_HEAD = 0x7a), not the older PROTECT_HEAD 0x78."),
    ("rar300", "multivol_oldnaming_rar300.rar",
        ["a", "-m3", "-v8k", "-vn", "-ep", "-cfg-", "-idp"],
        ["bigtext_64k.bin"],
        "Multi-volume (-v8k → 8 KB volumes), -vn forces OLD-style naming (.r00, .r01, …). MainHead carries MHD_VOLUME (0x0001) but NOT MHD_NEWNUMBERING."),
    ("rar300", "multivol_newnaming_rar300.rar",
        ["a", "-m3", "-v8k", "-ep", "-cfg-", "-idp"],
        ["bigtext_64k.bin"],
        "Same input, RAR 3.0+ default: new-style naming (.part01.rar, …). MainHead carries MHD_NEWNUMBERING (0x0010)."),
    ("rar300", "solid_rar300.rar",
        ["a", "-m3", "-s", "-ep", "-cfg-", "-idp"],
        ["hello.txt", "tiny.txt", "bigtext_64k.bin"],
        "Solid archive: MainHead has MHD_SOLID (0x0008) and FileHead carries FHD_SOLID (0x0010) on every file but the first."),

    # --- RAR 4.20 ---
    ("rar420", "ext_time_rar420.rar",
        ["a", "-m3", "-tsm,c,a", "-ep", "-cfg-", "-idp"],
        ["hello.txt"],
        "Extended-time block (LHD_EXTTIME 0x1000) with mtime+ctime+atime nibble groups. Each time gets a flag-nibble (4 bits) followed by 0..3 bytes of nanosecond precision."),
    ("rar420", "header_encrypted_rar420.rar",
        ["a", "-m3", "-hppassword", "-ep", "-cfg-", "-idp"],
        ["hello.txt"],
        "RAR 4.20 -hp variant for cross-version-of-encoder coverage (RAR 3.x format, but 4.20 encoder defaults may differ on flag bits)."),
    # FHD_UNICODE Form 1 fixture is intentionally NOT generated here. Wine on
    # Linux hands cp437-encoded argv to Rar.exe, dropping CJK characters and
    # mapping Latin-1 chars to their cp437 equivalents — so RAR sees a "fully
    # representable" filename and skips the FHD_UNICODE encoding. A genuine
    # FHD_UNICODE fixture needs either a real Windows host or a wine
    # configuration with a UTF-8-aware ANSI codepage. Documented in
    # IMPLEMENTATION_GAPS.md.
]


def make_sources():
    SOURCES_DIR.mkdir(parents=True, exist_ok=True)
    (SOURCES_DIR / "hello.txt").write_bytes(b"Hello, RAR 3.x fixture world.\n")
    (SOURCES_DIR / "tiny.txt").write_bytes(b"AAAAAAAA\n")
    (SOURCES_DIR / "comment.txt").write_bytes(b"This is the archive comment.\n")
    # Reuse the 64 KB lorem from the RAR 5.0 fixture batch
    rar5_src = REPO_ROOT / "fixtures" / "5.0" / "sources" / "bigtext_64k.bin"
    if rar5_src.exists():
        shutil.copyfile(rar5_src, SOURCES_DIR / "bigtext_64k.bin")
    else:
        # Fallback: regenerate inline
        import random
        rng = random.Random(1)
        words = [b"lorem", b"ipsum", b"dolor", b"sit", b"amet"]
        out = bytearray()
        while len(out) < 64 * 1024:
            out += rng.choice(words) + b" "
        (SOURCES_DIR / "bigtext_64k.bin").write_bytes(bytes(out[:64 * 1024]))
    # (No Unicode filename source — wine on Linux clobbers it before reaching
    # Rar.exe; see comment near the rar420 FIXTURES block.)


def run_rar(prefix_path, args, cwd):
    env = os.environ.copy()
    env["WINEPREFIX"] = str(prefix_path)
    env["WINEDEBUG"] = "-all"
    rar = prefix_path / "drive_c" / "Program Files (x86)" / "WinRAR" / "Rar.exe"
    cmd = ["wine", str(rar)] + args
    # RAR 3.x output is OEM codepage 437, not UTF-8 — capture as bytes and decode lossy
    result = subprocess.run(cmd, cwd=cwd, env=env,
                            capture_output=True, timeout=60)
    result.stdout_text = result.stdout.decode("cp437", errors="replace")
    result.stderr_text = result.stderr.decode("cp437", errors="replace")
    return result


def generate_one(version_tag, archive, switches, inputs, description):
    target_dir = FIXTURE_BASE / version_tag
    target_dir.mkdir(parents=True, exist_ok=True)
    expected_dir = target_dir / "expected"
    expected_dir.mkdir(exist_ok=True)

    work = FIXTURE_BASE / ".work"
    work.mkdir(exist_ok=True)
    # Per-archive isolation so accidental file collisions don't carry over
    for f in work.iterdir():
        if f.is_file():
            f.unlink()

    # Stage inputs + comment file (for -z<file>)
    for inp in inputs:
        src = SOURCES_DIR / inp
        if not src.exists():
            print(f"  ! missing source: {src}")
            return False
        shutil.copyfile(src, work / inp)
    for sw in switches:
        if sw.startswith("-z") and len(sw) > 2:
            csrc = SOURCES_DIR / sw[2:]
            if csrc.exists():
                shutil.copyfile(csrc, work / sw[2:])

    args = switches + [archive] + inputs
    result = run_rar(PREFIXES[version_tag], args, work)
    if result.returncode != 0:
        print(f"  ! rar failed (rc={result.returncode}): {result.stderr_text[:200]}")
        return False

    # Discover produced files. Priority:
    # 1. multi-volume new-style (.partNN.rar + optional .rev)
    # 2. primary .rar + multi-volume old-style (.r00, .r01, ...)
    # 3. just the primary .rar
    base = archive[:-4] if archive.endswith(".rar") else archive
    new_style = sorted(work.glob(f"{base}.part*.rar")) + \
                sorted(work.glob(f"{base}.part*.rev"))
    old_style = sorted(work.glob(f"{base}.r[0-9][0-9]"))
    if new_style:
        candidates = new_style
    elif old_style:
        # Old-style multi-volume: first vol is named exactly `<archive>` (the
        # `.rar` file), subsequent vols are `<base>.r00`, `<base>.r01`, ...
        primary = work / archive
        candidates = ([primary] if primary.exists() else []) + old_style
    else:
        candidates = [work / archive]

    produced = []
    for src in candidates:
        if src.exists():
            shutil.copyfile(src, target_dir / src.name)
            produced.append(src.name)

    if not produced:
        print(f"  ! nothing produced for {archive}")
        return False

    # Capture lt output (use first produced file as the primary archive name)
    lt_target = produced[0]
    listing = run_rar(PREFIXES[version_tag], ["lt", lt_target], work).stdout_text
    (expected_dir / f"{archive}.lt.txt").write_text(listing)
    return True


def resolve_fixture_files(archive, fixture_dir):
    """Map a declared archive name to the real files emitted on disk.

    Multi-volume archives don't produce a file matching the placeholder
    `<base>.rar` name passed to rar when new-style naming is in effect —
    they produce `<base>.part01.rar`, `<base>.part02.rar`, … Old-style
    (`-vn`) produces `<base>.rar` + `<base>.r00` + `<base>.r01` …
    Return the actual files in deterministic order so each manifest row
    points to a real on-disk artifact.
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
    for tag, prefix in PREFIXES.items():
        rar_exe = prefix / "drive_c" / "Program Files (x86)" / "WinRAR" / "Rar.exe"
        if not rar_exe.exists():
            raise SystemExit(f"missing prefix for {tag}: {rar_exe}")

    make_sources()

    succeeded = []
    failed = []
    for entry in FIXTURES:
        version_tag, archive, switches, inputs, description = entry
        print(f"  [{version_tag}] {archive}")
        if generate_one(version_tag, archive, switches, inputs, description):
            succeeded.append(entry)
        else:
            failed.append(archive)

    # Per-version manifest
    by_tag = {}
    for entry in succeeded:
        version_tag, archive, switches, inputs, description = entry
        target_dir = FIXTURE_BASE / version_tag
        for real in resolve_fixture_files(archive, target_dir):
            by_tag.setdefault(version_tag, []).append(
                f"{real.name}\t{real.stat().st_size}\t{description}"
            )
    for tag, lines in by_tag.items():
        manifest = FIXTURE_BASE / tag / "expected" / "MANIFEST.tsv"
        manifest.write_text("\n".join(lines) + "\n")

    print(f"\ndone: {len(succeeded)} ok, {len(failed)} failed")
    for a in failed:
        print(f"  FAIL: {a}")


if __name__ == "__main__":
    main()
