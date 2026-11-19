"""Host regressions for ZX0 sprite LRU arena caching and 240px cutscene rendering."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import image_resolve  # noqa: E402


def function_source(source: str, signature: str) -> str:
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 1
    for end in range(opening + 1, len(source)):
        depth += (source[end] == "{") - (source[end] == "}")
        if not depth:
            return source[start:end + 1]
    raise AssertionError("unterminated function " + signature)


HOST_PREAMBLE = r"""
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define SCREEN_W 320u
#define SCREEN_H 240u
#define SCENE_BYTES (320u * 180u)
#define SCENE_CG_SRC_W 160u

typedef struct {
    uint8_t width;
    uint8_t height;
    uint8_t data[];
} gfx_rletsprite_t;

static uint16_t read_u16le(const uint8_t *p) { return (uint16_t)(p[0] | (p[1] << 8)); }

typedef struct {
    uint8_t width;
    uint8_t height;
    uint16_t raw_size;
    int16_t dx;
    int16_t dy;
    uint8_t fill_byte;
} fake_sprite_t;

static fake_sprite_t fake_sprites[] = {
    { 80, 120,  6000,  1, -2, 0x11 }, /* id 0 */
    { 70, 110,  7000, -3,  4, 0x22 }, /* id 1 */
    { 60, 100,  6000,  5,  6, 0x33 }, /* id 2 -> forces LRU eviction in 16KB arena */
    { 50,  90,  5000,  0,  1, 0x44 }, /* id 3 */
    { 179, 179, 32401, 2,  3, 0x55 }, /* id 4 -> oversize > SPRITE_CACHE_ARENA_CAP */
};

static uint8_t fake_appvar_blobs[5][64];
static unsigned decompress_calls;
static unsigned ti_open_calls;

static uint8_t ti_Open(const char *name, const char *mode) {
    (void)mode;
    ti_open_calls++;
    if (strncmp(name, "DSPR", 4) == 0) {
        return (uint8_t)(atoi(name + 4) + 1);
    }
    if (strncmp(name, "DSCN", 4) == 0) {
        return 10;
    }
    return 0;
}

static void ti_Close(uint8_t handle) { (void)handle; }

static uint8_t cg_source_pixels[160u * 120u];

static const uint8_t *ti_GetDataPtr(uint8_t handle) {
    if (handle == 10) {
        return cg_source_pixels; /* ignored except passed to zx0_Decompress */
    }
    return fake_appvar_blobs[handle - 1];
}

static void zx0_Decompress(void *dst, const void *src) {
    decompress_calls++;
    const uint8_t *s = (const uint8_t *)src;
    if (s == cg_source_pixels) {
        memcpy(dst, cg_source_pixels, sizeof(cg_source_pixels));
        return;
    }
    /* Encoded test payload starts at compressed + 2 with marker index. */
    uint8_t id = s[0];
    uint8_t *out = (uint8_t *)dst;
    fake_sprite_t *fs = &fake_sprites[id];
    out[0] = fs->width;
    out[1] = fs->height;
    memset(out + 2, fs->fill_byte, fs->raw_size - 2);
}

static bool lut_lookup(const uint8_t *lut, uint16_t count, uint16_t id,
                       uint8_t *appvar_out, uint16_t *offset_out, uint16_t *len_out) {
    (void)lut; (void)count;
    if (id >= 5) return false;
    *appvar_out = (uint8_t)id;
    *offset_out = 0;
    if (len_out) *len_out = 8;
    return true;
}

static const uint8_t *sprite_lut = (const uint8_t *)"x";
static uint16_t sprite_lut_count = 5;
static const uint8_t *scene_lut = (const uint8_t *)"x";
static uint16_t scene_lut_count = 2;
static uint8_t sprite_off_buf[5 * 4];
static uint16_t sprite_off_count = 5;
static uint8_t cg_index_data[2] = { 0, 0xFF };
static uint8_t *cg_index_buf = cg_index_data;
static uint16_t cg_index_count = 2;
static uint8_t scene_full_data[2] = { 1, 0 };
static uint8_t *scene_full_buf = scene_full_data;
static uint16_t scene_full_count = 2;

static bool is_cg(uint8_t id) { return id < cg_index_count && cg_index_buf[id] != 0xFF; }
static bool cgpack_read_pixels(uint8_t id, uint8_t *dest) { (void)id; (void)dest; return false; }

static void fast_cg_upscale_2x_rows(uint8_t *dest, const uint8_t *src, uint8_t rows) {
    for (uint8_t r = 0; r < rows; r++) {
        uint8_t *r0 = dest + (size_t)(r * 2u) * SCREEN_W;
        uint8_t *r1 = r0 + SCREEN_W;
        const uint8_t *sr = src + (size_t)r * 160u;
        for (size_t x = 0; x < 160u; x++) {
            uint8_t px = sr[x];
            r0[x * 2] = r0[x * 2 + 1] = px;
            r1[x * 2] = r1[x * 2 + 1] = px;
        }
    }
}

static void fast_cg_upscale_2x(uint8_t *dest, const uint8_t *src) {
    fast_cg_upscale_2x_rows(dest, src, 90u);
}

static void fast_zx0_decompress(void *dst, const void *src) {
    zx0_Decompress(dst, src);
}
"""

HOST_TESTS = r"""
int main(void) {
    for (uint8_t i = 0; i < 5; i++) {
        fake_appvar_blobs[i][0] = (uint8_t)(fake_sprites[i].raw_size & 0xFF);
        fake_appvar_blobs[i][1] = (uint8_t)(fake_sprites[i].raw_size >> 8);
        fake_appvar_blobs[i][2] = i;
        sprite_off_buf[i * 4 + 0] = (uint8_t)(fake_sprites[i].dx & 0xFF);
        sprite_off_buf[i * 4 + 1] = (uint8_t)(fake_sprites[i].dx >> 8);
        sprite_off_buf[i * 4 + 2] = (uint8_t)(fake_sprites[i].dy & 0xFF);
        sprite_off_buf[i * 4 + 3] = (uint8_t)(fake_sprites[i].dy >> 8);
    }

    const gfx_rletsprite_t *rle = NULL;
    int16_t dx = 0, dy = 0;

    /* Cache miss then hit across animated redraws. */
    assert(sprite_open_rlet(0, &rle, &dx, &dy));
    assert(decompress_calls == 1);
    assert(rle->width == 80 && rle->height == 120);
    assert(dx == 1 && dy == -2);

    assert(sprite_open_rlet(1, &rle, &dx, &dy));
    assert(decompress_calls == 2);

    assets_sprite_end(); /* Flash handle closes between frames; RAM cache stays hot. */
    unsigned before_open = ti_open_calls;
    for (int f = 0; f < 10; f++) {
        assert(sprite_open_rlet(0, &rle, &dx, &dy));
        assert(rle->data[0] == 0x11 && rle->data[5997] == 0x11);
        assert(sprite_open_rlet(1, &rle, &dx, &dy));
        assert(rle->data[0] == 0x22 && rle->data[6997] == 0x22);
    }
    assert(decompress_calls == 2);
    assert(ti_open_calls == before_open);

    /* Adding id=2 (6000 B) + id=3 (5000 B) exceeds 16384 B and triggers LRU compaction. */
    assert(sprite_open_rlet(2, &rle, &dx, &dy));
    assert(decompress_calls == 3);
    assert(rle->data[0] == 0x33 && rle->data[5997] == 0x33);

    /* Keep id=2 hot, touch id=3 -> id=1 must stay or id=0 evicted as oldest unused. */
    assert(sprite_open_rlet(2, &rle, &dx, &dy));
    assert(decompress_calls == 3);

    assert(sprite_open_rlet(3, &rle, &dx, &dy));
    assert(decompress_calls == 4);
    assert(rle->data[0] == 0x44 && rle->data[4997] == 0x44);

    /* Confirm compacted id=2 pixels survived memmove intact. */
    assert(sprite_open_rlet(2, &rle, &dx, &dy));
    assert(decompress_calls == 4);
    assert(rle->width == 60 && rle->height == 100);
    assert(rle->data[0] == 0x33 && rle->data[5997] == 0x33);

    /* Oversize sprite (>16KB, e.g. 32401B glitch) decodes, hits on repeat, and restores 16KB arena afterward. */
    assert(sprite_open_rlet(4, &rle, &dx, &dy));
    assert(decompress_calls == 5);
    assert(rle->width == 179 && rle->height == 179);
    assert(rle->data[32398] == 0x55);

    assert(sprite_open_rlet(4, &rle, &dx, &dy));
    assert(decompress_calls == 5);

    assert(sprite_open_rlet(0, &rle, &dx, &dy));
    assert(decompress_calls == 6);
    assert(!sprite_cache_oversize);
    assert(sprite_arena_cap == SPRITE_CACHE_ARENA_CAP);

    assets_sprite_cache_clear();
    assert(sprite_arena == NULL && sprite_cache_count == 0);

    /* Verify fullscreen 160x120 -> 320x240 in-place 2x upscale without overlap corruption. */
    uint8_t fb[320u * 240u];
    memset(fb, 0xA5, sizeof(fb));
    for (unsigned y = 0; y < 120u; y++) {
        for (unsigned x = 0; x < 160u; x++) {
            cg_source_pixels[y * 160u + x] = (uint8_t)((y * 3u + x) & 0xFF);
        }
    }
    assert(assets_scene_fullscreen(0));
    assert(!assets_scene_fullscreen(1));
    assert(assets_scene(0, fb));
    for (unsigned y = 0; y < 240u; y++) {
        for (unsigned x = 0; x < 320u; x++) {
            uint8_t expected = cg_source_pixels[(y / 2u) * 160u + (x / 2u)];
            assert(fb[y * 320u + x] == expected);
        }
    }
    return 0;
}
"""


class TestZx0SpritesAndFullscreen(unittest.TestCase):
    def test_host_lru_sprite_arena_and_fullscreen_cg_upscale(self) -> None:
        cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
        if not cc:
            self.skipTest("no host C compiler")
        assets_c = (ROOT / "src/assets.c").read_text()

        # Extract the exact production sprite cache and scene upscale implementations.
        start = assets_c.index("static bool sprite_lookup(")
        end = assets_c.index("bool assets_draw_sprite(")
        sprite_cache_code = assets_c[start:end]

        code = "\n".join([
            HOST_PREAMBLE,
            sprite_cache_code,
            function_source(assets_c, "bool assets_scene_fullscreen(uint8_t id)"),
            function_source(assets_c, "bool assets_scene(uint8_t id, uint8_t *dest)"),
            HOST_TESTS,
        ])
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "test_assets_host.c"
            exe = Path(tmp) / "test_assets_host"
            src.write_text(code)
            subprocess.run([cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-O2",
                            str(src), "-o", str(exe)], check=True)
            subprocess.run([str(exe)], check=True)

    def test_fullscreen_height_selection_preserves_16_9_backgrounds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            raw = t / "raw"
            (raw / "images" / "bg").mkdir(parents=True)
            (raw / "images" / "cg").mkdir(parents=True)
            (raw / "images" / "poem_special").mkdir(parents=True)

            Image.new("RGB", (1280, 720), (100, 120, 140)).save(raw / "images/bg/club.png")
            Image.new("RGB", (512, 480), (200, 50, 200)).save(raw / "images/bg/glitch.jpg")
            Image.new("RGB", (540, 720), (20, 20, 20)).save(raw / "images/bg/eyes.png")
            Image.new("RGB", (1280, 1280), (40, 10, 10)).save(raw / "images/bg/end-glitch1.JPG")
            Image.new("RGB", (1920, 1080), (31, 103, 179)).save(raw / "images/bg/bsod.png")
            Image.new("RGB", (1280, 720), (250, 210, 190)).save(raw / "images/cg/n_cg1.png")
            Image.new("RGBA", (800, 720), (245, 245, 245, 255)).save(raw / "images/poem_special/poem_special1.png")
            Image.new("RGB", (1280, 720), (0, 0, 0)).save(raw / "images/poem_special/poem_end_clearall.png")
            Image.new("RGBA", (153, 672), (10, 10, 10, 255)).save(raw / "images/cg/s_kill_early.png")

            resolver = image_resolve.ImageResolver(raw, t / "build")
            resolver.table = {
                ("bg", "club"): image_resolve.ImageDef(imgname=("bg", "club"), kind="path", path="images/bg/club.png"),
                ("bg", "glitch"): image_resolve.ImageDef(imgname=("bg", "glitch"), kind="path", path="images/bg/glitch.jpg"),
                ("bg", "eyes"): image_resolve.ImageDef(imgname=("bg", "eyes"), kind="path", path="images/bg/eyes.png"),
                ("black",): image_resolve.ImageDef(imgname=("black",), kind="solid", color=(0, 0, 0)),
                ("exception_bg",): image_resolve.ImageDef(imgname=("exception_bg",), kind="solid", color=(221, 221, 221)),
                ("end_glitch1",): image_resolve.ImageDef(imgname=("end_glitch1",), kind="path", path="images/bg/end-glitch1.JPG"),
                ("bsod",): image_resolve.ImageDef(imgname=("bsod",), kind="path", path="images/bg/bsod.png"),
                ("n_cg1",): image_resolve.ImageDef(imgname=("n_cg1",), kind="path", path="images/cg/n_cg1.png"),
                ("poem_special1",): image_resolve.ImageDef(imgname=("poem_special1",), kind="path", path="images/poem_special/poem_special1.png"),
                ("poem_end_clearall",): image_resolve.ImageDef(imgname=("poem_end_clearall",), kind="path", path="images/poem_special/poem_end_clearall.png"),
                ("s_kill_early",): image_resolve.ImageDef(imgname=("s_kill_early",), kind="path", path="images/cg/s_kill_early.png"),
            }

            sid_club = resolver.scene_id(("bg", "club"))
            sid_glitch = resolver.scene_id(("bg", "glitch"))
            sid_eyes = resolver.scene_id(("bg", "eyes"))
            sid_black = resolver.scene_id(("black",))
            sid_exc = resolver.scene_id(("exception_bg",))
            sid_end = resolver.scene_id(("end_glitch1",))
            sid_bsod = resolver.scene_id(("bsod",))
            sid_ncg1 = resolver.scene_id(("n_cg1",))
            sid_ps1 = resolver.scene_id(("poem_special1",))
            sid_pe_ca = resolver.scene_id(("poem_end_clearall",))
            sid_ske = resolver.scene_id(("s_kill_early",))

            self.assertEqual((resolver.scenes[sid_club]["w"], resolver.scenes[sid_club]["h"]), (320, 180))
            self.assertEqual((resolver.scenes[sid_glitch]["w"], resolver.scenes[sid_glitch]["h"]), (320, 240))
            self.assertEqual((resolver.scenes[sid_eyes]["w"], resolver.scenes[sid_eyes]["h"]), (320, 240))
            self.assertEqual((resolver.scenes[sid_black]["w"], resolver.scenes[sid_black]["h"]), (320, 240))
            self.assertEqual((resolver.scenes[sid_exc]["w"], resolver.scenes[sid_exc]["h"]), (320, 240))
            self.assertEqual((resolver.scenes[sid_end]["w"], resolver.scenes[sid_end]["h"]), (160, 120))
            self.assertEqual((resolver.scenes[sid_bsod]["w"], resolver.scenes[sid_bsod]["h"]), (160, 120))
            self.assertEqual((resolver.scenes[sid_ncg1]["w"], resolver.scenes[sid_ncg1]["h"]), (160, 90))
            self.assertEqual((resolver.scenes[sid_ps1]["w"], resolver.scenes[sid_ps1]["h"]), (160, 120))
            self.assertEqual((resolver.scenes[sid_pe_ca]["w"], resolver.scenes[sid_pe_ca]["h"]), (160, 90))
            self.assertEqual((resolver.scenes[sid_ske]["w"], resolver.scenes[sid_ske]["h"]), (160, 120))


if __name__ == "__main__":
    unittest.main()
