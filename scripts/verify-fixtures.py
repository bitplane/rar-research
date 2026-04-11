#!/usr/bin/env python3
"""Verify committed RAR fixture checksums, RARVM blobs, and optional extracts."""

from __future__ import annotations

import argparse
import binascii
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]

RARVM_EXPECTED = {
    "E8": (53, "AD576887"),
    "E8E9": (57, "3CD7E57E"),
    "ITANIUM": (120, "3769893F"),
    "DELTA": (29, "0E06077D"),
    "RGB": (149, "1C2C5DC8"),
    "AUDIO": (216, "BC85E701"),
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def crc32_file(path: Path) -> str:
    crc = 0
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            crc = binascii.crc32(chunk, crc)
    return f"{crc & 0xffffffff:08X}"


def ok(message: str) -> None:
    print(f"ok: {message}")


def fail(errors: list[str], message: str) -> None:
    errors.append(message)
    print(f"FAIL: {message}")


def verify_readme_sha_tables(errors: list[str]) -> None:
    for readme in [
        ROOT / "fixtures/1.402/README.md",
        ROOT / "fixtures/1.54/README.md",
        ROOT / "fixtures/rarvm/README.md",
    ]:
        count = 0
        for line in readme.read_text().splitlines():
            match = re.match(r"^([0-9a-f]{64})\s+(.+)$", line)
            if not match:
                continue
            count += 1
            expected, rel = match.groups()
            path = readme.parent / rel
            if not path.exists():
                fail(errors, f"{readme}: missing {rel}")
                continue
            actual = sha256_file(path)
            if actual != expected:
                fail(errors, f"{readme}: {rel} sha256 {actual} != {expected}")
        ok(f"{readme.relative_to(ROOT)} sha256 table ({count} files)")


def load_rarvm_blobs() -> dict[str, bytes]:
    text = (ROOT / "fixtures/rarvm/captured-blobs.md").read_text()
    code = text.split("```python", 1)[1].split("```", 1)[0]
    ns: dict[str, object] = {}
    exec(code, ns)
    return ns["RAR3_STANDARD_FILTER_BYTECODE"]  # type: ignore[return-value]


def verify_rarvm(errors: list[str]) -> None:
    blobs = load_rarvm_blobs()
    for name, blob in blobs.items():
        expected_len, expected_crc = RARVM_EXPECTED[name]
        actual_crc = f"{binascii.crc32(blob) & 0xffffffff:08X}"
        actual_xor = 0
        for b in blob:
            actual_xor ^= b
        if len(blob) != expected_len or actual_crc != expected_crc or actual_xor != 0:
            fail(
                errors,
                f"RARVM {name}: len={len(blob)} crc={actual_crc} xor={actual_xor:02X}",
            )
    ok("RARVM blob length, CRC32, and XOR")

    log_count = 0
    for log in sorted((ROOT / "fixtures/rarvm/capture-logs").glob("*.jsonl")):
        for line_no, line in enumerate(log.read_text().splitlines(), 1):
            if not line.strip():
                continue
            log_count += 1
            row = json.loads(line)
            blob = blobs[row["standard_filter"]]
            expected_crc = f"{binascii.crc32(blob) & 0xffffffff:08X}"
            if not row["xor_ok"]:
                fail(errors, f"{log}:{line_no}: xor_ok is false")
            if row["code_size"] != len(blob):
                fail(errors, f"{log}:{line_no}: code_size mismatch")
            if row["crc32"].upper() != expected_crc:
                fail(errors, f"{log}:{line_no}: crc32 mismatch")
            if row["code_hex"] != blob.hex():
                fail(errors, f"{log}:{line_no}: code_hex mismatch")
    ok(f"RARVM capture logs ({log_count} entries)")


def read_manifest(path: Path) -> list[tuple[str, int, str, str]]:
    rows: list[tuple[str, int, str, str]] = []
    for line in path.read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        name, size, crc, sha = line.split("\t")
        rows.append((name, int(size), crc.upper(), sha.lower()))
    return rows


def verify_committed_expected_payloads(errors: list[str]) -> None:
    expected = [
        (ROOT / "fixtures/1.402/expected/README", 2016, "e70e00c521ee53176d194cfc66d2c284e340d50c07667776071b220ed956570e"),
        (ROOT / "fixtures/1.54/expected/README.md", 4198, "f3d51f2d627fdb20b876e61f9e7772d7b8bf869ca03aeea49d8a9b3153de6eff"),
    ]
    for path, size, sha in expected:
        if path.stat().st_size != size:
            fail(errors, f"{path.relative_to(ROOT)} size mismatch")
        if sha256_file(path) != sha:
            fail(errors, f"{path.relative_to(ROOT)} sha256 mismatch")
    ok("committed expected single-file payloads")

    for manifest in [
        ROOT / "fixtures/1.54/expected/doc_154_best.manifest.tsv",
        ROOT / "fixtures/1.54/expected/random.manifest.tsv",
    ]:
        rows = read_manifest(manifest)
        if not rows:
            fail(errors, f"{manifest.relative_to(ROOT)} is empty")
        ok(f"{manifest.relative_to(ROOT)} syntax ({len(rows)} entries)")


def verify_fixture_manifests(errors: list[str]) -> None:
    """Check generated fixture manifests that use name/size/description rows.

    Older extraction manifests use name/size/crc/sha and are validated by
    read_manifest() callers above. The generated fixture inventories use the
    same .tsv extension, but only promise that the named committed artifact
    exists and has the recorded byte size.
    """
    checked = 0
    rows = 0
    for manifest in sorted(ROOT.glob("fixtures/**/expected/MANIFEST.tsv")):
        checked += 1
        fixture_dir = manifest.parent.parent
        for line_no, line in enumerate(manifest.read_text().splitlines(), 1):
            if not line.strip() or line.startswith("#"):
                continue
            rows += 1
            parts = line.split("\t")
            if len(parts) < 3:
                fail(errors, f"{manifest.relative_to(ROOT)}:{line_no}: expected at least 3 tab-separated fields")
                continue
            name, size_text = parts[0], parts[1]
            try:
                expected_size = int(size_text)
            except ValueError:
                fail(errors, f"{manifest.relative_to(ROOT)}:{line_no}: invalid size {size_text!r}")
                continue
            path = fixture_dir / name
            if not path.exists():
                fail(errors, f"{manifest.relative_to(ROOT)}:{line_no}: missing {path.relative_to(ROOT)}")
                continue
            actual_size = path.stat().st_size
            if actual_size != expected_size:
                fail(
                    errors,
                    f"{manifest.relative_to(ROOT)}:{line_no}: {name} size {actual_size} != {expected_size}",
                )
    ok(f"generated fixture manifests ({checked} files, {rows} rows)")


RR_INLINE_FIXTURES = [
    # (name, rec_pct, archive_size, expected NR, expected shard_size)
    # archive_size = byte count of archive prefix before the RR service header.
    ("rar721_rr5_64k.rar",    5,  65681,  3, 1604),
    ("rar721_rr10_16k.rar",  10,  16531,  1, 1182),
    ("rar721_rr10_64k.rar",  10,  65681,  6, 1604),
    ("rar721_rr10_128k.rar", 10, 131223, 12, 2122),
    ("rar721_rr20_64k.rar",  20,  65681, 13, 1604),
    ("rar721_rr50_64k.rar",  50,  65681, 32, 1604),
]


def compute_inline_rr_dims(rec_pct: int, archive_size: int) -> tuple[int, int, int, int, int]:
    """Encoder-side formula. See `doc/INTEGRITY_WRITE_SIDE.md §4.6.2`.
    Returns (NR, shard_size, header_size, group_count, D).
    """
    pct = max(0, min(100, rec_pct))
    if archive_size >= 200 * 1024:
        D = 200
    else:
        D = max(1, (archive_size + 1023) // 1024)
    NR = (2 * pct * D) // 200
    if NR > D:
        NR = D
    if NR == 0 and archive_size < 200 * 1024:
        NR = 1
    group_count = (archive_size + D - 1) // D
    group_count += group_count & 1
    scale_factor = max(1, (group_count + 0xFFFF) // 0x10000)
    header_size = (D * 8 + 0x48) * scale_factor
    shard_size = header_size + group_count
    return NR, shard_size, header_size, group_count, D


CRC64_XZ_POLY = 0xC96C5795D7870F42
CRC64_INIT = 0xFFFFFFFFFFFFFFFF


def crc64_xz(data: bytes) -> int:
    """CRC-64/XZ: ECMA-182 reflected, init 0xFF..FF, final xor 0xFF..FF."""
    crc = CRC64_INIT
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ CRC64_XZ_POLY
            else:
                crc >>= 1
    return (crc ^ CRC64_INIT) & 0xFFFFFFFFFFFFFFFF


def verify_rr_inline_layout(errors: list[str]) -> None:
    """Fixture sanity check (not a validity rule) for the RAR 5.0
    inline RR `{RB}` layout. Spec ref: `doc/INTEGRITY_WRITE_SIDE.md
    §4.6`. Per fixture:

    1. The (NR, shard_size, header_size) values predicted by the
       encoder formula in §4.6.2 from (rec_pct, archive_size) match
       what is actually on disk.
    2. The file contains exactly `NR` occurrences of `{RB}` and they
       are all `shard_size` bytes apart. (Sanity check on this
       fixture set — RS parity bytes are uniform, so future
       fixtures may incidentally contain `{RB}` outside the RR data
       area without being invalid. A real reader uses the RR
       service header's `DataSize` field, not a global magic
       search.)
    3. Each chunk's fixed-prefix fields agree:
        - `total_size` (uint32 LE at chunk +0x0c) == shard_size
        - `header_size` (uint32 LE at chunk +0x10) == header_size
       This anchors the fixed-prefix part of the layout. The
       structured header after `+0x14` is a flat sequence of
       fixed-width LE fields (no vint encoding); §4.6.1.1 maps
       each one. The verifiable subset (9 fields) is asserted in
       step 5 below.
    4. The CRC-64/XZ at chunk +0x04..+0x0b matches the CRC of chunk
       bytes +0x0c..+shard_size (= shard_size - 12 bytes).
    5. Structured-header fields whose values are derivable from
       (rec_pct, archive_size, shard_index) match: version_a/b
       (constants 0x01), chunk_position (= 0 for inline RR),
       protected_archive_size, group_count, shard_size_u64, D, NR,
       and shard_index. The three remaining encoder-internal
       running-state fields are not asserted.
    """
    rr_dir = ROOT / "fixtures/5.0/rr_inline"
    for name, rec_pct, archive_size, nr_expected, shard_size_expected in RR_INLINE_FIXTURES:
        path = rr_dir / name
        if not path.exists():
            fail(errors, f"{path.relative_to(ROOT)}: missing")
            continue

        nr_pred, ss_pred, hs_pred, gc_pred, _D = compute_inline_rr_dims(rec_pct, archive_size)
        if (nr_pred, ss_pred) != (nr_expected, shard_size_expected):
            fail(
                errors,
                f"{path.relative_to(ROOT)}: formula predicts "
                f"NR={nr_pred} shard_size={ss_pred} but manifest expects "
                f"NR={nr_expected} shard_size={shard_size_expected}",
            )
            continue

        data = path.read_bytes()
        rb_offsets = [m.start() for m in re.finditer(b"\\{RB\\}", data)]
        if len(rb_offsets) != nr_pred:
            fail(
                errors,
                f"{path.relative_to(ROOT)}: found {len(rb_offsets)} {{RB}} "
                f"occurrences, expected NR={nr_pred}",
            )
            continue

        for i in range(1, nr_pred):
            gap = rb_offsets[i] - rb_offsets[i - 1]
            if gap != ss_pred:
                fail(
                    errors,
                    f"{path.relative_to(ROOT)}: shard {i} starts {gap} bytes "
                    f"after shard {i-1}, expected shard_size={ss_pred}",
                )

        for i, off in enumerate(rb_offsets):
            total_size = int.from_bytes(data[off + 0x0c:off + 0x10], "little")
            header_size = int.from_bytes(data[off + 0x10:off + 0x14], "little")
            if total_size != ss_pred:
                fail(
                    errors,
                    f"{path.relative_to(ROOT)} shard {i}: "
                    f"total_size_field={total_size} != shard_size={ss_pred}",
                )
            if header_size != hs_pred:
                fail(
                    errors,
                    f"{path.relative_to(ROOT)} shard {i}: "
                    f"header_size_field={header_size} != predicted={hs_pred}",
                )

            crc_field = int.from_bytes(data[off + 0x04:off + 0x0c], "little")
            crc_actual = crc64_xz(data[off + 0x0c:off + ss_pred])
            if crc_field != crc_actual:
                fail(
                    errors,
                    f"{path.relative_to(ROOT)} shard {i}: "
                    f"CRC-64/XZ field=0x{crc_field:016x} != computed=0x{crc_actual:016x}",
                )

            # Structured header fields (§4.6.1.1)
            checks = [
                (off + 0x14, 1, 1, "version_a"),
                (off + 0x15, 1, 1, "version_b"),
                (off + 0x16, 8, 0, "chunk_position"),
                (off + 0x22, 8, archive_size, "protected_archive_size"),
                (off + 0x2a, 8, gc_pred, "group_count"),
                (off + 0x32, 8, ss_pred, "shard_size_u64"),
                (off + 0x3a, 2, _D, "D"),
                (off + 0x3c, 2, nr_pred, "NR"),
                (off + 0x3e, 2, i, "shard_index"),
            ]
            for field_off, width, expected, fname in checks:
                actual = int.from_bytes(data[field_off:field_off + width], "little")
                if actual != expected:
                    fail(
                        errors,
                        f"{path.relative_to(ROOT)} shard {i}: "
                        f"{fname}={actual} != expected={expected}",
                    )
    ok(f"RAR 5.0 inline RR layout (formula + chunk prefix + CRC-64/XZ + structured header, {len(RR_INLINE_FIXTURES)} fixtures)")


def verify_head3_sign_layout(errors: list[str]) -> None:
    """Validate the WinRAR 2.90 HEAD3_SIGN shape fixture against
    `doc/RAR15_40_FORMAT_SPECIFICATION.md §10.9.1`.

    Per fixture:
    1. The block is locatable (TYPE = 0x79) inside the archive.
    2. HEAD_FLAGS == 0x4000.
    3. Body math: HEAD_SIZE - 15 == NAME1_SIZE + NAME2_SIZE + 0xa7.
    4. NAME1 matches the expected archive name.
    5. NAME2 is empty (BSS-zero in the patched-build fixture; a real
       registered build would populate it).
    6. HASH*_LEN + HASH*_BYTES + PADDING fills the body deterministically.
    7. HEAD_CRC == zlib.crc32(13-byte fixed prefix) & 0xFFFF.
    """
    fixture = ROOT / "fixtures/1.5-4.x/wrar290/wrar290_head3_sign_patched.rar"
    if not fixture.exists():
        fail(errors, f"{fixture.relative_to(ROOT)}: missing")
        return
    data = fixture.read_bytes()

    # Walk blocks until HEAD3_SIGN found.
    off = 7  # skip 7-byte marker
    sign_off = None
    while off + 7 <= len(data):
        htype = data[off + 2]
        hflags = int.from_bytes(data[off + 3:off + 5], "little")
        hsize = int.from_bytes(data[off + 5:off + 7], "little")
        if htype == 0x79:
            sign_off = off
            break
        block = hsize
        if hflags & 0x8000 and htype == 0x74:
            block += int.from_bytes(data[off + 7:off + 11], "little")
        off += block

    if sign_off is None:
        fail(errors, f"{fixture.relative_to(ROOT)}: no HEAD3_SIGN (0x79) block found")
        return

    head_crc = int.from_bytes(data[sign_off:sign_off + 2], "little")
    head_flags = int.from_bytes(data[sign_off + 3:sign_off + 5], "little")
    head_size = int.from_bytes(data[sign_off + 5:sign_off + 7], "little")
    name1_size = int.from_bytes(data[sign_off + 0x0b:sign_off + 0x0d], "little")
    name2_size = int.from_bytes(data[sign_off + 0x0d:sign_off + 0x0f], "little")
    name1 = bytes(data[sign_off + 0x0f:sign_off + 0x0f + name1_size])

    if head_flags != 0x4000:
        fail(errors, f"HEAD3_SIGN: HEAD_FLAGS=0x{head_flags:04x} != 0x4000")
    if head_size - 15 != name1_size + name2_size + 0xa7:
        fail(errors, f"HEAD3_SIGN: body math fails — {head_size}-15 != {name1_size}+{name2_size}+0xa7")
    if name1 != b"test.rar":
        fail(errors, f"HEAD3_SIGN: NAME1={name1!r} != b'test.rar' (expected archive name)")
    if name2_size != 0:
        fail(errors, f"HEAD3_SIGN: NAME2_SIZE={name2_size} != 0 (BSS-zero in patched fixture)")

    # Walk the 3 hash fields + padding
    body_off = sign_off + 0x0f + name1_size + name2_size
    hash_lens = []
    for _ in range(3):
        L = data[body_off]
        hash_lens.append(L)
        body_off += 1 + L
    pad_len = (sign_off + head_size) - body_off
    expected_pad = 0xa4 - sum(hash_lens)
    if pad_len != expected_pad:
        fail(errors, f"HEAD3_SIGN: padding={pad_len} != 0xa4-sum(hash_lens)={expected_pad}")
    if not all(b == 0 for b in data[body_off:sign_off + head_size]):
        fail(errors, "HEAD3_SIGN: padding bytes not all zero")

    # CRC scope: 13 bytes from HEAD_TYPE through end of NAME2_SIZE
    crc_input = data[sign_off + 2:sign_off + 15]
    expected_crc = binascii.crc32(crc_input) & 0xFFFF
    if head_crc != expected_crc:
        fail(errors, f"HEAD3_SIGN: HEAD_CRC=0x{head_crc:04x} != zlib.crc32(13-byte prefix)&0xFFFF=0x{expected_crc:04x}")

    ok(f"WinRAR 2.90 HEAD3_SIGN shape fixture (§10.9.1 layout, NAME1=archive-name, body math, CRC scope)")


RAR140_AV_FIXTURES = [
    # (filename, expected MHD_AV bit set?)
    ("rar140_noav_baseline.rar", False),
    ("rar140_av_patched.rar",    True),
]
RAR140_AV_PREFIX = bytes.fromhex("1a 69 6d 02 da ae".replace(" ", ""))


def verify_rar140_av_layout(errors: list[str]) -> None:
    """Validate the RAR 1.40 AV-in-main-header layout against
    `doc/RAR13_FORMAT_SPECIFICATION.md §4.1`. Per fixture:

    1. Marker is the 4-byte `RE~^` constant.
    2. The MHD_AV flag (bit 5 = 0x20) is set iff the fixture is the
       AV-bearing one.
    3. For the AV-bearing fixture: HeadSize > 7, the payload at +7
       is a length-prefixed body, the first 6 bytes after the length
       prefix are the fixed magic `1a 69 6d 02 da ae`, and the body
       length matches AVSize.
    """
    fixture_dir = ROOT / "fixtures/1.402/rar140_av"
    for name, expect_av in RAR140_AV_FIXTURES:
        path = fixture_dir / name
        if not path.exists():
            fail(errors, f"{path.relative_to(ROOT)}: missing")
            continue
        data = path.read_bytes()
        if data[:4] != b"RE~^":
            fail(errors, f"{path.relative_to(ROOT)}: marker={data[:4]!r} != b'RE~^'")
            continue
        head_size = int.from_bytes(data[4:6], "little")
        flags = data[6]
        has_av = bool(flags & 0x20)
        if has_av != expect_av:
            fail(errors, f"{path.relative_to(ROOT)}: MHD_AV bit = {has_av}, expected {expect_av} (Flags=0x{flags:02x})")
        if not expect_av:
            if head_size != 7:
                fail(errors, f"{path.relative_to(ROOT)}: HeadSize={head_size}, expected 7 for noav")
            continue
        if head_size <= 9:
            fail(errors, f"{path.relative_to(ROOT)}: HeadSize={head_size}, expected > 9 for AV-bearing")
            continue
        av_size = int.from_bytes(data[7:9], "little")
        if head_size != 9 + av_size:
            fail(errors, f"{path.relative_to(ROOT)}: HeadSize={head_size} != 9+AVSize={9+av_size}")
        body = data[9:7 + head_size]
        if body[:6] != RAR140_AV_PREFIX:
            fail(errors, f"{path.relative_to(ROOT)}: AV prefix={body[:6].hex()} != {RAR140_AV_PREFIX.hex()}")
    ok(f"RAR 1.40 AV-in-main-header layout (§4.1, {len(RAR140_AV_FIXTURES)} fixtures)")


def as_wine_path(path: Path) -> str:
    return "Z:" + str(path.resolve()).replace("/", "\\")


def run_unrar(args: argparse.Namespace, command: list[str]) -> None:
    if args.wine_prefix:
        cmd = ["env", f"WINEPREFIX={args.wine_prefix}", "wine", args.unrar_exe] + command
    else:
        cmd = [args.unrar_exe] + command
    subprocess.run(cmd, cwd=ROOT, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def archive_arg(args: argparse.Namespace, path: Path) -> str:
    return as_wine_path(path) if args.wine_prefix else str(path)


def verify_extract(
    errors: list[str],
    args: argparse.Namespace,
    archive: Path,
    out_dir: Path,
    password: str | None = None,
) -> None:
    command = ["x", "-o+", "-y"]
    if password:
        command.append(f"-p{password}")
    command += [archive_arg(args, archive), archive_arg(args, out_dir) + ("\\" if args.wine_prefix else os.sep)]
    try:
        run_unrar(args, command)
    except subprocess.CalledProcessError as exc:
        fail(errors, f"extract failed for {archive.relative_to(ROOT)}: {exc}")


def verify_optional_extraction(errors: list[str], args: argparse.Namespace) -> None:
    if not args.unrar_exe:
        print("skip: extraction checks need --unrar-exe")
        return
    temp = Path(tempfile.mkdtemp(prefix="rar-fixture-verify."))
    try:
        cases_1402 = [
            ("README_store.rar", None),
            ("README.RAR", None),
            ("README_password=password.rar", "password"),
        ]
        expected_1402 = ROOT / "fixtures/1.402/expected/README"
        for archive_name, password in cases_1402:
            out = temp / archive_name
            verify_extract(errors, args, ROOT / "fixtures/1.402" / archive_name, out, password)
            extracted = out / "README"
            if extracted.exists() and extracted.read_bytes() != expected_1402.read_bytes():
                fail(errors, f"{archive_name}: extracted payload mismatch")

        cases_154 = ["readme_154_normal.rar", "readme_154_store_solid.rar", "readme.EXE"]
        expected_154 = ROOT / "fixtures/1.54/expected/README.md"
        for archive_name in cases_154:
            out = temp / archive_name
            verify_extract(errors, args, ROOT / "fixtures/1.54" / archive_name, out)
            extracted = out / "README.md"
            if extracted.exists() and extracted.read_bytes() != expected_154.read_bytes():
                fail(errors, f"{archive_name}: extracted payload mismatch")

        doc_out = temp / "doc_154_best"
        verify_extract(errors, args, ROOT / "fixtures/1.54/doc_154_best.rar", doc_out)
        for name, size, crc, sha in read_manifest(ROOT / "fixtures/1.54/expected/doc_154_best.manifest.tsv"):
            path = doc_out / name
            if not path.exists():
                fail(errors, f"doc_154_best missing {name}")
                continue
            if path.stat().st_size != size or crc32_file(path) != crc or sha256_file(path) != sha:
                fail(errors, f"doc_154_best {name}: manifest mismatch")

        random_out = temp / "random"
        verify_extract(errors, args, ROOT / "fixtures/1.54/random.rar", random_out)
        for name, size, crc, sha in read_manifest(ROOT / "fixtures/1.54/expected/random.manifest.tsv"):
            path = random_out / name
            if not path.exists():
                fail(errors, f"random volume missing {name}")
                continue
            if path.stat().st_size != size or crc32_file(path) != crc or sha256_file(path) != sha:
                fail(errors, f"random volume {name}: manifest mismatch")
        ok("optional historical UnRAR extraction checks")
    finally:
        shutil.rmtree(temp)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--unrar-exe", help="Native UnRAR executable, or Windows UnRAR.exe when --wine-prefix is set")
    parser.add_argument("--wine-prefix", help="Wine prefix for a Windows UnRAR.exe")
    args = parser.parse_args()

    errors: list[str] = []
    verify_readme_sha_tables(errors)
    verify_committed_expected_payloads(errors)
    verify_rarvm(errors)
    verify_fixture_manifests(errors)
    verify_rr_inline_layout(errors)
    verify_head3_sign_layout(errors)
    verify_rar140_av_layout(errors)
    verify_optional_extraction(errors, args)

    if errors:
        print(f"\n{len(errors)} verification failure(s)")
        return 1
    print("\nall fixture checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
