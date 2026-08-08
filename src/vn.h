/**
 * @file vn.h
 * @brief Visual-novel bytecode VM.
 *
 * This translation unit is deliberately free of any calculator-specific
 * headers so it can also be compiled natively by tools/host_sim. Everything
 * platform-dependent goes through the vn_host_t callback table.
 *
 * The bytecode format is specified in docs/FORMAT.md.
 */

#ifndef VN_H
#define VN_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* ---------------------------------------------------------------------------
 * Opcodes
 * ------------------------------------------------------------------------ */

enum vn_op {
    OP_NOP      = 0x00, /*                                    unsupported node */
    OP_SAY      = 0x01, /* spk:u8 text:u16                                     */
    OP_SCENE    = 0x02, /* bg:u8 trans:u8                                      */
    OP_SHOW     = 0x03, /* ch:u8 sprite:u16 overlay:u16 pos:u8 -- overlay is
                          * VN_NO_OVERLAY for a single-layer sprite, or a
                          * second pre-baked atom (an expression) drawn on
                          * top of `sprite` (a body pose) at its own baked-in
                          * offset -- see docs/FORMAT.md's "Layered sprites"*/
    OP_HIDE     = 0x04, /* ch:u8                                               */
    OP_MENU     = 0x05, /* n:u8 [text:u16 tgt:u24]*  (tgt packed, see below)   */
    OP_JUMP     = 0x06, /* tgt:u24  (packed, see below)                        */
    OP_CALL     = 0x07, /* tgt:u24  (packed, see below)                        */
    OP_RETURN   = 0x08, /*                                                     */
    OP_SET      = 0x09, /* var:u8 val:i16                                      */
    OP_IF       = 0x0A, /* var:u8 cmp:u8 val:i16 tgt:u24  (jump if TRUE, tgt
                          * packed, see below)                                */
    OP_PAUSE    = 0x0B, /* frames:u8                                           */
    OP_SOUND    = 0x0C, /* id:u8  -- reserved, always a no-op (no CE audio)    */
    OP_END      = 0x0D, /*                                    end of script    */
    OP_ADD      = 0x0E, /* var:u8 delta:i16   (saturating, for `$ x += 1`)     */
    OP_MINIGAME = 0x0F, /* result_var:u8 -- runs a host-side minigame screen,
                          * stores its outcome in vars[result_var]. Only the
                          * poem word-picking game exists today; see
                          * docs/FORMAT.md's "Poem minigame" section.        */
};

/** Comparison selectors for OP_IF. */
enum vn_cmp {
    CMP_EQ = 0, CMP_NE = 1, CMP_LT = 2, CMP_LE = 3, CMP_GT = 4, CMP_GE = 5,
};

/** Scene transitions. Only CUT and FADE are implemented for now. */
enum vn_trans { TRANS_CUT = 0, TRANS_FADE = 1 };

/* ---------------------------------------------------------------------------
 * Chunk-packed addresses
 *
 * A "chunk" is one resident unit of compiled code + its string pool (see
 * docs/FORMAT.md's "Chunking"). Only one is ever resident at a time -- with
 * the real game compiled, the combined script is far too big for that, so
 * OP_JUMP/OP_CALL/OP_IF/OP_MENU's u24 target, vn_vm_t.pc, and vn_vm_t.stack[]
 * all pack `(chunk_id << 16) | local_offset` instead of a flat same-chunk
 * offset. This costs nothing: the chunk container's code_length is already
 * capped at u16 (65535), so 16 bits was already the most any local offset
 * could need, and a single-chunk build (chunk_id always 0) is numerically
 * identical to a flat offset -- fully backward compatible. vn_step() checks
 * the resident chunk against a target's packed chunk_id on every step and
 * calls vn_host_t.load_chunk to swap when they differ.
 * ------------------------------------------------------------------------ */

#define VN_CHUNK_ID(addr)   ((uint8_t)((addr) >> 16))
#define VN_CHUNK_OFFSET(addr) ((addr) & 0xFFFFu)
#define VN_PACK_ADDR(chunk, offset) (((uint32_t)(chunk) << 16) | (offset))

/* ---------------------------------------------------------------------------
 * Limits
 * ------------------------------------------------------------------------ */

#define VN_MAX_VARS      64   /* story flag / counter slots                   */
#define VN_CALL_DEPTH    8    /* nesting depth for OP_CALL                    */
#define VN_MAX_CHOICES   6    /* menu options the UI can display at once      */
#define VN_MAX_CHARS     4    /* simultaneously shown characters              */

#define VN_SPEAKER_NONE  0xFF   /* narration: no name plate                   */
#define VN_NO_SPRITE     0xFF   /* "unset" sprite/background id               */
#define VN_NO_OVERLAY    0xFFFF /* actor has no second (expression) layer     */

/* ---------------------------------------------------------------------------
 * Host interface
 * ------------------------------------------------------------------------ */

/**
 * One on-screen character slot.
 *
 * `sprite` and `overlay` are pre-baked image ids (tools/image_resolve.py):
 * most DDLC character art is a body pose plus an expression layer, and
 * rather than flattening every body+expression combination into its own
 * full sprite (the same body shipped over and over, once per expression),
 * the converter bakes each distinct body ("sprite") and expression
 * ("overlay") once and this struct carries both. `overlay` is VN_NO_OVERLAY
 * when a combo didn't fit that shape (see docs/FORMAT.md's "Layered
 * sprites") and `sprite` alone is the whole, already-flattened image, same
 * as before layering existed. Either way the renderer just draws `sprite`
 * then, if present, `overlay` at the same anchor -- no runtime layout logic,
 * every position is baked in.
 */
typedef struct {
    uint8_t  character; /* character id, or VN_NO_SPRITE when the slot is free */
    uint16_t sprite;    /* up to 65535 -- the full game needs 406, over a u8 */
    uint16_t overlay;   /* second layer, or VN_NO_OVERLAY                    */
    uint8_t  pos;       /* half the on-screen center X: center_x = pos * 2
                          * (tools/compile_script.py's _pos_from_x) */
} vn_actor_t;

/** Everything the renderer needs to draw a frame. Owned by the VM. */
typedef struct {
    uint8_t     background;             /* bg id, or VN_NO_SPRITE            */
    vn_actor_t  actors[VN_MAX_CHARS];
    uint8_t     speaker;                /* VN_SPEAKER_NONE for narration     */
    const char *text;                   /* current line, NUL-terminated      */
    uint16_t    text_index;             /* text's string-pool index, for save
                                          * games -- `text` itself is a pointer
                                          * into this run's malloc'd DSCRIPT
                                          * copy, unsafe to persist across a
                                          * restart (see save.h)             */
} vn_scene_t;

/**
 * Platform hooks. The VM never calls anything else that touches the outside
 * world, which is what lets the same vn.c drive both graphx and the host sim.
 */
typedef struct {
    /** Resolve a string-pool index to a NUL-terminated string. */
    const char *(*string)(void *ctx, uint16_t index);

    /** Present @p scene and block until the player advances the line. */
    void (*say)(void *ctx, const vn_scene_t *scene);

    /** Present a choice menu; return the index of the option chosen. */
    uint8_t (*menu)(void *ctx, const vn_scene_t *scene,
                    const char *const *choices, uint8_t count);

    /** Redraw after a scene/show/hide, running @p trans (enum vn_trans). */
    void (*update)(void *ctx, const vn_scene_t *scene, uint8_t trans);

    /** Idle for @p frames frames. */
    void (*pause)(void *ctx, uint8_t frames);

    /** Poll for a quit request (CLEAR on calc, EOF on host). */
    bool (*quit)(void *ctx);

    /**
     * Loads chunk @p chunk_id, handing back its code and size. Called
     * whenever a jump/call/return/menu-pick target's packed chunk_id differs
     * from the one currently resident (see "Chunk-packed addresses" above).
     * Optional: NULL means a single-chunk build, and any cross-chunk target
     * (chunk_id != 0) fails with VN_ERR_BOUNDS, same as any other invalid
     * address.
     */
    bool (*load_chunk)(void *ctx, uint8_t chunk_id,
                       const uint8_t **code_out, size_t *code_size_out);

    /**
     * Runs a host-side minigame screen (see OP_MINIGAME); returns its
     * outcome to be stored in a story variable. Optional: NULL means the
     * outcome defaults to 0, same spirit as @p pause being optional.
     */
    uint8_t (*minigame)(void *ctx);

    void *ctx;
} vn_host_t;

/* ---------------------------------------------------------------------------
 * VM state
 * ------------------------------------------------------------------------ */

typedef enum {
    VN_RUNNING = 0,
    VN_FINISHED,      /* hit OP_END                                          */
    VN_QUIT,          /* host asked to stop                                  */
    VN_ERR_OPCODE,    /* unknown opcode                                      */
    VN_ERR_BOUNDS,    /* pc ran past the end of the chunk                    */
    VN_ERR_STACK,     /* call stack over/underflow                           */
} vn_status_t;

typedef struct {
    const uint8_t *code;
    size_t         code_size;
    uint8_t        chunk_id;  /* which chunk `code`/`code_size` are for */

    uint32_t       pc;
    uint32_t       stack[VN_CALL_DEPTH];
    uint8_t        sp;

    int16_t        vars[VN_MAX_VARS];
    vn_scene_t     scene;

    const vn_host_t *host;
    vn_status_t      status;
} vn_vm_t;

/** Reset @p vm and point it at @p code. Does not draw anything. */
void vn_init(vn_vm_t *vm, const uint8_t *code, size_t code_size,
             const vn_host_t *host);

/**
 * Execute a single instruction.
 * @return true while the VM can keep going, false once it has stopped.
 */
bool vn_step(vn_vm_t *vm);

/** Run until the script ends, the host quits, or an error occurs. */
vn_status_t vn_run(vn_vm_t *vm);

/** Human-readable form of @p status, for the host simulator and debugging. */
const char *vn_status_str(vn_status_t status);

#endif /* VN_H */
