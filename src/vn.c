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
 * ------------------------------------------------------------------------ */

static bool have(const vn_vm_t *vm, uint32_t bytes)
{
    return vm->pc + bytes <= vm->code_size;
}

static uint8_t read_u8(vn_vm_t *vm)
{
    if (!have(vm, 1)) {
        vm->status = VN_ERR_BOUNDS;
        return 0;
    }
    return vm->code[vm->pc++];
}

static uint16_t read_u16(vn_vm_t *vm)
{
    if (!have(vm, 2)) {
        vm->status = VN_ERR_BOUNDS;
        return 0;
    }
    uint16_t v = (uint16_t)vm->code[vm->pc] |
                 (uint16_t)((uint16_t)vm->code[vm->pc + 1] << 8);
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
    uint32_t v = (uint32_t)vm->code[vm->pc] |
                 ((uint32_t)vm->code[vm->pc + 1] << 8) |
                 ((uint32_t)vm->code[vm->pc + 2] << 16);
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

static void actor_show(vn_scene_t *scene, uint8_t character, uint8_t sprite,
                       uint8_t pos)
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
    scene->actors[slot].pos       = pos;
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

void vn_init(vn_vm_t *vm, const uint8_t *code, size_t code_size,
             const vn_host_t *host)
{
    memset(vm, 0, sizeof(*vm));

    vm->code       = code;
    vm->code_size  = code_size;
    vm->host       = host;
    vm->status     = VN_RUNNING;

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

    if (vm->pc >= vm->code_size) {
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
            vm->scene.speaker = spk;
            vm->scene.text    = vm->host->string(vm->host->ctx, text);
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
            uint8_t ch     = read_u8(vm);
            uint8_t sprite = read_u8(vm);
            uint8_t pos    = read_u8(vm);
            if (vm->status != VN_RUNNING) {
                break;
            }
            actor_show(&vm->scene, ch, sprite, pos);
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
            if (var >= VN_MAX_VARS) {
                vm->status = VN_ERR_BOUNDS;
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
            if (var >= VN_MAX_VARS) {
                vm->status = VN_ERR_BOUNDS;
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
            if (var >= VN_MAX_VARS) {
                vm->status = VN_ERR_BOUNDS;
                break;
            }
            if (compare(vm->vars[var], cmp, val)) {
                vm->pc = tgt;
            }
            break;
        }

        case OP_PAUSE: {
            uint8_t frames = read_u8(vm);
            if (vm->status != VN_RUNNING) {
                break;
            }
            if (vm->host->pause) {
                vm->host->pause(vm->host->ctx, frames);
            }
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
