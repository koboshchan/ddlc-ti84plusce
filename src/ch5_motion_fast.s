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
	ld hl,(iy+18)
	push hl
.hit_loop:
	ld hl,(iy-3)
	ld a,h
	or a,l
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
	; Hash ((fg<<5)-fg+bg)&4095. Keep upper HL/DE/BC zero.
	ld de,0
	ld e,h
	ld bc,0
	ld c,l
	ld hl,0
	ld l,e
	add hl,hl
	add hl,hl
	add hl,hl
	add hl,hl
	add hl,hl
	or a,a
	sbc hl,de
	add hl,bc
	ld a,h
	and a,15
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
	; Matching tag. Recompute hash from the pair in BC.
	ld de,0
	ld e,b
	ld hl,0
	ld l,c
	push hl
	ld hl,0
	ld l,e
	add hl,hl
	add hl,hl
	add hl,hl
	add hl,hl
	add hl,hl
	or a,a
	sbc hl,de
	pop bc
	add hl,bc
	ld a,h
	and a,15
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
	ld hl,(iy-3)
	dec hl
	ld (iy-3),hl
	jp .hit_loop
.hit_miss:
	pop de
	pop bc
.hit_return:
	; Return original count minus remaining in 24-bit HL.
	ld bc,(iy-3)
	ld hl,(iy+18)
	or a,a
	sbc hl,bc
	pop bc
	pop iy
	pop ix
	ret

	.global _ch5_motion_expand_fast
	.type _ch5_motion_expand_fast,@function
_ch5_motion_expand_fast:
	push ix
	push iy
	ld ix,0
	add ix,sp
	ld de,(ix+9)
	ld hl,(ix+12)
	ld iy,(ix+15)
.expand_loop:
	ld a,(hl)
	ld (de),a
	dec de
	ld (de),a
	dec de
	dec hl
	dec iy
	ld a,iyl
	or a,iyh
	jr nz,.expand_loop
	pop iy
	pop ix
	ret


 .global _ch5_motion_quant_fast
 .type _ch5_motion_quant_fast,@function
_ch5_motion_quant_fast:
 push ix
 push iy
 ld iy,0
 add iy,sp
 ld de,(iy+9)
 ld bc,(iy+12)
 ld ix,(iy+15)
 ld hl,(iy+18)
 push hl
.aligned_blocks:
 ld hl,(iy-3)
 ld a,h
 or a,a
 jr nz,.four
 ld a,l
 cp a,4
 jp c,.tail
.four:

 ld hl,0
 ld l,(ix+0)
 ld h,(ix+1)
 add hl,bc
 ld a,(hl)
 cp a,255
 jr z,.advance0
 ld hl,(iy+21)
 ld l,a
 ld a,(hl)
 ld hl,(iy+24)
 ld h,a
 ld a,(de)
 ld l,a
 ld a,(hl)
 ld (de),a
.advance0:
 inc de
 lea ix,ix+2

 ld hl,0
 ld l,(ix+0)
 ld h,(ix+1)
 add hl,bc
 ld a,(hl)
 cp a,255
 jr z,.advance1
 ld hl,(iy+21)
 ld l,a
 ld a,(hl)
 ld hl,(iy+24)
 ld h,a
 ld a,(de)
 ld l,a
 ld a,(hl)
 ld (de),a
.advance1:
 inc de
 lea ix,ix+2

 ld hl,0
 ld l,(ix+0)
 ld h,(ix+1)
 add hl,bc
 ld a,(hl)
 cp a,255
 jr z,.advance2
 ld hl,(iy+21)
 ld l,a
 ld a,(hl)
 ld hl,(iy+24)
 ld h,a
 ld a,(de)
 ld l,a
 ld a,(hl)
 ld (de),a
.advance2:
 inc de
 lea ix,ix+2

 ld hl,0
 ld l,(ix+0)
 ld h,(ix+1)
 add hl,bc
 ld a,(hl)
 cp a,255
 jr z,.advance3
 ld hl,(iy+21)
 ld l,a
 ld a,(hl)
 ld hl,(iy+24)
 ld h,a
 ld a,(de)
 ld l,a
 ld a,(hl)
 ld (de),a
.advance3:
 inc de
 lea ix,ix+2

 ld hl,(iy-3)
 dec hl
 dec hl
 dec hl
 dec hl
 ld (iy-3),hl
 jp .aligned_blocks
.tail:
 ld hl,(iy-3)
 ld a,h
 or a,l
 jr z,.aligned_done

 ld hl,0
 ld l,(ix+0)
 ld h,(ix+1)
 add hl,bc
 ld a,(hl)
 cp a,255
 jr z,.advance_tail
 ld hl,(iy+21)
 ld l,a
 ld a,(hl)
 ld hl,(iy+24)
 ld h,a
 ld a,(de)
 ld l,a
 ld a,(hl)
 ld (de),a
.advance_tail:
 inc de
 lea ix,ix+2

 ld hl,(iy-3)
 dec hl
 ld (iy-3),hl
 jp .tail
.aligned_done:
 pop hl
 pop iy
 pop ix
 ret

.global _ch5_motion_quant_direct1
.type _ch5_motion_quant_direct1,@function
_ch5_motion_quant_direct1:
 push ix
 push iy
 ld ix,0
 add ix,sp
 ld de,(ix+9)
 ld hl,(ix+12)
 ld bc,(ix+15)
 ld iy,(ix+21)
 ld ix,(ix+18)
 ld a,c
 and a,7
 push af
 srl b
 rr c
 srl b
 rr c
 srl b
 rr c
 ld a,b
 or a,c
 jp z,.quant1_tailsetup
.quant1_blocks:
 ld a,(hl)
 cp a,255
 jr z,.quant1_block0_skip
 ld ixl,a
 ld a,(ix)
 ld iyh,a
 ld a,(de)
 ld iyl,a
 ld a,(iy)
 ld (de),a
 inc de
 jr .quant1_block0_advance
.quant1_block0_skip:
 inc de
.quant1_block0_advance:
 inc hl
 ld a,(hl)
 cp a,255
 jr z,.quant1_block1_skip
 ld ixl,a
 ld a,(ix)
 ld iyh,a
 ld a,(de)
 ld iyl,a
 ld a,(iy)
 ld (de),a
 inc de
 jr .quant1_block1_advance
.quant1_block1_skip:
 inc de
.quant1_block1_advance:
 inc hl
 ld a,(hl)
 cp a,255
 jr z,.quant1_block2_skip
 ld ixl,a
 ld a,(ix)
 ld iyh,a
 ld a,(de)
 ld iyl,a
 ld a,(iy)
 ld (de),a
 inc de
 jr .quant1_block2_advance
.quant1_block2_skip:
 inc de
.quant1_block2_advance:
 inc hl
 ld a,(hl)
 cp a,255
 jr z,.quant1_block3_skip
 ld ixl,a
 ld a,(ix)
 ld iyh,a
 ld a,(de)
 ld iyl,a
 ld a,(iy)
 ld (de),a
 inc de
 jr .quant1_block3_advance
.quant1_block3_skip:
 inc de
.quant1_block3_advance:
 inc hl
 ld a,(hl)
 cp a,255
 jr z,.quant1_block4_skip
 ld ixl,a
 ld a,(ix)
 ld iyh,a
 ld a,(de)
 ld iyl,a
 ld a,(iy)
 ld (de),a
 inc de
 jr .quant1_block4_advance
.quant1_block4_skip:
 inc de
.quant1_block4_advance:
 inc hl
 ld a,(hl)
 cp a,255
 jr z,.quant1_block5_skip
 ld ixl,a
 ld a,(ix)
 ld iyh,a
 ld a,(de)
 ld iyl,a
 ld a,(iy)
 ld (de),a
 inc de
 jr .quant1_block5_advance
.quant1_block5_skip:
 inc de
.quant1_block5_advance:
 inc hl
 ld a,(hl)
 cp a,255
 jr z,.quant1_block6_skip
 ld ixl,a
 ld a,(ix)
 ld iyh,a
 ld a,(de)
 ld iyl,a
 ld a,(iy)
 ld (de),a
 inc de
 jr .quant1_block6_advance
.quant1_block6_skip:
 inc de
.quant1_block6_advance:
 inc hl
 ld a,(hl)
 cp a,255
 jr z,.quant1_block7_skip
 ld ixl,a
 ld a,(ix)
 ld iyh,a
 ld a,(de)
 ld iyl,a
 ld a,(iy)
 ld (de),a
 inc de
 jr .quant1_block7_advance
.quant1_block7_skip:
 inc de
.quant1_block7_advance:
 inc hl
 dec bc
 ld a,b
 or a,c
 jp nz,.quant1_blocks
.quant1_tailsetup:
 pop af
 ld c,a
 ld b,0
 or a,a
 jr z,.quant1_done
.quant1_tail:
 ld a,(hl)
 cp a,255
 jr z,.quant1_last_skip
 ld ixl,a
 ld a,(ix)
 ld iyh,a
 ld a,(de)
 ld iyl,a
 ld a,(iy)
 ld (de),a
 inc de
 jr .quant1_last_advance
.quant1_last_skip:
 inc de
.quant1_last_advance:
 inc hl
 dec c
 jp nz,.quant1_tail
.quant1_done:
 pop iy
 pop ix
 ret

.global _ch5_motion_quant_direct2
.type _ch5_motion_quant_direct2,@function
_ch5_motion_quant_direct2:
 push ix
 push iy
 ld ix,0
 add ix,sp
 ld de,(ix+9)
 ld hl,(ix+12)
 ld bc,(ix+15)
 ld iy,(ix+21)
 ld ix,(ix+18)
 ld a,c
 and a,7
 push af
 srl b
 rr c
 srl b
 rr c
 srl b
 rr c
 ld a,b
 or a,c
 jp z,.quant2_tailsetup
.quant2_blocks:
 ld a,(hl)
 cp a,255
 jr z,.quant2_block0_skip
 ld ixl,a
 ld a,(ix)
 ld iyh,a
 ld a,(de)
 ld iyl,a
 ld a,(iy)
 ld (de),a
 inc de
 ld a,(de)
 ld iyl,a
 ld a,(iy)
 ld (de),a
 inc de
 jr .quant2_block0_advance
.quant2_block0_skip:
 inc de
 inc de
.quant2_block0_advance:
 inc hl
 ld a,(hl)
 cp a,255
 jr z,.quant2_block1_skip
 ld ixl,a
 ld a,(ix)
 ld iyh,a
 ld a,(de)
 ld iyl,a
 ld a,(iy)
 ld (de),a
 inc de
 ld a,(de)
 ld iyl,a
 ld a,(iy)
 ld (de),a
 inc de
 jr .quant2_block1_advance
.quant2_block1_skip:
 inc de
 inc de
.quant2_block1_advance:
 inc hl
 ld a,(hl)
 cp a,255
 jr z,.quant2_block2_skip
 ld ixl,a
 ld a,(ix)
 ld iyh,a
 ld a,(de)
 ld iyl,a
 ld a,(iy)
 ld (de),a
 inc de
 ld a,(de)
 ld iyl,a
 ld a,(iy)
 ld (de),a
 inc de
 jr .quant2_block2_advance
.quant2_block2_skip:
 inc de
 inc de
.quant2_block2_advance:
 inc hl
 ld a,(hl)
 cp a,255
 jr z,.quant2_block3_skip
 ld ixl,a
 ld a,(ix)
 ld iyh,a
 ld a,(de)
 ld iyl,a
 ld a,(iy)
 ld (de),a
 inc de
 ld a,(de)
 ld iyl,a
 ld a,(iy)
 ld (de),a
 inc de
 jr .quant2_block3_advance
.quant2_block3_skip:
 inc de
 inc de
.quant2_block3_advance:
 inc hl
 ld a,(hl)
 cp a,255
 jr z,.quant2_block4_skip
 ld ixl,a
 ld a,(ix)
 ld iyh,a
 ld a,(de)
 ld iyl,a
 ld a,(iy)
 ld (de),a
 inc de
 ld a,(de)
 ld iyl,a
 ld a,(iy)
 ld (de),a
 inc de
 jr .quant2_block4_advance
.quant2_block4_skip:
 inc de
 inc de
.quant2_block4_advance:
 inc hl
 ld a,(hl)
 cp a,255
 jr z,.quant2_block5_skip
 ld ixl,a
 ld a,(ix)
 ld iyh,a
 ld a,(de)
 ld iyl,a
 ld a,(iy)
 ld (de),a
 inc de
 ld a,(de)
 ld iyl,a
 ld a,(iy)
 ld (de),a
 inc de
 jr .quant2_block5_advance
.quant2_block5_skip:
 inc de
 inc de
.quant2_block5_advance:
 inc hl
 ld a,(hl)
 cp a,255
 jr z,.quant2_block6_skip
 ld ixl,a
 ld a,(ix)
 ld iyh,a
 ld a,(de)
 ld iyl,a
 ld a,(iy)
 ld (de),a
 inc de
 ld a,(de)
 ld iyl,a
 ld a,(iy)
 ld (de),a
 inc de
 jr .quant2_block6_advance
.quant2_block6_skip:
 inc de
 inc de
.quant2_block6_advance:
 inc hl
 ld a,(hl)
 cp a,255
 jr z,.quant2_block7_skip
 ld ixl,a
 ld a,(ix)
 ld iyh,a
 ld a,(de)
 ld iyl,a
 ld a,(iy)
 ld (de),a
 inc de
 ld a,(de)
 ld iyl,a
 ld a,(iy)
 ld (de),a
 inc de
 jr .quant2_block7_advance
.quant2_block7_skip:
 inc de
 inc de
.quant2_block7_advance:
 inc hl
 dec bc
 ld a,b
 or a,c
 jp nz,.quant2_blocks
.quant2_tailsetup:
 pop af
 ld c,a
 ld b,0
 or a,a
 jr z,.quant2_done
.quant2_tail:
 ld a,(hl)
 cp a,255
 jr z,.quant2_last_skip
 ld ixl,a
 ld a,(ix)
 ld iyh,a
 ld a,(de)
 ld iyl,a
 ld a,(iy)
 ld (de),a
 inc de
 ld a,(de)
 ld iyl,a
 ld a,(iy)
 ld (de),a
 inc de
 jr .quant2_last_advance
.quant2_last_skip:
 inc de
 inc de
.quant2_last_advance:
 inc hl
 dec c
 jp nz,.quant2_tail
.quant2_done:
 pop iy
 pop ix
 ret

.global _ch5_motion_opaque_direct1
.type _ch5_motion_opaque_direct1,@function
_ch5_motion_opaque_direct1:
 push ix
 ld ix,0
 add ix,sp
 ld de,(ix+6)
 ld hl,(ix+9)
 ld bc,(ix+12)
 ld a,c
 and a,7
 push af
 srl b
 rr c
 srl b
 rr c
 srl b
 rr c
 ld a,b
 or a,c
 jp z,.opaque1_tailsetup
.opaque1_blocks:
 ld a,(hl)
 cp a,255
 jr z,.opaque1_block0_skip
 ld (de),a
 inc de
 jr .opaque1_block0_advance
.opaque1_block0_skip:
 inc de
.opaque1_block0_advance:
 inc hl
 ld a,(hl)
 cp a,255
 jr z,.opaque1_block1_skip
 ld (de),a
 inc de
 jr .opaque1_block1_advance
.opaque1_block1_skip:
 inc de
.opaque1_block1_advance:
 inc hl
 ld a,(hl)
 cp a,255
 jr z,.opaque1_block2_skip
 ld (de),a
 inc de
 jr .opaque1_block2_advance
.opaque1_block2_skip:
 inc de
.opaque1_block2_advance:
 inc hl
 ld a,(hl)
 cp a,255
 jr z,.opaque1_block3_skip
 ld (de),a
 inc de
 jr .opaque1_block3_advance
.opaque1_block3_skip:
 inc de
.opaque1_block3_advance:
 inc hl
 ld a,(hl)
 cp a,255
 jr z,.opaque1_block4_skip
 ld (de),a
 inc de
 jr .opaque1_block4_advance
.opaque1_block4_skip:
 inc de
.opaque1_block4_advance:
 inc hl
 ld a,(hl)
 cp a,255
 jr z,.opaque1_block5_skip
 ld (de),a
 inc de
 jr .opaque1_block5_advance
.opaque1_block5_skip:
 inc de
.opaque1_block5_advance:
 inc hl
 ld a,(hl)
 cp a,255
 jr z,.opaque1_block6_skip
 ld (de),a
 inc de
 jr .opaque1_block6_advance
.opaque1_block6_skip:
 inc de
.opaque1_block6_advance:
 inc hl
 ld a,(hl)
 cp a,255
 jr z,.opaque1_block7_skip
 ld (de),a
 inc de
 jr .opaque1_block7_advance
.opaque1_block7_skip:
 inc de
.opaque1_block7_advance:
 inc hl
 dec bc
 ld a,b
 or a,c
 jp nz,.opaque1_blocks
.opaque1_tailsetup:
 pop af
 ld c,a
 ld b,0
 or a,a
 jr z,.opaque1_done
.opaque1_tail:
 ld a,(hl)
 cp a,255
 jr z,.opaque1_last_skip
 ld (de),a
 inc de
 jr .opaque1_last_advance
.opaque1_last_skip:
 inc de
.opaque1_last_advance:
 inc hl
 dec c
 jp nz,.opaque1_tail
.opaque1_done:
 pop ix
 ret

.global _ch5_motion_opaque_direct2
.type _ch5_motion_opaque_direct2,@function
_ch5_motion_opaque_direct2:
 push ix
 ld ix,0
 add ix,sp
 ld de,(ix+6)
 ld hl,(ix+9)
 ld bc,(ix+12)
 ld a,c
 and a,7
 push af
 srl b
 rr c
 srl b
 rr c
 srl b
 rr c
 ld a,b
 or a,c
 jp z,.opaque2_tailsetup
.opaque2_blocks:
 ld a,(hl)
 cp a,255
 jr z,.opaque2_block0_skip
 ld (de),a
 inc de
 ld (de),a
 inc de
 jr .opaque2_block0_advance
.opaque2_block0_skip:
 inc de
 inc de
.opaque2_block0_advance:
 inc hl
 ld a,(hl)
 cp a,255
 jr z,.opaque2_block1_skip
 ld (de),a
 inc de
 ld (de),a
 inc de
 jr .opaque2_block1_advance
.opaque2_block1_skip:
 inc de
 inc de
.opaque2_block1_advance:
 inc hl
 ld a,(hl)
 cp a,255
 jr z,.opaque2_block2_skip
 ld (de),a
 inc de
 ld (de),a
 inc de
 jr .opaque2_block2_advance
.opaque2_block2_skip:
 inc de
 inc de
.opaque2_block2_advance:
 inc hl
 ld a,(hl)
 cp a,255
 jr z,.opaque2_block3_skip
 ld (de),a
 inc de
 ld (de),a
 inc de
 jr .opaque2_block3_advance
.opaque2_block3_skip:
 inc de
 inc de
.opaque2_block3_advance:
 inc hl
 ld a,(hl)
 cp a,255
 jr z,.opaque2_block4_skip
 ld (de),a
 inc de
 ld (de),a
 inc de
 jr .opaque2_block4_advance
.opaque2_block4_skip:
 inc de
 inc de
.opaque2_block4_advance:
 inc hl
 ld a,(hl)
 cp a,255
 jr z,.opaque2_block5_skip
 ld (de),a
 inc de
 ld (de),a
 inc de
 jr .opaque2_block5_advance
.opaque2_block5_skip:
 inc de
 inc de
.opaque2_block5_advance:
 inc hl
 ld a,(hl)
 cp a,255
 jr z,.opaque2_block6_skip
 ld (de),a
 inc de
 ld (de),a
 inc de
 jr .opaque2_block6_advance
.opaque2_block6_skip:
 inc de
 inc de
.opaque2_block6_advance:
 inc hl
 ld a,(hl)
 cp a,255
 jr z,.opaque2_block7_skip
 ld (de),a
 inc de
 ld (de),a
 inc de
 jr .opaque2_block7_advance
.opaque2_block7_skip:
 inc de
 inc de
.opaque2_block7_advance:
 inc hl
 dec bc
 ld a,b
 or a,c
 jp nz,.opaque2_blocks
.opaque2_tailsetup:
 pop af
 ld c,a
 ld b,0
 or a,a
 jr z,.opaque2_done
.opaque2_tail:
 ld a,(hl)
 cp a,255
 jr z,.opaque2_last_skip
 ld (de),a
 inc de
 ld (de),a
 inc de
 jr .opaque2_last_advance
.opaque2_last_skip:
 inc de
 inc de
.opaque2_last_advance:
 inc hl
 dec c
 jp nz,.opaque2_tail
.opaque2_done:
 pop ix
 ret
