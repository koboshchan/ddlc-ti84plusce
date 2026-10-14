"""Failed-load backing lifetime and caller unwind regressions."""
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def function_source(source, signature):
    """Extract a top-level function, including its actual production body.

    Brace counting is sufficient for these functions (no brace literals).
    Fail loudly if the signature disappears rather than silently using a stub.
    """
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 1
    for end in range(opening + 1, len(source)):
        depth += (source[end] == "{") - (source[end] == "}")
        if not depth:
            return source[start:end + 1]
    raise AssertionError("unterminated function " + signature)


PREAMBLE = r"""
#include "vn.h"
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <assert.h>
#define MAX_STRINGS 2048
static uint8_t *script_buf;
static const uint8_t *script_code;
static size_t script_code_size;
static uint16_t string_count;
static const char *string_ptrs[MAX_STRINGS];
static bool quit_requested;
static uint16_t read_u16le(const uint8_t *p) { return p[0]|(p[1]<<8); }
static void assets_zoom_cache_clear(void) {}
/* A missing DSCR is a real failure before decompression, not a mocked
 * assets_load_chunk result. The production loader must invalidate backing. */
static uint8_t ti_Open(const char *n, const char *m) { (void)n; (void)m; return 0; }
static void ti_Close(uint8_t h) { (void)h; }
static unsigned ti_GetSize(uint8_t h) { (void)h; return 0; }
static const uint8_t *ti_GetDataPtr(uint8_t h) { (void)h; return NULL; }
static void zx0_Decompress(void *d, const void *s) { (void)d; (void)s; }
static bool host_quit(void *ctx) { (void)ctx; return quit_requested; }
"""

MAIN = r"""
static bool explicit_status;
static unsigned say_calls;
static void failed_load_say(void *ctx, const vn_scene_t *scene) {
    vn_vm_t *vm = ctx;
    assert(scene->text && !strcmp(scene->text, "old dialogue"));
    say_calls++;
    /* Models the failed pause Load during host_say's wait_for_advance.
     * The real loader frees both code and the string just consumed above. */
    assert(!assets_load_chunk(7));
    assert(!script_buf && !script_code && !script_code_size);
    quit_requested = true;
    if (explicit_status) vm->status = VN_QUIT;
    /* No textbox access after returning from the failed pause menu. */
}
static const char *host_string(void *ctx, uint16_t index) {
    (void)ctx; assert(index == 0);
    return (const char *)script_buf + 8;
}
int main(void) {
    for (unsigned mode=0; mode<2; mode++) {
        explicit_status = mode != 0;
        quit_requested = false;
        script_buf = calloc(32, 1); assert(script_buf);
        script_code = script_buf + 2; script_code_size = 4;
        script_buf[2] = OP_SAY; script_buf[3] = 0;
        strcpy((char *)script_buf + 8, "old dialogue");
        vn_vm_t vm;
        vn_host_t host = {.quit=host_quit, .say=failed_load_say,
                          .string=host_string, .ctx=&vm};
        vn_init(&vm, script_code, script_code_size, &host, 1);
        assert(vn_run(&vm) == VN_QUIT);
        assert(vm.pc == 4 && say_calls == mode + 1);
        /* Stale pointers deliberately remain, just like production.
         * ASan checks neither OP_SAY's return nor next vn_step reads them. */
    }
    puts("actual asset loader and VM failed-load unwind PASS");
}
"""


class FailedLoadUnwindHost(unittest.TestCase):
    def test_actual_loader_and_vm_sanitizers(self):
        source = function_source(
            (ROOT / "src/assets.c").read_text(),
            "bool assets_load_chunk(uint8_t chunk_id)",
        )
        self.assertIn("free(script_buf)", source)
        compilers = [cc for cc in ("gcc", "clang") if shutil.which(cc)]
        self.assertTrue(compilers, "GCC or clang required")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "probe.c").write_text(PREAMBLE + source + MAIN)
            for cc in compilers:
                with self.subTest(compiler=cc):
                    binary = path / cc
                    subprocess.run([
                        cc, "-std=c11", "-Wall", "-Wextra", "-Werror",
                        "-g", "-fno-pie", "-no-pie", "-fno-omit-frame-pointer",
                        "-fsanitize=address,undefined", "-I", str(ROOT / "src"),
                        str(path / "probe.c"), str(ROOT / "src/vn.c"),
                        "-o", str(binary),
                    ], check=True)
                    subprocess.run(
                        [str(binary)], check=True,
                        env=dict(os.environ,
                                 ASAN_OPTIONS="detect_leaks=1:halt_on_error=1",
                                 UBSAN_OPTIONS="halt_on_error=1"),
                    )

    def test_ui_failure_unwinds_before_textbox_or_final_frame(self):
        # main.c depends on calculator graphics/input. Check only the narrow
        # UI ordering contract; the actual VM and loader execute above.
        source = (ROOT / "src/main.c").read_text()
        menu = function_source(source, "static uint8_t run_game_menu(")
        failure = re.search(
            r"if \(!save_load\(slot, vm\)\)\s*\{([^}]+)\}", menu
        )
        self.assertIsNotNone(failure)
        block = failure.group(1)
        for statement in ("returning_to_title = true;",
                          "quit_requested = true;", "vm->status = VN_QUIT;",
                          "return 0;"):
            self.assertIn(statement, block)
        self.assertNotIn("render", block)
        pause = function_source(source, "static bool run_pause_menu(")
        self.assertRegex(pause, r"if \(!returning_to_title && vm\)")
        wait = function_source(source, "static void wait_for_advance(")
        self.assertRegex(wait, r"if \(run_pause_menu\(vm\)\)\s*\{[^}]*return;")
        say = function_source(source, "static void host_say(")
        self.assertRegex(say, r"wait_for_advance\(vm, true\);\s*\}$")
        main = function_source(source, "int main(void)")
        self.assertRegex(
            main, r"if \(!save_load\(slot, &vm\)\)\s*\{\s*continue;"
        )
        after_run = main[main.index("vn_run(&vm);"):]
        title = after_run.index("if (returning_to_title)")
        final_frame = after_run.index("wait_for_advance(&vm, false)")
        self.assertLess(title, final_frame)
        self.assertRegex(after_run[title:final_frame],
                         r"if \(returning_to_title\)\s*\{[^}]*continue;")
        persist = function_source(
            (ROOT / "src/persist.c").read_text(),
            "bool persist_save(const vn_vm_t *vm)",
        )
        self.assertNotRegex(persist, r"vm->(?:code|scene)")


if __name__ == "__main__":
    unittest.main()
