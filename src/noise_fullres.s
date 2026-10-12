	.assume adl=1
	.section .text,"ax",@progbits
	.global _noise_draw_row_asm
	.global _noise_undo_row_asm
; Row context, all fields native 24-bit:
; +0 framebuffer +3 undo +6 seed +9 coverage +12 width
; +15 per-pixel palette indices +18 colors.
; One byte saved per selected FULL-RES pixel. Caller proves buffers/width.
; Serial only. IX/IY preserved; no OS calls or interrupt masking.
_noise_draw_row_asm:
 push ix
 push iy
 ld ix,0
 add ix,sp
 ld ix,(ix+9)
 ld hl,(ix+0)
 ld de,(ix+3)
 ld bc,(ix+6)
 ld iy,(ix+15)
 push hl
 ld hl,(ix+12)
 ld (.row_left),hl
 pop hl
.draw_pixel:
 srl b
 rr c
 jr nc,.draw_advanced
 ld a,b
 xor 0B4h
 ld b,a
.draw_advanced:
 ld a,c
 and 15
 cp (ix+9)
 jr nc,.draw_skip
 ld a,(hl)
 ld (de),a
 inc de
 push hl
 push bc
 ; Seed bit 8 selects black/white palette bank.
 ld a,b
 and 1
 rlca
 rlca
 rlca
 rlca
 add a,(iy+0)
 ld bc,0
 ld c,a
 ld hl,(ix+18)
 add hl,bc
 ld a,(hl)
 pop bc
 pop hl
 ld (hl),a
.draw_skip:
 inc hl
 inc iy
 push hl
 ld hl,(.row_left)
 dec hl
 ld (.row_left),hl
 ld a,h
 or l
 pop hl
 jr nz,.draw_pixel
 ld (ix+0),hl
 ld (ix+3),de
 ld (ix+6),bc
 pop iy
 pop ix
 ret
_noise_undo_row_asm:
 push ix
 push iy
 ld ix,0
 add ix,sp
 ld ix,(ix+9)
 ld hl,(ix+0)
 ld de,(ix+3)
 ld bc,(ix+6)
 push hl
 ld hl,(ix+12)
 ld (.row_left),hl
 pop hl
.undo_pixel:
 srl b
 rr c
 jr nc,.undo_advanced
 ld a,b
 xor 0B4h
 ld b,a
.undo_advanced:
 ld a,c
 and 15
 cp (ix+9)
 jr nc,.undo_skip
 ld a,(de)
 inc de
 ld (hl),a
.undo_skip:
 inc hl
 push hl
 ld hl,(.row_left)
 dec hl
 ld (.row_left),hl
 ld a,h
 or l
 pop hl
 jr nz,.undo_pixel
 ld (ix+0),hl
 ld (ix+3),de
 ld (ix+6),bc
 pop iy
 pop ix
 ret
	.section .data,"aw",@progbits
.row_left: .long 0
