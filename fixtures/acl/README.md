# Windows ACL fixtures

Three archives of the same one file, each written by a different WinRAR
generation with `-ow` (save file owner and security data). They exist to pin
the `SECURITY_DESCRIPTOR` record's wire format across the two RAR 1.5-4.x
shapes and the RAR 5.0 one, without reference to any decoder's source.

Generated under wine from `Rar.exe` extracted out of the installers in
`_refs/rarbins/`:

```
7zz x wrar290.exe wrar300.exe winrar-x64-721b1.exe
printf 'acl payload for the wire-format oracle\n' > FILE.TXT
wine Rar.exe a -ow <archive> FILE.TXT
```

Wine synthesises a `SECURITY_DESCRIPTOR` from the file's Unix permissions, so
the descriptor's *contents* are wine's invention. Its size, its checksum and
every framing field around it are real WinRAR output, which is what the wire
format needs.

| Fixture | Writer | Record shape |
|---|---|---|
| `acl_wrar290.rar` | WinRAR 2.90 | Block type `0x77`, `SubType` `0x0104`, `Level` 0 |
| `acl_wrar300.rar` | WinRAR 3.00 | Block type `0x7a` |
| `acl_rar721.rar` | WinRAR 7.21 | RAR 5.0 service header named `ACL` |

## What they establish

The 2.90 and 3.00 archives differ in **block type**, which is the thing worth
having a fixture for: 2.90 writes the old subblock type and 3.00 writes the
service type. An implementation that reads only `0x7a` silently ignores every
RAR 2.x ACL.

The 2.90 subblock header is 24 bytes:

```
+0  HEAD_CRC   uint16
+2  HEAD_TYPE  uint8   0x77
+3  HEAD_FLAGS uint16  0xC000
+5  HEAD_SIZE  uint16  24
+7  ADD_SIZE   uint32  84      packed descriptor bytes that follow
+11 SubType    uint16  0x0104
+13 Level      uint8   0
+14 UnpSize    uint32  160     unpacked descriptor size
+18 UnpVer     uint8   20
+19 Method     uint8   0x33
+20 EACRC      uint32  0x4EE02053
```

`UnpSize` and `EACRC` start at +14, not +7: the subblock header is 14 bytes
before the ACL-specific tail begins, not the 7 of a plain block header.

The RAR 5.0 archive stores the same descriptor for the same file and reports
`UnpSize` 160 and `DataCRC` `0x4EE02053`, identical to the 2.90 values. Two
encoders twenty-five years apart agreeing on both numbers is what confirms
those two fields mean what §5.4 says they mean.

## SHA-256

```text
6a10a78c92757acf902453a6dd829fa67ae897b4bfd9473468e2703cfddedea0  acl_wrar290.rar
9d3ae52714cc6a7858c4f0653c593fa2a08b0f1d8ab18e9ba8b49c8fe814fe74  acl_wrar300.rar
3a54a233f7bc7a3c074700dc0e7277119145d202aff655ef3158f8f6133a7ce9  acl_rar721.rar
```

## No NTFS stream (`STM`) counterpart

The same route does not produce alternate-data-stream records. Wine has no
NTFS streams: creating `NAME:stream` makes a literal file with a colon in it,
and `Rar.exe a -os` archives the host file alone. Getting an `STM` fixture
needs a real NTFS volume.
