; Read-only preflight before stock convbin loader relocates, allocates, or executes.
; Verifies both split engine code AppVars (DDLC0, DDLC1) exist and match their
; build-time byte lengths and CRC-32/ISO-HDLC (poly 0xEDB88320, init/final 0xFFFFFFFF).

	.assume	adl=1
	.section	.text,"ax",@progbits

	.equ	ti_GetCSC,      0x02014C
	.equ	ti_Mov9ToOP1,   0x020320
	.equ	ti_ChkFindSym,  0x02050C
	.equ	ti_PopRealO1,   0x0205DC
	.equ	ti_PushRealO1,  0x020614
	.equ	ti_PutS,        0x0207C0
	.equ	ti_ClrScrnFull, 0x020810
	.equ	ti_HomeUp,      0x020828
	.equ	ti_ChkInRam,    0x021F98

	.global	_guard_start
_guard_start:
	call	ti_PushRealO1
	ld	hl, part0
	call	verify
	jr	c, fail0
	ld	hl, part1
	call	verify
	jr	c, fail1
	call	ti_PopRealO1
	jp	guard_end

fail0:
	ld	hl, msg0
	jr	failure

fail1:
	ld	hl, msg1

failure:
	push	hl
	call	ti_PopRealO1
	call	ti_ClrScrnFull
	call	ti_HomeUp
	pop	hl
	call	ti_PutS

wait_release:
	call	ti_GetCSC
	or	a, a
	jr	nz, wait_release

wait_press:
	call	ti_GetCSC
	or	a, a
	jr	z, wait_press

wait_exit_release:
	call	ti_GetCSC
	or	a, a
	jr	nz, wait_exit_release
	ret

verify:
	ld	(record), hl
	call	ti_Mov9ToOP1
	call	ti_ChkFindSym
	jp	c, bad
	call	ti_ChkInRam
	jr	z, ram
	ex	de, hl
	ld	de, 9
	add	hl, de
	ld	e, (hl)
	add	hl, de
	inc	hl
	ex	de, hl

ram:
	ex	de, hl
	ld	bc, 0
	ld	c, (hl)
	inc	hl
	ld	b, (hl)
	inc	hl
	push	hl
	ld	hl, (record)
	ld	de, 9
	add	hl, de
	ld	a, (hl)
	cp	a, c
	jp	nz, bad_pop
	inc	hl
	ld	a, (hl)
	cp	a, b
	jp	nz, bad_pop
	ld	(count), bc
	pop	hl
	ld	(pointer), hl
	ld	hl, crc
	ld	(hl), 255
	inc	hl
	ld	(hl), 255
	inc	hl
	ld	(hl), 255
	inc	hl
	ld	(hl), 255

byte_loop:
	ld	hl, (pointer)
	ld	a, (hl)
	inc	hl
	ld	(pointer), hl
	ld	hl, crc
	xor	a, (hl)
	ld	(hl), a
	ld	b, 8

bit_loop:
	ld	hl, crc + 3
	srl	(hl)
	dec	hl
	rr	(hl)
	dec	hl
	rr	(hl)
	dec	hl
	rr	(hl)
	jr	nc, no_xor
	ld	a, (hl)
	xor	a, 0x20
	ld	(hl), a
	inc	hl
	ld	a, (hl)
	xor	a, 0x83
	ld	(hl), a
	inc	hl
	ld	a, (hl)
	xor	a, 0xB8
	ld	(hl), a
	inc	hl
	ld	a, (hl)
	xor	a, 0xED
	ld	(hl), a

no_xor:
	djnz	bit_loop
	ld	bc, (count)
	dec	bc
	ld	(count), bc
	ld	a, b
	or	a, c
	jr	nz, byte_loop
	ld	hl, (record)
	ld	de, 11
	add	hl, de
	ld	de, crc
	ld	b, 4

compare:
	ld	a, (de)
	cpl
	cp	a, (hl)
	jp	nz, bad
	inc	hl
	inc	de
	djnz	compare
	or	a, a
	ret

bad_pop:
	pop	hl

bad:
	scf
	ret

record:
	.byte	0, 0, 0
pointer:
	.byte	0, 0, 0
count:
	.byte	0, 0, 0
crc:
	.byte	0, 0, 0, 0

part0:
	.byte	21, 'D', 'D', 'L', 'C', '0', 0, 0, 0
	.short	LENGTH0
	.long	CRC0

part1:
	.byte	21, 'D', 'D', 'L', 'C', '1', 0, 0, 0
	.short	LENGTH1
	.long	CRC1

msg0:
	.asciz	"DDLC0 mismatch, resend bundle"
msg1:
	.asciz	"DDLC1 mismatch, resend bundle"

guard_end:
