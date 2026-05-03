# node-unrar-js RAR4 Fixtures

Source corpus: <https://github.com/YuJianrong/node-unrar.js>.

| Fixture | SHA-256 | Use |
|---|---|---|
| `file_enc_by_name_unknown_password.rar` | `54bd3e3eb16ed6e40b6d305ddb7eec93886246a76716d585d0b187b66712c026` | Metadata and partial extraction oracle. The archive has visible names, one unencrypted stored member (`1File.txt` -> `1File`, CRC32 `0x578a2019`), and two encrypted compressed members (`2中文.txt`, `3Sec.txt`) with unknown passwords. |

This fixture is intentionally **not** an encrypted-payload success oracle until
the passwords for the encrypted members are known.

Password audit: the local `node-unrar-js` corpus directory contains only the
RAR files and license, and no password hints. The upstream
<https://github.com/YuJianrong/node-unrar.js> README and `testFiles/` listing
document the fixture files but do not publish per-file passwords for
`FileEncByName.rar`.
