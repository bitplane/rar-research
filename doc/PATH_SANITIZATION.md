# RAR Path Sanitization (Extraction Side)

Rules an extractor must apply to filenames from file headers before
creating files on the host filesystem. Every rule here is a known
attack vector; skipping any of them makes the extractor exploitable by
a hostile archive.

Oracles: `fixtures/paths/` holds fourteen archives, one hostile or
borderline filename each, with a table of what `UnRAR.exe` 7.21 on
Windows and `unrar` 7.20 on Linux actually produce from them. Anything
in §3, §4 or §7 stated as behaviour was measured there. Where a claim
was not measured, it says so.

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
host separator internally before applying any path-component logic.
Comparing `../` against `..\\` across OS boundaries is the #1 source
of sanitizer bypasses.

### 2.1 A backslash in a RAR 5.0 name is not a separator

RAR 5.0 uses `/` for every host, so a `\` that survives into a stored
name is a literal character rather than a delimiter. Unix filesystems
accept it in a filename; Windows does not.

Measured by patching a literal backslash into a stored name and
extracting on both platforms:

| Archive `Host OS` | Extracted on Windows | Extracted on Unix |
|---|---|---|
| 1 (Unix) | `sub_nam.t` | `sub\nam.t`, kept verbatim |
| 0 (Windows) | `sub_nam.t` | `sub_nam.t` |

Two rules fall out, and both are needed:

- **Extracting on Windows, always replace `\` with `_`.** The character
  cannot appear in a Windows filename at all, so the host OS recorded in
  the archive makes no difference.
- **Extracting on Unix, replace `\` with `_` only for a Windows-host
  entry.** There the byte came from a platform where it meant a
  separator, and flattening it keeps one archive entry as one file. For
  a Unix-host entry, keep it: it is an ordinary character in the name
  the archive means to store.

**Never split a RAR 5.0 name on `\`.** Doing so invents directory levels
the archive never declared, turning one entry into a tree. Verified
against RAR 7.12 and WinRAR 7.21's `Rar.exe`: neither creates a
directory for `sub\nam.t` on any host-OS combination.

This is the opposite of RAR 1.5–4.x, where `\` **is** the wire
separator and splitting on it is correct. A reader that shares one path
routine across formats has to branch on the format here.

Traversal is unaffected either way. A name like `..\..\pwn` reaches the
component checks in §3 as either one literal component or as `.._.._pwn`,
and neither escapes the output directory. Measured on RAR 7.12: the
Unix-host form lands as a file named `..\..\pwn` and the Windows-host
form as `.._.._pwn`, both inside the extraction directory.

## 3. ConvertPath — the path-level sanitizer

Applied to every filename from a file header before any filesystem
operation.

**Measured** (`fixtures/paths/traversal_dotdot.rar`, stored name
`../yy.txt`): both `rar712` and `unrar 7.20` list the name unchanged,
then extract it to `./yy.txt` inside the destination. They sanitise and
carry on, reporting `All OK`. rars refuses the entry instead, with
`unsafe archive path`, and writes nothing. Both are safe; the difference
is whether a hostile entry is dropped or silently renamed. Say which
yours does.

The algorithm:

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

Applied per character after `ConvertPath`, and **only on Windows**. Every
rule in this section is a no-op on a Linux extractor: `unrar 7.20` writes
`a:b.txt`, `a*b.txt`, `a\b.txt`, `aux.txt` and `tail. ` out verbatim.
Running these rules unconditionally renames files nobody asked to rename.

Measured on Windows, via `UnRAR.exe` 7.21 under wine:

| Stored name | Extracted as |
|---|---|
| `a:b.txt` | `a_b.txt` |
| `a*b.txt` | `a_b.txt` |
| `a\b.txt` | `a_b.txt` |
| `tail. ` (trailing space) | `tail._` |
| `aux.txt` | `_aux.txt` |
| `d/aux.txt` | `d/_aux.txt` |
| `aux2.txt` | `aux2.txt` |
| `auxx.txt` | `auxx.txt` |

The device-name rule is an **exact match on the stem** before the first
dot, and the underscore prefixes the **component** rather than the path.
`aux2` and `auxx` are not reserved and pass through untouched.

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
| `CON`, `AUX`, `NUL`, `PRN`, `COM0..9`, `LPT0..9` as a path component | Prepend an underscore to that component: `CON` becomes `_CON`, `dir/con` becomes `dir/_con`. Reserved device names. |
| Trailing space or dot on a component | Strip — Windows silently drops these and the result may collide with a sibling |

**The digit range starts at zero.** `COM0` and `LPT0` are device names
too, and a reader sanitises them alongside `1..9`. Measured: `com0` and
`lpt0` both come out as `_com0` and `_lpt0`.

**The match is on the stem, and an extension does not exempt it — on
Windows 10.** This is the one rule here that depends on the host OS
version, and it flips:

| Archived name | Extracted on Windows 10 | On Windows 11 |
|---|---|---|
| `aux` | `_aux` | `_aux` |
| `con` | `_con` | `_con` |
| `aux.txt` | `_aux.txt` | `aux.txt` |
| `con.log` | `_con.log` | `con.log` |

Windows 10 and earlier normalise `aux.txt` to the `AUX` device, so the
rename is load-bearing there. Windows 11 does not, and a reader stops
renaming the extension case. Bare device names are renamed on both.

Measured by extracting the same archives twice from the same reader,
changing only the Windows version the host reports (build 19043 versus
22000) and holding everything else fixed.

**For an implementation, rename in both cases.** The relaxation buys
nothing except fidelity to whichever Windows the extraction happens on,
and a name that survives extraction on Windows 11 can still be a
problem for whatever opens it afterwards. Matching a reference reader
byte-for-byte on this point means making the output depend on the host
OS version, which is worth doing deliberately rather than by accident.

The stem match is exact: `aux2` and `auxx` are not device names and pass
through untouched on either version.

Reserved-name handling is also done at file-open time by the OS itself,
but a defence-in-depth implementation should rename proactively.

### 4.3 Unix-only

No character is truly forbidden except NUL and `/`. But an extractor
should still apply the Windows rules if the destination is a
network share mounted from Windows, because the file creation will
fail otherwise. That is what the "extended" mode in §4.5 is for.

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
the full reserved set. Readers typically enable extended mode only after
a filesystem error suggests the destination will not tolerate the name.

A security-focused reader should always run in extended mode: the
"native Unix drive" fast path that compatible RAR reader favours for performance can
miss cross-filesystem extraction.

## 5. Symlink protection

Symlinks are the most dangerous filename-level feature. A hostile
archive can emit a symlink followed by a file whose path traverses
the link, escaping the extraction root even after `ConvertPath` has
stripped every `..`.

### 5.1 Relative-target validation: `IsRelativeSymlinkSafe`

Called before creating any symlink:

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

Called before extracting **every** file, from the first one, not only
after a symlink-with-`..` has turned up in the archive. Walks every
component of the target path on disk, looking for a component that
already exists and is a symlink.

The reason it cannot be gated on having seen a suspicious symlink is
that the link need not come from *this* archive. Extract one archive
that plants `link -> /somewhere/else`, then a second containing
`link/payload.txt` into the same directory, and the second archive
carries nothing suspicious at all: no symlink, no `..`, one perfectly
ordinary file in a subdirectory. The trap was laid by the first run and
sprung by the second.

Measured on that exact pair. Both reference readers extract the second
archive into a **real directory**, having replaced the planted symlink,
and `payload.txt` stays inside the destination. Neither reports anything
unusual; both say `All OK`.

rars refuses the first archive instead, with `unsafe archive redirection
target`, because the link points outside the extraction root (§5.3). The
symlink is never created, so there is nothing to convert when the second
archive arrives, and the payload lands in a real directory too. Same
outcome by a stricter route, and it is worth knowing which of the two a
given reader does.

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

A full-root-path test returns true for:

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
  opts in, gated on the same relative-target test as §5.1.
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

There are **five** modes, not four. Names below are what each stores
when archiving; the effects were measured with `rar712` on Linux and
`Rar.exe` 7.21 under wine, archiving `c:\eptest\sub\f.txt` or the
equivalent relative tree.

| Switch | Stores | Notes |
|---|---|---|
| *(none)* | the path exactly as typed on the command line | Relative argument stays relative; absolute argument is stored absolute, minus the leading separator. **This is the default, not `-ep1`.** |
| `-ep` | `f.txt` | Whole path discarded, flat names. |
| `-ep1` | `base/sub/deep/f.txt` | Drops the directory named on the command line. A no-op when the argument is already relative to the cwd. Ignored if the argument has wildcards. |
| `-ep2` | `eptest/sub/f.txt` | Expands to the full path, minus drive letter and leading separator. |
| `-ep3` | `c_/eptest/sub/f.txt` | Expands to the full path **including the drive**, with the colon replaced by `_`. Windows only. WinRAR's manual adds that UNC `\\server\share` becomes `__server\share`; not measured here. |
| `-ep4<path>` | `sub/deep/f.txt` for `-ep4base/` | Strips a given prefix, compared against the name already prepared for storage. |

`-ep2` is listed in neither `rar712 -?` nor `unrar -?`, but both accept
it. `-ep4` is listed in both and was missing from this table entirely.

**The security-relevant part is the `-ep3` substitution.** Extracting
with `-ep3` turns `c_` back into `c:` and `__server` back into
`\\server`, and ignores any destination the user gave. So an entry named
`c_/windows/system32/x.dll` is completely inert under a normal extract
and becomes an absolute write the moment someone passes `-ep3`.

`fixtures/paths/latent_drive_underscore.rar` carries exactly that shape.
Both readers extract it to a literal `c_/x.txt` directory under the
destination, on Windows and on Linux, with no warning of any kind. A
sanitizer that looks for `:` or a leading separator will not see it.

A writer choosing for users should leave the default alone and make
`-ep3` require an explicit flag plus confirmation.

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
