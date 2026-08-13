/**
 * @file tools/host_sim/main.c
 * @brief Native harness that runs the VM with a stdout renderer.
 *
 * Because src/vn.c and src/text.c avoid calculator headers, they compile
 * unchanged on the host. This gives us a regression check for the bytecode
 * and the word wrapper without needing CEmu or hardware -- and later, a way
 * to replay every converted chapter looking for unknown opcodes.
 *
 * Modes:
 *   (default)   auto-play the compiled-in demo script, always taking menu option 0
 *   --vnb=FILE  replay a real compiled chunk (tools/vnasm.py's
 *               Assembler.to_chunk_bytes() container -- see docs/FORMAT.md's
 *               "Chunking" section) instead of the demo script. Repeatable:
 *               the first --vnb is chunk 0, the second chunk 1, and so on --
 *               matching tools/import_game.py's DSCR0/DSCR1/... numbering --
 *               so a real multi-chunk build can be replayed by passing every
 *               DSCR*.vnb in chunk order. A cross-chunk jump/call swaps
 *               between them the same way src/assets.c does on-calc.
 *   --pc=N      start execution at packed address N instead of 0 -- N is a
 *               raw vn_vm_t.pc value (see vn.h's VN_PACK_ADDR/VN_CHUNK_ID/
 *               VN_CHUNK_OFFSET), i.e. chunk_id in the high byte, local
 *               offset in the low 16 bits. A combined single-chunk build's
 *               actual entry point (Ren'Py's `label start`) usually isn't at
 *               offset 0 either, since compile_script.py emits files in the
 *               order they were given, not by role.
 *   --choices=  comma-separated menu picks, e.g. --choices=0,1,2,3
 *   --trace     also print SCENE/SHOW/HIDE state changes
 *   --seed=N    OP_RANDOM's PRNG seed (default 1) -- fixed rather than
 *               clock-based (unlike src/main.c) so a replay is
 *               reproducible; vary it deliberately to sample a different
 *               random branch, e.g. checking an easter egg's OP_RANDOM
 *               condition actually flips at some seed
 */

#include "../../src/demo.h"
#include "../../src/text.h"
#include "../../src/vn.h"

#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define WRAP_COLS 64

static bool     opt_trace;
static unsigned choices[64];
static unsigned choice_count;
static unsigned choice_pos;
static uint32_t opt_seed = 1;

/* One entry per --vnb=FILE, in the order given (chunk 0, chunk 1, ...). Set
 * when at least one --vnb is passed, so host_load_chunk()/host_string() read
 * from these instead of the compiled-in demo_code/demo_strings[]. */
#define MAX_CHUNKS 64
typedef struct {
    const uint8_t *code;
    size_t         code_size;
    char         **strings;
    uint16_t       string_count;
} chunk_t;

static chunk_t  chunks[MAX_CHUNKS];
static unsigned chunk_count;
static uint8_t  active_chunk; /* which chunks[] entry host_string() reads */

static unsigned say_count;
static unsigned menu_count;

/* One "pixel" per character, so wrapping is measured in columns. */
static unsigned measure(void *ctx, const char *str, size_t len)
{
    (void)ctx;
    (void)str;
    return (unsigned)len;
}

static const char *speaker_name(uint8_t speaker)
{
    static const char *const names[] = { "Sayori", "Natsuki", "Yuri", "Monika" };

    if (speaker == VN_SPEAKER_NONE ||
        speaker >= sizeof(names) / sizeof(names[0])) {
        return NULL;
    }
    return names[speaker];
}

static const char *host_string(void *ctx, uint16_t index)
{
    (void)ctx;
    if (chunk_count > 0) {
        /* String indices are chunk-local (tools/vnasm.py's per-chunk pool),
         * so this has to read whichever chunk vn_step() most recently
         * swapped in via host_load_chunk(), not always chunk 0. */
        const chunk_t *c = &chunks[active_chunk];
        return index < c->string_count ? c->strings[index] : "";
    }
    return index < demo_string_count ? demo_strings[index] : "";
}

static bool host_load_chunk(void *ctx, uint8_t chunk_id,
                            const uint8_t **code_out, size_t *code_size_out)
{
    (void)ctx;
    if (chunk_id >= chunk_count) {
        fprintf(stderr, "! chunk %u requested but only %u loaded (pass more --vnb=)\n",
                chunk_id, chunk_count);
        return false;
    }
    active_chunk  = chunk_id;
    *code_out      = chunks[chunk_id].code;
    *code_size_out = chunks[chunk_id].code_size;
    return true;
}

static void host_say(void *ctx, const vn_scene_t *scene)
{
    (void)ctx;
    say_count++;

    const char *name = speaker_name(scene->speaker);
    printf("%s\n", name ? name : "(narration)");

    /* Run the real wrapper so its output is part of what we verify. */
    text_layout_t layout;
    text_wrap(&layout, scene->text, WRAP_COLS, measure, NULL);

    for (uint8_t i = 0; i < layout.count; i++) {
        printf("  | %.*s\n", (int)layout.lines[i].len, layout.lines[i].start);
    }

    /* The dialogue box holds TEXT_MAX_LINES; anything past that would be
     * silently cut on-calc, so surface it here instead. */
    size_t consumed = 0;
    for (uint8_t i = 0; i < layout.count; i++) {
        consumed += layout.lines[i].len;
    }
    if (consumed + layout.count < strlen(scene->text)) {
        printf("  ! WARNING: line overflows %d display lines\n", TEXT_MAX_LINES);
    }
    printf("\n");
}

static uint8_t host_menu(void *ctx, const vn_scene_t *scene,
                         const char *const *options, uint8_t count)
{
    (void)ctx;
    (void)scene;
    menu_count++;

    unsigned picked = 0;
    if (choice_pos < choice_count) {
        picked = choices[choice_pos++];
        if (picked >= count) {
            picked = 0;
        }
    }

    printf("MENU:\n");
    for (uint8_t i = 0; i < count; i++) {
        printf("  %c %u) %s\n", i == picked ? '>' : ' ', i, options[i]);
    }
    printf("\n");

    return (uint8_t)picked;
}

static void host_update(void *ctx, const vn_scene_t *scene, uint8_t trans)
{
    (void)ctx;

    if (!opt_trace) {
        return;
    }

    printf("[scene bg=%u trans=%u actors=", scene->background, trans);
    for (int i = 0; i < VN_MAX_CHARS; i++) {
        if (scene->actors[i].character != VN_NO_SPRITE) {
            printf("{ch=%u sprite=%u overlay=%u pos=%u flags=%u seq=%u}",
                   scene->actors[i].character, scene->actors[i].sprite,
                   scene->actors[i].overlay, scene->actors[i].pos,
                   scene->actors[i].flags, scene->actors[i].show_seq);
        }
    }
    printf("]\n");
}

static void host_pause(void *ctx, uint8_t frames)
{
    (void)ctx;
    if (opt_trace) {
        printf("[pause %u]\n", frames);
    }
}

static bool host_quit(void *ctx)
{
    (void)ctx;

    /* An infinite loop in generated bytecode would otherwise hang the build,
     * so cap the run rather than trusting the script to terminate. */
    return say_count > 10000;
}

static const vn_host_t host = {
    .string     = host_string,
    .say        = host_say,
    .menu       = host_menu,
    .update     = host_update,
    .pause      = host_pause,
    .quit       = host_quit,
    .load_chunk = host_load_chunk,
    /* .minigame left NULL: OP_MINIGAME's vn.c null-guard defaults the
     * result var to 0 rather than crashing, which is fine for a bytecode
     * regression tool that isn't rendering anything. */
    .ctx        = NULL,
};

static void parse_choices(const char *arg)
{
    const char *p = arg;

    while (*p != '\0' && choice_count < sizeof(choices) / sizeof(choices[0])) {
        char *end;
        unsigned long v = strtoul(p, &end, 10);
        if (end == p) {
            break;
        }
        choices[choice_count++] = (unsigned)v;
        p = (*end == ',') ? end + 1 : end;
    }
}

static uint16_t read_u16le(const uint8_t *p)
{
    return (uint16_t)(p[0] | (p[1] << 8));
}

/**
 * Load a tools/vnasm.py Assembler.to_chunk_bytes() container (see
 * docs/FORMAT.md's "Chunking" section): u16 code_length, code bytes, u16
 * string_count, then per string a u16 byte_length + UTF-8 bytes. Appends the
 * result as the next entry in chunks[] (chunk id = call order, matching
 * tools/import_game.py's DSCR0/DSCR1/... numbering).
 *
 * Leaks its allocations deliberately -- this is a short-lived CLI replay
 * tool, not the on-calc loader.
 */
static bool load_vnb(const char *path)
{
    if (chunk_count >= MAX_CHUNKS) {
        fprintf(stderr, "too many --vnb= (max %d)\n", MAX_CHUNKS);
        return false;
    }

    FILE *f = fopen(path, "rb");
    if (f == NULL) {
        fprintf(stderr, "cannot open %s\n", path);
        return false;
    }

    fseek(f, 0, SEEK_END);
    long total = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (total < 4) {
        fprintf(stderr, "%s: too small to be a chunk container\n", path);
        fclose(f);
        return false;
    }

    uint8_t *buf = malloc((size_t)total);
    if (fread(buf, 1, (size_t)total, f) != (size_t)total) {
        fprintf(stderr, "%s: short read\n", path);
        fclose(f);
        return false;
    }
    fclose(f);

    size_t pos = 0;
    uint16_t code_len = read_u16le(buf + pos);
    pos += 2;
    if (pos + code_len + 2 > (size_t)total) {
        fprintf(stderr, "%s: truncated code section\n", path);
        return false;
    }
    const uint8_t *code = buf + pos;
    pos += code_len;

    uint16_t string_count = read_u16le(buf + pos);
    pos += 2;

    char **strings = malloc(sizeof(char *) * (string_count ? string_count : 1));
    for (uint16_t i = 0; i < string_count; i++) {
        if (pos + 2 > (size_t)total) {
            fprintf(stderr, "%s: truncated string table at entry %u\n", path, i);
            return false;
        }
        uint16_t len = read_u16le(buf + pos);
        pos += 2;
        /* +1: each string carries a trailing NUL in the container (see
         * vnasm.py's to_chunk_bytes) that isn't counted in len. */
        if (pos + len + 1 > (size_t)total) {
            fprintf(stderr, "%s: truncated string data at entry %u\n", path, i);
            return false;
        }
        char *s = malloc(len + 1);
        memcpy(s, buf + pos, len);
        s[len] = '\0';
        strings[i] = s;
        pos += len + 1;
    }

    unsigned id = chunk_count++;
    chunks[id].code         = code;
    chunks[id].code_size    = code_len;
    chunks[id].strings      = strings;
    chunks[id].string_count = string_count;

    printf("loaded chunk %u from %s: %u bytes code, %u strings\n",
          id, path, code_len, string_count);
    return true;
}

int main(int argc, char **argv)
{
    uint32_t start_pc = 0;

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--trace") == 0) {
            opt_trace = true;
        } else if (strncmp(argv[i], "--seed=", 7) == 0) {
            opt_seed = (uint32_t)strtoul(argv[i] + 7, NULL, 0);
        } else if (strncmp(argv[i], "--choices=", 10) == 0) {
            parse_choices(argv[i] + 10);
        } else if (strncmp(argv[i], "--vnb=", 6) == 0) {
            if (!load_vnb(argv[i] + 6)) {
                return 2;
            }
        } else if (strncmp(argv[i], "--pc=", 5) == 0) {
            start_pc = (uint32_t)strtoul(argv[i] + 5, NULL, 0);
        } else {
            fprintf(stderr, "unknown option: %s\n", argv[i]);
            return 2;
        }
    }

    const uint8_t *run_code = demo_code;
    size_t run_code_size = demo_code_size;

    if (chunk_count > 0) {
        run_code = chunks[0].code;
        run_code_size = chunks[0].code_size;
    }

    vn_vm_t vm;
    vn_init(&vm, run_code, run_code_size, &host, opt_seed);
    vm.pc = start_pc;
    vn_status_t status = vn_run(&vm);

    printf("---\n");
    printf("status : %s\n", vn_status_str(status));
    printf("chunk  : %u\n", vm.chunk_id);
    printf("pc     : %u / %u\n", (unsigned)VN_CHUNK_OFFSET(vm.pc), (unsigned)vm.code_size);
    printf("lines  : %u\n", say_count);
    printf("menus  : %u\n", menu_count);

    /* Anything other than a clean finish is a build failure. */
    return status == VN_FINISHED ? 0 : 1;
}
