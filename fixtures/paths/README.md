# Path sanitization fixtures

Fourteen tiny stored archives, each carrying one hostile or borderline
filename. They exist so §3, §4 and §7 of `doc/PATH_SANITIZATION.md` can state
what real extractors do rather than describe a decoder's source.

## How they were made

RAR will not archive these names, so each one is patched in after the fact.
Archive a placeholder of the same byte length, overwrite the name in the file
header, recompute the RAR 5.0 header CRC32 over `[4, header_end)`. Both
`rar712` and `unrar 7.20` list every fixture without complaint, so the framing
is valid and only the name is unusual.

| Fixture | Stored name | Probes |
|---|---|---|
| `traversal_dotdot.rar` | `../yy.txt` | §3 syntactic traversal |
| `device_con.rar` | `con` | reserved stem, no extension |
| `device_nul_dat.rar` | `nul.dat` | reserved stem with extension |
| `device_com1.rar` | `com1` | numbered device |
| `device_lpt9.rar` | `lpt9.a` | numbered device with extension |
| `device_aux_txt.rar` | `aux.txt` | reserved stem with extension |
| `device_aux_in_subdir.rar` | `d/aux.txt` | reserved stem below the root |
| `plain_aux2.rar` | `aux2.txt` | near-miss, must pass through |
| `plain_auxx.rar` | `auxx.txt` | near-miss, must pass through |
| `char_colon.rar` | `a:b.txt` | §4 forbidden character |
| `char_star.rar` | `a*b.txt` | §4 wildcard character |
| `char_backslash.rar` | `a\b.txt` | separator inside a component |
| `char_trailing_space.rar` | `tail. ` | trailing space |
| `latent_drive_underscore.rar` | `c_/x.txt` | §7 `-ep3` latent absolute path |

## Measured behaviour

Windows column is `UnRAR.exe` 7.21 under wine; Linux is `unrar` 7.20 native.
Both extract into an empty directory with `x -y`.

| Stored name | Windows | Linux |
|---|---|---|
| `../yy.txt` | `./yy.txt` | `./yy.txt` |
| `con` | `_con` | `con` |
| `nul.dat` | `_nul.dat` | `nul.dat` |
| `com1` | `_com1` | `com1` |
| `lpt9.a` | `_lpt9.a` | `lpt9.a` |
| `aux.txt` | `_aux.txt` | `aux.txt` |
| `d/aux.txt` | `d/_aux.txt` | `d/aux.txt` |
| `aux2.txt` | `aux2.txt` | `aux2.txt` |
| `auxx.txt` | `auxx.txt` | `auxx.txt` |
| `a:b.txt` | `a_b.txt` | `a:b.txt` |
| `a*b.txt` | `a_b.txt` | `a*b.txt` |
| `a\b.txt` | `a_b.txt` | `a\b.txt` |
| `tail. ` | `tail._` | `tail. ` |
| `c_/x.txt` | `c_/x.txt` | `c_/x.txt` |

Three things fall out of that table.

**Nothing is applied on Linux.** Every character rule is a Windows-side rule.
An extractor that runs them unconditionally renames files no one asked to
rename.

**The device-name rule is an exact stem match, prefixed per component.**
`aux2` and `auxx` pass through untouched, so it is not a prefix test. `d/aux.txt`
becomes `d/_aux.txt`, so the underscore goes on the component and not on the
path.

**`c_/x.txt` is inert everywhere**, which is the point of having it. It is a
latent absolute path that only `-ep3` turns back into `c:\`, so a sanitizer
scanning for `:` or a leading separator will not flag it.
