/**
 * @file main.c
 * @brief Entry point: wires the VM to graphx rendering and keypad input,
 * plus the title screen, pause menu, and save/load flow around it.
 */

#include "assets.h"
#include "chars.h"
#include "name.h"
#include "poem.h"
#include "render.h"
#include "save.h"
#include "text.h"
#include "vn.h"

#include <fileioc.h>
#include <graphx.h>
#include <keypadc.h>
#include <ti/getcsc.h>
#include <ti/screen.h>

#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <time.h>

/* Characters revealed per frame by the typewriter. */
#define TYPE_SPEED 4

/* Not static: src/poem.c's own input loop (a self-contained screen, like the
 * ones in this file, but living separately given how much word-bank state it
 * owns) sets this directly on Clear, the same way every screen here does, so
 * a quit mid-minigame still unwinds the whole app instead of just the
 * minigame -- vn_step()'s host->quit() check picks it up on the next step. */
bool quit_requested;

/* Set by the pause menu's "Title Screen" choice, alongside quit_requested,
 * to unwind out of vn_run() the same way a real quit does (see
 * run_pause_menu) -- main()'s outer loop checks this afterward to tell the
 * two apart and loop back to the title screen instead of exiting. */
static bool returning_to_title;

/* Defined down with the VM host callbacks, where the player-name state it
 * reads lives; declared here because the pause menu and the idle wait, both
 * above it, draw a dialogue box too. */
static const char *speaker_display_name(const vn_vm_t *vm, uint8_t speaker);

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
    bool pause;     /* mode -- opens the pause menu, or cancels a submenu */
    bool quit;      /* clear       */
} input_t;

static void input_poll(input_t *in)
{
    static bool held_advance, held_up, held_down, held_pause;

    kb_Scan();

    bool advance = kb_IsDown(kb_Key2nd) || kb_IsDown(kb_KeyEnter);
    bool up      = kb_IsDown(kb_KeyUp);
    bool down    = kb_IsDown(kb_KeyDown);
    bool pause   = kb_IsDown(kb_KeyMode);

    in->advance = advance && !held_advance;
    in->up      = up      && !held_up;
    in->down    = down    && !held_down;
    in->pause   = pause   && !held_pause;
    in->quit    = kb_IsDown(kb_KeyClear);

    held_advance = advance;
    held_up      = up;
    held_down    = down;
    held_pause   = pause;

    if (in->quit) {
        quit_requested = true;
    }
}

/* ---------------------------------------------------------------------------
 * Pause menu, save/load slots, title screen, help
 * ------------------------------------------------------------------------ */

#define PAUSE_BOX_X 80
#define PAUSE_BOX_Y 60
#define PAUSE_BOX_W 160
#define PAUSE_BOX_H 100

static const char *const pause_items[] = { "Resume", "Save", "Load", "Title Screen" };
#define PAUSE_RESUME 0
#define PAUSE_SAVE   1
#define PAUSE_LOAD   2
#define PAUSE_TITLE  3
#define PAUSE_COUNT  4

/** Runs the save-slot picker over a plain backdrop. Returns the chosen slot
 * (1..SAVE_SLOTS), or 0 if the player cancelled (Mode) or quit. When
 * @p require_existing is set (Load), empty slots are shown but can't be
 * confirmed -- there's nothing to load. */
static uint8_t run_slot_picker(const char *title, bool require_existing)
{
    char labels[SAVE_SLOTS][20];
    const char *items[SAVE_SLOTS];
    uint8_t selected = 0;

    for (uint8_t i = 0; i < SAVE_SLOTS; i++) {
        sprintf(labels[i], "Slot %u - %s", i + 1, save_exists(i + 1) ? "Saved" : "Empty");
        items[i] = labels[i];
    }

    for (;;) {
        render_backdrop(COL_BOX_FILL);
        render_text(title, 14, 12, COL_NAME);
        render_list_menu(items, SAVE_SLOTS, selected, 20, 50, COL_WHITE, COL_HIGHLIGHT);
        render_text("2nd: choose   Mode: cancel", 14, SCREEN_H - 18, COL_BOX_EDGE);
        render_present(TRANS_CUT);
        gfx_Wait();

        input_t in;
        input_poll(&in);
        if (quit_requested || in.pause) {
            return 0;
        }
        if (in.up) {
            selected = selected == 0 ? SAVE_SLOTS - 1 : (uint8_t)(selected - 1);
        }
        if (in.down) {
            selected = (uint8_t)((selected + 1) % SAVE_SLOTS);
        }
        if (in.advance) {
            if (require_existing && !save_exists(selected + 1)) {
                continue; /* nothing there to load */
            }
            return selected + 1;
        }
    }
}

/** Runs the pause menu (opened via Mode while reading a fully-revealed
 * line) over the already-rendered current scene. Save/Load are handled
 * entirely here, writing/reading @p vm directly: vn_step() always re-reads
 * vm->pc fresh, so overwriting it mid-callback like this is enough for the
 * VM to resume from a loaded position once control unwinds back to it.
 * Returns true if the player chose "Title Screen" (caller should unwind),
 * false to resume gameplay (either untouched, or just loaded). */
static bool run_pause_menu(vn_vm_t *vm)
{
    uint8_t selected = 0;

    for (;;) {
        render_scene(&vm->scene);
        render_box(&vm->scene, speaker_display_name(vm, vm->scene.speaker),
                   vm->scene.text, SIZE_MAX);
        render_pause_box(PAUSE_BOX_X, PAUSE_BOX_Y, PAUSE_BOX_W, PAUSE_BOX_H);
        render_text("Paused", PAUSE_BOX_X + 12, PAUSE_BOX_Y + 8, COL_NAME);
        render_list_menu(pause_items, PAUSE_COUNT, selected,
                         PAUSE_BOX_X + 14, PAUSE_BOX_Y + 26, COL_WHITE, COL_HIGHLIGHT);
        render_present(TRANS_CUT);
        gfx_Wait();

        input_t in;
        input_poll(&in);
        if (quit_requested || in.pause) {
            return false; /* Mode again just resumes, same as picking Resume */
        }
        if (in.up) {
            selected = selected == 0 ? PAUSE_COUNT - 1 : (uint8_t)(selected - 1);
        }
        if (in.down) {
            selected = (uint8_t)((selected + 1) % PAUSE_COUNT);
        }
        if (!in.advance) {
            continue;
        }

        switch (selected) {
            case PAUSE_RESUME:
                return false;

            case PAUSE_SAVE: {
                uint8_t slot = run_slot_picker("Save Game", false);
                if (slot != 0) {
                    save_write(slot, vm);
                }
                break; /* back to the pause list */
            }

            case PAUSE_LOAD: {
                uint8_t slot = run_slot_picker("Load Game", true);
                if (slot != 0) {
                    save_load(slot, vm);
                    return false; /* resume play at the loaded position */
                }
                break;
            }

            case PAUSE_TITLE:
                return true;
        }
    }
}

/** Block until any of the tracked keys goes down, or a quit is requested.
 * Keeps redrawing @p vm's scene each frame (render_scene_lazy(), so this
 * costs nothing once any zoom/hop/sink transition has settled) so a
 * still-animating Show finishes playing out even if the player is just
 * looking at an already fully-revealed line, not only during the
 * typewriter reveal.
 *
 * @p allow_pause gates Mode opening the pause menu here -- only true while
 * vn_run() is actually driving @p vm. The other caller (main()'s final
 * "hold the last frame" after vn_run() has already returned, story
 * finished or errored) must not offer it: a Load from the pause menu only
 * works by mutating @p vm and letting vn_step() pick the new pc back up on
 * its next call, and there is no next call once vn_run() has exited -- the
 * loaded scene would flash on screen and then just be discarded.
 */
static void wait_for_advance(vn_vm_t *vm, bool allow_pause)
{
    const vn_scene_t *scene = &vm->scene;
    input_t in;

    do {
        input_poll(&in);
        if (allow_pause && in.pause) {
            if (run_pause_menu(vm)) {
                returning_to_title = true;
                quit_requested = true;
                return;
            }
            continue; /* resumed (or just loaded) -- redraw fresh next loop */
        }
        if (!quit_requested) {
            render_scene_lazy(scene);
            render_box(scene, speaker_display_name(vm, scene->speaker),
                       scene->text, SIZE_MAX);
            render_present(TRANS_CUT);
            gfx_Wait();
        }
    } while (!in.advance && !quit_requested);
}

/* Must match the item order render_title_screen() draws (render.c). */
#define TITLE_NEW   0
#define TITLE_LOAD  1
#define TITLE_HELP  2
#define TITLE_QUIT  3
#define TITLE_COUNT 4

/** @p play_intro replays DDLC's entrance animation. Callers pass false when
 * coming back from a submenu (help, a cancelled load) -- sitting through the
 * intro every time you back out of a menu gets old fast. */
static uint8_t run_title_screen(bool play_intro)
{
    uint8_t selected = 0;
    clock_t start = clock();

    /* When skipped (or on this replay's own skip keypress below), @p t jumps
     * straight past TITLE_INTRO_MS -- see render.h: it's real elapsed time,
     * not a frame count, so "already done" has to be a value, not a flag. The
     * offset keeps growing with clock() afterward, which is what keeps the
     * background scroll animating once the one-shot entrance is finished. */
    bool skipped = !play_intro;

    assets_use_title_palette(true);

    for (;;) {
        unsigned elapsed = (unsigned)((clock() - start) * 1000UL / CLOCKS_PER_SEC);
        unsigned t = skipped ? TITLE_INTRO_MS + elapsed : elapsed;

        render_title_screen(selected, t);
        render_present(TRANS_CUT);
        gfx_Wait();

        input_t in;
        input_poll(&in);
        if (quit_requested) {
            assets_use_title_palette(false);
            return TITLE_QUIT;
        }

        /* During the intro any key skips to the resting layout and is
         * swallowed, so the same press doesn't also pick a menu item. */
        if (!skipped && t < TITLE_INTRO_MS) {
            if (in.advance || in.up || in.down || in.pause) {
                skipped = true;
            }
            continue;
        }

        if (in.up) {
            selected = selected == 0 ? TITLE_COUNT - 1 : (uint8_t)(selected - 1);
        }
        if (in.down) {
            selected = (uint8_t)((selected + 1) % TITLE_COUNT);
        }
        if (in.advance) {
            assets_use_title_palette(false);
            return selected;
        }
    }
}

static void run_help_screen(void)
{
    static const char *const lines[] = {
        "2nd / Enter - advance, confirm",
        "Up / Down   - move selection",
        "Mode        - pause menu, cancel",
        "Clear       - quit",
    };

    for (;;) {
        render_backdrop(COL_BOX_FILL);
        render_text("Keybinds", 14, 12, COL_NAME);
        for (uint8_t i = 0; i < 4; i++) {
            render_text(lines[i], 14, 40 + i * 14, COL_WHITE);
        }
        render_text("2nd / Enter to return", 14, SCREEN_H - 20, COL_BOX_EDGE);
        render_present(TRANS_CUT);
        gfx_Wait();

        input_t in;
        input_poll(&in);
        if (quit_requested || in.advance) {
            return;
        }
    }
}

/* ---------------------------------------------------------------------------
 * VM host callbacks
 * ------------------------------------------------------------------------ */

static char player_name[NAME_MAX_LEN + 1] = "you";

/* Replaces the first "[player]" in @p s with the saved name. compile_script
 * has no opcode for real-time text substitution (that would mean suspending
 * mid-line for a value only known at runtime), so this happens here instead,
 * each time a line is fetched -- see name.h. Confirmed no dialogue line in
 * ch0 uses "[player]" more than once, so only the first match is handled. */
static const char *substitute_player_name(const char *s)
{
    static char buf[256];

    const char *tag = strstr(s, "[player]");
    if (!tag) {
        return s; /* common case: no substitution, zero-copy */
    }

    size_t prefix_len = (size_t)(tag - s);
    size_t name_len = strlen(player_name);
    size_t suffix_len = strlen(tag + 8); /* strlen("[player]") == 8 */

    if (prefix_len + name_len + suffix_len >= sizeof(buf)) {
        return s; /* would overflow the scratch buffer -- shouldn't happen
                    * given the longest known line (170 chars) plus a name,
                    * but degrade to the literal tag rather than corrupt */
    }

    memcpy(buf, s, prefix_len);
    memcpy(buf + prefix_len, player_name, name_len);
    memcpy(buf + prefix_len + name_len, tag + 8, suffix_len + 1); /* +NUL */
    return buf;
}

/* The name to show on the dialogue box's plate for @p speaker, or NULL for
 * narration.
 *
 * Read out of the story variables rather than a fixed table, because DDLC
 * treats a character's displayed name as plot state: script.rpyc opens with
 * s_name = "???" and y/n/m_name = "Girl 1"/"Girl 2"/"Girl 3", ch0 assigns
 * each real name at the moment she introduces herself, and Act 2 puts Monika
 * back to "???". Rendering a fixed table is why every character used to be
 * named from her very first line.
 *
 * Slot and character id are the same number by construction -- see
 * VN_NAME_VAR and compile_script.py's Compiler.NAME_VARS. The literal table
 * is the fallback for a bundle whose script never assigned a name (the
 * variable still holds a number, so assets_var_string() declines it), not
 * the normal path. */
static const char *speaker_display_name(const vn_vm_t *vm, uint8_t speaker)
{
    static const char *const fallback[] = { "Sayori", "Natsuki", "Yuri", "Monika" };

    if (speaker == VN_SPEAKER_NONE) {
        return NULL;              /* narration -- no plate at all */
    }
    if (speaker == VN_SPEAKER_PLAYER) {
        return player_name;
    }
    if (speaker >= VN_MAX_CHARS) {
        return NULL;
    }

    const char *name = assets_var_string(vm->vars[VN_NAME_VAR(speaker)]);
    return (name != NULL && name[0] != '\0') ? name : fallback[speaker];
}

static const char *host_string(void *ctx, uint16_t index)
{
    (void)ctx;
    return substitute_player_name(assets_string(index));
}

static void host_update(void *ctx, const vn_scene_t *scene, uint8_t trans)
{

    /* Most scenes share the game palette (assets_scene_palette() just hands
     * back a pointer to it), but a CG scene needs its own -- see
     * src/assets.c. Applying it here, once per update rather than every
     * draw_background() call, is also what makes the fade case below safe:
     * a fade needs to control *when* the swap becomes visible, which a
     * per-frame write inside render_scene() couldn't do. */
    const uint16_t *palette = assets_scene_palette(scene->background);

    /* Fade out first: the VM has already swapped in the new scene, but the
     * *displayed* buffer still holds the old one, so darkening the palette
     * here fades out what the player is actually looking at. The new scene is
     * then drawn and presented while the palette is still black, and faded
     * up -- matching DDLC's dissolve-to-black / pause / dissolve-back.
     *
     * If the new scene also changes palette (a CG), retargeting here rather
     * than popping it straight to gfx_palette keeps the screen black through
     * the swap -- see render_fade_retarget()'s doc comment. */
    if (trans == TRANS_FADE) {
        render_fade_out();
        render_fade_retarget(palette);
    } else {
        render_apply_palette(palette);
    }

    render_scene(scene);
    render_box(scene, speaker_display_name(ctx, scene->speaker),
               scene->text, SIZE_MAX);
    render_present(trans);

    if (trans == TRANS_FADE) {
        render_fade_in();
    }
}

static void host_say(void *ctx, const vn_scene_t *scene)
{
    vn_vm_t *vm = ctx;

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

        render_scene_lazy(scene);
        render_box(scene, speaker_display_name(vm, scene->speaker),
                   scene->text, visible);
        render_present(TRANS_CUT);
        gfx_Wait();
    }

    wait_for_advance(vm, true);
}

static uint8_t host_menu(void *ctx, const vn_scene_t *scene,
                         const char *const *choices, uint8_t count)
{
    (void)ctx;

    uint8_t selected = 0;

    for (;;) {
        render_scene(scene);
        render_menu(choices, count, selected);
        render_box(scene, NULL, "", SIZE_MAX);
        render_present(TRANS_CUT);
        gfx_Wait();

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

/* Compiles DDLC's own `pause(seconds)` helper (tools/compile_script.py's
 * pause() handling) -- a real dramatic beat in Acts 2/3, not a fixed frame
 * count: waits up to @p ms milliseconds, or until the player advances,
 * whichever comes first. @p ms == 0 means no timeout at all -- DDLC's bare
 * `pause()`, "wait for a click" -- matching splash_wait()'s ms==0 behavior
 * below (clock() - start is always < 0, so only the advance/quit checks can
 * end the loop).
 *
 * Only `advance` breaks it early, not up/down/pause -- those are menu-
 * navigation and pause-menu-open keys with no meaning mid-beat, and
 * reaching the pause menu from here isn't possible anyway (this callback
 * has no vm to hand it). */
static void host_pause(void *ctx, uint16_t ms)
{
    (void)ctx;

    clock_t start = clock();
    input_t in;

    for (;;) {
        input_poll(&in);
        if (quit_requested || in.advance) {
            return;
        }
        if (ms != 0 && (clock() - start) * 1000UL / CLOCKS_PER_SEC >= ms) {
            return;
        }
        gfx_Wait();
    }
}

static bool host_quit(void *ctx)
{
    (void)ctx;
    return quit_requested;
}

/* Called by vn_step() whenever a Jump/Call/Return/Menu-pick target's chunk
 * differs from the one currently resident -- see vn.h's "Chunk-packed
 * addresses" and docs/FORMAT.md's "Chunking". */
static bool host_load_chunk(void *ctx, uint8_t chunk_id,
                            const uint8_t **code_out, size_t *code_size_out)
{
    (void)ctx;
    if (!assets_load_chunk(chunk_id)) {
        return false;
    }
    *code_out = assets_script(code_size_out);
    return true;
}

static uint8_t host_minigame(void *ctx)
{
    (void)ctx;
    return poem_run();
}

/* .ctx is set to &vm once, in main(), after vm exists -- host_say needs it
 * to reach the pause menu (see wait_for_advance). */
static vn_host_t host = {
    .string     = host_string,
    .say        = host_say,
    .menu       = host_menu,
    .update     = host_update,
    .pause      = host_pause,
    .quit       = host_quit,
    .load_chunk = host_load_chunk,
    .minigame   = host_minigame,
    .resolve_label = assets_resolve_label,
    .ctx        = NULL,
};

/* ---------------------------------------------------------------------------
 * Startup splash: Team Salvato's logo, then DDLC's content warning.
 * Plays once before the title screen ever shows. Any key skips ahead to the
 * next screen (not straight past both); Clear quits like everywhere else.
 * ------------------------------------------------------------------------ */

/* tools/import_game.py's do_compile() bakes this scene first and
 * unconditionally, specifically so its id is always 0 regardless of which
 * chapters get compiled -- see resolver.explicit_bg_scene(). (The poem
 * minigame's notebook background is baked separately, outside this scene id
 * space entirely -- see src/poem.c's assets_poem_bg().) */
#define SPLASH_LOGO_SCENE 0
#define SPLASH_HOLD_MS 1600

/** Waits up to @p ms, or until a key is pressed. Returns false on quit. */
static bool splash_wait(unsigned ms)
{
    clock_t start = clock();

    for (;;) {
        input_t in;
        input_poll(&in);
        if (quit_requested) {
            return false;
        }
        if (in.advance || in.up || in.down || in.pause) {
            return true;
        }
        if ((clock() - start) * 1000UL / CLOCKS_PER_SEC >= ms) {
            return true;
        }
        gfx_Wait();
    }
}

/** Fades in @p draw's result, holds (or waits for a skip), fades out.
 * Returns false on quit, in which case the caller should stop immediately
 * rather than proceeding to whatever screen would come next. */
static bool splash_screen(void (*draw)(void))
{
    render_fade_out();
    draw();
    render_present(TRANS_CUT);
    render_fade_in();

    return splash_wait(SPLASH_HOLD_MS);
}

static void draw_splash_logo(void)
{
    if (!assets_scene(SPLASH_LOGO_SCENE, (uint8_t *)gfx_vbuffer)) {
        render_backdrop(COL_WHITE);
    } else {
        gfx_SetColor(COL_WHITE);
        gfx_FillRectangle_NoClip(0, SCENE_H, SCREEN_W, SCREEN_H - SCENE_H);
    }
}

static void draw_splash_warning(void)
{
    render_backdrop(COL_WHITE);
    render_text_centered("This game is not suitable for children", 108, COL_BLACK);
    render_text_centered("or those who are easily disturbed.", 124, COL_BLACK);
}

static void run_splash_screens(void)
{
    if (!splash_screen(draw_splash_logo)) {
        return;
    }
    splash_screen(draw_splash_warning);
}

/* ---------------------------------------------------------------------------
 * Player name entry
 *
 * DDLC asks for this once, right at the start of Act 1 (`renpy.input()`),
 * and every later "[player]" in dialogue substitutes it in. Typed directly
 * on the keypad rather than picked from a list: os_GetCSC() (ti/getcsc.h)
 * returns the OS's own keypad scancode, and its doc comment gives the exact
 * scancode -> letter table the OS uses for its own text-entry routines --
 * that's also the mapping printed above each key in ALPHA mode (MATH=A,
 * APPS=B, PRGM=C, ...), so typing a name here works the same way it would in
 * the TI-OS itself. Deliberately not mixed with input_poll()'s kb_Scan()
 * layer in the same loop -- both read the same hardware, but through
 * different APIs, and os_GetCSC() already reports Clear/Enter/Del as plain
 * scancodes, so there's nothing input_poll() would add here.
 * ------------------------------------------------------------------------ */

/* Verbatim from ti/getcsc.h's os_GetCSC() doc comment: index by scancode to
 * get the letter printed on that key in ALPHA mode, or NUL if it has none. */
static const char name_entry_keymap[] =
    "\0\0\0\0\0\0\0\0\0\0\"WRMH\0\0?[VQLG\0\0:ZUPKFC\0 YTOJEB\0\0XSNIDA\0\0\0\0\0\0\0\0";

static void run_name_entry(void)
{
    char name[NAME_MAX_LEN + 1];
    size_t len = 0;

    for (;;) {
        render_backdrop(COL_WHITE);
        render_text_centered("What is your name?", 60, COL_BLACK);

        char shown[NAME_MAX_LEN + 2];
        memcpy(shown, name, len);
        shown[len] = '_';
        shown[len + 1] = '\0';
        render_text_centered(shown, 100, COL_BLACK);

        render_text_centered("Type your name on the keypad", 140, COL_BOX_FILL);
        render_text_centered("Del erases, Enter confirms", 154, COL_BOX_FILL);
        render_present(TRANS_CUT);
        gfx_Wait();

        uint8_t key = os_GetCSC();
        if (!key) {
            continue;
        }
        if (key == sk_Clear) {
            quit_requested = true;
            return;
        }
        if (key == sk_Enter || key == sk_2nd) {
            if (len == 0) {
                continue; /* need at least one character */
            }
            name[len] = '\0';
            name_save(name);
            return;
        }
        if (key == sk_Del) {
            if (len > 0) {
                len--;
            }
            continue;
        }
        if (key < sizeof(name_entry_keymap)) {
            char c = name_entry_keymap[key];
            if (c >= 'A' && c <= 'Z' && len < NAME_MAX_LEN) {
                name[len++] = c;
            }
        }
    }
}

/* ------------------------------------------------------------------------ */

/** Plain OS-text screen so a missing/broken asset bundle is legible. Shows
 * *which* AppVar/step failed and, if the AppVar this failed on can still be
 * opened at all, its size on-calc -- the difference between "not found" and
 * "found but N bytes, not what was expected" points at two very different
 * bugs (a missing send vs a corrupt/truncated one, or a real loader bug).
 *
 * Called after render_init() has already put the LCD in 8bpp graphx mode
 * (see main()'s ordering comment), so this backs out of that first --
 * os_ClrHome()/os_PutStrFull() are text-mode OS routines. */
static void show_assets_error_screen(assets_status_t status)
{
    char line[32];
    uint8_t handle;

    render_end();
    os_ClrHome();
    os_PutStrFull("DDLC-CE: asset load failed.");
    os_NewLine();
    os_NewLine();
    os_PutStrFull(assets_status_str(status));
    os_NewLine();

    handle = ti_Open("DSCRIPT", "r");
    if (handle) {
        sprintf(line, "DSCRIPT size: %u", ti_GetSize(handle));
        ti_Close(handle);
    } else {
        sprintf(line, "DSCRIPT: not openable");
    }
    os_PutStrFull(line);
    os_NewLine();
    os_NewLine();

    os_PutStrFull("Send the AppVars from:");
    os_NewLine();
    os_PutStrFull("make bundle GAME_DIR=...");
    os_NewLine();
    os_NewLine();
    os_PutStrFull("(see docs/FORMAT.md)");
    while (!os_GetCSC()) {
        /* wait for a keypress */
    }
}

int main(void)
{
    vn_vm_t vm;
    const uint8_t *code;
    size_t code_size;
    assets_status_t status;
    uint32_t entry_pc;

    chars_init();

    /* render_init() (gfx_Begin() + gfx_SetDrawBuffer()) must run before
     * assets_init() writes gfx_palette: gfx_Begin()'s docs say it should
     * run before any other graphx routine, and in practice a palette
     * write before it doesn't reliably stick -- observed on real
     * hardware/CEmu as a correctly-decompressed background (verified
     * against the reference ZX0 decompressor) rendering with wildly wrong
     * colors. */
    render_init();

    status = assets_init();
    if (status != ASSETS_OK) {
        show_assets_error_screen(status);
        return 1;
    }

    entry_pc = assets_entry_pc();
    host.ctx = &vm;

    run_splash_screens();
    if (quit_requested) {
        render_end();
        return 0;
    }

    if (!name_exists()) {
        run_name_entry();
    }
    name_load(player_name, sizeof(player_name));

    /* The intro plays on the first visit and whenever the player comes back
     * out of the story, but not when they merely back out of a submenu. */
    bool play_intro = true;

    while (!quit_requested) {
        uint8_t choice = run_title_screen(play_intro);
        play_intro = false;

        if (choice == TITLE_HELP) {
            run_help_screen();
            continue;
        }
        if (choice == TITLE_QUIT || quit_requested) {
            break;
        }

        /* Every "New Game"/"Continue" needs the entry chunk fresh and
         * resident: a previous session may have crossed into a different
         * chunk, and assets_load_chunk() frees the old buffer on every
         * swap, so code/code_size from before this loop (or from a prior
         * iteration) would be a stale, already-freed pointer by now -- see
         * docs/FORMAT.md's "Chunking". assets_init() already proved the
         * entry chunk loads, so this can't newly fail here. */
        assets_load_chunk(VN_CHUNK_ID(entry_pc));
        code = assets_script(&code_size);
        /* clock() varies with how long the player spent on the title screen
         * (or any prior screen this session), which is the only entropy
         * this platform readily offers -- see vn.h's vn_init() doc comment.
         * Not meant to be unpredictable against someone probing for it,
         * only different from one playthrough to the next. */
        vn_init(&vm, code, code_size, &host, (uint32_t)clock());
        vm.pc       = entry_pc;
        vm.chunk_id = VN_CHUNK_ID(entry_pc);
        /* Ren'Py's own `default` values (s_name = "Sayori", playthrough = 0,
         * ...) -- unconditionally, before checking TITLE_LOAD below: a
         * loaded save's save_load() call overwrites vars[] wholesale right
         * after this if it runs, so applying defaults first for every path
         * costs nothing on Load and is exactly right for New Game/Continue. */
        assets_apply_var_defaults(&vm);

        if (choice == TITLE_LOAD) {
            uint8_t slot = run_slot_picker("Load Game", true);
            if (slot == 0) {
                continue; /* cancelled -- back to the title screen */
            }
            save_load(slot, &vm);
        }

        returning_to_title = false;
        vn_run(&vm);

        if (returning_to_title) {
            quit_requested = false;
            returning_to_title = false;
            play_intro = true;
            continue;
        }
        if (quit_requested) {
            break;
        }

        /* Story finished or hit an error: hold the last frame so it stays
         * readable, then loop back to the title screen. */
        wait_for_advance(&vm, false);
        if (quit_requested) {
            break;
        }
    }

    render_end();
    return 0;
}
