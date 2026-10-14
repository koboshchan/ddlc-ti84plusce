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

.global _ch5_motion_quant_solid1
.type _ch5_motion_quant_solid1,@function
_ch5_motion_quant_solid1:
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
 jp z,.quantsolid1_tailsetup
.quantsolid1_blocks:
 ld a,(hl)

 ld ixl,a
 ld a,(ix)
 ld iyh,a
 ld a,(de)
 ld iyl,a
 ld a,(iy)
 ld (de),a
 inc de
 inc hl
 ld a,(hl)

 ld ixl,a
 ld a,(ix)
 ld iyh,a
 ld a,(de)
 ld iyl,a
 ld a,(iy)
 ld (de),a
 inc de
 inc hl
 ld a,(hl)

 ld ixl,a
 ld a,(ix)
 ld iyh,a
 ld a,(de)
 ld iyl,a
 ld a,(iy)
 ld (de),a
 inc de
 inc hl
 ld a,(hl)

 ld ixl,a
 ld a,(ix)
 ld iyh,a
 ld a,(de)
 ld iyl,a
 ld a,(iy)
 ld (de),a
 inc de
 inc hl
 ld a,(hl)

 ld ixl,a
 ld a,(ix)
 ld iyh,a
 ld a,(de)
 ld iyl,a
 ld a,(iy)
 ld (de),a
 inc de
 inc hl
 ld a,(hl)

 ld ixl,a
 ld a,(ix)
 ld iyh,a
 ld a,(de)
 ld iyl,a
 ld a,(iy)
 ld (de),a
 inc de
 inc hl
 ld a,(hl)

 ld ixl,a
 ld a,(ix)
 ld iyh,a
 ld a,(de)
 ld iyl,a
 ld a,(iy)
 ld (de),a
 inc de
 inc hl
 ld a,(hl)

 ld ixl,a
 ld a,(ix)
 ld iyh,a
 ld a,(de)
 ld iyl,a
 ld a,(iy)
 ld (de),a
 inc de
 inc hl
 dec bc
 ld a,b
 or a,c
 jp nz,.quantsolid1_blocks
.quantsolid1_tailsetup:
 pop af
 ld c,a
 ld b,0
 or a,a
 jr z,.quantsolid1_done
.quantsolid1_tail:
 ld a,(hl)

 ld ixl,a
 ld a,(ix)
 ld iyh,a
 ld a,(de)
 ld iyl,a
 ld a,(iy)
 ld (de),a
 inc de
 inc hl
 dec c
 jp nz,.quantsolid1_tail
.quantsolid1_done:
 pop iy
 pop ix
 ret

.global _ch5_motion_quant_solid2
.type _ch5_motion_quant_solid2,@function
_ch5_motion_quant_solid2:
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
 jp z,.quantsolid2_tailsetup
.quantsolid2_blocks:
 ld a,(hl)

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
 inc hl
 ld a,(hl)

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
 inc hl
 ld a,(hl)

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
 inc hl
 ld a,(hl)

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
 inc hl
 ld a,(hl)

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
 inc hl
 ld a,(hl)

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
 inc hl
 ld a,(hl)

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
 inc hl
 ld a,(hl)

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
 inc hl
 dec bc
 ld a,b
 or a,c
 jp nz,.quantsolid2_blocks
.quantsolid2_tailsetup:
 pop af
 ld c,a
 ld b,0
 or a,a
 jr z,.quantsolid2_done
.quantsolid2_tail:
 ld a,(hl)

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
 inc hl
 dec c
 jp nz,.quantsolid2_tail
.quantsolid2_done:
 pop iy
 pop ix
 ret

.global _ch5_motion_opaque_solid2
.type _ch5_motion_opaque_solid2,@function
_ch5_motion_opaque_solid2:
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
 jp z,.opaquesolid2_tailsetup
.opaquesolid2_blocks:
 ld a,(hl)

 ld (de),a
 inc de
 ld (de),a
 inc de
 inc hl
 ld a,(hl)

 ld (de),a
 inc de
 ld (de),a
 inc de
 inc hl
 ld a,(hl)

 ld (de),a
 inc de
 ld (de),a
 inc de
 inc hl
 ld a,(hl)

 ld (de),a
 inc de
 ld (de),a
 inc de
 inc hl
 ld a,(hl)

 ld (de),a
 inc de
 ld (de),a
 inc de
 inc hl
 ld a,(hl)

 ld (de),a
 inc de
 ld (de),a
 inc de
 inc hl
 ld a,(hl)

 ld (de),a
 inc de
 ld (de),a
 inc de
 inc hl
 ld a,(hl)

 ld (de),a
 inc de
 ld (de),a
 inc de
 inc hl
 dec bc
 ld a,b
 or a,c
 jp nz,.opaquesolid2_blocks
.opaquesolid2_tailsetup:
 pop af
 ld c,a
 ld b,0
 or a,a
 jr z,.opaquesolid2_done
.opaquesolid2_tail:
 ld a,(hl)

 ld (de),a
 inc de
 ld (de),a
 inc de
 inc hl
 dec c
 jp nz,.opaquesolid2_tail
.opaquesolid2_done:
 pop ix
 ret

.global _ch5_fused_bg_0
.type _ch5_fused_bg_0,@function
_ch5_fused_bg_0:
 push ix
 push iy
 ld ix,0
 add ix,sp
 ld bc,(ix+9)
 ld hl,(ix+12)
 ld de,(ix+15)
 ld iy,(ix+21)
 ld ix,(ix+18)
 push hl
 ld hl,80
 ex (sp),hl
.ch5_fused_bg_0_loop:
 ld a,(de)
 ld ixl,a
 ld a,(ix)
 ld iyh,a
 ld a,(hl)
 ld iyl,a
 ld a,(iy)
 ld (bc),a
 inc bc
 ld (bc),a
 inc bc
 inc hl
 inc de

 ld a,(de)
 ld ixl,a
 ld a,(ix)
 ld iyh,a
 ld a,(hl)
 ld iyl,a
 ld a,(iy)
 ld (bc),a
 inc bc
 ld (bc),a
 inc bc
 inc hl
 inc de
 ex (sp),hl
 dec hl
 ld a,h
 or a,l
 ex (sp),hl
 jp nz,.ch5_fused_bg_0_loop
 pop hl
 pop iy
 pop ix
 ret

.global _ch5_fused_bg_1
.type _ch5_fused_bg_1,@function
_ch5_fused_bg_1:
 push ix
 push iy
 ld ix,0
 add ix,sp
 ld bc,(ix+9)
 ld hl,(ix+12)
 ld de,(ix+15)
 ld iy,(ix+21)
 ld ix,(ix+18)
 push hl
 ld hl,80
 ex (sp),hl
.ch5_fused_bg_1_loop:
 ld a,(de)
 ld ixl,a
 ld a,(ix)
 ld iyh,a
 ld a,(hl)
 ld iyl,a
 ld a,(iy)
 ld (bc),a
 inc bc
 inc hl
 ld a,(de)
 ld ixl,a
 ld a,(ix)
 ld iyh,a
 ld a,(hl)
 ld iyl,a
 ld a,(iy)
 ld (bc),a
 inc bc
 inc de

 ld a,(de)
 ld ixl,a
 ld a,(ix)
 ld iyh,a
 ld a,(hl)
 ld iyl,a
 ld a,(iy)
 ld (bc),a
 inc bc
 inc hl
 ld a,(de)
 ld ixl,a
 ld a,(ix)
 ld iyh,a
 ld a,(hl)
 ld iyl,a
 ld a,(iy)
 ld (bc),a
 inc bc
 inc de
 ex (sp),hl
 dec hl
 ld a,h
 or a,l
 ex (sp),hl
 jp nz,.ch5_fused_bg_1_loop
 pop hl
 pop iy
 pop ix
 ret

.global _ch5_fused_bg_2
.type _ch5_fused_bg_2,@function
_ch5_fused_bg_2:
 push ix
 push iy
 ld ix,0
 add ix,sp
 ld bc,(ix+9)
 ld hl,(ix+12)
 ld de,(ix+15)
 ld iy,(ix+21)
 ld ix,(ix+18)
 push hl
 ld hl,80
 ex (sp),hl
.ch5_fused_bg_2_loop:
 ld a,(de)
 ld ixl,a
 ld a,(ix)
 ld iyh,a
 ld a,(hl)
 ld iyl,a
 ld a,(iy)
 ld (bc),a
 inc bc
 inc de
 ld a,(de)
 ld ixl,a
 ld a,(ix)
 ld iyh,a
 ld a,(hl)
 ld iyl,a
 ld a,(iy)
 ld (bc),a
 inc bc
 inc hl

 ld a,(de)
 ld ixl,a
 ld a,(ix)
 ld iyh,a
 ld a,(hl)
 ld iyl,a
 ld a,(iy)
 ld (bc),a
 inc bc
 inc de
 ld a,(de)
 ld ixl,a
 ld a,(ix)
 ld iyh,a
 ld a,(hl)
 ld iyl,a
 ld a,(iy)
 ld (bc),a
 inc bc
 inc hl
 ex (sp),hl
 dec hl
 ld a,h
 or a,l
 ex (sp),hl
 jp nz,.ch5_fused_bg_2_loop
 pop hl
 pop iy
 pop ix
 ret

.global _ch5_fused_bg_3
.type _ch5_fused_bg_3,@function
_ch5_fused_bg_3:
 push ix
 push iy
 ld ix,0
 add ix,sp
 ld bc,(ix+9)
 ld hl,(ix+12)
 ld de,(ix+15)
 ld iy,(ix+21)
 ld ix,(ix+18)
 push hl
 ld hl,80
 ex (sp),hl
.ch5_fused_bg_3_loop:
 ld a,(de)
 ld ixl,a
 ld a,(ix)
 ld iyh,a
 ld a,(hl)
 ld iyl,a
 ld a,(iy)
 ld (bc),a
 inc bc
 inc hl
 inc de
 ld a,(de)
 ld ixl,a
 ld a,(ix)
 ld iyh,a
 ld a,(hl)
 ld iyl,a
 ld a,(iy)
 ld (bc),a
 inc bc

 ld a,(de)
 ld ixl,a
 ld a,(ix)
 ld iyh,a
 ld a,(hl)
 ld iyl,a
 ld a,(iy)
 ld (bc),a
 inc bc
 inc hl
 inc de
 ld a,(de)
 ld ixl,a
 ld a,(ix)
 ld iyh,a
 ld a,(hl)
 ld iyl,a
 ld a,(iy)
 ld (bc),a
 inc bc
 ex (sp),hl
 dec hl
 ld a,h
 or a,l
 ex (sp),hl
 jp nz,.ch5_fused_bg_3_loop
 pop hl
 pop iy
 pop ix
 ret
