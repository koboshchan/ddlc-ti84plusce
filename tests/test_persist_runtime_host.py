"""Native regressions for the real character, persistence and save modules.

Only calculator I/O and assets are stubbed. Every case runs in a fresh process
so persist.c's process-lifetime DPSLOT cache behaves like a calculator launch.
"""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

FILEIOC = r"""
#ifndef FILEIOC_H
#define FILEIOC_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
uint8_t ti_Open(const char *, const char *);
void ti_Close(uint8_t);
uint16_t ti_GetSize(uint8_t);
size_t ti_Read(void *, size_t, size_t, uint8_t);
size_t ti_Write(const void *, size_t, size_t, uint8_t);
int ti_Delete(const char *);
void ti_SetArchiveStatus(bool, uint8_t);
#endif
"""

HARNESS = r"""
#include "vn.h"
#include "chars.h"
#include "persist.h"
#include "save.h"
#include "assets.h"
#include <fileioc.h>
#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    char name[9];
    uint8_t *data;
    size_t size, pos;
    bool exists;
} appvar_t;
static appvar_t files[32];
static const char *short_read;
static uint8_t loaded_chunk;
static bool fail_chunk, null_script;
static size_t target_size = 32;
static const uint8_t old_code[16] = {0};
static const uint8_t target_code[32] = {OP_END};
static unsigned chunk_calls, string_calls;
static const char resolved[] = "loaded chunk dialogue";

static unsigned lookup(const char *name, bool create) {
    for (unsigned i = 1; i < 32; i++)
        if (files[i].name[0] && !strcmp(files[i].name, name)) return i;
    if (create) for (unsigned i = 1; i < 32; i++) if (!files[i].name[0]) {
        assert(strlen(name) <= 8);
        strcpy(files[i].name, name);
        return i;
    }
    return 0;
}
uint8_t ti_Open(const char *name, const char *mode) {
    unsigned i = lookup(name, mode[0] == 'w');
    if (!i) return 0;
    appvar_t *f = &files[i];
    if (mode[0] == 'w') {
        free(f->data); f->data = NULL; f->size = 0; f->exists = true;
    } else if (!f->exists) return 0;
    f->pos = 0;
    return (uint8_t)i;
}
void ti_Close(uint8_t h) { assert(h && h < 32); }
uint16_t ti_GetSize(uint8_t h) { return (uint16_t)files[h].size; }
size_t ti_Read(void *out, size_t size, size_t count, uint8_t h) {
    appvar_t *f = &files[h];
    if (!size) return 0;
    size_t n = count < (f->size - f->pos) / size
        ? count : (f->size - f->pos) / size;
    if (short_read && !strcmp(short_read, f->name) && n) n--;
    if (n) memcpy(out, f->data + f->pos, n * size);
    f->pos += n * size;
    return n;
}
size_t ti_Write(const void *in, size_t size, size_t count, uint8_t h) {
    appvar_t *f = &files[h];
    size_t n = size * count;
    if (!n) return 0;
    f->data = realloc(f->data, f->pos + n);
    assert(f->data);
    memcpy(f->data + f->pos, in, n);
    f->pos += n; f->size = f->pos;
    return count;
}
int ti_Delete(const char *name) {
    unsigned i = lookup(name, false);
    if (!i || !files[i].exists) return 1;
    files[i].exists = false; free(files[i].data); files[i].data = NULL;
    files[i].size = 0; return 0;
}
void ti_SetArchiveStatus(bool a, uint8_t h) { (void)a; (void)h; }
int mini_sprintf(char *out, const char *fmt, ...) {
    va_list ap; va_start(ap, fmt);
    int n = vsprintf(out, fmt, ap); va_end(ap); return n;
}
bool assets_load_chunk(uint8_t chunk) {
    chunk_calls++;
    if (fail_chunk) return false;
    loaded_chunk = chunk;
    return true;
}
const uint8_t *assets_script(size_t *size) {
    *size = target_size;
    return null_script ? NULL : target_code;
}
static const char *host_string(void *ctx, uint16_t index) {
    (void)ctx;
    assert(loaded_chunk == 7 && index == 12);
    string_calls++;
    return resolved;
}
static const vn_host_t host = {.string = host_string};
static void put(const char *name, const void *data, size_t size) {
    uint8_t h = ti_Open(name, "w");
    assert(h && ti_Write(data, 1, size, h) == size);
    ti_Close(h);
}
static bool exists(const char *name) {
    unsigned i = lookup(name, false); return i && files[i].exists;
}
static void seed_slots(void) {
    const uint8_t slots[] = {3, 0, VN_DELETED_VAR(CHAR_SAYORI),
                            VN_PLAYTHROUGH_VAR, 20};
    put("DPSLOT", slots, sizeof(slots));
}
static void seed_values(void) {
    const uint8_t values[] = {1, 0, 2, 0, 0x34, 0x12};
    put("DPERSIST", values, sizeof(values));
}
static void seed_reset(void) {
    uint8_t byte = 1;
    put("FIRSTRUN", &byte, 1); seed_slots(); seed_values();
    put("DNAME", "Player", 7);
    for (unsigned i = 1; i <= SAVE_SLOTS; i++) {
        char name[9]; snprintf(name, sizeof(name), "DSAVE%u", i);
        put(name, &byte, 1);
    }
    put("MONIKA", &byte, 1);
}
static void check_reset(void) {
    assert(exists("FIRSTRUN"));
    for (unsigned i = 0; i < CHAR_COUNT; i++) assert(chars_present(i));
    assert(!exists("DPERSIST"));
    for (unsigned i = 1; i <= SAVE_SLOTS; i++) assert(!save_exists(i));
    unsigned i = lookup("DNAME", false);
    assert(files[i].size == 7 && !memcmp(files[i].data, "Player", 7));
    assert(exists("DPSLOT"));
}
static void seed_save(vn_vm_t *vm) {
    memset(vm, 0, sizeof(*vm));
    vm->host = &host; vm->pc = VN_PACK_ADDR(7, 5);
    vm->chunk_id = 7; vm->code = target_code; vm->code_size = sizeof(target_code);
    vm->sp = 2; vm->stack[0] = VN_PACK_ADDR(3, 4);
    vm->stack[1] = VN_PACK_ADDR(5, 6);
    vm->vars[VN_DELETED_VAR(CHAR_SAYORI)] = 0;
    vm->vars[VN_PLAYTHROUGH_VAR] = 0; vm->vars[20] = 0; vm->vars[100] = 99;
    vm->scene.background = 23; vm->scene.speaker = CHAR_MONIKA;
    vm->scene.text_index = 12;
    vm->scene.actors[0].character = CHAR_YURI;
    vm->scene.actors[0].sprite = 312;
    vm->scene.actors[0].overlay = 407;
    vm->scene.actors[0].flags = VN_FLAG_ZOOM;
    assert(save_write(1, vm));
}
static void seed_current(vn_vm_t *vm) {
    memset(vm, 0, sizeof(*vm));
    vm->host = &host; vm->pc = VN_PACK_ADDR(1, 2);
    vm->chunk_id = 1; vm->code = old_code; vm->code_size = sizeof(old_code);
    vm->scene.background = 3; vm->status = VN_FINISHED;
}
int main(int argc, char **argv) {
    assert(argc == 2);
    const char *which = argv[1];
    vn_vm_t vm, saved, before;
    if (!strcmp(which, "missing_firstrun") || !strcmp(which, "missing_dpersist")) {
        seed_reset();
        assert(ti_Delete(!strcmp(which, "missing_firstrun") ? "FIRSTRUN" : "DPERSIST") == 0);
        chars_init(); check_reset();
    } else if (!strcmp(which, "normal_deleted_sayori")) {
        seed_reset(); uint8_t byte = 9;
        put("NATSUKI", &byte, 1); put("YURI", &byte, 1);
        chars_init();
        assert(!chars_present(CHAR_SAYORI));
        assert(chars_present(CHAR_NATSUKI) && chars_present(CHAR_YURI));
        assert(exists("DPERSIST") && save_exists(SAVE_SLOTS));
        unsigned i = lookup("DPERSIST", false);
        const uint8_t values[] = {1, 0, 2, 0, 0x34, 0x12};
        assert(files[i].size == sizeof(values) && !memcmp(files[i].data, values, sizeof(values)));
        assert(files[lookup("NATSUKI", false)].data[0] == 9);
    } else if (!strcmp(which, "load_current_persistent_and_scene")) {
        seed_slots(); seed_values(); seed_save(&saved); seed_current(&vm);
        assert(save_load(1, &vm));
        assert(vm.vars[VN_DELETED_VAR(CHAR_SAYORI)] == 1);
        assert(vm.vars[VN_PLAYTHROUGH_VAR] == 2 && vm.vars[20] == 0x1234);
        assert(vm.vars[100] == 99);
        assert(vm.pc == saved.pc && vm.chunk_id == 7);
        assert(vm.code == target_code && vm.code_size == sizeof(target_code));
        assert(vm.sp == 2 && !memcmp(vm.stack, saved.stack, sizeof(vm.stack)));
        assert(vm.scene.background == 23 && vm.scene.speaker == CHAR_MONIKA);
        assert(!memcmp(vm.scene.actors, saved.scene.actors, sizeof(vm.scene.actors)));
        assert(vm.scene.text == resolved && vm.scene.text_index == 12);
        assert(vm.status == VN_RUNNING && chunk_calls == 1 && string_calls == 1);
    } else if (!strcmp(which, "failed_chunk_unchanged") ||
               !strcmp(which, "null_script_unchanged") ||
               !strcmp(which, "out_of_bounds_pc_unchanged") ||
               !strcmp(which, "short_save_read_unchanged")) {
        seed_save(&saved); seed_current(&vm); before = vm;
        fail_chunk = !strcmp(which, "failed_chunk_unchanged");
        null_script = !strcmp(which, "null_script_unchanged");
        if (!strcmp(which, "out_of_bounds_pc_unchanged")) target_size = 5;
        if (!strcmp(which, "short_save_read_unchanged")) short_read = "DSAVE1";
        assert(!save_load(1, &vm));
        assert(!memcmp(&vm, &before, sizeof(vm)));
        assert(string_calls == 0);
    } else {
        memset(&vm, 0, sizeof(vm));
        vm.vars[5] = 41; vm.vars[9] = 42; vm.vars[20] = 43;
        seed_values();
        if (!strcmp(which, "one_byte_dpslot")) {
            uint8_t p[] = {3}; put("DPSLOT", p, sizeof(p));
        } else if (!strcmp(which, "empty_dpslot")) {
            uint8_t h = ti_Open("DPSLOT", "w"); ti_Close(h);
        } else if (!strcmp(which, "truncated_dpslot")) {
            uint8_t p[] = {3, 0, 5}; put("DPSLOT", p, sizeof(p));
        } else if (!strcmp(which, "huge_dpslot_count")) {
            uint8_t p[] = {255, 255, 5}; put("DPSLOT", p, sizeof(p));
        } else if (!strcmp(which, "short_dpslot_read")) {
            seed_slots(); short_read = "DPSLOT";
        } else if (!strcmp(which, "short_dpersist_read")) {
            seed_slots(); short_read = "DPERSIST";
        } else if (!strcmp(which, "partial_values_prefix")) {
            seed_slots(); uint8_t p[] = {7, 0, 9};
            put("DPERSIST", p, sizeof(p));
            persist_load(&vm);
            assert(vm.vars[5] == 7 && vm.vars[9] == 42 && vm.vars[20] == 43);
            return 0;
        } else if (!strcmp(which, "valid_roundtrip")) {
            seed_slots();
            assert(persist_save(&vm));
            vn_vm_t restored = {0}; restored.vars[100] = 71;
            persist_load(&restored);
            assert(restored.vars[5] == 41 && restored.vars[9] == 42);
            assert(restored.vars[20] == 43 && restored.vars[100] == 71);
            return 0;
        } else { assert(!"unknown test"); }
        persist_load(&vm);
        assert(vm.vars[5] == 41 && vm.vars[9] == 42 && vm.vars[20] == 43);
        if (strcmp(which, "short_dpersist_read")) {
            assert(persist_save(&vm));
            unsigned i = lookup("DPERSIST", false);
            const uint8_t original[] = {1, 0, 2, 0, 0x34, 0x12};
            assert(files[i].size == sizeof(original));
            assert(!memcmp(files[i].data, original, sizeof(original)));
        }
    }
    return 0;
}
"""

class PersistentRuntimeHost(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        path = Path(cls.tmp.name)
        (path / "fileioc.h").write_text(FILEIOC)
        # Actual assets.h is retained; it only needs graphx's header to exist.
        (path / "graphx.h").write_text("/* No graphx types used by assets.h. */\n")
        (path / "harness.c").write_text(HARNESS)
        cls.binary = path / "runtime"
        subprocess.run([
            os.environ.get("CC", "cc"), "-std=c11", "-Wall", "-Wextra",
            "-Werror", "-g", "-fno-omit-frame-pointer", "-fno-pie", "-no-pie",
            "-fsanitize=address,undefined", "-I", str(path),
            "-I", str(ROOT / "src"), str(path / "harness.c"),
            *(str(ROOT / "src" / name) for name in ("chars.c", "persist.c", "save.c")),
            "-o", str(cls.binary),
        ], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_case(self, case):
        env = dict(os.environ, ASAN_OPTIONS="detect_leaks=0:halt_on_error=1",
                   UBSAN_OPTIONS="halt_on_error=1:print_stacktrace=1")
        result = subprocess.run([str(self.binary), case], env=env,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

def _case(case):
    return lambda self: self.run_case(case)

for _name in (
    "missing_firstrun", "missing_dpersist", "normal_deleted_sayori",
    "load_current_persistent_and_scene", "failed_chunk_unchanged",
    "null_script_unchanged", "out_of_bounds_pc_unchanged",
    "short_save_read_unchanged", "empty_dpslot", "one_byte_dpslot",
    "truncated_dpslot", "huge_dpslot_count", "short_dpslot_read",
    "short_dpersist_read", "partial_values_prefix", "valid_roundtrip",
):
    setattr(PersistentRuntimeHost, "test_" + _name, _case(_name))

if __name__ == "__main__":
    unittest.main()
