/**
 * @file vn.c
 * @brief Visual-novel bytecode VM. See vn.h and docs/FORMAT.md.
 */

#include "vn.h"

#include <string.h>

/* ---------------------------------------------------------------------------
 * Operand readers
 *
 * Every read is bounds-checked against code_size; a truncated or corrupt chunk
 * stops the VM with VN_ERR_BOUNDS instead of running off into memory.
 *
 * vm->pc carries a chunk id in its high byte (VN_CHUNK_ID/VN_CHUNK_OFFSET,
 * vn.h) once vn_step() has swapped in the chunk it points at, but `code` only
 * ever holds *that* chunk's bytes -- so every actual read/index here uses
 * just the low 16 bits (VN_CHUNK_OFFSET), never the raw packed pc. This is
 * safe to do with a plain `+=` on the full pc value: a valid offset never
 * exceeds code_size (<= 65535), so advancing it a few bytes at a time can
 * never carry into the chunk-id byte mid-instruction.
 * ------------------------------------------------------------------------ */

static bool have(const vn_vm_t *vm, uint32_t bytes)
{
    return VN_CHUNK_OFFSET(vm->pc) + bytes <= vm->code_size;
}

/* Every opcode that names a variable reads the slot with read_u8(), so an
 * index can never exceed 255. With VN_MAX_VARS at 256 that makes every index
 * valid by construction, which is why the opcodes below index vm->vars[]
 * without a bounds check -- they used to carry one, and the compiler
 * correctly reported it as unreachable once the ceiling reached the
 * operand's own range.
 *
 * Lowering VN_MAX_VARS reopens the hole (a slot byte past the end would
 * index out of bounds), so it fails here rather than silently. */
_Static_assert(VN_MAX_VARS >= 256,
               "variable opcodes read a u8 slot and index vm->vars[] "
               "unchecked; VN_MAX_VARS must cover the whole u8 range");

static uint8_t read_u8(vn_vm_t *vm)
{
    if (!have(vm, 1)) {
        vm->status = VN_ERR_BOUNDS;
        return 0;
    }
    return vm->code[VN_CHUNK_OFFSET(vm->pc++)];
}

static uint16_t read_u16(vn_vm_t *vm)
{
    if (!have(vm, 2)) {
        vm->status = VN_ERR_BOUNDS;
        return 0;
    }
    uint32_t off = VN_CHUNK_OFFSET(vm->pc);
    uint16_t v = (uint16_t)vm->code[off] |
                 (uint16_t)((uint16_t)vm->code[off + 1] << 8);
    vm->pc += 2;
    return v;
}

static int16_t read_i16(vn_vm_t *vm)
{
    return (int16_t)read_u16(vm);
}

static uint32_t read_u24(vn_vm_t *vm)
{
    if (!have(vm, 3)) {
        vm->status = VN_ERR_BOUNDS;
        return 0;
    }
    uint32_t off = VN_CHUNK_OFFSET(vm->pc);
    uint32_t v = (uint32_t)vm->code[off] |
                 ((uint32_t)vm->code[off + 1] << 8) |
                 ((uint32_t)vm->code[off + 2] << 16);
    vm->pc += 3;
    return v;
}

/* ---------------------------------------------------------------------------
 * Actor slots
 * ------------------------------------------------------------------------ */

/** Find the slot holding @p character, or the first free slot, else -1. */
static int actor_slot(vn_scene_t *scene, uint8_t character)
{
    int free_slot = -1;

    for (int i = 0; i < VN_MAX_CHARS; i++) {
        if (scene->actors[i].character == character) {
            return i;
        }
        if (free_slot < 0 && scene->actors[i].character == VN_NO_SPRITE) {
            free_slot = i;
        }
    }
    return free_slot;
}

static void actor_show(vn_scene_t *scene, uint8_t character, uint16_t sprite,
                       uint16_t overlay, uint8_t pos, uint8_t flags)
{
    int slot = actor_slot(scene, character);

    /* With every slot taken, replace the last one rather than dropping the
     * show outright -- a missing character reads as a bug, a shuffled one
     * merely as a cosmetic glitch. */
    if (slot < 0) {
        slot = VN_MAX_CHARS - 1;
    }

    scene->actors[slot].character = character;
    scene->actors[slot].sprite    = sprite;
    scene->actors[slot].overlay   = overlay;
    scene->actors[slot].pos       = pos;
    scene->actors[slot].flags     = flags;
    /* Every real OP_SHOW bumps this, whether or not anything above actually
     * changed value -- see vn_actor_t's show_seq comment for why that
     * matters (a `hop` re-authored on an otherwise-unchanged pose still
     * needs to re-trigger the bounce). */
    scene->actors[slot].show_seq++;
}

static void actor_hide(vn_scene_t *scene, uint8_t character)
{
    for (int i = 0; i < VN_MAX_CHARS; i++) {
        if (scene->actors[i].character == character) {
            scene->actors[i].character = VN_NO_SPRITE;
        }
    }
}

static void actors_clear(vn_scene_t *scene)
{
    for (int i = 0; i < VN_MAX_CHARS; i++) {
        scene->actors[i].character = VN_NO_SPRITE;
    }
}

/* ---------------------------------------------------------------------------
 * Comparisons
 * ------------------------------------------------------------------------ */

static bool compare(int16_t lhs, uint8_t cmp, int16_t rhs)
{
    switch (cmp) {
        case CMP_EQ: return lhs == rhs;
        case CMP_NE: return lhs != rhs;
        case CMP_LT: return lhs <  rhs;
        case CMP_LE: return lhs <= rhs;
        case CMP_GT: return lhs >  rhs;
        case CMP_GE: return lhs >= rhs;
        default:     return false;
    }
}

/* ---------------------------------------------------------------------------
 * Public API
 * ------------------------------------------------------------------------ */

/* xorshift32 (Marsaglia) -- small, fast, no multiply/divide needed on the
 * eZ80, and its one requirement (a nonzero state) is already guaranteed by
 * vn_init() below. Not cryptographic, doesn't need to be: every OP_RANDOM
 * caller is a cosmetic easter egg (see docs/FORMAT.md), never anything the
 * player could exploit by predicting it. */
static uint32_t vn_rand_next(vn_vm_t *vm)
{
    uint32_t x = vm->rng_state;
    x ^= x << 13;
    x ^= x >> 17;
    x ^= x << 5;
    vm->rng_state = x;
    return x;
}

void vn_init(vn_vm_t *vm, const uint8_t *code, size_t code_size,
             const vn_host_t *host, uint32_t seed)
{
    memset(vm, 0, sizeof(*vm));

    vm->code       = code;
    vm->code_size  = code_size;
    vm->host       = host;
    vm->status     = VN_RUNNING;
    vm->rng_state  = seed ? seed : 0x9E3779B9u; /* 0 -> a fixed nonzero
                                                  * fallback, see vn.h */
    /* A handful of throwaway rounds before the state is ever used for a
     * real draw: xorshift32 mixes well over time but a single round from a
     * seed that's a "simple" perturbation of another (exactly what
     * clock()-derived seeds from two playthroughs a moment apart tend to be
     * -- low bits differ, high bits mostly don't) can still correlate with
     * that other seed's first draw. Doesn't matter in aggregate (verified:
     * the distribution across many seeds already tracks the true
     * probability closely even with zero warm-up), but does matter for the
     * one draw an actual playthrough gets -- and four rounds cost nothing,
     * run once per session, not per frame. */
    for (int i = 0; i < 4; i++) {
        vn_rand_next(vm);
    }

    vm->scene.background = VN_NO_SPRITE;
    vm->scene.speaker    = VN_SPEAKER_NONE;
    vm->scene.text       = "";
    actors_clear(&vm->scene);
}

bool vn_step(vn_vm_t *vm)
{
    if (vm->status != VN_RUNNING) {
        return false;
    }

    if (vm->host->quit && vm->host->quit(vm->host->ctx)) {
        vm->status = VN_QUIT;
        return false;
    }

    /* Swap chunks *before* the bounds check below: code_size still describes
     * whichever chunk was resident last step, so it has to be refreshed
     * first or a jump straight to another chunk's valid offset would look
     * out of bounds against the wrong chunk's size. */
    uint8_t want_chunk = VN_CHUNK_ID(vm->pc);
    if (want_chunk != vm->chunk_id) {
        const uint8_t *code;
        size_t         code_size;
        if (!vm->host->load_chunk ||
            !vm->host->load_chunk(vm->host->ctx, want_chunk, &code, &code_size)) {
            vm->status = VN_ERR_BOUNDS;
            return false;
        }
        vm->code      = code;
        vm->code_size = code_size;
        vm->chunk_id  = want_chunk;
    }

    if (VN_CHUNK_OFFSET(vm->pc) >= vm->code_size) {
        vm->status = VN_ERR_BOUNDS;
        return false;
    }

    uint8_t op = read_u8(vm);

    switch (op) {
        case OP_NOP:
        case OP_SOUND: {
            /* OP_SOUND still consumes its operand so the stream stays aligned
             * if audio is ever implemented. */
            if (op == OP_SOUND) {
                (void)read_u8(vm);
            }
            break;
        }

        case OP_SAY: {
            uint8_t  spk  = read_u8(vm);
            uint16_t text = read_u16(vm);
            if (vm->status != VN_RUNNING) {
                break;
            }
            vm->scene.speaker    = spk;
            vm->scene.text       = vm->host->string(vm->host->ctx, text);
            vm->scene.text_index = text;
            vm->host->say(vm->host->ctx, &vm->scene);
            break;
        }

        case OP_SCENE: {
            uint8_t bg    = read_u8(vm);
            uint8_t trans = read_u8(vm);
            if (vm->status != VN_RUNNING) {
                break;
            }
            /* A scene change clears the stage, matching Ren'Py semantics. */
            vm->scene.background = bg;
            actors_clear(&vm->scene);
            vm->host->update(vm->host->ctx, &vm->scene, trans);
            break;
        }

        case OP_SHOW: {
            uint8_t  ch      = read_u8(vm);
            uint16_t sprite  = read_u16(vm);
            uint16_t overlay = read_u16(vm);
            uint8_t  pos     = read_u8(vm);
            uint8_t  flags   = read_u8(vm);
            if (vm->status != VN_RUNNING) {
                break;
            }
            actor_show(&vm->scene, ch, sprite, overlay, pos, flags);
            vm->host->update(vm->host->ctx, &vm->scene, TRANS_CUT);
            break;
        }

        case OP_HIDE: {
            uint8_t ch = read_u8(vm);
            if (vm->status != VN_RUNNING) {
                break;
            }
            actor_hide(&vm->scene, ch);
            vm->host->update(vm->host->ctx, &vm->scene, TRANS_CUT);
            break;
        }

        case OP_MENU: {
            const char *choices[VN_MAX_CHOICES];
            uint32_t    targets[VN_MAX_CHOICES];

            uint8_t count = read_u8(vm);
            if (vm->status != VN_RUNNING) {
                break;
            }

            uint8_t shown = 0;
            for (uint8_t i = 0; i < count; i++) {
                uint16_t text = read_u16(vm);
                uint32_t tgt  = read_u24(vm);
                if (vm->status != VN_RUNNING) {
                    break;
                }
                /* Options past VN_MAX_CHOICES are parsed but not offered, so
                 * an oversized menu still leaves pc at the next instruction. */
                if (shown < VN_MAX_CHOICES) {
                    choices[shown] = vm->host->string(vm->host->ctx, text);
                    targets[shown] = tgt;
                    shown++;
                }
            }
            if (vm->status != VN_RUNNING || shown == 0) {
                break;
            }

            uint8_t picked = vm->host->menu(vm->host->ctx, &vm->scene,
                                            choices, shown);
            if (picked >= shown) {
                picked = 0;
            }
            vm->pc = targets[picked];
            break;
        }

        case OP_JUMP: {
            uint32_t tgt = read_u24(vm);
            if (vm->status == VN_RUNNING) {
                vm->pc = tgt;
            }
            break;
        }

        case OP_CALL: {
            uint32_t tgt = read_u24(vm);
            if (vm->status != VN_RUNNING) {
                break;
            }
            if (vm->sp >= VN_CALL_DEPTH) {
                vm->status = VN_ERR_STACK;
                break;
            }
            vm->stack[vm->sp++] = vm->pc;
            vm->pc = tgt;
            break;
        }

        case OP_RETURN: {
            /* A return with nothing on the stack is how a top-level script
             * signals completion, mirroring Ren'Py's `return`. */
            if (vm->sp == 0) {
                vm->status = VN_FINISHED;
                break;
            }
            vm->pc = vm->stack[--vm->sp];
            break;
        }

        case OP_SET: {
            uint8_t var = read_u8(vm);
            int16_t val = read_i16(vm);
            if (vm->status != VN_RUNNING) {
                break;
            }
            vm->vars[var] = val;
            break;
        }

        case OP_ADD: {
            uint8_t var   = read_u8(vm);
            int16_t delta = read_i16(vm);
            if (vm->status != VN_RUNNING) {
                break;
            }
            /* Saturate rather than wrap: a runaway counter should pin at the
             * limit, not silently flip sign and take the wrong branch. */
            int32_t sum = (int32_t)vm->vars[var] + delta;
            if (sum > INT16_MAX) {
                sum = INT16_MAX;
            } else if (sum < INT16_MIN) {
                sum = INT16_MIN;
            }
            vm->vars[var] = (int16_t)sum;
            break;
        }

        case OP_IF: {
            uint8_t  var = read_u8(vm);
            uint8_t  cmp = read_u8(vm);
            int16_t  val = read_i16(vm);
            uint32_t tgt = read_u24(vm);
            if (vm->status != VN_RUNNING) {
                break;
            }
            if (compare(vm->vars[var], cmp, val)) {
                vm->pc = tgt;
            }
            break;
        }

        case OP_PAUSE: {
            uint16_t ms = read_u16(vm);
            if (vm->status != VN_RUNNING) {
                break;
            }
            if (vm->host->pause) {
                vm->host->pause(vm->host->ctx, ms);
            }
            break;
        }

        case OP_MINIGAME: {
            uint8_t winner_var = read_u8(vm);
            uint8_t s_var      = read_u8(vm);
            uint8_t n_var      = read_u8(vm);
            uint8_t y_var      = read_u8(vm);
            if (vm->status != VN_RUNNING) {
                break;
            }
            int16_t s = 0, n = 0, y = 0;
            uint8_t winner = vm->host->minigame
                           ? vm->host->minigame(vm->host->ctx, &s, &n, &y)
                           : 0;
            vm->vars[winner_var] = (int16_t)winner;
            vm->vars[s_var]      = s;
            vm->vars[n_var]      = n;
            vm->vars[y_var]      = y;
            break;
        }

        case OP_RANDOM: {
            uint8_t var = read_u8(vm);
            int16_t lo  = read_i16(vm);
            int16_t hi  = read_i16(vm);
            if (vm->status != VN_RUNNING) {
                break;
            }
            /* A malformed chunk could carry hi < lo; treat that as an empty
             * range collapsing to lo rather than computing a negative or
             * huge modulus. Every real caller (compile_script.py's
             * _randint_call) only ever emits lo=0, hi>=0. */
            uint32_t span = (hi > lo) ? (uint32_t)(hi - lo) + 1u : 1u;
            vm->vars[var] = (int16_t)(lo + (int16_t)(vn_rand_next(vm) % span));
            break;
        }

        case OP_JUMP_VAR:
        case OP_CALL_VAR: {
            uint8_t var = read_u8(vm);
            if (vm->status != VN_RUNNING) {
                break;
            }
            uint32_t tgt;
            /* An unresolved name is an expected outcome (the label just
             * isn't part of this build), not corruption -- unlike every
             * other failure mode in this switch, it doesn't set an
             * VN_ERR_* status. Ending cleanly is the same choice
             * vnasm.Assembler.patch_missing_labels already makes for a
             * *static* jump/call to an uncompiled label; this is that same
             * degrade for the dynamic case. */
            if (!vm->host->resolve_label ||
                !vm->host->resolve_label(vm->host->ctx, vm->vars[var], &tgt)) {
                vm->status = VN_FINISHED;
                break;
            }
            if (op == OP_CALL_VAR) {
                if (vm->sp >= VN_CALL_DEPTH) {
                    vm->status = VN_ERR_STACK;
                    break;
                }
                vm->stack[vm->sp++] = vm->pc;
            }
            vm->pc = tgt;
            break;
        }

        case OP_DELETE_SAVES: {
            if (vm->status != VN_RUNNING) {
                break;
            }
            if (vm->host->delete_saves) {
                vm->host->delete_saves(vm->host->ctx);
            }
            break;
        }

        case OP_TEAR_SHOW: {
            uint8_t  chunks = read_u8(vm);
            int16_t  lo     = read_i16(vm);
            int16_t  hi     = read_i16(vm);
            uint16_t period = read_u16(vm);
            if (vm->status != VN_RUNNING) {
                break;
            }
            vm->scene.tear_on         = true;
            vm->scene.tear_chunks     = chunks;
            vm->scene.tear_offset_min = lo;
            vm->scene.tear_offset_max = hi;
            vm->scene.tear_period_ms  = period;
            break;
        }

        case OP_TEAR_HIDE: {
            if (vm->status != VN_RUNNING) {
                break;
            }
            vm->scene.tear_on = false;
            break;
        }

        case OP_WINDOW_HIDE: {
            if (vm->status != VN_RUNNING) {
                break;
            }
            vm->scene.window_hidden = true;
            break;
        }

        case OP_WINDOW_SHOW: {
            if (vm->status != VN_RUNNING) {
                break;
            }
            vm->scene.window_hidden = false;
            break;
        }

        case OP_END:
            vm->status = VN_FINISHED;
            break;

        default:
            vm->status = VN_ERR_OPCODE;
            break;
    }

    return vm->status == VN_RUNNING;
}

vn_status_t vn_run(vn_vm_t *vm)
{
    while (vn_step(vm)) {
        /* keep stepping */
    }
    return vm->status;
}

const char *vn_status_str(vn_status_t status)
{
    switch (status) {
        case VN_RUNNING:    return "running";
        case VN_FINISHED:   return "finished";
        case VN_QUIT:       return "quit";
        case VN_ERR_OPCODE: return "error: unknown opcode";
        case VN_ERR_BOUNDS: return "error: out of bounds";
        case VN_ERR_STACK:  return "error: call stack";
        default:            return "error: unknown";
    }
}
