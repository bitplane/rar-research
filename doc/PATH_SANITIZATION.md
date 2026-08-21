# RAR Path Sanitization (Extraction Side)

Rules an extractor must apply to filenames from file headers before
creating files on the host filesystem. Every rule here is a known
attack vector; skipping any of them makes the extractor exploitable by
a hostile archive.

Canonical sources: a public RAR reader, `extinfo.cpp`, `filefn.cpp`,
`ulinks.cpp`, `win32lnk.cpp`, `extract.cpp`.

## 1. Threat model

An attacker crafts a RAR archive; a victim extracts it with default
options. The attacker wants to:

1. **Write outside the extraction root.** Via `..`, absolute paths,
   UNC paths, or drive letters in the stored filename.
2. **Write outside the extraction root via symlink redirection.**
   Extract a symlink first (`link → ..` or `link → /etc`), then
   extract a file whose path traverses the link.
3. **Overwrite system files.** Via Windows reserved names (`CON`,
   `AUX`, …), path components that alias existing files, or
   alternate data streams (`file.txt:hidden`).
4. **Cause denial of service via path components.** Via excessively
   long paths, control characters in names, trailing spaces/dots on
   Windows.

The extractor's job is to ensure every extracted file lives strictly
inside `ExtrPath` (the user-chosen destination directory) regardless
of what the archive says.

## 2. Path forms RAR archives may contain

RAR stores filenames as the platform-native form of the creating OS:

| Source OS | Separator | Characteristics |
|-----------|-----------|-----------------|
| MS-DOS / Windows | `\` | CP437/OEM or UTF-16; drive letters possible; reserved names; forbidden chars `?*<>|":` |
| Unix | `/` | Byte strings, historically OEM or UTF-8; any byte except `/` and NUL is legal |
| RAR 5.0 | `/` | UTF-8 always (`RAR5_FORMAT_SPECIFICATION.md` §8); encoder converts native to `/` |
| OS/2, BeOS, macOS (pre-X) | `/` or `\` | Rare; treat as Unix-ish |

Regardless of source, a conforming reader **must** normalize to the
host separator internally (`UnixSlashToDos` at `pathfn.cpp:553-582`
does this in compatible RAR reader) before applying any path-component logic.
Comparing `../` against `..\\` across OS boundaries is the #1 source
of sanitizer bypasses.

## 3. ConvertPath — the path-level sanitizer

Entry point: `ConvertPath` (`pathfn.cpp:40-88`). Applied to every
filename from a file header before any filesystem operation. The
algorithm:

### 3.1 Step 1 — strip everything up to the last `/../`

```
# S is a byte string with no terminator. Every index below is inside it.
DestPos = 0
for I in range(len(S) - 2):                  # I, I+1 and I+2 are always valid
    if IsPathDiv(S[I]) and S[I+1] == '.' and S[I+2] == '.' and \
       (I + 3 == len(S) or IsPathDiv(S[I+3])):
        DestPos = min(I + 4, len(S))
```

The bound is on the loop, not on the reads. The C original walks to the end of
the string and leans on two things to stay in bounds: a NUL terminator, so
`S[I+1]` is always readable, and short-circuit `&&`, so `S[I+2]` and `S[I+3]`
are never reached once an earlier test fails. Translate it literally into a
language with eager slicing (`S[I+1..I+3]`) or without short-circuiting and
`"a/"` reads two bytes past the end. Stopping the loop at `len(S) - 3` removes
the dependency instead of documenting it.

After this, `S[DestPos..]` is the substring starting after the last
`..` component. A name like `good/../../bad/evil.txt` becomes
`bad/evil.txt`; a name like `a/b/..` becomes the empty string.

**Why "last" and not "all"**: An attacker cannot reconstruct a working
traversal by layering them. Stripping up to the last one guarantees
no `/../` survives in the output regardless of source.

### 3.2 Step 2 — strip leading drive letters, UNC roots, and `.`/`..` sequences

```
while DestPos < len(S):
    if S[DestPos+1] is ':':
        DestPos += 2                # strip "C:"
    if S[DestPos] == S[DestPos+1] == '/':
        # Count two more separators and skip past the second one. This is
        # what neutralises Win32 device namespaces as well as UNC roots.
        skip to just after the second path-sep found at DestPos+2 or later
    skip any run of "/", "./", "../", ".../"
    if nothing was stripped: break
```

This handles:

- `C:\foo\bar` → `foo\bar`
- `\\server\share\foo` → `foo`
- `\\?\C:\Windows\foo` → `Windows\foo`
- `\??\C:\Windows\foo` → `Windows\foo`
- `./foo` → `foo`
- `../foo` (any leading `..` that survived step 1) → `foo`
- `.\.\.\foo` → `foo`

Looping until fixed-point catches layered attacks like `C:\..\C:\foo`.

**Win32 device namespaces fall out of the same rule.** `\\?\C:\Windows\foo`
and `\??\C:\Windows\foo` open with two separators, so the UNC branch takes
them, and the count-two-more-separators walk lands after the drive component
exactly as it would for a share name. A reader that instead matches the literal
shape `//server/share` leaves the prefix in place, the path stays absolute, and
the archive writes wherever it likes. Do not special-case the device prefixes.
Do count separators rather than matching a pattern.

### 3.3 Post-conditions

After `ConvertPath`, the filename:

- Does not start with a path separator.
- Does not start with a drive letter.
- Does not contain `/../` or `\..\` anywhere.
- Does not start with `./` or `../`.

It may still contain individual dots as filename components (`foo.bar`
is fine) or trailing dots/spaces (handled in §4).

### 3.4 Not enough on its own

`ConvertPath` handles syntactic traversal but cannot handle semantic
traversal via symlinks — see §5. It also doesn't filter character-set
issues — see §4.

## 4. MakeNameUsable — character-level sanitizer

Entry point: `MakeNameUsable` (`pathfn.cpp:514-550`). Applied per
character after `ConvertPath`.

### 4.1 Always-forbidden characters

Replace with `_`:

| Chars | Reason |
|-------|--------|
| `? *` | Wildcards — Windows filesystem forbids; Unix allows but risky |
| `< > \| "` | Shell metacharacters; Windows forbids |
| Control chars (U+0000..U+001F) | Terminal injection, filesystem oddities |

### 4.2 Windows-only

| Case | Action |
|------|--------|
| `:` anywhere except position 1 | Replace with `_` (position 0–1 is a valid drive letter, already stripped in §3) |
| `CON`, `AUX`, `NUL`, `PRN`, `COM1..9`, `LPT1..9` as a path component, with or without an extension | Prepend an underscore to that component: `CON` becomes `_CON`, `dir/con.txt` becomes `dir/_con.txt`. Reserved device names. |
| Trailing space or dot on a component | Strip — Windows silently drops these and the result may collide with a sibling |

`MakeNameUsable` in compatible RAR reader covers the character replacement; reserved-
name handling is done at file-open time by the OS itself, but a
defence-in-depth implementation should rename proactively.

### 4.3 Unix-only

No character is truly forbidden except NUL and `/`. But an extractor
should still apply the Windows rules if the destination is a
network share mounted from Windows, because the file creation will
fail otherwise — `MakeNameUsable(Name, Extended=true)` enables the
stricter mode (`pathfn.cpp:518, 526`).

### 4.4 Unicode normalization across hosts

macOS stores filenames decomposed, so `ä` is `a` followed by `U+0308`, and an
archive written there carries the decomposed form. NTFS accepts those bytes but
a great deal of Windows software will not find the file afterwards, because it
looks for the precomposed `U+00E4`.

A reader extracting on Windows normalizes to precomposed form when the header
says the archive came from a Unix host (`HostOS` 1 in RAR 5.0). Not otherwise:
a Windows-written name is already in the form its filesystem expects, and
normalizing it anyway would rename files nobody asked to rename.

This is a compatibility measure, not a security one. It runs after the path
sanitization above, never instead of any of it, and normalizing must not
re-introduce a separator or a `..` component. Check the result again if the
normalizer is not one you control.

### 4.5 The "extended" mode toggle

`MakeNameUsable` takes an `Extended` parameter (`bool`). In non-
extended mode it only strips `?` and `*`. In extended mode it adds
the full reserved set. compatible RAR reader enables extended mode only after a
filesystem error suggests the destination doesn't tolerate the
name (`pathfn.cpp:521-544`).

A security-focused reader should always run in extended mode: the
"native Unix drive" fast path that compatible RAR reader favours for performance can
miss cross-filesystem extraction.

## 5. Symlink protection

Symlinks are the most dangerous filename-level feature. A hostile
archive can emit a symlink followed by a file whose path traverses
the link, escaping the extraction root even after `ConvertPath` has
stripped every `..`.

### 5.1 Relative-target validation: `IsRelativeSymlinkSafe`

Entry point: `extinfo.cpp:107-155`. Called before creating any
symlink:

```
inputs:
    SrcName      — link path from archive header (e.g. "dir/link")
    PrepSrcName  — SrcName prefixed with ExtrPath (the real disk path)
    TargetName   — target stored in the link record

rules:
    1. Reject if SrcName or TargetName is a full-root path
       (absolute path or UNC). See §5.3.
    2. Count ".." components in TargetName → UpLevels.
    3. If UpLevels > 0 and any component of PrepSrcName is already
       a symlink on disk → reject (§5.4 below).
    4. Compute AllowedDepth = depth of SrcName (excluding "." / "..")
       and PrepAllowedDepth = depth of the part of PrepSrcName below
       ExtrPath.
    5. Accept iff AllowedDepth >= UpLevels and PrepAllowedDepth >= UpLevels.
```

Rule 5 is the key invariant: a link at depth N from the extraction
root can safely target `../` at most N times. A link at `dir/link`
(depth 2) may target `../../foo` but not `../../../foo` — the latter
would escape.

### 5.2 Symlink-chain detection: `LinksToDirs`

Called before extracting any file once a symlink-with-`..` has been seen. Walks
every component of the target path on disk, looking for a component that already
exists and is a symlink.

**It does not only detect.** The reference reader deletes the offending link,
the symlink on Unix or the junction on Windows, and carries on so the extraction
can create a real directory in its place. Only a failed deletion stops the file
being extracted. So a reader built from a detect-and-abort reading of this
section refuses archives the official tools extract, whenever a directory in the
archive collides with a link left on disk by an earlier one.

Deleting is also the more dangerous of the two, since it removes something the
user already had. A reader that would rather not may refuse the entry instead,
and should say so: the security property is the same either way, and only the
outcome for the user differs. What is not safe is following the link.

The attack it defeats:

```
1. Extract: "dir/link1"  →  ".."
2. Extract: "dir/link1/link2"  →  ".."
3. Extract: "dir/link1/link2/poc.txt"
```

Each individual link passes `IsRelativeSymlinkSafe` in isolation, but
the chain compounds. `LinksToDirs` catches step 3 by seeing that
`dir/link1` on disk is a symlink.

For performance, compatible RAR reader caches the last checked path (`LastChecked`
parameter) and only re-validates tail components; a fresh
implementation can do the same.

### 5.3 Absolute and UNC targets: always reject

`IsFullRootPath` (`pathfn.cpp:695-698`) returns true for:

- Windows drive-letter paths (`C:\foo`)
- Windows UNC (`\\server\share\foo`)
- Unix absolute paths (`/foo`)

A symlink whose target is a full-root path is always unsafe and
always rejected, regardless of depth accounting. An extractor that
wants to allow absolute symlinks (rare, but some use cases exist)
must require the user to opt in explicitly and warn per link.

### 5.4 Windows hardlinks and junctions

Windows has three link-like constructs: symlinks, hardlinks, and
NTFS reparse points (junctions). RAR 5.0's File System Redirection
Record (§8 of the RAR 5.0 spec) can express any of them via the
`RedirType` field (1 = unix symlink, 2 = windows symlink, 3 = junction,
4 = hardlink, 5 = file copy).

Apply the same safety checks to all three:

- **Symlinks**: §5.1–§5.3 above.
- **Junctions**: target is always absolute — reject unless the user
  opts in (`win32lnk.cpp:67-69` gates on `IsRelativeSymlinkSafe`).
- **Hardlinks**: target is a sibling file already in the archive.
  Validate the target has already been extracted and lives inside
  `ExtrPath`; never create a hardlink to anything outside the root.

**Writing the reparse point.** Once a junction or Windows symlink has passed
those checks, the target string still cannot be written to disk as it stands.
The `REPARSE_DATA_BUFFER` holds two strings and they are not the same text:

- `SubstituteName`, the path the kernel resolves, prefixed `\??\`. A UNC
  target becomes `\??\UNC\server\share`, with the leading two separators
  replaced by that prefix.
- `PrintName`, the path shown to the user, in ordinary Win32 form with no
  prefix: `C:\target`, or `\\server\share`.

Put the header's raw target in both and the link is created but resolves to
nothing, in Explorer and everywhere else. Derive `PrintName` from
`SubstituteName` by dropping a leading `\??\` and, if `UNC\` follows it,
replacing that with a single leading separator.

This is a Windows API requirement rather than an archive-format one, so it
belongs after the security checks above, never in place of them: the target
is still attacker-controlled text at this point.

## 6. Destination path handling

The extractor builds `DestFileName = ExtrPath + "/" + SanitizedArchiveName`.
At this point several filesystem-facing checks apply:

### 6.1 Containment check

After `ConvertPath` the sanitized name is relative, but the runtime
can still produce a path outside `ExtrPath` if `ExtrPath` itself
contains symlinks or is manipulated by another process. A defence-
in-depth reader does a final `realpath(DestFileName)` and verifies
`realpath` starts with `realpath(ExtrPath)`. compatible RAR reader does not do this
explicitly — it relies on the upstream guards — but modern extractors
should.

### 6.2 Race-safe creation

The `realpath` containment check is still time-of-check/time-of-use sensitive:
another process can replace an intermediate directory with a symlink between
validation and file creation. A hardened extractor should create files by
walking the destination path one component at a time from an already-open
extraction-root directory handle:

1. Open `ExtrPath` as a directory handle.
2. For every intermediate component, open it relative to the current directory
   handle with "no symlink following" semantics where the platform supports it
   (`openat(..., O_NOFOLLOW|O_DIRECTORY)` on Unix-like systems).
3. Create the final file relative to the final directory handle with exclusive
   creation or the user's selected overwrite policy.
4. If a component is discovered to be a symlink/reparse point while opening,
   re-run the link-safety rules in §5 or reject.

If the host platform does not expose race-safe relative opens, the extractor
should still run the `realpath` check immediately before opening and immediately
after creation, and should refuse to run as a privileged user by default.

### 6.3 Length limit

Windows imposes `MAX_PATH = 260` on most API entry points. compatible RAR reader uses
the `\\?\` prefix to bypass this limit, but a reader that doesn't
should truncate component names before exceeding 260 bytes. Always
reject archives that produce a single component longer than 255 bytes
(Windows and most Unix filesystems' per-component limit).

### 6.4 Collision handling

Sanitization can make two different archive names map to the same destination:
`AUX` and `_AUX`, `foo.txt.` and `foo.txt`, or two Unicode spellings that the
destination filesystem normalizes to the same name. A reader must detect this
before writing. Safe policies are:

- reject the later entry and report the sanitized collision;
- rename the later entry with a deterministic suffix; or
- require an explicit overwrite mode from the caller.

Silent overwrite is unsafe. It lets a hostile archive hide a safe-looking file
behind a later dangerous one after sanitization.

### 6.5 Archive text printed to a terminal

Names are not the only attacker-controlled text a reader prints. Archive and
file comments (`CMT` service records in RAR 5.0, `COMM_HEAD` blocks before it)
are shown during listing and extraction, and they are raw bytes chosen by
whoever wrote the archive.

A terminal reading those bytes will act on the escape sequences in them. The
one worth naming is key redefinition, `ESC [ {key} ; "{string}" p`, which binds
a key on some terminals to a string of the attacker's choosing. The user then
runs it themselves at some later prompt. Cursor movement and screen clearing
are milder but still let a comment forge output that looks like the reader's.

Two workable policies:

- **Refuse the comment.** Scan for `ESC [`, and if the bytes that follow are
  digits and semicolons up to a `"`, treat the whole comment as unsafe and
  print nothing. This is what the reference reader does, and it targets the
  key-redefinition sequence specifically.
- **Escape everything.** Print printable characters as they are and render
  every other byte as `\xNN`. Strictly stronger, since it makes no judgement
  about which sequences are dangerous, and it keeps the comment readable.

Either way the comment must not reach the terminal unfiltered. This applies to
listing output as much as extraction, since listing is what a cautious user
runs *first* on an archive they do not trust.

## 7. `-ep` switch modes (compatible RAR reader CLI behaviour)

compatible RAR reader exposes four path-handling modes via `-ep` (`options.hpp:12-15`):

| Mode | Switch | Effect |
|------|--------|--------|
| `EXCL_SKIPWHOLEPATH` | `-ep` | Discard the entire path — extract to flat `ExtrPath/filename`. |
| `EXCL_BASEPATH` | `-ep1` (default for create) | Strip the base path component. |
| `EXCL_SAVEFULLPATH` | `-ep2` | Keep the full path without the drive letter. |
| `EXCL_ABSPATH` | `-ep3` | Keep the absolute path including the drive letter — **only valid with `x` (extract with full paths) on Windows** (`extract.cpp:1244`). |

`-ep3` is the one security-relevant mode: it deliberately allows
archived absolute paths. A writer making the choice for users should
default to `-ep1` (the compatible RAR reader default) and make `-ep3` require an
explicit flag plus confirmation.

## 8. Attack scenarios and required mitigations

| Attack | Example filename in archive | Mitigation |
|--------|-----------------------------|------------|
| Path traversal (simple) | `../../../etc/passwd` | §3.1 `ConvertPath` |
| Path traversal (drive) | `C:\Windows\System32\evil.dll` | §3.2 `ConvertPath` |
| Path traversal (UNC) | `\\attacker.local\share\poc.exe` | §3.2 `ConvertPath` |
| Layered traversal | `foo/./bar/../../etc/shadow` | §3.1 last-occurrence rule |
| Symlink target | `link → /etc`, then `link/passwd` | §5.1 `IsRelativeSymlinkSafe` |
| Symlink chain | see §5.2 example | §5.2 `LinksToDirs` |
| Windows reserved | `CON.txt`, `LPT1` | §4.2 rename |
| Trailing-dot collision | `foo.txt.` vs `foo.txt` | §4.2 strip |
| Wildcard filename | `*` in the path | §4.1 replace |
| Control-char filename | `\x1b[2J` (clear screen) | §4.1 replace |
| Hardlink outside root | RedirType=4 target=`/etc/passwd` | §5.4 validate |
| Junction to C:\ | RedirType=3 target=`C:\` | §5.4 reject non-relative |
| Long path DoS | 50000-char single component | §6.3 reject |
| UTF-8 bidi trick | `file.exe‮txt.cod` | §4.1 (filter controls U+202E et al.) |
| Terminal key redefinition in a comment | `ESC [ 0 ; "rm -rf ~" p` | §6.5 filter or refuse |
| TOCTOU symlink swap | replace `dir` with a symlink after validation | §6.2 race-safe creation |
| Sanitized-name collision | `AUX` plus `_AUX` | §6.4 collision handling |

## 9. Implementation checklist

A correct extractor must:

- [ ] Normalize path separator to host style before any string comparison.
- [ ] Call `ConvertPath`-equivalent on every filename from the archive.
- [ ] Call `MakeNameUsable`-equivalent with `Extended=true` always.
- [ ] Validate every symlink's target against `IsRelativeSymlinkSafe`.
- [ ] Call `LinksToDirs`-equivalent before file creation once any
      symlink with `..` has been extracted in the current run.
- [ ] Reject absolute or UNC symlink targets unless the user opts in.
- [ ] Validate hardlink / junction targets same as symlinks.
- [ ] Final `realpath(DestFileName).starts_with(realpath(ExtrPath))`
      before opening the file for write.
- [ ] Prefer race-safe relative opens from an extraction-root directory
      handle; otherwise re-check containment immediately before and after
      creation.
- [ ] Detect collisions after sanitization and reject/rename/explicitly
      overwrite; never silently overwrite.
- [ ] Reject single path components > 255 bytes.
- [ ] Log all rejected names — never silently skip, the user needs to
      know the archive was hostile.

Skipping any of these points has real CVEs attached. Between 2015 and
2024 at least five different RAR extractors shipped with one or more
of these missing, with public exploitation ([CVE-2018-20250],
[CVE-2022-30333], [CVE-2023-40477] among others).

[CVE-2018-20250]: https://nvd.nist.gov/vuln/detail/CVE-2018-20250
[CVE-2022-30333]: https://nvd.nist.gov/vuln/detail/CVE-2022-30333
[CVE-2023-40477]: https://nvd.nist.gov/vuln/detail/CVE-2023-40477
