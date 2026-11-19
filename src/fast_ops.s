	.assume	adl=1

	.global	_fast_cg_upscale_2x
	.type	_fast_cg_upscale_2x, @function

	.global	_fast_cg_upscale_2x_rows
	.type	_fast_cg_upscale_2x_rows, @function

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

	.global	_fast_remap_bytes
	.type	_fast_remap_bytes, @function

	.global	_fast_stretch_row
	.type	_fast_stretch_row, @function

	.global	_fast_bayer_dissolve
	.type	_fast_bayer_dissolve, @function

	.global	_fast_zx0_decompress
	.type	_fast_zx0_decompress, @function

; ===========================================================================
; void fast_cg_upscale_2x_rows(uint8_t *dest, const uint8_t *src, uint8_t rows)
; void fast_cg_upscale_2x(uint8_t *dest, const uint8_t *src)
; ===========================================================================
	.section	.text._fast_cg_upscale_2x,"ax",@progbits
_fast_cg_upscale_2x:
	push	ix
	push	iy

	ld	iy, 0
	add	iy, sp

	ld	a, 90
	jr	.cg_common

_fast_cg_upscale_2x_rows:
	push	ix
	push	iy

	ld	iy, 0
	add	iy, sp

	ld	a, (iy + 15)		; rows
	or	a
	jr	z, .cg_done

.cg_common:
	ld	de, (iy + 9)		; DE = dest (row 0)
	ld	hl, (iy + 12)		; HL = src
	ld	iyl, a

.cg_row_loop:
	push	de
	pop	ix			; IX = start of row 0
	ld	b, 40			; 40 * 4 = 160 src pixels -> 320 dst pixels

.cg_pixel_loop:
	ld	a, (hl)
	inc	hl
	ld	(de), a
	inc	de
	ld	(de), a
	inc	de

	ld	a, (hl)
	inc	hl
	ld	(de), a
	inc	de
	ld	(de), a
	inc	de

	ld	a, (hl)
	inc	hl
	ld	(de), a
	inc	de
	ld	(de), a
	inc	de

	ld	a, (hl)
	inc	hl
	ld	(de), a
	inc	de
	ld	(de), a
	inc	de

	djnz	.cg_pixel_loop

	; Duplicate completed 320-byte row 0 into row 1 via hardware LDIR.
	; DE is already at row0 + 320 and finishes at row0 + 640 (next row 0).
	push	hl
	lea	hl, ix + 0
	ld	bc, 320
	ldir
	pop	hl

	dec	iyl
	jr	nz, .cg_row_loop

.cg_done:
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

	ld	bc, (iy + 21)		; BC = w
	ld	hl, 0
	or	a, a
	sbc	hl, bc
	jp	z, .rect_done

	ld	ix, (iy + 24)		; IX = h
	ld	hl, 0
	push	ix
	pop	de
	or	a, a
	sbc	hl, de
	jp	z, .rect_done

	; Precompute dest_skip = dest_pitch - w, src_skip = src_pitch - w
	ld	hl, (iy + 12)		; dest_pitch
	or	a, a
	sbc	hl, bc
	push	hl			; (iy - 3) = dest_skip

	ld	hl, (iy + 18)		; src_pitch
	or	a, a
	sbc	hl, bc
	push	hl			; (iy - 6) = src_skip

	ld	de, (iy + 9)		; DE = dest
	ld	hl, (iy + 15)		; HL = src

	; Fast 8-bit height paths when h <= 255 (every screen blit on 320x240)
	ld	a, (iy + 25)
	or	a, (iy + 26)
	jr	nz, .rect_loop_wide

	ld	a, (iy + 24)
	ld	ixl, a
	; Check if both 24-bit skips are zero (contiguous block, e.g. 320x60 textbox)
	ld	a, (iy - 3)
	or	a, (iy - 2)
	or	a, (iy - 1)
	or	a, (iy - 6)
	or	a, (iy - 5)
	or	a, (iy - 4)
	jr	nz, .rect_strided

.rect_contig:
	ld	bc, (iy + 21)
	ldir
	dec	ixl
	jr	nz, .rect_contig
	jr	.rect_pop_done

.rect_strided:
	ld	bc, (iy + 21)
	ldir
	ld	bc, (iy - 6)		; src_skip
	add	hl, bc
	ex	de, hl
	ld	bc, (iy - 3)		; dest_skip
	add	hl, bc
	ex	de, hl
	dec	ixl
	jr	nz, .rect_strided
	jr	.rect_pop_done

.rect_loop_wide:
	ld	bc, (iy + 21)
	ldir
	ld	bc, (iy - 6)
	add	hl, bc
	ex	de, hl
	ld	bc, (iy - 3)
	add	hl, bc
	ex	de, hl
	dec	ix
	push	hl
	ld	hl, 0
	push	ix
	pop	bc
	or	a, a
	sbc	hl, bc
	pop	hl
	jr	nz, .rect_loop_wide

.rect_pop_done:
	pop	hl
	pop	hl

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
.macro scale_px_fast n
	ld	a, (hl)
	or	a, a
	jr	z, .sc_skip\n
	ld	(de), a
.sc_skip\n:
	inc	de
	exx
	add	hl, bc
	jr	nc, .sc_no\n
	add	hl, de
	exx
	inc	hl
	jr	.sc_next\n
.sc_no\n:
	exx
.sc_next\n:
.endm

_fast_scale_row_core:
	; Use 4x unrolled carry-wrap DDA when count < 1024, step_x <= div_x, and acc_x < div_x.
	ld	a, b
	cp	a, 4
	jp	nc, .scale_slow
	push	hl
	exx
	push	hl			; save acc_x
	push	de
	pop	hl			; HL' = div_x (preserve DE' = div_x!)
	or	a, a
	sbc	hl, bc			; div_x - step_x
	pop	hl			; HL' = acc_x, DE' = div_x
	jp	c, .scale_slow_pop
	or	a, a
	sbc	hl, de			; HL' = acc_x - div_x (< 0)
	jp	nc, .scale_slow_fix
	push	hl
	ld	hl, 0
	or	a, a
	sbc	hl, de
	ex	de, hl			; DE' = -div_x
	pop	hl			; HL' = acc_x - div_x
	exx
	pop	hl			; HL = src_row

	ld	a, c
	and	a, 3
	push	af
	srl	b
	rr	c
	srl	b
	rr	c
	ld	a, c
	or	a, a
	jr	z, .sc_tail_chk
	ld	b, a
.sc_fast4:
	scale_px_fast 0
	scale_px_fast 1
	scale_px_fast 2
	scale_px_fast 3
	djnz	.sc_fast4
.sc_tail_chk:
	pop	af
	or	a, a
	ret	z
	ld	b, a
.sc_fast_tail:
	scale_px_fast t
	djnz	.sc_fast_tail
	ret

.scale_slow_fix:
	add	hl, de
.scale_slow_pop:
	exx
	pop	hl

.scale_slow:
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

; ===========================================================================
; void fast_remap_bytes(uint8_t *buf, const uint8_t *lut_aligned256, uint16_t count)
; @pre lut_aligned256 is 256-byte aligned (L == 0).
; ===========================================================================
	.section	.text._fast_remap_bytes,"ax",@progbits
_fast_remap_bytes:
	push	ix
	push	iy

	ld	iy, 0
	add	iy, sp

	ld	de, (iy + 9)		; DE = buf
	ld	hl, (iy + 12)		; HL = lut (L == 0)
	ld	bc, (iy + 15)		; BC = count (0..65535)
	ld	a, b
	or	a, c
	jr	z, .remap_done

	; 4x unrolled loop over count >> 2 quads, then 0..3 tail bytes
	ld	a, c
	and	a, 3
	ld	ixl, a			; tail = count & 3
	srl	b
	rr	c
	srl	b
	rr	c
	ld	a, b
	or	a, c
	jr	z, .remap_tail_chk

.remap_quad:
	ld	a, (de)
	ld	l, a
	ld	a, (hl)
	ld	(de), a
	inc	de

	ld	a, (de)
	ld	l, a
	ld	a, (hl)
	ld	(de), a
	inc	de

	ld	a, (de)
	ld	l, a
	ld	a, (hl)
	ld	(de), a
	inc	de

	ld	a, (de)
	ld	l, a
	ld	a, (hl)
	ld	(de), a
	inc	de

	dec	bc
	ld	a, b
	or	a, c
	jr	nz, .remap_quad

.remap_tail_chk:
	ld	a, ixl
	or	a, a
	jr	z, .remap_done
	ld	b, a
.remap_tail:
	ld	a, (de)
	ld	l, a
	ld	a, (hl)
	ld	(de), a
	inc	de
	djnz	.remap_tail

.remap_done:
	pop	iy
	pop	ix
	ret

; ===========================================================================
; void fast_stretch_row(uint8_t *dst, const uint8_t *src,
;                       size_t dst_w, size_t src_w)
; Opaque nearest-neighbor horizontal stretch (dst_w <= 511, src_w >= 1).
; ===========================================================================
	.section	.text._fast_stretch_row,"ax",@progbits
.macro stretch_px_fast n
	ld	a, (hl)
	ld	(de), a
	inc	de
	exx
	add	hl, bc
	jr	nc, .st_no\n
	add	hl, de
	exx
	inc	hl
	jr	.st_next\n
.st_no\n:
	exx
.st_next\n:
.endm

_fast_stretch_row:
	push	ix
	push	iy

	ld	iy, 0
	add	iy, sp

	ld	bc, (iy + 15)		; BC = dst_w
	ld	hl, 0
	or	a, a
	sbc	hl, bc
	jp	z, .stretch_done

	ld	de, (iy + 18)		; DE = src_w
	ld	hl, 0
	or	a, a
	sbc	hl, de
	jp	z, .stretch_done

	; Fast 4x carry-wrap path when src_w <= dst_w and dst_w < 1024
	ld	a, b
	cp	a, 4
	jp	nc, .stretch_slow
	ld	hl, (iy + 15)
	or	a, a
	sbc	hl, de			; dst_w - src_w
	jp	c, .stretch_slow

	ld	hl, 0
	or	a, a
	sbc	hl, bc			; -dst_w
	exx
	ld	de, (iy + 15)		; tmp dst_w
	ld	hl, 0
	or	a, a
	sbc	hl, de
	ex	de, hl			; DE' = -dst_w
	push	de
	pop	hl			; HL' = -dst_w (acc = 0)
	ld	bc, (iy + 18)		; BC' = src_w
	exx

	ld	de, (iy + 9)		; DE = dst
	ld	hl, (iy + 12)		; HL = src
	ld	a, c
	and	a, 3
	push	af
	srl	b
	rr	c
	srl	b
	rr	c
	ld	a, c
	or	a, a
	jr	z, .st_tail_chk
	ld	b, a
.st_fast4:
	stretch_px_fast 0
	stretch_px_fast 1
	stretch_px_fast 2
	stretch_px_fast 3
	djnz	.st_fast4
.st_tail_chk:
	pop	af
	or	a, a
	jr	z, .stretch_done
	ld	b, a
.st_fast_tail:
	stretch_px_fast t
	djnz	.st_fast_tail
	jr	.stretch_done

.stretch_slow:
	ld	de, (iy + 9)		; DE = dst
	ld	hl, (iy + 12)		; HL = src

	exx
	ld	hl, 0			; HL' = acc = 0
	ld	de, (iy + 15)		; DE' = dst_w
	ld	bc, (iy + 18)		; BC' = src_w
	exx

	ld	a, b
	or	a, a
	jr	z, .stretch_rem

	ld	b, 0
	call	.stretch_run

.stretch_rem:
	ld	a, c
	or	a, a
	jr	z, .stretch_done

	ld	b, a
	call	.stretch_run

.stretch_done:
	pop	iy
	pop	ix
	ret

.stretch_run:
	ld	a, (hl)
	ld	(de), a
	inc	de
	exx
	add	hl, bc
	or	a, a
	sbc	hl, de
	jr	c, .stretch_restore
.stretch_step:
	exx
	inc	hl
	exx
	or	a, a
	sbc	hl, de
	jr	nc, .stretch_step
.stretch_restore:
	add	hl, de
	exx
	djnz	.stretch_run
	ret

; ===========================================================================
; void fast_bayer_dissolve(uint8_t *dst, const uint8_t *src, uint8_t step)
; Copies pixels matching Bayer 4x4 threshold for step 0..3 across 320x180.
; step 0: 25%, step 1: 50%, step 2: 75%, step 3: 100%.
; ===========================================================================
	.section	.text._fast_bayer_dissolve,"ax",@progbits
_fast_bayer_dissolve:
	push	ix
	push	iy

	ld	iy, 0
	add	iy, sp

	ld	a, (iy + 15)		; A = step
	and	a, 3
	ld	ixh, 0xAA
	ld	ixl, 0x00
	jr	z, .bayer_masks_ok
	cp	a, 1
	jr	nz, .bayer_chk2
	ld	ixl, 0x55
	jr	.bayer_masks_ok
.bayer_chk2:
	cp	a, 2
	jr	nz, .bayer_step3
	ld	ixh, 0xFF
	ld	ixl, 0x55
	jr	.bayer_masks_ok
.bayer_step3:
	ld	ixh, 0xFF
	ld	ixl, 0xFF

.bayer_masks_ok:
	ld	de, (iy + 9)		; DE = dst
	ld	hl, (iy + 12)		; HL = src
	ld	iyl, 180		; 180 rows

.bayer_row:
	ld	c, ixh
	ld	a, iyl
	bit	0, a
	jr	z, .bayer_has_mask
	ld	c, ixl
.bayer_has_mask:
	ld	a, c
	or	a, a
	jr	nz, .bayer_not_zero
	push	bc
	ld	bc, 320
	add	hl, bc
	ex	de, hl
	add	hl, bc
	ex	de, hl
	pop	bc
	jr	.bayer_next_row

.bayer_not_zero:
	cp	a, 0xFF
	jr	nz, .bayer_masked
	ld	bc, 320
	ldir
	jr	.bayer_next_row

.bayer_masked:
	ld	b, 80			; 80 * 4 = 320 pixels
.bayer_px4:
	rlc	c
	jr	nc, .bp0
	ld	a, (hl)
	ld	(de), a
.bp0:
	inc	hl
	inc	de

	rlc	c
	jr	nc, .bp1
	ld	a, (hl)
	ld	(de), a
.bp1:
	inc	hl
	inc	de

	rlc	c
	jr	nc, .bp2
	ld	a, (hl)
	ld	(de), a
.bp2:
	inc	hl
	inc	de

	rlc	c
	jr	nc, .bp3
	ld	a, (hl)
	ld	(de), a
.bp3:
	inc	hl
	inc	de

	djnz	.bayer_px4

.bayer_next_row:
	dec	iyl
	jr	nz, .bayer_row

	pop	iy
	pop	ix
	ret

; ===========================================================================
; void fast_zx0_decompress(void *dst, const void *src)
; Turbo eZ80 ZX0 decompressor with full 24-bit run-length support (>= 65,536 B).
; ===========================================================================
	.section	.text._fast_zx0_decompress,"ax",@progbits
_fast_zx0_decompress:
	push	ix
	push	iy

	ld	iy, 0
	add	iy, sp
	ld	de, (iy + 9)		; DE = dst
	ld	hl, (iy + 12)		; HL = src

	ld	iy, 0xFFFFFF		; IY = -1 (last_offset = 1)
	lea	bc, iy + 2		; BC = 1 (BCU = 0)
	scf				; sentinel bit for first rla
	jr	.zx0t_literals_start

.zx0t_new_offset:
	dec	bc
	dec	bc			; BC = 0xFFFFFE (C = 0xFE, B = 0xFF)
	add	a, a
	jr	nz, .zx0t_no_skip
	ld	a, (hl)
	inc	hl
	rla
.zx0t_no_skip:
	call	nc, .zx0t_elias_off
	inc	c
	jr	z, .zx0t_done
	ld	b, (hl)
	inc	hl
	rr	c
	rr	b
	ld	iyl, b
	ld	iyh, c			; IY = -offset (IYU remains 0xFF)
	ld	bc, 1			; BC = 1 (BCU = 0)
	call	nc, .zx0t_elias_len
	inc	bc

.zx0t_copy:
	push	hl
	lea	hl, iy + 0
	add	hl, de
	ldir
	pop	hl
	add	a, a
	jr	c, .zx0t_new_offset

.zx0t_literals:
	inc	c			; BC = 1 (BCU = 0)
	add	a, a
	jr	nz, .zx0t_lit_skip
.zx0t_literals_start:
	ld	a, (hl)
	inc	hl
	rla
.zx0t_lit_skip:
	call	nc, .zx0t_elias_len
	ldir
	add	a, a
	jr	c, .zx0t_new_offset

.zx0t_last_offset:
	inc	c			; BC = 1 (BCU = 0)
	add	a, a
	jr	nz, .zx0t_lo_skip
	ld	a, (hl)
	inc	hl
	rla
.zx0t_lo_skip:
	call	nc, .zx0t_elias_len
	jr	.zx0t_copy

.zx0t_done:
	pop	iy
	pop	ix
	ret

; Fast 4-step Elias prefix for offsets (< 16 in C, then 16-bit loop).
.zx0t_elias_off:
	add	a, a
	rl	c
	add	a, a
	jr	nc, .zx0t_elias_off
	ret	nz
	ld	a, (hl)
	inc	hl
	rla
	ret	c
	add	a, a
	rl	c
	add	a, a
	ret	c
	add	a, a
	rl	c
	add	a, a
	ret	c
	add	a, a
	rl	c
	add	a, a
	ret	c
.zx0t_eo_loop:
	add	a, a
	rl	c
	rl	b
	add	a, a
	jr	nc, .zx0t_eo_loop
	ret	nz
	ld	a, (hl)
	inc	hl
	rla
	jr	nc, .zx0t_eo_loop
	ret

; Fast Elias length reader: C fast prefix (< 16), 16-bit (B,C), 24-bit IX on carry.
.zx0t_elias_len:
	add	a, a
	rl	c
	add	a, a
	jr	nc, .zx0t_elias_len
	ret	nz
	ld	a, (hl)
	inc	hl
	rla
	ret	c
	add	a, a
	rl	c
	add	a, a
	ret	c
	add	a, a
	rl	c
	add	a, a
	ret	c
	add	a, a
	rl	c
	add	a, a
	ret	c
.zx0t_el_loop16:
	add	a, a
	jr	nz, .zx0t_el_d16
	ld	a, (hl)
	inc	hl
	rla
.zx0t_el_d16:
	rl	c
	rl	b
	jr	c, .zx0t_elias_24
	add	a, a
	jr	nz, .zx0t_el_c16
	ld	a, (hl)
	inc	hl
	rla
.zx0t_el_c16:
	jr	nc, .zx0t_el_loop16
	ret

.zx0t_elias_24:
	push	hl
	ld	hl, 0x010000
	add	hl, bc
	push	hl
	pop	ix
	pop	hl
.zx0t_el24_ctl:
	add	a, a
	jr	nz, .zx0t_el24_have_ctl
	ld	a, (hl)
	inc	hl
	rla
.zx0t_el24_have_ctl:
	jr	c, .zx0t_el24_done
	add	ix, ix
	add	a, a
	jr	nz, .zx0t_el24_have_data
	ld	a, (hl)
	inc	hl
	rla
.zx0t_el24_have_data:
	jr	nc, .zx0t_el24_ctl
	inc	ix
	jr	.zx0t_el24_ctl
.zx0t_el24_done:
	push	ix
	pop	bc
	ret
