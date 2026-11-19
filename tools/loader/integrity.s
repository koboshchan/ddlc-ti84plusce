; Read-only preflight before stock convbin loader relocates, allocates, or executes.
; If a valid DDEBUG AppVar exists (in RAM or Archive), verifies its DDBG header,
; free RAM fit, and CRC-32, then relocates to plotSScreen (0xD09466) and ZX0-
; decompresses the separate debug engine directly to userMem (0xD1A881).
; Otherwise verifies both split engine code AppVars (DDLC0, DDLC1) and falls
; through to the stock convbin split loader.

	.assume	adl=1
	.section	.text,"ax",@progbits

	.equ	ti_GetCSC,       0x02014C
	.equ	ti_Mov9ToOP1,    0x020320
	.equ	ti_MovFrOP1,     0x02032C
	.equ	ti_MemChk,       0x0204FC
	.equ	ti_ChkFindSym,   0x02050C
	.equ	ti_InsertMem,    0x020514
	.equ	ti_DelMem,       0x020590
	.equ	ti_PopRealO1,    0x0205DC
	.equ	ti_PushRealO1,   0x020614
	.equ	ti_PutS,         0x0207C0
	.equ	ti_ClrScrnFull,  0x020810
	.equ	ti_HomeUp,       0x020828
	.equ	ti_ChkInRam,     0x021F98
	.equ	ti_asm_prgm_size,0xD0118C
	.equ	ti_userMem,      0xD1A881
	.equ	RELOC_BASE,      0xD09466

	.global	_guard_start
_guard_start:
	call	ti_PushRealO1
	ld	hl, ddebug_name
	call	find_appvar
	jr	nc, boot_ddebug
	ld	hl, part0
	call	verify
	jp	c, fail0
	ld	hl, part1
	call	verify
	jp	c, fail1
	call	ti_PopRealO1
	jp	guard_end

; DDEBUG payload layout: [4B "DDBG"][4B u32 raw_size][4B u32 comp_crc32][zx0 stream]
boot_ddebug:
	push	hl
	push	bc
	pop	hl
	ld	de, 16
	or	a, a
	sbc	hl, de
	pop	hl
	jp	c, fail_dbg
	ld	a, (hl)
	cp	a, 'D'
	jp	nz, fail_dbg
	inc	hl
	ld	a, (hl)
	cp	a, 'D'
	jp	nz, fail_dbg
	inc	hl
	ld	a, (hl)
	cp	a, 'B'
	jp	nz, fail_dbg
	inc	hl
	ld	a, (hl)
	cp	a, 'G'
	jp	nz, fail_dbg
	inc	hl
	ld	de, (hl)
	inc	hl
	inc	hl
	inc	hl
	ld	a, (hl)
	or	a, a
	jp	nz, fail_dbg
	inc	hl
	ld	(rel_raw_size_src), de
	push	hl
	push	de
	pop	hl
	ld	de, 64
	or	a, a
	sbc	hl, de
	pop	hl
	jp	c, fail_dbg
	push	hl
	ld	hl, 0x024001
	ld	de, (rel_raw_size_src)
	or	a, a
	sbc	hl, de
	pop	hl
	jp	c, fail_dbg
	ld	(dbg_crc_ptr), hl
	ld	de, 4
	add	hl, de
	push	hl
	push	bc
	pop	hl
	ld	de, 12
	or	a, a
	sbc	hl, de
	push	hl
	pop	bc
	pop	hl
	call	crc32_compute
	ld	hl, (dbg_crc_ptr)
	ld	de, crc
	ld	b, 4
dbg_cmp_crc:
	ld	a, (de)
	cpl
	cp	a, (hl)
	jr	nz, fail_dbg
	inc	hl
	inc	de
	djnz	dbg_cmp_crc
	call	ti_MemChk
	ld	de, (rel_raw_size_src)
	or	a, a
	sbc	hl, de
	jr	c, fail_dbg_mem
	call	ti_PopRealO1
	ld	hl, ddebug_reloc_src
	ld	de, RELOC_BASE
	ld	bc, ddebug_reloc_end - ddebug_reloc_src
	ldir
	jp	RELOC_BASE

fail_dbg:
	ld	hl, msg_dbg
	jr	failure

fail_dbg_mem:
	ld	hl, msg_dbg_mem
	jr	failure

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

; Output: CF=1 if missing; else CF=0, HL=payload start, BC=payload bytes (BCU=0).
find_appvar:
	call	ti_Mov9ToOP1
	call	ti_ChkFindSym
	ret	c
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
	or	a, a
	ret

verify:
	ld	(record), hl
	call	find_appvar
	jp	c, bad
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
	pop	hl
	call	crc32_compute
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

crc32_compute:
	ld	(count), bc
	ld	(pointer), hl
	ld	hl, crc
	ld	(hl), 255
	inc	hl
	ld	(hl), 255
	inc	hl
	ld	(hl), 255
	inc	hl
	ld	(hl), 255
	ld	a, b
	or	a, c
	ret	z

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
dbg_crc_ptr:
	.byte	0, 0, 0

ddebug_name:
	.byte	21, 'D', 'D', 'E', 'B', 'U', 'G', 0, 0

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
msg_dbg:
	.asciz	"DDEBUG invalid, resend or del"
msg_dbg_mem:
	.asciz	"DDEBUG needs more free RAM"

; Relocatable DDEBUG decompressor stub (copied to RELOC_BASE = 0xD09466).
ddebug_reloc_src:
	ld	de, rel_op1_save - ddebug_reloc_src + RELOC_BASE
	call	ti_MovFrOP1
	ld	hl, ti_userMem
	ld	de, (ti_asm_prgm_size)
	call	ti_DelMem
	or	a, a
	sbc	hl, hl
	ld	(ti_asm_prgm_size), hl
	ld	hl, (rel_raw_size_src - ddebug_reloc_src + RELOC_BASE)
	ld	de, ti_userMem
	push	hl
	call	ti_InsertMem
	pop	hl
	ld	(ti_asm_prgm_size), hl
	ld	hl, rel_ddebug_name - ddebug_reloc_src + RELOC_BASE
	call	ti_Mov9ToOP1
	call	ti_ChkFindSym
	call	ti_ChkInRam
	jr	z, .Lrel_ram
	ex	de, hl
	ld	de, 9
	add	hl, de
	ld	e, (hl)
	add	hl, de
	inc	hl
	ex	de, hl
.Lrel_ram:
	ex	de, hl
	ld	de, 14
	add	hl, de
	ld	de, ti_userMem
	push	iy
	ld	iy, 0xFFFFFF
	lea	bc, iy + 2
	scf
	jr	.Lrz_lit_start
.Lrz_new_off:
	dec	bc
	dec	bc
	add	a, a
	jr	nz, .Lrz_no_skip
	ld	a, (hl)
	inc	hl
	rla
.Lrz_no_skip:
	call	nc, .Lrz_elias_off - ddebug_reloc_src + RELOC_BASE
	inc	c
	jr	z, .Lrz_done
	ld	b, (hl)
	inc	hl
	rr	c
	rr	b
	ld	iyl, b
	ld	iyh, c
	ld	bc, 1
	call	nc, .Lrz_elias_len - ddebug_reloc_src + RELOC_BASE
	inc	bc
.Lrz_copy:
	push	hl
	lea	hl, iy + 0
	add	hl, de
	ldir
	pop	hl
	add	a, a
	jr	c, .Lrz_new_off
.Lrz_lit:
	inc	c
	add	a, a
	jr	nz, .Lrz_lit_skip
.Lrz_lit_start:
	ld	a, (hl)
	inc	hl
	rla
.Lrz_lit_skip:
	call	nc, .Lrz_elias_len - ddebug_reloc_src + RELOC_BASE
	ldir
	add	a, a
	jr	c, .Lrz_new_off
.Lrz_last_off:
	inc	c
	add	a, a
	jr	nz, .Lrz_lo_skip
	ld	a, (hl)
	inc	hl
	rla
.Lrz_lo_skip:
	call	nc, .Lrz_elias_len - ddebug_reloc_src + RELOC_BASE
	jr	.Lrz_copy
.Lrz_done:
	pop	iy
	ld	hl, rel_op1_save - ddebug_reloc_src + RELOC_BASE
	call	ti_Mov9ToOP1
	jp	ti_userMem

.Lrz_elias_off:
	add	a, a
	rl	c
	add	a, a
	jr	nc, .Lrz_elias_off
	ret	nz
	ld	a, (hl)
	inc	hl
	rla
	ret	c
	add	a, a
	rl	c
	add	a, a
	ret	c
	add	a, a
	rl	c
	add	a, a
	ret	c
	add	a, a
	rl	c
	add	a, a
	ret	c
.Lrz_eo_loop:
	add	a, a
	rl	c
	rl	b
	add	a, a
	jr	nc, .Lrz_eo_loop
	ret	nz
	ld	a, (hl)
	inc	hl
	rla
	jr	nc, .Lrz_eo_loop
	ret

.Lrz_elias_len:
	add	a, a
	rl	c
	add	a, a
	jr	nc, .Lrz_elias_len
	ret	nz
	ld	a, (hl)
	inc	hl
	rla
	ret	c
	add	a, a
	rl	c
	add	a, a
	ret	c
	add	a, a
	rl	c
	add	a, a
	ret	c
	add	a, a
	rl	c
	add	a, a
	ret	c
.Lrz_el_loop16:
	add	a, a
	jr	nz, .Lrz_el_d16
	ld	a, (hl)
	inc	hl
	rla
.Lrz_el_d16:
	rl	c
	rl	b
	jr	c, .Lrz_elias_24
	add	a, a
	jr	nz, .Lrz_el_c16
	ld	a, (hl)
	inc	hl
	rla
.Lrz_el_c16:
	jr	nc, .Lrz_el_loop16
	ret
.Lrz_elias_24:
	push	hl
	ld	hl, 0x010000
	add	hl, bc
	push	hl
	pop	ix
	pop	hl
.Lrz_el24_ctl:
	add	a, a
	jr	nz, .Lrz_el24_have_ctl
	ld	a, (hl)
	inc	hl
	rla
.Lrz_el24_have_ctl:
	jr	c, .Lrz_el24_done
	add	ix, ix
	add	a, a
	jr	nz, .Lrz_el24_have_data
	ld	a, (hl)
	inc	hl
	rla
.Lrz_el24_have_data:
	jr	nc, .Lrz_el24_ctl
	inc	ix
	jr	.Lrz_el24_ctl
.Lrz_el24_done:
	push	ix
	pop	bc
	ret

rel_raw_size_src:
	.byte	0, 0, 0
rel_op1_save:
	.byte	0, 0, 0, 0, 0, 0, 0, 0, 0
rel_ddebug_name:
	.byte	21, 'D', 'D', 'E', 'B', 'U', 'G', 0, 0
ddebug_reloc_end:

guard_end:
