#!/usr/bin/env python3
"""Scan RAR 1.5-4.x archives for possible Unpack20 audio-block candidates.

This is a triage helper, not a decoder. It walks old-format RAR block headers,
finds FILE_HEAD members with UnpVer 20/26, and reports members whose first two
raw packed bytes have bit 15 set. That raw condition is only a candidate: solid
continuations, encrypted members, and stored files can be false positives
because their first raw bytes are not necessarily a fresh ReadTables20 peek.
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path


RAR15_SIGNATURE = b"Rar!\x1a\x07\x00"
LONG_BLOCK = 0x8000
FHD_SPLIT_BEFORE = 0x0001
FHD_PASSWORD = 0x0004
FHD_SOLID = 0x0010
FHD_LARGE = 0x0100
FILE_HEAD = 0x74


def u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def is_rar_path(path: Path) -> bool:
    suffix = path.suffix.lower()
    return suffix == ".rar" or (
        len(suffix) == 4 and suffix[1] == "r" and suffix[2:].isdigit()
    )


def iter_rar_files(roots: list[Path]):
    """Yield RAR paths under roots, ignoring hidden scratch directories."""

    for root in roots:
        if root.is_file() and is_rar_path(root):
            yield root
        elif root.is_dir():
            for path in root.rglob("*"):
                if any(part.startswith(".") for part in path.relative_to(root).parts):
                    continue
                if path.is_file() and is_rar_path(path):
                    yield path


def flags_label(flags: int, method: int) -> str:
    labels = []
    if flags & FHD_SPLIT_BEFORE:
        labels.append("split-before")
    if flags & FHD_PASSWORD:
        labels.append("encrypted")
    if flags & FHD_SOLID:
        labels.append("solid-continuation")
    if method == 0x30:
        labels.append("stored")
    return ",".join(labels) if labels else "clean-candidate"


def scan_archive(path: Path):
    data = path.read_bytes()
    sig = data.find(RAR15_SIGNATURE)
    if sig < 0:
        return

    pos = sig + len(RAR15_SIGNATURE)
    file_index = 0
    while pos + 7 <= len(data):
        head_type = data[pos + 2]
        flags = u16(data, pos + 3)
        head_size = u16(data, pos + 5)
        if head_size < 7 or pos + head_size > len(data):
            return

        add_size = 0
        if flags & LONG_BLOCK:
            if pos + 11 > len(data):
                return
            add_size = u32(data, pos + 7)

        if head_type == FILE_HEAD and head_size >= 32:
            file_index += 1
            pack_size = u32(data, pos + 7)
            unp_size = u32(data, pos + 11)
            unp_ver = data[pos + 24]
            method = data[pos + 25]
            name_size = u16(data, pos + 26)
            name_start = pos + 32
            if flags & FHD_LARGE:
                if pos + 40 > pos + head_size:
                    return
                pack_size |= u32(data, pos + 32) << 32
                unp_size |= u32(data, pos + 36) << 32
                name_start = pos + 40

            data_start = pos + head_size
            if unp_ver in (20, 26) and pack_size >= 2 and data_start + 2 <= len(data):
                peek = int.from_bytes(data[data_start : data_start + 2], "big")
                if peek & 0x8000:
                    name = data[name_start : name_start + name_size].decode(
                        "latin1", "replace"
                    )
                    yield {
                        "path": path,
                        "file_index": file_index,
                        "name": name,
                        "peek": peek,
                        "flags": flags,
                        "label": flags_label(flags, method),
                        "method": method,
                        "unp_ver": unp_ver,
                        "pack_size": pack_size,
                        "unp_size": unp_size,
                    }

        next_pos = pos + head_size + add_size
        if next_pos <= pos:
            return
        pos = next_pos


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="+", type=Path)
    args = parser.parse_args()

    archive_count = 0
    member_count = 0
    candidates = []
    for path in iter_rar_files(args.roots):
        archive_count += 1
        found = list(scan_archive(path))
        candidates.extend(found)
        member_count += len(found)

    print(f"archives scanned: {archive_count}")
    print(f"raw bit-15 candidates: {member_count}")
    clean = [row for row in candidates if row["label"] == "clean-candidate"]
    print(f"clean candidates: {len(clean)}")
    for row in candidates:
        print(
            f"{row['label']:18} "
            f"peek=0x{row['peek']:04x} "
            f"ver={row['unp_ver']} "
            f"method=0x{row['method']:02x} "
            f"pack={row['pack_size']} "
            f"unp={row['unp_size']} "
            f"file#{row['file_index']} "
            f"name={row['name']!r} "
            f"path={row['path']}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
