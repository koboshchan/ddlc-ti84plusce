; Read-only preflight before stock loader relocates, allocates, or executes.
; CRC-32/ISO-HDLC reflected polynomial EDB88320 init/final FFFFFFFF.
include '/home/kat/ti/opt/convbin-reference/src/deps/fasmg-ez80/ez80.inc'
include 'ti84pceg.inc'
include 'expected.inc'
org ti.userMem
start:
 call ti.PushRealO1
 ld hl,part0
 call verify
 jr c,fail0
 ld hl,part1
 call verify
 jr c,fail1
 call ti.PopRealO1
 jp guard_end
fail0:
 ld hl,msg0
 jr failure
fail1:
 ld hl,msg1
failure:
 push hl
 call ti.PopRealO1
 call ti.ClrScrnFull
 call ti.HomeUp
 pop hl
 call ti.PutS
wait_release:
 call ti.GetCSC
 or a,a
 jr nz,wait_release
wait_press:
 call ti.GetCSC
 or a,a
 jr z,wait_press
wait_exit_release:
 call ti.GetCSC
 or a,a
 jr nz,wait_exit_release
 ret
verify:
 ld (record),hl
 call ti.Mov9ToOP1
 call ti.ChkFindSym
 jp c,bad
 call ti.ChkInRam
 jr z,ram
 ex de,hl
 ld de,9
 add hl,de
 ld e,(hl)
 add hl,de
 inc hl
 ex de,hl
ram:
 ex de,hl
 ld bc,0
 ld c,(hl)
 inc hl
 ld b,(hl)
 inc hl
 push hl
 ld hl,(record)
 ld de,9
 add hl,de
 ld a,(hl)
 cp a,c
 jp nz,bad_pop
 inc hl
 ld a,(hl)
 cp a,b
 jp nz,bad_pop
 ld (count),bc
 pop hl
 ld (pointer),hl
 ld hl,crc
 ld (hl),255
 inc hl
 ld (hl),255
 inc hl
 ld (hl),255
 inc hl
 ld (hl),255
byte_loop:
 ld hl,(pointer)
 ld a,(hl)
 inc hl
 ld (pointer),hl
 ld hl,crc
 xor a,(hl)
 ld (hl),a
 ld b,8
bit_loop:
 ld hl,crc+3
 srl (hl)
 dec hl
 rr (hl)
 dec hl
 rr (hl)
 dec hl
 rr (hl)
 jr nc,no_xor
 ld a,(hl)
 xor a,020h
 ld (hl),a
 inc hl
 ld a,(hl)
 xor a,083h
 ld (hl),a
 inc hl
 ld a,(hl)
 xor a,0B8h
 ld (hl),a
 inc hl
 ld a,(hl)
 xor a,0EDh
 ld (hl),a
no_xor:
 djnz bit_loop
 ld bc,(count)
 dec bc
 ld (count),bc
 ld a,b
 or a,c
 jr nz,byte_loop
 ld hl,(record)
 ld de,11
 add hl,de
 ld de,crc
 ld b,4
compare:
 ld a,(de)
 cpl
 cp a,(hl)
 jp nz,bad
 inc hl
 inc de
 djnz compare
 or a,a
 ret
bad_pop:
 pop hl
bad:
 scf
 ret
record: dl 0
pointer: dl 0
count: dl 0
crc: db 0,0,0,0
part0: db 21,'DDLC0',0,0,0
 dw LENGTH0
 dd CRC0
part1: db 21,'DDLC1',0,0,0
 dw LENGTH1
 dd CRC1
msg0: db 'DDLC0 mismatch, resend bundle',0
msg1: db 'DDLC1 mismatch, resend bundle',0
guard_end:
