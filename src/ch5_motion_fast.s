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


	.global _ch5_motion_lut_fast
	.type _ch5_motion_lut_fast,@function
; Direct bounded offline blend table; no channel math or palette search.
; Main bank BC source, IX xmap, IY destination.
; Alternate bank BC LUT, DE 256-byte background groups, HL count.
_ch5_motion_lut_fast:
	push ix
	push iy
	ld ix,0
	add ix,sp
	ld iy,(ix+9)
	ld bc,(ix+12)
	exx
	ld hl,(ix+18)
	ld de,(ix+21)
	ld bc,(ix+24)
	ld a,l
	or a,h
	exx
	ld ix,(ix+15)
	jr z,.lut_done
.lut_loop:
	ld hl,0
	ld l,(ix+0)
	ld h,(ix+1)
	add hl,bc
	ld a,(hl)
	cp a,255
	jr z,.lut_transparent
	ld hl,0
	ld l,a
	add hl,hl
	add hl,hl
	add hl,hl
	add hl,hl
	add hl,hl
	push hl
	ld a,(iy+0)
	exx
	push hl
	ld hl,0
	ld l,a
	add hl,de
	ld a,(hl)
	pop hl
	push bc
	exx
	pop de
	pop hl
	add hl,de
	ld de,0
	ld e,a
	add hl,de
	ld a,(hl)
	ld (iy+0),a
.lut_transparent:
	inc iy
	lea ix,ix+2
	exx
	dec hl
	ld a,l
	or a,h
	exx
	jr nz,.lut_loop
.lut_done:
	pop iy
	pop ix
	ret
