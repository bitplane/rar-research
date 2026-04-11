# WinRAR 1.54 Fixtures

Generated with WinRAR 1.54 from `_refs/rarbins/wrar154.zip`.

These archives use the RAR 1.5 container (`Rar!` marker) with `UNP_VER = 15`.
They are intended as historical fixtures for the shared RAR 1.3/1.5 adaptive
Huffman + LZ codec (`Unpack15`) and related archive framing. They do not cover
the older RAR 1.3 `RE~^` container.

Public backup of the generator binaries:
https://archive.org/details/old_winrar_binaries

## Files

| File | Purpose | Notes |
|------|---------|-------|
| `readme_154_normal.rar` | Single-file compressed archive. | Contains `README.md`, method `0x33`, `UNP_VER = 15`. |
| `readme_154_store_solid.rar` | Single-file solid-archive variant. | Same payload as `readme_154_normal.rar`; main archive flags differ. The file is still method `0x33`, so this is not a stored-method fixture. |
| `readme.EXE` | RAR 1.5 self-extracting archive. | RAR marker starts at offset `0x1c5b`. |
| `doc_154_best.rar` | Multi-file compressed text corpus. | Contains the repository documentation files with DOS 8.3 names plus `README.md`. |
| `random.rar`, `random.r00`, `random.r01` | Multi-volume archive. | Split `random.bin`; first and middle parts carry placeholder CRC `0xffffffff`, final part carries the real CRC. |

`expected/README.md` is the extracted single-file payload from
`readme_154_normal.rar`. Use it as the byte-for-byte comparison target for
`readme_154_normal.rar`, `readme_154_store_solid.rar`, and `readme.EXE`.
`expected/doc_154_best.manifest.tsv` and `expected/random.manifest.tsv` record
the expected extracted file sizes, CRC32 values, and SHA-256 values for the
larger fixtures without committing duplicate payload trees.

## SHA-256

```text
faa2b922d3ac7ae5bb4d7660e2b7da5169ab79295574f1ba63bd72b59d2c407a  doc_154_best.rar
ee311322dd9a650ce027346e9886199666516bb80296cc534c5205a4f6dc0a8c  random.rar
fcb20f59a99d4a7458ccd0d65c5c2503ccce79c06c3fb59f35f2e4c7a9f268ab  random.r00
3fb7deabfdae35b89cddc7dc6ba11e0d9723051d029a9d91bb81c6c926e495da  random.r01
586cf810349fe6ed0327988d905ae4e240037595abadbebbf8849e002ca49484  readme.EXE
d9d504970d565c784cf9146715f2c68885fd7b4ce825b35e0023ea1cf3c26445  readme_154_normal.rar
9cc2f52e29e5d00ac3bff156c1f89bffc052bcfaccab29f77e62026f1d7afaa0  readme_154_store_solid.rar
f3d51f2d627fdb20b876e61f9e7772d7b8bf869ca03aeea49d8a9b3153de6eff  expected/README.md
9a2d762912f0895ab97dee82459b98b2099845bdefc7d2fc470feaca31373537  expected/doc_154_best.manifest.tsv
aaadc72ddd05d5dd222451ef3dad9947e9ac9a71302173c53a9e0680b6f48fc5  expected/random.manifest.tsv
```
