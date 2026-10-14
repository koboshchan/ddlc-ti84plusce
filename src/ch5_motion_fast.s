	.assume adl=1
	.section .text,"ax",@progbits
	.global _ch5_motion_opaque_fast
	.type _ch5_motion_opaque_fast,@function
; Exact indexed scanline, already clipped by the caller.
; void ch5_motion_opaque_fast(dst, src, xmap, count)
; Every xmap entry is a nonnegative uint16 source offset.
_ch5_motion_opaque_fast:
	push ix
	push iy
	ld ix,0
	add ix,sp
	ld de,(ix+9)
	ld bc,(ix+12)
	ld iy,(ix+18)
	ld ix,(ix+15)
	ld a,iyl
	or a,iyh
	jr z,.done
.loop:
	ld hl,0
	ld l,(ix+0)
	ld h,(ix+1)
	add hl,bc
	ld a,(hl)
	cp a,255
	jr z,.transparent
	ld (de),a
.transparent:
	inc de
	lea ix,ix+2
	dec iy
	ld a,iyl
	or a,iyh
	jr nz,.loop
.done:
	pop iy
	pop ix
	ret
