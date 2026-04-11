#!/usr/bin/env python3
"""Generate deterministic corrupt fixture copies for reader error-path tests."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

SOURCES = [
    ROOT / "fixtures/1.54/readme_154_normal.rar",
    ROOT / "fixtures/1.54/readme.EXE",
    ROOT / "fixtures/1.54/random.rar",
    ROOT / "fixtures/rarvm/archives/delta_4ch_rar393.rar",
]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_case(out_dir: Path, source: Path, suffix: str, data: bytes, rows: list[str]) -> None:
    name = f"{source.stem}.{suffix}{source.suffix}"
    target = out_dir / name
    target.write_bytes(data)
    rows.append(f"{name}\t{len(data)}\t{sha256(data)}\tderived from {source.relative_to(ROOT)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("out_dir", type=Path, help="Directory to receive generated corrupt fixtures")
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    rows = ["# path\tsize\tsha256\tnote"]
    for source in SOURCES:
        data = source.read_bytes()
        write_case(args.out_dir, source, "trunc1", data[:1], rows)
        write_case(args.out_dir, source, "trunc-half", data[: len(data) // 2], rows)
        mutated = bytearray(data)
        offset = min(32, len(mutated) - 1)
        mutated[offset] ^= 0x40
        write_case(args.out_dir, source, "bitflip", bytes(mutated), rows)

    (args.out_dir / "MANIFEST.tsv").write_text("\n".join(rows) + "\n")
    print(f"wrote {len(rows) - 1} negative fixtures to {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
