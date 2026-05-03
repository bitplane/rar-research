# RAR 2.02 Fixtures

Small RAR 2.x archives from the external `rarfile` corpus, copied here as
stable test vectors for the RAR 1.5-4.x container and Unpack20/encryption
paths.

These archives use the `Rar!\x1a\x07\x00` marker, `UnpVer = 20`, and an
old-format main header with `MHD_COMMENT` (`0x0002`). The embedded main-header
comment subblock is included in `HEAD_SIZE` but not in the main-header
`HEAD_CRC`; this is the historical CRC boundary behavior these fixtures pin.

## Files

| Archive | Password | Contents | Spec exercise |
|---|---|---|---|
| `rar202-comment-nopsw.rar` | none | Stored `FILE1.TXT` and `FILE2.TXT` (`file1\r\n`, `file2\r\n`) | Old-format main-header comment extension plus stored RAR 2.x members. |
| `rar202-comment-psw.rar` | `password` | Encrypted compressed `FILE1.TXT` and `FILE2.TXT` (`file1\r\n`, `file2\r\n`) | RAR 2.0 `CRYPT_RAR20` Feistel encryption, Unpack20 decode, and old-format main-header comment extension. |

## SHA-256

```text
16715eaf4163092733579f8a616e83a78e3feae2b8bae15b1538bc88bf0dbc86  rar202-comment-nopsw.rar
ec28322015b8102cc569c5564cdfecf1eb634350e2a67258c4230858182b8663  rar202-comment-psw.rar
```
