/**
 * @file main.c
 * @brief Entry point: wires the VM to graphx rendering and keypad input.
 */

#include "chars.h"
#include "demo.h"
#include "render.h"
#include "text.h"
#include "vn.h"

#include <graphx.h>
#include <keypadc.h>

#include <stdbool.h>
#include <stddef.h>
#include <string.h>

/* Characters revealed per frame by the typewriter. */
#define TYPE_SPEED 2

static bool quit_requested;

/* ---------------------------------------------------------------------------
 * Input
 *
 * kb_Scan latches the keypad, so every read in a frame sees the same state.
 * These helpers report edges rather than levels: a held key must be released
 * before it registers again, which is what a VN's click-to-advance needs.
 * ------------------------------------------------------------------------ */

typedef struct {
    bool advance;   /* 2nd / enter */
    bool up;
    bool down;
    bool quit;      /* clear       */
} input_t;

static void input_poll(input_t *in)
{
    static bool held_advance, held_up, held_down;

    kb_Scan();

    bool advance = kb_IsDown(kb_Key2nd) || kb_IsDown(kb_KeyEnter);
    bool up      = kb_IsDown(kb_KeyUp);
    bool down    = kb_IsDown(kb_KeyDown);

    in->advance = advance && !held_advance;
    in->up      = up      && !held_up;
    in->down    = down    && !held_down;
    in->quit    = kb_IsDown(kb_KeyClear);

    held_advance = advance;
    held_up      = up;
    held_down    = down;

    if (in->quit) {
        quit_requested = true;
    }
}

/** Block until any of the tracked keys goes down, or a quit is requested. */
static void wait_for_advance(void)
{
    input_t in;

    do {
        input_poll(&in);
    } while (!in.advance && !quit_requested);
}

/* ---------------------------------------------------------------------------
 * VM host callbacks
 * ------------------------------------------------------------------------ */

static const char *host_string(void *ctx, uint16_t index)
{
    (void)ctx;
    return index < demo_string_count ? demo_strings[index] : "";
}

static void host_update(void *ctx, const vn_scene_t *scene, uint8_t trans)
{
    (void)ctx;

    render_scene(scene);
    render_box(scene, scene->text, SIZE_MAX);
    render_present(trans);
}

static void host_say(void *ctx, const vn_scene_t *scene)
{
    (void)ctx;

    /* Typewriter reveal, interruptible: the first press completes the line
     * rather than advancing past it. */
    const size_t len = strlen(scene->text);
    size_t visible = 0;

    while (visible < len && !quit_requested) {
        input_t in;
        input_poll(&in);
        if (in.advance) {
            break;
        }

        visible += TYPE_SPEED;
        if (visible > len) {
            visible = len;
        }

        render_scene(scene);
        render_box(scene, scene->text, visible);
        render_present(TRANS_CUT);
    }

    render_scene(scene);
    render_box(scene, scene->text, SIZE_MAX);
    render_present(TRANS_CUT);

    wait_for_advance();
}

static uint8_t host_menu(void *ctx, const vn_scene_t *scene,
                         const char *const *choices, uint8_t count)
{
    (void)ctx;

    uint8_t selected = 0;

    for (;;) {
        render_scene(scene);
        render_menu(choices, count, selected);
        render_box(scene, "", SIZE_MAX);
        render_present(TRANS_CUT);

        input_t in;
        input_poll(&in);

        if (quit_requested) {
            return 0;
        }
        if (in.up) {
            selected = selected == 0 ? (uint8_t)(count - 1) : (uint8_t)(selected - 1);
        }
        if (in.down) {
            selected = (uint8_t)((selected + 1) % count);
        }
        if (in.advance) {
            return selected;
        }
    }
}

static void host_pause(void *ctx, uint8_t frames)
{
    (void)ctx;

    input_t in;
    for (uint8_t i = 0; i < frames && !quit_requested; i++) {
        gfx_Wait();
        input_poll(&in);
    }
}

static bool host_quit(void *ctx)
{
    (void)ctx;
    return quit_requested;
}

static const vn_host_t host = {
    .string = host_string,
    .say    = host_say,
    .menu   = host_menu,
    .update = host_update,
    .pause  = host_pause,
    .quit   = host_quit,
    .ctx    = NULL,
};

/* ------------------------------------------------------------------------ */

int main(void)
{
    vn_vm_t vm;

    chars_init();
    render_init();

    vn_init(&vm, demo_code, demo_code_size, &host);
    vn_run(&vm);

    /* Hold the final frame so an error or the closing line stays readable. */
    if (!quit_requested) {
        wait_for_advance();
    }

    render_end();
    return 0;
}
