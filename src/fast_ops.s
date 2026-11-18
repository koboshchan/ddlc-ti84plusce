	.assume	adl=1

	.global	_fast_cg_upscale_2x
	.type	_fast_cg_upscale_2x, @function

	.global	_fast_rect_blit
	.type	_fast_rect_blit, @function

	.global	_fast_zoom_row
	.type	_fast_zoom_row, @function

	.global	_fast_row_shift
	.type	_fast_row_shift, @function

	.global	_fast_scale_row_trans
	.type	_fast_scale_row_trans, @function

	.global	_fast_scale_sprite_trans
	.type	_fast_scale_sprite_trans, @function

	.global	_fast_rlet_skip_rows
	.type	_fast_rlet_skip_rows, @function

	.global	_fast_rlet_decode_row
	.type	_fast_rlet_decode_row, @function

	.global	_fast_title_bg
	.type	_fast_title_bg, @function

; ===========================================================================
; void fast_cg_upscale_2x(uint8_t *dest, const uint8_t *src)
; ===========================================================================
	.section	.text._fast_cg_upscale_2x,"ax",@progbits
_fast_cg_upscale_2x:
	push	ix
	push	iy

	ld	iy, 0
	add	iy, sp

	ld	de, (iy + 9)		; DE = dest (row 0)
	ld	hl, (iy + 12)		; HL = src

	ld	bc, 320
	push	de
	pop	ix
	add	ix, bc			; IX = dest + 320 (row 1)

	ld	iyl, 90

.cg_row_loop:
	ld	b, 160

.cg_pixel_loop:
	ld	a, (hl)
	inc	hl

	ld	(de), a
	inc	de
	ld	(de), a
	inc	de

	ld	(ix + 0), a
	ld	(ix + 1), a
	lea	ix, ix + 2

	djnz	.cg_pixel_loop

	push	ix
	pop	de			; DE = start of next row 0
	ld	bc, 320
	add	ix, bc			; IX = start of next row 1

	dec	iyl
	jr	nz, .cg_row_loop

	pop	iy
	pop	ix
	ret

; ===========================================================================
; void fast_rect_blit(uint8_t *dest, size_t dest_pitch,
;                     const uint8_t *src, size_t src_pitch,
;                     size_t w, size_t h)
; ===========================================================================
	.section	.text._fast_rect_blit,"ax",@progbits
_fast_rect_blit:
	push	ix
	push	iy

	ld	iy, 0
	add	iy, sp

	ld	de, (iy + 9)		; DE = dest
	ld	hl, (iy + 15)		; HL = src

	ld	bc, (iy + 21)		; BC = w
	push	hl
	ld	hl, 0
	or	a, a
	sbc	hl, bc
	pop	hl
	jr	z, .rect_done

	ld	ix, (iy + 24)		; height
	push	hl
	ld	hl, 0
	push	ix
	pop	bc
	or	a, a
	sbc	hl, bc
	pop	hl
	jr	z, .rect_done

.rect_loop:
	push	hl
	push	de
	ld	bc, (iy + 21)
	ldir
	pop	de
	pop	hl

	ld	bc, (iy + 12)		; dest_pitch
	ex	de, hl
	add	hl, bc
	ex	de, hl

	ld	bc, (iy + 18)		; src_pitch
	add	hl, bc

	dec	ix
	push	hl
	ld	hl, 0
	push	ix
	pop	bc
	or	a, a
	sbc	hl, bc
	pop	hl
	jr	nz, .rect_loop

.rect_done:
	pop	iy
	pop	ix
	ret

; ===========================================================================
; void fast_zoom_row(uint8_t *dst, const uint8_t *src, size_t zw)
; ===========================================================================
	.section	.text._fast_zoom_row,"ax",@progbits
_fast_zoom_row:
	push	ix
	push	iy

	ld	iy, 0
	add	iy, sp

	ld	de, (iy + 9)		; DE = dst
	ld	hl, (iy + 12)		; HL = src
	ld	bc, (iy + 15)		; BC = zw

	push	hl
	ld	hl, 0
	or	a, a
	sbc	hl, bc
	pop	hl
	jr	z, .zoom_done

	xor	a, a
	ld	ixh, a			; IXH = ax = 0

.zoom_loop:
	ld	a, (hl)
	ld	(de), a
	inc	de

	ld	a, ixh
	add	a, 20
	cp	a, 21
	jr	c, .zoom_no_adv
	sub	a, 21
	inc	hl

.zoom_no_adv:
	ld	ixh, a
	dec	bc
	ld	a, b
	or	a, c
	jr	nz, .zoom_loop

.zoom_done:
	pop	iy
	pop	ix
	ret

; ===========================================================================
; void fast_row_shift(uint8_t *row, uint8_t *scratch, size_t off_wrapped)
; ===========================================================================
	.section	.text._fast_row_shift,"ax",@progbits
_fast_row_shift:
	push	ix
	push	iy

	ld	iy, 0
	add	iy, sp

	ld	bc, (iy + 15)		; BC = off_wrapped
	push	hl
	ld	hl, 0
	or	a, a
	sbc	hl, bc
	pop	hl
	jr	z, .shift_done

	ld	hl, (iy + 9)		; HL = row
	ld	de, (iy + 12)		; DE = scratch
	ld	bc, 320
	ldir

	ld	hl, (iy + 12)
	ld	bc, 320
	add	hl, bc
	ld	bc, (iy + 15)
	or	a, a
	sbc	hl, bc			; HL = scratch + 320 - off_wrapped

	ld	de, (iy + 9)		; DE = row
	ldir

	ld	hl, (iy + 9)
	ld	bc, (iy + 15)
	add	hl, bc
	ex	de, hl			; DE = row + off_wrapped

	ld	hl, (iy + 12)		; HL = scratch
	push	hl
	ld	hl, 320
	ld	bc, (iy + 15)
	or	a, a
	sbc	hl, bc
	push	hl
	pop	bc
	pop	hl
	ldir

.shift_done:
	pop	iy
	pop	ix
	ret

; ===========================================================================
; void fast_scale_row_trans(uint8_t *dest, const uint8_t *src_row, size_t count,
;                           unsigned int acc_x, unsigned int step_x, unsigned int div_x)
; ===========================================================================
	.section	.text._fast_scale_row_trans,"ax",@progbits
_fast_scale_row_trans:
	push	ix
	push	iy

	ld	iy, 0
	add	iy, sp

	ld	bc, (iy + 15)		; BC = count
	push	hl
	ld	hl, 0
	or	a, a
	sbc	hl, bc
	pop	hl
	jr	z, .row_done

	ld	de, (iy + 9)		; DE = dest
	ld	hl, (iy + 12)		; HL = src_row

	exx
	ld	hl, (iy + 18)		; HL' = acc_x
	ld	bc, (iy + 21)		; BC' = step_x
	ld	de, (iy + 24)		; DE' = div_x
	exx

	call	_fast_scale_row_core

.row_done:
	pop	iy
	pop	ix
	ret

; Shared internal row scaler core (separate section referenced by both scalers).
	.section	.text._fast_scale_row_core,"ax",@progbits
_fast_scale_row_core:
	ld	a, b
	or	a, a
	jr	z, .short_row

	ld	b, 0
.loop256:
	ld	a, (hl)
	or	a, a
	jr	z, .skip256
	ld	(de), a
.skip256:
	inc	de
	exx
	add	hl, bc
	or	a, a
	sbc	hl, de
	jr	c, .restore256
.step256:
	exx
	inc	hl
	exx
	or	a, a
	sbc	hl, de
	jr	nc, .step256
.restore256:
	add	hl, de
	exx
	djnz	.loop256

.short_row:
	ld	a, c
	or	a, a
	ret	z
	ld	b, a

.loop_rem:
	ld	a, (hl)
	or	a, a
	jr	z, .skip_rem
	ld	(de), a
.skip_rem:
	inc	de
	exx
	add	hl, bc
	or	a, a
	sbc	hl, de
	jr	c, .restore_rem
.step_rem:
	exx
	inc	hl
	exx
	or	a, a
	sbc	hl, de
	jr	nc, .step_rem
.restore_rem:
	add	hl, de
	exx
	djnz	.loop_rem
	ret

; ===========================================================================
; void fast_scale_sprite_trans(uint8_t *dest, size_t dest_pitch,
;                              const uint8_t *src, size_t src_w, size_t src_h,
;                              size_t dst_w, size_t dst_h)
; ===========================================================================
	.section	.text._fast_scale_sprite_trans,"ax",@progbits
_fast_scale_sprite_trans:
	push	ix
	push	iy

	ld	iy, 0
	add	iy, sp

	ld	bc, (iy + 18)		; src_w
	push	hl
	ld	hl, 0
	or	a, a
	sbc	hl, bc
	pop	hl
	jp	z, .sprite_done

	ld	bc, (iy + 21)		; src_h
	push	hl
	ld	hl, 0
	or	a, a
	sbc	hl, bc
	pop	hl
	jp	z, .sprite_done

	ld	bc, (iy + 24)		; dst_w
	push	hl
	ld	hl, 0
	or	a, a
	sbc	hl, bc
	pop	hl
	jp	z, .sprite_done

	ld	bc, (iy + 27)		; dst_h
	push	hl
	ld	hl, 0
	or	a, a
	sbc	hl, bc
	pop	hl
	jp	z, .sprite_done

	ld	hl, (iy + 9)
	push	hl			; (iy - 3) = cur_dest
	ld	hl, (iy + 15)
	push	hl			; (iy - 6) = cur_src

	ld	hl, (iy + 27)
	srl	h
	rr	l
	push	hl			; (iy - 9) = acc_y

	ld	hl, (iy + 27)
	push	hl			; (iy - 12) = y_counter

.sprite_row_loop:
	ld	de, (iy - 3)
	ld	hl, (iy - 6)
	ld	bc, (iy + 24)

	exx
	ld	hl, (iy + 24)
	srl	h
	rr	l
	ld	bc, (iy + 18)
	ld	de, (iy + 24)
	exx

	call	_fast_scale_row_core

	ld	hl, (iy - 3)
	ld	bc, (iy + 12)
	add	hl, bc
	ld	(iy - 3), hl

	ld	hl, (iy - 9)
	ld	bc, (iy + 21)
	add	hl, bc
	ld	de, (iy + 27)

.acc_y_loop:
	or	a, a
	sbc	hl, de
	jr	c, .acc_y_done
	push	hl
	ld	hl, (iy - 6)
	ld	bc, (iy + 18)
	add	hl, bc
	ld	(iy - 6), hl
	pop	hl
	jr	.acc_y_loop

.acc_y_done:
	add	hl, de
	ld	(iy - 9), hl

	ld	hl, (iy - 12)
	dec	hl
	ld	(iy - 12), hl
	ld	a, h
	or	a, l
	jr	nz, .sprite_row_loop

	ld	hl, 12
	add	hl, sp
	ld	sp, hl

.sprite_done:
	pop	iy
	pop	ix
	ret

; ===========================================================================
; const uint8_t *fast_rlet_skip_rows(const uint8_t *rlet, size_t n_rows, uint8_t width)
; ===========================================================================
	.section	.text._fast_rlet_skip_rows,"ax",@progbits
_fast_rlet_skip_rows:
	push	ix
	ld	ix, 0
	add	ix, sp

	ld	hl, (ix + 6)		; HL = rlet
	ld	de, (ix + 9)		; DE = n_rows
	ld	a, (ix + 12)		; A = width
	or	a, a
	jr	z, .rlet_skip_done

	push	hl
	ld	hl, 0
	or	a, a
	sbc	hl, de
	pop	hl
	jr	z, .rlet_skip_done

	ld	ixl, a			; IXL = width
	ld	bc, 0			; keep BCU=0 for 24-bit add hl, bc

.rlet_skip_row:
	ld	a, ixl
	ld	ixh, a			; IXH = remaining pixels in row

.rlet_skip_seg:
	ld	c, (hl)			; trans_run
	inc	hl
	ld	a, ixh
	sub	a, c
	jr	c, .rlet_skip_row_next
	jr	z, .rlet_skip_row_next

	ld	c, (hl)			; opaque_run
	inc	hl
	ld	b, 0
	add	hl, bc
	sub	a, c
	jr	c, .rlet_skip_row_next
	jr	z, .rlet_skip_row_next

	ld	ixh, a
	jr	.rlet_skip_seg

.rlet_skip_row_next:
	dec	de
	push	hl
	ld	hl, 0
	or	a, a
	sbc	hl, de
	pop	hl
	jr	nz, .rlet_skip_row

.rlet_skip_done:
	pop	ix
	ret

; ===========================================================================
; const uint8_t *fast_rlet_decode_row(uint8_t *dst_row, const uint8_t *rlet, uint8_t width)
; ===========================================================================
	.section	.text._fast_rlet_decode_row,"ax",@progbits
_fast_rlet_decode_row:
	push	ix
	ld	ix, 0
	add	ix, sp

	ld	de, (ix + 6)		; DE = dst_row
	ld	hl, (ix + 9)		; HL = rlet
	ld	a, (ix + 12)		; A = width
	or	a, a
	jr	z, .rlet_dec_done

	ld	ixh, a			; IXH = remaining pixels in row
	ld	bc, 0			; keep BCU=0 for 24-bit ldir

.rlet_dec_seg:
	ld	c, (hl)			; trans_run
	inc	hl
	ld	a, ixh
	cp	a, c
	jr	nc, .rlet_dec_trans_ok
	ld	c, a
.rlet_dec_trans_ok:
	ld	a, c
	or	a, a
	jr	z, .rlet_dec_after_trans
	ld	b, a
	xor	a, a
.rlet_dec_zloop:
	ld	(de), a
	inc	de
	djnz	.rlet_dec_zloop

.rlet_dec_after_trans:
	ld	a, ixh
	sub	a, c
	jr	z, .rlet_dec_done
	ld	ixh, a

	ld	c, (hl)			; opaque_run
	inc	hl
	ld	a, ixh
	cp	a, c
	jr	nc, .rlet_dec_op_ok
	ld	c, a
.rlet_dec_op_ok:
	ld	a, c
	or	a, a
	jr	z, .rlet_dec_seg
	ld	ixl, a
	ld	b, 0
	ldir

	ld	a, ixh
	sub	a, ixl
	jr	z, .rlet_dec_done
	ld	ixh, a
	jr	.rlet_dec_seg

.rlet_dec_done:
	pop	ix
	ret

; ===========================================================================
; void fast_title_bg(uint8_t *dest, const uint8_t *strip, uint8_t px, uint8_t py)
; ===========================================================================
	.section	.text._fast_title_bg,"ax",@progbits
_fast_title_bg:
	push	ix
	push	iy

	ld	iy, 0
	add	iy, sp

	ld	hl, (iy + 12)		; HL = strip
	ld	bc, 0
	ld	c, (iy + 15)		; px
	add	hl, bc
	push	hl			; (iy - 3) = strip + px

	ld	h, (iy + 18)		; py
	ld	l, 185			; 370 / 2
	mlt	hl			; HL = py * 185
	add	hl, hl			; HL = py * 370
	ex	de, hl
	ld	hl, (iy - 3)
	add	hl, de			; HL = strip + px + py * 370

	ld	de, (iy + 9)		; DE = dest
	ld	a, 50
	sub	a, (iy + 18)
	ld	ixh, a			; IXH = rows remaining before tile wrap
	ld	ixl, 240		; IXL = total screen rows

.tbg_row_loop:
	ld	bc, 320
	ldir
	dec	ixh
	jr	nz, .tbg_next_row
	ld	hl, (iy - 3)
	ld	ixh, 50
	jr	.tbg_check_done
.tbg_next_row:
	ld	bc, 50
	add	hl, bc
.tbg_check_done:
	dec	ixl
	jr	nz, .tbg_row_loop

	pop	hl
	pop	iy
	pop	ix
	ret
