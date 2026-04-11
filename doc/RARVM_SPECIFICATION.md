# RARVM Specification (RAR 3.x / 4.x)

RARVM is the small virtual machine used by RAR 3.x/4.x filter records in
Unpack29 streams. Official archives normally use only six stock programs
recognized by length+CRC32 fingerprints, but a robust reader can execute
non-standard bytecode by implementing this VM.

This document covers the VM bytecode and execution semantics. The outer filter
record framing is in `RAR15_40_FORMAT_SPECIFICATION.md` Section 20.2.

Source basis:

- `_refs/7zip/CPP/7zip/Compress/Rar3Vm.cpp` and `.h`
- `_refs/XADMaster/XADRARVirtualMachine.m`
- `_refs/XADMaster/RARVirtualMachine.c` and `.h`

## 1. Machine Model

| Property | Value |
|----------|-------|
| Memory size | `0x40000` bytes |
| Memory mask | `0x3ffff` |
| Work area | `0x00000..0x3bfff` |
| Global area | `0x3c000..0x3dfff` |
| System global size | 64 bytes |
| User global start | `0x3c040` |
| Registers | `R0..R7`, 32-bit |
| Stack pointer | `R7` |
| Flags | Carry `C = 1`, zero `Z = 2`, sign `S = 0x80000000` |

All 32-bit memory loads/stores are little-endian. Register and arithmetic
operations wrap modulo 32 bits. Byte-mode operations read/write only the low
8 bits; byte writes to registers replace the register's low byte and preserve
the high 24 bits.

Memory operands wrap with `address & 0x3ffff`. Implementations should allocate
at least `0x40000` bytes and may add a few guard bytes for unaligned 32-bit
loads at the end, but the architectural address is masked.

## 2. Program Blob

The bytecode stored in a RAR filter record is a program blob:

```text
byte 0: XOR checksum
byte 1..N: compressed RARVM program bitstream
```

The XOR checksum byte must equal the XOR of all following bytes. Equivalently,
the XOR of the entire blob is zero. The CRC32 fingerprints used for standard
filters are computed over the whole blob, including this checksum byte.

Program decoding starts at byte 1.

### Static Data

The first bit of the program bitstream selects whether static data is present:

```text
0: no static data
1: RARVM number (size_minus_1), then size bytes of static data
```

If present, `size = decoded_number + 1`. Static data is copied into VM global
memory before execution, after the current invocation global data. In the common
case the invocation global data is exactly the 64-byte system global area, so
static data starts at `0x3c040`.

### Instruction Stream

After optional static data, instructions are read until the bitstream is
exhausted. Public implementations differ on whether fewer than 8 trailing bits
are enough to attempt another instruction; a conservative decoder should stop
when fewer than 8 bits remain or when the next instruction cannot be fully
decoded.

If the decoded program is empty or is not terminated by an unconditional control
transfer, append an implicit `ret`. This matches XADMaster's behavior and avoids
falling off the end for oddball programs.

## 3. RARVM Number Encoding

RARVM numbers are read MSB-first from the VM bitstream:

| Prefix | Payload | Decoded value |
|--------|---------|---------------|
| `00` | 4 bits | `0..15` |
| `01` | 8 bits | If payload >= 16, the payload. Otherwise `0xffffff00 | (payload << 4) | next4bits`. |
| `10` | 16 bits | 16-bit unsigned value |
| `11` | 32 bits | 32-bit unsigned value |

The special `01` / payload `< 16` form is a compact negative 32-bit constant.
It is mostly useful inside generic VM programs, not in the outer filter-record
fields.

## 4. Opcode Encoding

Each instruction begins with either a short or long opcode:

```text
0 bbb       -> opcode 0..7
1 bbbbb     -> opcode 8..39
```

For opcodes with byte mode, one bit follows the opcode:

```text
0: 32-bit mode
1: 8-bit mode
```

Byte-mode variants use the same encoded opcode, but execute with byte operands.
For clarity this document names them as `.b`, for example `mov.b`.

## 5. Operand Encoding

Operands are decoded according to the instruction's operand count and byte mode.

| Bits | Operand | Meaning |
|------|---------|---------|
| `1 rrr` | register | `R[rrr]` |
| `00 ...` | immediate | 8-bit immediate in byte mode, otherwise RARVM number |
| `010 rrr` | register indirect | memory at `R[rrr]` |
| `0110 rrr number` | indexed absolute | memory at `number + R[rrr]` |
| `0111 number` | absolute | memory at `number` |

Memory addresses are masked with `0x3ffff` for reads and writes. Absolute
addresses should be masked when the operand is prepared; indexed addresses are
masked after adding the register.

Immediate operands are read-only. A program that tries to write to an immediate
operand is invalid.

### Relative Jump Immediates

For one-operand jump/call instructions, an immediate operand is remapped to an
instruction index:

```text
dist = immediate
if dist >= 256:
    target = dist - 256
else:
    if dist >= 136:
        dist -= 264
    elif dist >= 16:
        dist -= 8
    elif dist >= 8:
        dist -= 16
    target = current_instruction_index + dist
```

Register and memory jump operands are not remapped; their runtime value is the
target instruction index.

## 6. Instruction Table

| Opcode | Mnemonic | Operands | Byte mode | Flags |
|-------:|----------|---------:|:---------:|-------|
| 0 | `mov` | 2 | yes | none |
| 1 | `cmp` | 2 | yes | writes |
| 2 | `add` | 2 | yes | writes |
| 3 | `sub` | 2 | yes | writes |
| 4 | `jz` | 1 | no | reads |
| 5 | `jnz` | 1 | no | reads |
| 6 | `inc` | 1 | yes | writes |
| 7 | `dec` | 1 | yes | writes |
| 8 | `jmp` | 1 | no | none |
| 9 | `xor` | 2 | yes | writes |
| 10 | `and` | 2 | yes | writes |
| 11 | `or` | 2 | yes | writes |
| 12 | `test` | 2 | yes | writes |
| 13 | `js` | 1 | no | reads |
| 14 | `jns` | 1 | no | reads |
| 15 | `jb` | 1 | no | reads |
| 16 | `jbe` | 1 | no | reads |
| 17 | `ja` | 1 | no | reads |
| 18 | `jae` | 1 | no | reads |
| 19 | `push` | 1 | no | none |
| 20 | `pop` | 1 | no | none |
| 21 | `call` | 1 | no | none |
| 22 | `ret` | 0 | no | none |
| 23 | `not` | 1 | yes | none |
| 24 | `shl` | 2 | yes | writes |
| 25 | `shr` | 2 | yes | writes |
| 26 | `sar` | 2 | yes | writes |
| 27 | `neg` | 1 | yes | writes |
| 28 | `pusha` | 0 | no | none |
| 29 | `popa` | 0 | no | none |
| 30 | `pushf` | 0 | no | reads |
| 31 | `popf` | 0 | no | writes |
| 32 | `movzx` | 2 | no | none |
| 33 | `movsx` | 2 | no | none |
| 34 | `xchg` | 2 | yes | none |
| 35 | `mul` | 2 | yes | none |
| 36 | `div` | 2 | yes | none |
| 37 | `adc` | 2 | yes | reads+writes |
| 38 | `sbb` | 2 | yes | reads+writes |
| 39 | `print` | 0 | no | none |

## 7. Execution Semantics

Notation:

- `dst` is operand 1.
- `src` is operand 2.
- `width` is 8 or 32 depending on byte mode.
- Arithmetic wraps to `width`.
- `signbit` is `0x80` in byte mode and `0x80000000` in 32-bit mode.

Flag helpers:

```text
set_zs(result):
    Flags = Z if result == 0 else (result & signbit ? S : 0)

set_zsc(result, carry):
    Flags = (carry ? C : 0) | (Z if result == 0 else (result & signbit ? S : 0))
```

Instruction behavior:

| Mnemonic | Behavior |
|----------|----------|
| `mov dst, src` | `dst = src`. |
| `cmp a, b` | Compute `a - b`; set `C` if borrow, `Z` if zero, `S` from result sign. Does not store the result. |
| `add dst, src` | `dst = dst + src`; set `C` on unsigned carry, plus `Z`/`S`. |
| `sub dst, src` | `dst = dst - src`; set `C` on unsigned borrow, plus `Z`/`S`. |
| `inc dst` | `dst = dst + 1`; updates `Z`/`S` only. |
| `dec dst` | `dst = dst - 1`; updates `Z`/`S` only. |
| `xor dst, src` | `dst = dst ^ src`; updates `Z`/`S`, clears `C`. |
| `and dst, src` | `dst = dst & src`; updates `Z`/`S`, clears `C`. |
| `or dst, src` | `dst = dst | src`; updates `Z`/`S`, clears `C`. |
| `test a, b` | Compute `a & b`; updates `Z`/`S`, clears `C`. Does not store. |
| `not dst` | `dst = ~dst`; flags unchanged. |
| `neg dst` | `dst = 0 - dst`; if result is zero set `Z`, otherwise set `C` plus sign if negative. |
| `shl dst, n` | `dst = dst << n`; `C` is the last bit shifted out, plus `Z`/`S`. |
| `shr dst, n` | Logical right shift; `C` is the last bit shifted out, plus `Z`/`S`. |
| `sar dst, n` | Arithmetic right shift; `C` is the last bit shifted out, plus `Z`/`S`. |
| `jmp target` | Set instruction pointer to `target`. |
| `jz target` / `jnz target` | Jump if `Z` set / clear. |
| `js target` / `jns target` | Jump if `S` set / clear. |
| `jb target` / `jae target` | Jump if `C` set / clear. |
| `jbe target` / `ja target` | Jump if `C or Z` set / both clear. |
| `push src` | `R7 -= 4`; store 32-bit `src` at `[R7]`. |
| `pop dst` | Load 32-bit `[R7]` into `dst`; `R7 += 4`. |
| `call target` | Push next instruction index, then jump to `target`. |
| `ret` | If `R7 >= 0x40000`, terminate successfully. Otherwise pop an instruction index from `[R7]` and jump to it. |
| `pusha` | Push `R0..R7` as eight 32-bit values and decrement `R7` by 32. |
| `popa` | Restore `R0..R7` from the `pusha` layout; the saved `R7` value is restored. |
| `pushf` | Push `Flags` as a 32-bit value. |
| `popf` | Pop a 32-bit value into `Flags`. |
| `movzx dst, src` | Read `src` as 8-bit and zero-extend into 32-bit `dst`. |
| `movsx dst, src` | Read `src` as signed 8-bit and sign-extend into 32-bit `dst`. |
| `xchg a, b` | Swap operands. |
| `mul dst, src` | `dst = dst * src`; flags unchanged. |
| `div dst, src` | If `src != 0`, `dst = dst / src`; divide by zero leaves `dst` unchanged. Flags unchanged. |
| `adc dst, src` | `dst = dst + src + (Flags & C)`; updates `C`/`Z`/`S`. |
| `sbb dst, src` | `dst = dst - src - (Flags & C)`; updates `C`/`Z`/`S`. |
| `print` | No-op. |

Instruction-pointer edge case: public readers differ on direct jumps to an
instruction index beyond the decoded command count. A lenient mode can treat
that as successful termination; a strict mode can reject the program. Ordinary
stock filters do not rely on this edge case.

Shift counts of zero are not useful and can expose host-language undefined
behavior in naive implementations. A defensive interpreter should either reject
zero shift counts or emulate the source implementation explicitly.

## 8. Program Invocation

The outer Unpack29 filter record creates a program invocation with:

- Initial registers from the filter record, plus decoder defaults.
- Filter data copied into VM memory at address 0.
- Invocation global data copied to `0x3c000`.
- Optional static data copied after the current invocation global data.

Default register/global setup for filter records:

| Field | Value |
|-------|-------|
| `R0..R2` | Zero unless overridden by register-init mask. |
| `R3` | `0x3c000` system global address. |
| `R4` | Filter block length. |
| `R5` | Stored program execution count. |
| `R6` | Output file position when the filter executes. |
| `R7` | Stack pointer; VM execution initializes it to `0x40000`. |
| global `0x1c` | Filter block size. |
| global `0x20` | Filter output block position. Initialized to zero for a new invocation. |
| global `0x24` | Low 32 bits of output file position. |
| global `0x28` | High 32 bits of output file position. |
| global `0x2c` | Stored program execution count. |
| global `0x30` | Number of user global bytes to preserve after execution. |

After execution, the decoder reads:

```text
output_size = u32(global + 0x1c) & 0x3ffff
output_pos  = u32(global + 0x20) & 0x3ffff
if output_pos + output_size >= 0x40000:
    output_pos = 0
    output_size = 0
```

The filtered bytes returned to the unpacker are
`memory[output_pos : output_pos + output_size]`.

If `u32(global + 0x30)` is nonzero, preserve the 64-byte system global area plus
that many user-global bytes for the next invocation of the same stored program.
Clamp the user-global count to `0x2000 - 64`.

## 9. Termination and Safety

RARVM bytecode is untrusted archive input. A decoder should apply all of these
limits:

- Reject a program blob whose XOR checksum fails.
- Reject bytecode length zero or `>= 65536`.
- Reject decoded instruction opcodes outside `0..39`.
- Reject writes to immediate operands.
- Mask all VM memory addresses with `0x3ffff`.
- Limit execution count. 7-Zip uses 25,000,000 instructions for the generic VM
  path; that is a reasonable compatibility ceiling.
- Treat malformed operands or truncated bitstreams as unsupported filters.

If generic VM execution is unavailable, a reader can still support ordinary
archives by recognizing the six standard filter fingerprints and dispatching the
native transforms documented in `FILTER_TRANSFORMS.md`.
