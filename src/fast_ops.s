	.assume	adl=1
	.section	.text,"ax",@progbits

	.global	_fast_cg_upscale_2x
	.type	_fast_cg_upscale_2x, @function

	.global	_fast_rect_blit
	.type	_fast_rect_blit, @function

	.global	_fast_zoom_row
	.type	_fast_zoom_row, @function

	.global	_fast_row_shift
	.type	_fast_row_shift, @function

; ===========================================================================
; void fast_cg_upscale_2x(uint8_t *dest, const uint8_t *src)
;
; Parameters on stack:
;   (SP + 3): dest (24-bit pointer to 320-wide framebuffer area)
;   (SP + 6): src  (24-bit pointer to 160x90 source pixels)
; ===========================================================================
_fast_cg_upscale_2x:
	push	ix
	push	iy

	ld	iy, 0
	add	iy, sp

	ld	de, (iy + 9)		; DE = dest (row 0)
	ld	hl, (iy + 12)		; HL = src

	; Set IX = dest + 320 (row 1)
	ld	bc, 320
	push	de
	pop	ix
	add	ix, bc

	; Use IYL as row counter (90 rows -> 180 scanlines)
	ld	iyl, 90

.cg_row_loop:
	ld	b, 160			; 160 pixels per row

.cg_pixel_loop:
	ld	a, (hl)			; Read source pixel
	inc	hl

	ld	(de), a			; Write pixel twice to row 0
	inc	de
	ld	(de), a
	inc	de

	ld	(ix + 0), a		; Write pixel twice to row 1
	ld	(ix + 1), a
	lea	ix, ix + 2

	djnz	.cg_pixel_loop

	; After 160 pixels:
	; DE has advanced 320 bytes -> start of row 2y + 1 (previous IX start)
	; IX has advanced 320 bytes -> start of row 2y + 2 (next row 0)
	; Set next DE = IX, next IX = IX + 320
	push	ix
	pop	de			; DE = start of next row 0

	ld	bc, 320
	add	ix, bc			; IX = start of next row 1

	; Decrement row counter in IYL
	dec	iyl
	jr	nz, .cg_row_loop

	pop	iy
	pop	ix
	ret

; ===========================================================================
; void fast_rect_blit(uint8_t *dest, size_t dest_pitch,
;                     const uint8_t *src, size_t src_pitch,
;                     size_t w, size_t h)
;
; Parameters on stack:
;   (SP + 3):  dest
;   (SP + 6):  dest_pitch
;   (SP + 9):  src
;   (SP + 12): src_pitch
;   (SP + 15): w
;   (SP + 18): h
; ===========================================================================
_fast_rect_blit:
	push	ix
	push	iy

	ld	iy, 0
	add	iy, sp

	ld	de, (iy + 9)		; DE = dest
	ld	hl, (iy + 15)		; HL = src

	; Check if h == 0 or w == 0
	ld	bc, (iy + 21)		; BC = w
	ld	a, b
	or	a, c
	jr	z, .rect_done

	ld	a, (iy + 24)		; A = h (low byte)
	or	a, a
	jr	z, .rect_done

.rect_loop:
	push	hl
	push	de
	ld	bc, (iy + 21)		; BC = w
	ldir				; Copy w bytes from (HL) to (DE)
	pop	de
	pop	hl

	; Advance DE by dest_pitch
	ld	bc, (iy + 12)		; dest_pitch
	ex	de, hl
	add	hl, bc
	ex	de, hl

	; Advance HL by src_pitch
	ld	bc, (iy + 18)		; src_pitch
	add	hl, bc

	dec	a
	jr	nz, .rect_loop

.rect_done:
	pop	iy
	pop	ix
	ret

; ===========================================================================
; void fast_zoom_row(uint8_t *dst, const uint8_t *src, size_t zw)
;
; Parameters on stack:
;   (SP + 3): dst
;   (SP + 6): src
;   (SP + 9): zw
; ===========================================================================
_fast_zoom_row:
	push	ix
	push	iy

	ld	iy, 0
	add	iy, sp

	ld	de, (iy + 9)		; DE = dst
	ld	hl, (iy + 12)		; HL = src
	ld	bc, (iy + 15)		; BC = zw

	ld	a, b
	or	a, c
	jr	z, .zoom_done

	xor	a, a
	ld	ixh, a			; IXH = ax = 0

.zoom_loop:
	ld	a, (hl)			; Read source pixel
	ld	(de), a			; Write to destination
	inc	de

	ld	a, ixh			; Load ax
	add	a, 20			; ax += ZOOM_DEN (20)
	cp	a, 21			; ax >= ZOOM_NUM (21)?
	jr	c, .zoom_no_adv
	sub	a, 21			; ax -= 21
	inc	hl			; src++

.zoom_no_adv:
	ld	ixh, a			; Save ax
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
;
; Parameters on stack:
;   (SP + 3): row
;   (SP + 6): scratch
;   (SP + 9): off_wrapped
; ===========================================================================
_fast_row_shift:
	push	ix
	push	iy

	ld	iy, 0
	add	iy, sp

	ld	bc, (iy + 15)		; BC = off_wrapped
	ld	a, b
	or	a, c
	jr	z, .shift_done		; If off_wrapped == 0, nothing to do

	; Step 1: Copy 320 bytes from row to scratch
	ld	hl, (iy + 9)		; HL = row
	ld	de, (iy + 12)		; DE = scratch
	ld	bc, 320
	ldir

	; Step 2: Copy off_wrapped bytes from scratch + (320 - off_wrapped) to row
	; HL = scratch + 320 - off_wrapped
	ld	hl, (iy + 12)		; HL = scratch
	ld	bc, 320
	add	hl, bc			; HL = scratch + 320
	ld	bc, (iy + 15)		; BC = off_wrapped
	or	a, a
	sbc	hl, bc			; HL = scratch + 320 - off_wrapped

	ld	de, (iy + 9)		; DE = row
	push	bc			; Save off_wrapped
	ldir
	pop	bc			; Restore off_wrapped in BC

	; Step 3: Copy (320 - off_wrapped) bytes from scratch to row + off_wrapped
	; DE = row + off_wrapped
	ld	hl, (iy + 9)		; HL = row
	ld	bc, (iy + 15)		; BC = off_wrapped
	add	hl, bc			; HL = row + off_wrapped
	ex	de, hl			; DE = row + off_wrapped

	; HL = scratch
	ld	hl, (iy + 12)		; HL = scratch

	; Count = 320 - off_wrapped
	push	hl
	ld	hl, 320
	ld	bc, (iy + 15)		; BC = off_wrapped
	or	a, a
	sbc	hl, bc			; HL = 320 - off_wrapped
	push	hl
	pop	bc			; BC = 320 - off_wrapped
	pop	hl

	ldir

.shift_done:
	pop	iy
	pop	ix
	ret
