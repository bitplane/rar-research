# junrar RAR4 Password Fixtures

Copied from the Apache-2.0 `junrar` test corpus via `rar-test-data`.

| Fixture | Password | Purpose |
|---|---|---|
| `rar4-password-junrar.rar` | `junrar` | RAR4 per-file encrypted compressed member `file1.txt`; plaintext is `file1\n`, CRC32 `0xe229f704`. |
| `rar4-encrypted-junrar.rar` | `junrar` | RAR4 header-encrypted archive with the same `file1.txt` payload. |
| `rar4-only-file-content-encrypted.rar` | `test` | RAR4 per-file encrypted member with compact Unicode filename `新建文本文档.txt`; plaintext is `aaaaaaaaaa`, CRC32 `0x4c11cdf0`. |

RAR 3.93 tests all three archives OK with the listed passwords.

## SHA-256

```text
d8c3117434d142cfdb479ffd08c6239126f7805e0547907578a41b3740668735  rar4-encrypted-junrar.rar
aff90e1e40d84663554a5cab09d54a5ba5547afb7df9460a72c9b2351bfa4cbc  rar4-only-file-content-encrypted.rar
6eaba6677e056caee5548669eed13ac932e6a8ac150d6c5c69ae95d23f002588  rar4-password-junrar.rar
```
