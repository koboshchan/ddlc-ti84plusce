	.assume adl=1
	.section .text,"ax",@progbits
	.global _ch5_motion_opaque_fast
	.type _ch5_motion_opaque_fast,@function
; Exact indexed scanline, already clipped by the caller.
; void ch5_motion_opaque_fast(dst, src, xmap, count)
; Every xmap entry is a nonnegative uint16 source offset.
.macro opfast_px k
	or	a, a
	sbc	hl, hl
	ld	l, (ix + 2*\k)
	ld	h, (ix + 2*\k + 1)
	add	hl, bc
	ld	a, (hl)
	cp	a, 255
	jr	z, .opf_sk\k
	ld	(de), a
.opf_sk\k:
	inc	de
.endm

_ch5_motion_opaque_fast:
	push	ix
	push	iy
	ld	ix, 0
	add	ix, sp
	ld	de, (ix + 9)		; DE = dst
	ld	bc, (ix + 12)		; BC = src
	ld	iy, (ix + 18)		; IY = count
	ld	ix, (ix + 15)		; IX = xmap
	ld	a, iyl
	and	a, 7
	push	af			; save tail (0..7)
	push	iy
	pop	hl
	srl	h
	rr	l
	srl	h
	rr	l
	srl	h
	rr	l			; HL = count >> 3 (assume < 65536)
	ld	a, h
	or	a, l
	jp	z, .opf_tail_setup
	push	hl
	pop	iy			; IY = groups of 8
.opf_loop8:
	opfast_px 0
	opfast_px 1
	opfast_px 2
	opfast_px 3
	opfast_px 4
	opfast_px 5
	opfast_px 6
	opfast_px 7
	lea	ix, ix + 16
	dec	iy
	ld	a, iyl
	or	a, iyh
	jp	nz, .opf_loop8
.opf_tail_setup:
	pop	af
	or	a, a
	jr	z, .done
	ld	iyl, a
.loop:
	or	a, a
	sbc	hl, hl
	ld	l, (ix + 0)
	ld	h, (ix + 1)
	add	hl, bc
	ld	a, (hl)
	cp	a, 255
	jr	z, .transparent
	ld	(de), a
.transparent:
	inc	de
	lea	ix, ix + 2
	dec	iyl
	jr	nz, .loop
.done:
	pop	iy
	pop	ix
	ret


.macro qfast_px k
	or	a, a
	sbc	hl, hl
	ld	l, (ix + 2*\k)
	ld	h, (ix + 2*\k + 1)
	add	hl, bc
	ld	a, (hl)
	cp	a, 255
	jr	z, .qf_sk\k
	exx
	ld	l, a
	ld	a, (hl)
	exx
	ld	iyh, a
	ld	a, (de)
	ld	iyl, a
	ld	a, (iy)
	ld	(de), a
.qf_sk\k:
	inc	de
.endm

 .global _ch5_motion_quant_fast
 .type _ch5_motion_quant_fast,@function
_ch5_motion_quant_fast:
 push ix
 push iy
 ld ix,0
 add ix,sp
 ld hl,(ix+18)
 ld a,l
 and a,7
 push af
 srl h
 rr l
 srl h
 rr l
 srl h
 rr l
 ld de,(ix+21)
 push de
 push hl
 exx
 pop bc
 pop hl
 ld a,b
 or a,c
 exx
 ld de,(ix+9)
 ld bc,(ix+12)
 ld iy,(ix+24)
 ld ix,(ix+15)
 jp z,.qf_tail
.qf_loop8:
 qfast_px 0
 qfast_px 1
 qfast_px 2
 qfast_px 3
 qfast_px 4
 qfast_px 5
 qfast_px 6
 qfast_px 7
 lea ix,ix+16
 exx
 dec bc
 ld a,b
 or a,c
 exx
 jp nz,.qf_loop8
.qf_tail:
 pop af
 or a,a
 jr z,.qf_done
.qf_tloop:
 ex af,af'
 or a,a
 sbc hl,hl
 ld l,(ix+0)
 ld h,(ix+1)
 add hl,bc
 ld a,(hl)
 cp a,255
 jr z,.qf_tsk
 exx
 ld l,a
 ld a,(hl)
 exx
 ld iyh,a
 ld a,(de)
 ld iyl,a
 ld a,(iy)
 ld (de),a
.qf_tsk:
 inc de
 lea ix,ix+2
 ex af,af'
 dec a
 jr nz,.qf_tloop
.qf_done:
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
 ld a,80
.ch5_fused_bg_0_loop:
 ex af,af'
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
 ex af,af'
 dec a
 jp nz,.ch5_fused_bg_0_loop
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
 ld a,80
.ch5_fused_bg_1_loop:
 ex af,af'
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
 ld a,(hl)
 ld iyl,a
 ld a,(iy)
 ld (bc),a
 inc bc
 inc de
 ex af,af'
 dec a
 jp nz,.ch5_fused_bg_1_loop
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
 ld a,80
.ch5_fused_bg_2_loop:
 ex af,af'
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
 ld a,(iy)
 ld (bc),a
 inc bc
 inc hl
 ex af,af'
 dec a
 jp nz,.ch5_fused_bg_2_loop
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
 ld a,80
.ch5_fused_bg_3_loop:
 ex af,af'
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
 ex af,af'
 dec a
 jp nz,.ch5_fused_bg_3_loop
 pop iy
 pop ix
 ret
