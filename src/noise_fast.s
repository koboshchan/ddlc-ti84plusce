; Exact sparse 2x2 noise, 16 blocks unrolled with fixed IX/IY displacements.
; Advancing pointers once per group and register counters removes per-block
; LEA and RAM-counter overhead without changing the LFSR or saved-byte order.
; Serial non-reentrant, writable RAM code (CP immediates patched at entry).
; IX/IY preserved; shadow BC is caller-clobbered under the CEdev ABI.
	.assume adl=1
	.section .text,"ax",@progbits
	.global _noise_restore_asm
	.type _noise_restore_asm,@function
; void noise_restore_asm(uint8_t *fb, uint8_t *undo, unsigned seed,
;                        unsigned coverage)
; Serial non-reentrant; IX/IY saved, 24-bit arguments at original SP+3*n.
_noise_restore_asm:
 push ix
 push iy
 ld iy,0
 add iy,sp
 ld ix,(iy+9)
 ld de,(iy+12)
 ld bc,(iy+15)
 ld a,(iy+18)
 ld (.coverage_operand0),a
 ld (.coverage_operand1),a
 ld (.coverage_operand2),a
 ld (.coverage_operand3),a
 ld (.coverage_operand4),a
 ld (.coverage_operand5),a
 ld (.coverage_operand6),a
 ld (.coverage_operand7),a
 ld (.coverage_operand8),a
 ld (.coverage_operand9),a
 ld (.coverage_operand10),a
 ld (.coverage_operand11),a
 ld (.coverage_operand12),a
 ld (.coverage_operand13),a
 ld (.coverage_operand14),a
 ld (.coverage_operand15),a
 ld a,120
 ld (.rows),a
 push bc
 ld bc,320
 push ix
 pop iy
 add iy,bc
 pop bc
.row:
 ld l,10
.macro noise_restore_block n
.pixel\n:
 ; BC holds only 16-bit nonzero state, BCU zero. No carry from BCU.
 srl b
 rr c
 jr nc,.advanced\n
 ld a,b
 xor 0B4h
 ld b,a
.advanced\n:
 ld a,c
 and 15
 .byte 0FEh
.coverage_operand\n: .byte 0
 jr nc,.skip\n
 ld a,(de)
 ld (ix+2*\n),a
 inc de
 ld a,(de)
 ld (ix+2*\n+1),a
 inc de
 ld a,(de)
 ld (iy+2*\n),a
 inc de
 ld a,(de)
 ld (iy+2*\n+1),a
 inc de
.skip\n:
.endm
 noise_restore_block 0
 noise_restore_block 1
 noise_restore_block 2
 noise_restore_block 3
 noise_restore_block 4
 noise_restore_block 5
 noise_restore_block 6
 noise_restore_block 7
 noise_restore_block 8
 noise_restore_block 9
 noise_restore_block 10
 noise_restore_block 11
 noise_restore_block 12
 noise_restore_block 13
 noise_restore_block 14
 noise_restore_block 15
 lea iy,iy+32
 lea ix,ix+32
 dec l
 jp nz,.pixel0
 push bc
 ld bc,320
 add ix,bc
 add iy,bc
 pop bc
 ld hl,.rows
 dec (hl)
 jp nz,.row
 pop iy
 pop ix
 ret
	.section .data,"aw",@progbits
.rows: .byte 0
	.section .text,"ax",@progbits
	.global _noise_draw_asm
; Context native24fields fb,undo,seed,coverage,mask160,colors32.
; 320x240 only; C wrapper must bound allocation and clip other regions.
_noise_draw_asm:
 push ix
 push iy
 ld iy,0
 add iy,sp
 ld iy,(iy+9)
 ld (.draw_context),iy
 ld hl,(iy+15)
 ld (.draw_colors),hl
 ld hl,(iy+12)
 ld (.draw_mask_start),hl
 ld ix,(iy+0)
 ld de,(iy+3)
 ld bc,(iy+6)
 ld a,(iy+9)
 ld (.draw_coverage_operand0),a
 ld (.draw_coverage_operand1),a
 ld (.draw_coverage_operand2),a
 ld (.draw_coverage_operand3),a
 ld (.draw_coverage_operand4),a
 ld (.draw_coverage_operand5),a
 ld (.draw_coverage_operand6),a
 ld (.draw_coverage_operand7),a
 ld (.draw_coverage_operand8),a
 ld (.draw_coverage_operand9),a
 ld (.draw_coverage_operand10),a
 ld (.draw_coverage_operand11),a
 ld (.draw_coverage_operand12),a
 ld (.draw_coverage_operand13),a
 ld (.draw_coverage_operand14),a
 ld (.draw_coverage_operand15),a
 ld a,(iy+18)
 ld (.draw_rows),a
 push bc
 ld bc,320
 push ix
 pop iy
 add iy,bc
 pop bc
.draw_row:
 exx
 ld c,10
 exx
 ld hl,(.draw_mask_start)
.macro noise_draw_block n
.draw_pixel\n:
 srl b
 rr c
 jr nc,.draw_next\n
 ld a,b
 xor 0B4h
 ld b,a
.draw_next\n:
 ld a,c
 and 15
 .byte 0FEh
.draw_coverage_operand\n: .byte 0
 jr nc,.draw_skip\n
 ld a,(ix+2*\n)
 ld (de),a
 inc de
 ld a,(ix+2*\n+1)
 ld (de),a
 inc de
 ld a,(iy+2*\n)
 ld (de),a
 inc de
 ld a,(iy+2*\n+1)
 ld (de),a
 inc de
 push hl
 push bc
 ld a,b
 and 1
 rlca
 rlca
 rlca
 rlca
 add a,(hl)
 ld bc,0
 ld c,a
 ld hl,(.draw_colors)
 add hl,bc
 ld a,(hl)
 pop bc
 pop hl
 ld (ix+2*\n),a
 ld (ix+2*\n+1),a
 ld (iy+2*\n),a
 ld (iy+2*\n+1),a
.draw_skip\n:
 inc hl
.endm
 noise_draw_block 0
 noise_draw_block 1
 noise_draw_block 2
 noise_draw_block 3
 noise_draw_block 4
 noise_draw_block 5
 noise_draw_block 6
 noise_draw_block 7
 noise_draw_block 8
 noise_draw_block 9
 noise_draw_block 10
 noise_draw_block 11
 noise_draw_block 12
 noise_draw_block 13
 noise_draw_block 14
 noise_draw_block 15
 lea iy,iy+32
 lea ix,ix+32
 exx
 dec c
 exx
 jp nz,.draw_pixel0
 push bc
 ld bc,320
 add ix,bc
 add iy,bc
 pop bc
 ld hl,.draw_rows
 dec (hl)
 jp nz,.draw_row
 ld iy,(.draw_context)
 ld (iy+3),de
 ld (iy+6),bc
 pop iy
 pop ix
 ret
	.section .data,"aw",@progbits
.draw_rows: .byte 0

.draw_context: .long 0
.draw_colors: .long 0
.draw_mask_start: .long 0
