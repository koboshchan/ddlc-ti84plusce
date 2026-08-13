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
    OP_SHOW     = 0x03, /* ch:u8 sprite:u16 overlay:u16 pos:u8 flags:u8 --
                          * overlay is VN_NO_OVERLAY for a single-layer
                          * sprite, or a second pre-baked atom (an
                          * expression) drawn on top of `sprite` (a body
                          * pose) at its own baked-in offset -- see
                          * docs/FORMAT.md's "Layered sprites". flags is
                          * VN_FLAG_ZOOM/VN_FLAG_HOP/VN_FLAG_SINK, DDLC's real
                          * per-line speaking/movement signal (which named
                          * ATL transform -- "f11" vs "t11" vs "h11" vs
                          * "s11" -- authored this Show) -- see "The speaking
                          * pop" in docs/FORMAT.md                          */
    OP_HIDE     = 0x04, /* ch:u8                                               */
    OP_MENU     = 0x05, /* n:u8 [text:u16 tgt:u24]*  (tgt packed, see below)   */
    OP_JUMP     = 0x06, /* tgt:u24  (packed, see below)                        */
    OP_CALL     = 0x07, /* tgt:u24  (packed, see below)                        */
    OP_RETURN   = 0x08, /*                                                     */
    OP_SET      = 0x09, /* var:u8 val:i16                                      */
    OP_IF       = 0x0A, /* var:u8 cmp:u8 val:i16 tgt:u24  (jump if TRUE, tgt
                          * packed, see below)                                */
    OP_PAUSE    = 0x0B, /* ms:u16 -- 0 means "wait for input, no timeout"
                          * (DDLC's own bare `pause()`); otherwise waits up
                          * to ms milliseconds *or* until the player advances,
                          * whichever comes first. Widened from an earlier
                          * frames:u8 (max ~4s at 60fps) once real durations
                          * up to 10s turned up in the compiled game -- see
                          * tools/compile_script.py's pause() handling.     */
    OP_SOUND    = 0x0C, /* id:u8  -- reserved, always a no-op (no CE audio)    */
    OP_END      = 0x0D, /*                                    end of script    */
    OP_ADD      = 0x0E, /* var:u8 delta:i16   (saturating, for `$ x += 1`)     */
    OP_MINIGAME = 0x0F, /* winner_var:u8 s_var:u8 n_var:u8 y_var:u8 -- runs
                          * a host-side minigame screen; the poem word-
                          * picking game (only one that exists) reports a
                          * winning character plus each club member's own
                          * appeal score, so DDLC's poemwinner[chapter]/
                          * s_poemappeal[chapter]/n_poemappeal[chapter]/
                          * y_poemappeal[chapter] can all be written from
                          * one run. compile_script.py inlines this once
                          * per `call poem` site with that site's own
                          * compile-time-known chapter -- see
                          * docs/FORMAT.md's "Poem minigame" section.       */
    OP_RANDOM   = 0x10, /* var:u8 lo:i16 hi:i16 -- vars[var] = a uniform
                          * random integer in [lo, hi]. Compiles DDLC's own
                          * `renpy.random.randint(lo, hi)`, always found as
                          * `== N` right next to it -- see
                          * tools/compile_script.py's _randint_call() and
                          * "The speaking pop"'s sibling section on easter
                          * eggs in docs/FORMAT.md.                        */
    OP_JUMP_VAR = 0x11, /* var:u8 -- jump to whatever label vars[var] names.
                          * Compiles `jump expression <name>` (DDLC's own
                          * `jump persistent.autoload`, its post-restart
                          * resume mechanism). vars[var] holds an interned
                          * string id (see VN_STR_BASE) that the host
                          * resolves to a packed address via
                          * vn_host_t.resolve_label -- vn.c itself has no
                          * notion of label names, only vn_host_t does.
                          * Falls through to VN_FINISHED, not a crash, if
                          * the name doesn't resolve (an uncompiled label,
                          * or the variable never held one).             */
    OP_CALL_VAR = 0x12, /* var:u8 -- like OP_JUMP_VAR, but pushes a return
                          * address first (`call expression <name>`).    */
    OP_DELETE_SAVES = 0x13, /* -- compiles DDLC's own `delete_all_saves()`
                          * call: erases every DSAVEn slot for real, no
                          * undo. Genuinely destructive, on purpose --
                          * confirmed explicitly with the person building
                          * this port before this opcode existed at all
                          * (see git history), not a default this engine
                          * would take on its own. */
    OP_TEAR_SHOW = 0x14, /* chunks:u8 offset_min:i16 offset_max:i16
                          * period_ms:u16 -- compiles `show screen
                          * tear(...)`, Act 2's signature glitch: the
                          * scene area splits into `chunks` horizontal
                          * bands, each re-rolling a random horizontal
                          * displacement in [offset_min, offset_max] every
                          * period_ms while shown. `tear`'s real
                          * implementation is a custom Python Displayable
                          * class (effects.rpy) not preserved in any
                          * compiled .rpyc this engine reads, so this is a
                          * faithful reinterpretation from the effect's
                          * name/parameters/genre convention -- not a
                          * decompilation of its exact original pixel
                          * algorithm, which isn't recoverable here. See
                          * render.c's tear rendering and
                          * docs/FORMAT.md's write-up.                   */
    OP_TEAR_HIDE = 0x15, /* -- `hide screen tear`.                       */
    OP_WINDOW_HIDE = 0x16, /* -- compiles `window hide`/`window hide(...)`.
                          * Hides the dialogue box until the next
                          * OP_WINDOW_SHOW; see vn_scene_t.window_hidden
                          * and render.c's render_box().                */
    OP_WINDOW_SHOW = 0x17, /* -- compiles `window show(...)`/`window auto`.
                          * `auto` and an explicit `show` both compile to
                          * this: this engine has no notion of a
                          * windowless Say to make "automatically show
                          * when there's dialogue" mean anything beyond
                          * "showing", since a real Say always draws the
                          * box regardless of this flag.                */
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

/* Story flag / counter slots. 256 is the format ceiling, not a round number:
 * every opcode that names a variable encodes it as a u8 (see the bytecode
 * table below), so slot 255 is the last one addressable without widening the
 * instruction encoding.
 *
 * It was 64, which the full game had already grown to within 15 slots of
 * (49 used). Compile-time string interning and indexed variables both hand
 * out slots -- DDLC keeps per-chapter state in lists (poemwinner[N],
 * X_poemappeal[N]) that become one slot per index -- so the old ceiling was
 * about to start failing builds. Costs 512 bytes of RAM in vn_vm_t and the
 * same again in a save. */
#define VN_MAX_VARS     256

/* Where interned string ids begin. The VM's variables are int16 and hold no
 * strings, but DDLC only ever assigns a string and later compares it for
 * equality -- which an integer does perfectly well if each distinct string
 * gets a distinct one. tools/compile_script.py interns them at compile time
 * and ships the pool as DVSTR; assets_var_string() resolves a value back to
 * its text for the one place that needs the characters themselves, the name
 * plate.
 *
 * Ids start here rather than at 0 so a string-valued variable never collides
 * with the small integers the numeric variables hold. Nothing in the game
 * compares one variable against both kinds, so this is belt and braces -- it
 * also makes a wrong value obvious in a trace instead of looking like a
 * plausible counter. */
#define VN_STR_BASE   16384

/* The variable slot holding character @p ch's displayed name. The four name
 * variables (s_name/n_name/y_name/m_name) are reserved slots 0..3 in
 * character-id order by tools/compile_script.py's Compiler.NAME_VARS, so a
 * character's id *is* the slot -- no table needs shipping to map between
 * them. A contract with the compiler, like the character ids themselves. */
#define VN_NAME_VAR(ch)  (ch)
#define VN_CALL_DEPTH    8    /* nesting depth for OP_CALL                    */
#define VN_MAX_CHOICES   6    /* menu options the UI can display at once      */
#define VN_MAX_CHARS     4    /* simultaneously shown characters              */

#define VN_SPEAKER_NONE  0xFF   /* narration: no name plate                   */
#define VN_SPEAKER_PLAYER 0xFE  /* the protagonist: plate shows the player's
                                 * own entered name. Distinct from NONE --
                                 * narration is the MC's inner voice and gets
                                 * no plate, while a line he says aloud does,
                                 * and DDLC writes them as different speakers */
#define VN_NO_SPRITE     0xFF   /* "unset" sprite/background id               */
#define VN_NO_OVERLAY    0xFFFF /* actor has no second (expression) layer     */

/* OP_SHOW's flags:u8 bitmask -- see docs/FORMAT.md's "The speaking pop". */
#define VN_FLAG_ZOOM     0x01 /* authored "at f.." / "at hf.." -- speaking  */
#define VN_FLAG_HOP      0x02 /* authored "at h.." / "at hf.." -- one-shot
                                * bounce, triggered fresh each real OP_SHOW */
#define VN_FLAG_SINK     0x04 /* authored "at s.." -- drifts down and holds
                                * until the next Show lands back on t/f     */

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
    uint8_t  flags;     /* VN_FLAG_ZOOM/VN_FLAG_HOP/VN_FLAG_SINK, as authored --
                          * wire format */
    uint8_t  show_seq;  /* bumped once per real OP_SHOW targeting this slot --
                          * NOT part of the wire format. Distinguishes a
                          * genuine re-Show (possibly of the identical sprite,
                          * e.g. `hop` used purely for emphasis on an
                          * unchanged pose) from render_scene()'s many redraws
                          * of unchanged state (every typewriter tick, menu,
                          * pause) -- the renderer needs the former to know
                          * when to (re)trigger the one-shot hop bounce. */
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

    /* The tear glitch overlay (OP_TEAR_SHOW/OP_TEAR_HIDE) -- see render.c.
     * Purely a rendering instruction, not story state a save needs to
     * capture exactly (a loaded save resuming with the tear off, even if
     * it was mid-glitch when saved, is imperceptible -- the beat this
     * plays under isn't one that offers a save point anyway). */
    bool     tear_on;
    uint8_t  tear_chunks;
    int16_t  tear_offset_min;
    int16_t  tear_offset_max;
    uint16_t tear_period_ms;

    /* `window hide`/`window show` -- whether the dialogue box is drawn at
     * all. DDLC uses this for beats with no text over a clean shot of the
     * scene. This engine's background art is baked at a fixed 320x180 (see
     * docs/FORMAT.md's "Image assets"), always assuming the box covers the
     * bottom 60 rows, so hiding the box doesn't expand the scene to fill
     * the screen the way real Ren'Py's window hide can -- that would need
     * every background re-baked at a different aspect ratio. Instead
     * render_box() simply skips the box and fills the same rows black,
     * matching the part of the effect that actually matters most --
     * dialogue disappearing for a clean shot -- without a wider art
     * pipeline change. See OP_WINDOW_SHOW/OP_WINDOW_HIDE. */
    bool     window_hidden;
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
    void (*pause)(void *ctx, uint16_t ms);

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
     * Runs a host-side minigame screen (see OP_MINIGAME); returns the
     * winning character (TAG_TO_CHAR order) and writes each of the three
     * club members' own outcome value -- poem: sayori/natsuki/yuri's
     * individual appeal scores, which the winner is picked from but which
     * DDLC's own per-chapter poemappeal[] variables need on their own
     * terms too -- through @p s/@p n/@p y. Mirrors poem_run()'s signature
     * exactly, so main.c's implementation is a direct passthrough.
     * Optional: NULL means every outcome defaults to 0, same spirit as
     * @p pause being optional.
     */
    uint8_t (*minigame)(void *ctx, int16_t *s, int16_t *n, int16_t *y);

    /**
     * Resolves @p str_id (an interned string -- see VN_STR_BASE -- that a
     * story variable currently holds) to the packed address of the label
     * it names, for OP_JUMP_VAR/OP_CALL_VAR. Returns false (leaving
     * *addr_out untouched) if @p str_id doesn't name any compiled label --
     * an ordinary, expected outcome (the label just isn't part of this
     * build's --files set), not a corruption signal. vn.c has no notion of
     * label names itself, only the host does (see assets.c's DVLBL).
     * Optional: NULL means every dynamic jump/call fails to resolve, which
     * degrades to VN_FINISHED rather than a crash -- same spirit as
     * @p minigame defaulting to 0 when absent.
     */
    bool (*resolve_label)(void *ctx, int16_t str_id, uint32_t *addr_out);

    /**
     * Erases every save slot, permanently -- see OP_DELETE_SAVES. Optional:
     * NULL means the call is silently skipped, same spirit as @p pause and
     * @p minigame being optional, but worth calling out here specifically
     * since unlike those two, skipping this one changes what a real DDLC
     * playthrough would do at this exact story beat (Monika's threat stays
     * a threat, nothing actually happens) rather than just losing a cosmetic
     * flourish.
     */
    void (*delete_saves)(void *ctx);

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

    /* OP_RANDOM's state (xorshift32 -- see vn.c). Seeded once by vn_init()'s
     * caller, who owns the only genuinely platform-specific piece of this --
     * where the entropy comes from -- keeping vn.c itself free of any
     * platform header the way its file comment requires. Not part of a save
     * (src/save.c): a loaded game reseeds like a fresh one rather than
     * replaying the exact same sequence of future draws, which is what a
     * player would actually expect from "random". */
    uint32_t       rng_state;

    const vn_host_t *host;
    vn_status_t      status;
} vn_vm_t;

/** Reset @p vm and point it at @p code. Does not draw anything. */
/**
 * @p seed feeds OP_RANDOM's PRNG (see vn_vm_t.rng_state) -- pass something
 * that actually varies call to call (main.c uses the free-running clock() at
 * the moment the player reaches the title screen) so a random easter egg
 * doesn't draw identically on every playthrough. 0 is remapped internally to
 * a fixed nonzero value (xorshift32 can't recover from an all-zero state),
 * so a caller with no real entropy source degrades to deterministic rather
 * than broken -- tools/host_sim relies on exactly that for reproducible
 * output.
 */
void vn_init(vn_vm_t *vm, const uint8_t *code, size_t code_size,
             const vn_host_t *host, uint32_t seed);

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
