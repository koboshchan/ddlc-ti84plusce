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
 ld (.coverage),a
 ld a,120
 ld (.rows),a
 push bc
 ld bc,320
 push ix
 pop iy
 add iy,bc
 pop bc
.row:
 ld a,160
 ld (.cols),a
.pixel:
 ; BC holds only 16-bit nonzero state, BCU zero. No carry from BCU.
 srl b
 rr c
 jr nc,.advanced
 ld a,b
 xor 0B4h
 ld b,a
.advanced:
 ld a,c
 and 15
 ld hl,.coverage
 cp (hl)
 jr nc,.skip
 ld a,(de)
 ld (ix+0),a
 inc de
 ld a,(de)
 ld (ix+1),a
 inc de
 ld a,(de)
 ld (iy+0),a
 inc de
 ld a,(de)
 ld (iy+1),a
 inc de
.skip:
 lea iy,iy+2
 lea ix,ix+2
 ld hl,.cols
 dec (hl)
 jr nz,.pixel
 push bc
 ld bc,320
 add ix,bc
 add iy,bc
 pop bc
 ld hl,.rows
 dec (hl)
 jr nz,.row
 pop iy
 pop ix
 ret
	.section .data,"aw",@progbits
.coverage: .byte 0
.rows: .byte 0
.cols: .byte 0
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
 ld (.draw_coverage_operand),a
 ld a,(iy+18)
 ld (.draw_rows),a
 push bc
 ld bc,320
 push ix
 pop iy
 add iy,bc
 pop bc
.draw_row:
 ld a,160
 ld (.draw_cols),a
 ld hl,(.draw_mask_start)
.draw_pixel:
 srl b
 rr c
 jr nc,.draw_next
 ld a,b
 xor 0B4h
 ld b,a
.draw_next:
 ld a,c
 and 15
 .byte 0FEh
.draw_coverage_operand: .byte 0
 jr nc,.draw_skip
 ld a,(ix+0)
 ld (de),a
 inc de
 ld a,(ix+1)
 ld (de),a
 inc de
 ld a,(iy+0)
 ld (de),a
 inc de
 ld a,(iy+1)
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
 ld (ix+0),a
 ld (ix+1),a
 ld (iy+0),a
 ld (iy+1),a
.draw_skip:
 lea iy,iy+2
 lea ix,ix+2
 inc hl
 push hl
 ld hl,.draw_cols
 dec (hl)
 pop hl
 jp nz,.draw_pixel
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
.draw_coverage: .byte 0
.draw_rows: .byte 0
.draw_cols: .byte 0
.draw_mask: .long 0

.draw_context: .long 0
.draw_colors: .long 0
.draw_mask_start: .long 0
