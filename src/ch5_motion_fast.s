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

	.global _ch5_motion_cached_fast
	.type _ch5_motion_cached_fast,@function
; size_t ch5_motion_cached_fast(dst,src,map,count,tags,colors)
; Returns number consumed before first nontransparent cache miss.
; Cache is read-only. IY keeps frame arguments; IX is clipped xmap.
_ch5_motion_cached_fast:
	push ix
	push iy
	ld iy,0
	add iy,sp
	ld de,(iy+9)
	ld bc,(iy+12)
	ld ix,(iy+15)
	exx
	ld bc,(iy+18)
	exx
.hit_loop:
	exx
	ld a,b
	or a,c
	exx
	jr z,.hit_return
	ld hl,0
	ld l,(ix+0)
	ld h,(ix+1)
	add hl,bc
	ld a,(hl)
	cp a,255
	jr z,.hit_advance
	push bc
	push de
	ld hl,0
	ld h,a
	ld a,(de)
	ld l,a
	push hl
	; Hash ((fg<<4)^bg)&4095, with no multiply helper.
	ld a,h
	and a,15
	add a,a
	add a,a
	add a,a
	add a,a
	xor a,l
	ld l,a
	ld a,h
	srl a
	srl a
	srl a
	srl a
	ld h,a
	add hl,hl
	ld bc,(iy+21)
	add hl,bc
	pop bc
	ld a,(hl)
	cp a,c
	jr nz,.hit_miss
	inc hl
	ld a,(hl)
	cp a,b
	jr nz,.hit_miss
	; Matching tag. Look up color with the same unsigned hash.
	ld hl,0
	ld a,b
	and a,15
	add a,a
	add a,a
	add a,a
	add a,a
	xor a,c
	ld l,a
	ld a,b
	srl a
	srl a
	srl a
	srl a
	ld h,a
	ld bc,(iy+24)
	add hl,bc
	ld a,(hl)
	pop de
	pop bc
	ld (de),a
.hit_advance:
	inc de
	lea ix,ix+2
	exx
	dec bc
	exx
	jr .hit_loop
.hit_miss:
	pop de
	pop bc
.hit_return:
	; Return original count minus remaining in 24-bit HL.
	exx
	push bc
	exx
	pop bc
	ld hl,(iy+18)
	or a,a
	sbc hl,bc
	pop iy
	pop ix
	ret
