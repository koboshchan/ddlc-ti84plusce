/**
 * @file cgpack.c
 * @brief USB/mass-storage/FAT lifecycle for the optional full-res CG pack.
 * See cgpack.h.
 *
 * usb_Init() is called once and never blocks; everything else happens
 * reactively through the event callback (cgpack_poll() just pumps pending
 * events -- see usb_HandleEvents()'s own doc comment) and lazily, on the
 * next cgpack_read_pixels() call, for the actual per-CG reads. There is no
 * blocking "wait for a drive" loop anywhere in this file: with nothing
 * plugged in, cgpack_read_pixels() simply keeps returning false forever,
 * same as any other optional asset this project treats as absent rather
 * than fatal (see assets.c's file comment on DVSTR/DVDEF/title art/etc).
 *
 * fat_callback_usr_t is redefined to msd_t (matching CEdev's own fat_test
 * example) so fatdrvce can read/write blocks straight through msd_Read()/
 * msd_Write() without a wrapper -- must come before <fatdrvce.h>.
 */

#include "cgpack.h"

#include "assets.h"
#include "render.h"
#include "text.h"

#include <stdio.h>
#include <string.h>

#include <usbdrvce.h>
#include <msddrvce.h>
#define fat_callback_usr_t msd_t
#include <fatdrvce.h>

#define PACK_DIR "/DDLC"
#define MARKER_MAGIC "DCGP"
#define MARKER_SIZE_BLOCKS 1

/* 320x180 raw pixels, padded to a whole number of 512-byte blocks -- see
 * tools/export_cgpack.py's own comment on the file layout. Read straight
 * into the caller's dest (gfx_vbuffer), never into a scratch buffer here:
 * dest is always >= SCREEN_W*SCREEN_H (320x240), and the 256 bytes of
 * padding past row 180 land in the dialogue-box area, overwritten by
 * render_box() immediately after -- see cgpack_read_pixels(). */
#define PIXEL_BLOCKS 113

static usb_device_t usb_dev;
static msd_t         msd;
static fat_t          fat;
static bool           usb_ready;   /* usb_Init() succeeded */
static bool           mounted;     /* msd_Open()+fat_Open() succeeded */
static bool           pack_ok;     /* mounted AND BUILD.ID fingerprint matched */

static void unmount(void)
{
    if (mounted) {
        fat_Close(&fat);
        msd_Close(&msd);
        mounted = false;
    }
    pack_ok = false;
}

/** Reads /DDLC/BUILD.ID and compares its fingerprint against this build's
 * own DCGVER (assets_cgpack_fingerprint()) -- see cgpack.h's file comment
 * on why a mismatch has to be treated as "no pack", not "close enough". */
static bool validate_pack(void)
{
    uint8_t expected[4];
    if (!assets_cgpack_fingerprint(expected)) {
        return false; /* this build shipped no CGs / no DCGVER at all */
    }

    fat_file_t file;
    if (fat_OpenFile(&fat, PACK_DIR "/BUILD.ID", 0, &file) != FAT_SUCCESS) {
        return false;
    }
    uint8_t marker[MARKER_SIZE_BLOCKS * 512];
    bool ok = fat_ReadFile(&file, MARKER_SIZE_BLOCKS, marker) == MARKER_SIZE_BLOCKS;
    fat_CloseFile(&file);

    return ok && memcmp(marker, MARKER_MAGIC, 4) == 0 &&
          memcmp(marker + 4, expected, sizeof(expected)) == 0;
}

static void try_mount(void)
{
    if (mounted) {
        unmount();
    }
    if (msd_Open(&msd, usb_dev) != MSD_SUCCESS) {
        return;
    }

    msd_partition_t partitions[8];
    uint8_t n = msd_FindPartitions(&msd, partitions, 8);
    for (uint8_t i = 0; i < n; i++) {
        if (fat_Open(&fat, msd_Read, msd_Write, &msd, partitions[i].first_lba) == FAT_SUCCESS) {
            mounted = true;
            pack_ok = validate_pack();
            if (pack_ok) {
                return;
            }
            fat_Close(&fat);
            mounted = false;
        }
    }
    msd_Close(&msd);
}

static usb_error_t cgpack_usb_event(usb_event_t event, void *event_data,
                                    usb_callback_data_t *data) __attribute__((unused));
static usb_error_t cgpack_usb_event(usb_event_t event, void *event_data,
                                    usb_callback_data_t *data)
{
    (void)data;
    bool was_ok = pack_ok;

    switch (event) {
        case USB_DEVICE_CONNECTED_EVENT:
            usb_ResetDevice(event_data);
            break;
        case USB_DEVICE_ENABLED_EVENT:
            usb_dev = event_data;
            try_mount();
            break;
        case USB_DEVICE_DISABLED_EVENT:
        case USB_DEVICE_DISCONNECTED_EVENT:
            unmount();
            usb_dev = NULL;
            break;
        default:
            break;
    }

    /* A scene already on screen when the pack becomes (un)available won't
     * otherwise be revisited -- render_scene_lazy() skips redrawing a
     * settled scene on purpose (see its own comment). Clearing that gate
     * here means the very next idle/typewriter frame picks the change up,
     * instead of it waiting for an unrelated real scene change. */
    if (pack_ok != was_ok) {
        render_invalidate_scene();
    }

    return USB_SUCCESS;
}

void cgpack_init(void)
{
    /* Both C BSS and heap now end BEFORE usbArea. OS heap buffers are
     * disjoint too; never select USB_USE_C_HEAP with this linker layout. */
    usb_ready = usb_Init(cgpack_usb_event, NULL, NULL,
                         USB_DEFAULT_INIT_FLAGS) == USB_SUCCESS;
}

void cgpack_end(void)
{
    if (!usb_ready) {
        return;
    }
    unmount();
    usb_Cleanup();
    usb_ready = false;
}

void cgpack_poll(void)
{
    if (usb_ready) {
        usb_HandleEvents();
    }
}

cgpack_status_t cgpack_status(void)
{
    if (!usb_ready) {
        return CGPACK_NO_USB;
    }
    if (!mounted) {
        return CGPACK_NO_DRIVE;
    }
    return pack_ok ? CGPACK_READY : CGPACK_WRONG_PACK;
}

bool cgpack_read_pixels(uint8_t id, uint8_t *dest)
{
    if (!pack_ok) {
        return false;
    }

    char path[24];
    sprintf(path, PACK_DIR "/CG%03u.BIN", id);

    fat_file_t file;
    if (fat_OpenFile(&fat, path, 0, &file) != FAT_SUCCESS) {
        return false;
    }
    uint24_t read = fat_ReadFile(&file, PIXEL_BLOCKS, dest);
    fat_CloseFile(&file);

    if (read != PIXEL_BLOCKS) {
        /* Read failure mid-file (drive yanked without a clean disconnect
         * event, a corrupt filesystem) -- treat the pack as gone rather
         * than retry a wedged mount on every future draw call. */
        unmount();
        render_invalidate_scene();
        return false;
    }
    return true;
}

/* Timed-frame reader, no display/palette mutations. Caller must retire noise
 * undo BEFORE this call and provide the INACTIVE 76800-byte framebuffer.
 * Any false result invalidates all destination bytes: reconstruct the FULL
 * fallback underlay before drawing or swapping. No frame heap allocation. */
static uint32_t timed_u32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1]<<8) |
           ((uint32_t)p[2]<<16) | ((uint32_t)p[3]<<24);
}
/* Exact reflected CRC32 via 16-entry nibble table (64 B vs 1024 B).
 * Every fetched frame remains independently integrity-checked. */
static const uint32_t timed_crc_nibble[16] = {
    0x00000000UL, 0x1DB71064UL, 0x3B6E20C8UL, 0x26D930ACUL,
    0x76DC4190UL, 0x6B6B51F4UL, 0x4DB26158UL, 0x5005713CUL,
    0xEDB88320UL, 0xF00F9344UL, 0xD6D6A3E8UL, 0xCB61B38CUL,
    0x9B64C2B0UL, 0x86D3D2D4UL, 0xA00AE278UL, 0xBDBDF21CUL,
};
static uint32_t timed_crc32(const uint8_t *p, unsigned length) {
    uint32_t crc = 0xFFFFFFFFUL;
    while (length--) {
        uint8_t b = *p++;
        crc = (crc >> 4) ^ timed_crc_nibble[(crc ^ b) & 0x0FU];
        crc = (crc >> 4) ^ timed_crc_nibble[(crc ^ (b >> 4)) & 0x0FU];
    }
    return ~crc;
}
bool cgpack_read_timed_frame(uint8_t scene, uint16_t index,
                            uint32_t expected_ms, uint8_t *inactive,
                            uint16_t *palette) {
    uint8_t *header=inactive; /* staging only, never visible */
    uint32_t palette_crc, pixel_crc;
    fat_file_t file;
    char path[32];
    bool ok = false;
    if (!pack_ok || !inactive || !palette || index >= 4096) return false;
    sprintf(path, PACK_DIR "/T%03u/F%04u.BIN", scene, index);
    if (fat_OpenFile(&fat,path,0,&file)!=FAT_SUCCESS) return false;
    /* One header sector, one palette sector, 113 bounded pixel sectors.
     * Last 256 bytes are padding inside the inactive dialogue region. */
    if (fat_GetFileSize(&file)!=58880UL ||
        fat_ReadFile(&file,1,header)!=1) goto done;
    if (memcmp(header,"DTV1",4) || header[4]!=scene || header[5]!=0 ||
        header[6]!=(uint8_t)index || header[7]!=(uint8_t)(index>>8) ||
        timed_u32(header+8)!=expected_ms ||
        timed_u32(header+12)!=57600UL ||
        header[16]!=64 || header[17]!=1 || header[18]!=180 || header[19]!=0 ||
        timed_u32(header+20)!=512UL || timed_u32(header+24)!=1024UL)
        goto done;
    palette_crc=timed_u32(header+28);
    pixel_crc=timed_u32(header+32);
    if (fat_ReadFile(&file,1,palette)!=1 ||
        fat_ReadFile(&file,113,inactive)!=113) goto done;
    if (timed_crc32((const uint8_t *)palette,512)!=palette_crc ||
        timed_crc32(inactive,57600u)!=pixel_crc) goto done;
    ok=true;
done:
    fat_CloseFile(&file);
    if (!ok) { unmount(); render_invalidate_scene(); }
    return ok;
}

/* DTM1 header: scene, reserved, count(u16), period(u32), duration(u32),
 * CRC32(first 16 bytes). Uniform schedule keeps target metadata bounded.
 * Reading this sector may overwrite inactive pixels, never visible pixels. */
bool cgpack_select_timed_frame(uint8_t scene, uint32_t elapsed_ms,
                              uint8_t *scratch, uint16_t *index,
                              uint32_t *timestamp_ms) {
    fat_file_t file;
    char path[32];
    bool ok=false;
    if (!pack_ok || !scratch || !index || !timestamp_ms) return false;
    sprintf(path, PACK_DIR "/T%03u/SCHED.BIN", scene);
    if (fat_OpenFile(&fat,path,0,&file)!=FAT_SUCCESS) return false;
    if (fat_GetFileSize(&file)!=512UL ||
        fat_ReadFile(&file,1,scratch)!=1) goto done;
    unsigned count=scratch[6]|((unsigned)scratch[7]<<8);
    uint32_t period=timed_u32(scratch+8),duration=timed_u32(scratch+12);
    if (memcmp(scratch,"DTM1",4) || scratch[4]!=scene || scratch[5] ||
        !count || count>4096 || !period || period>60000UL ||
        duration!=(uint32_t)count*period ||
        timed_crc32(scratch,16)!=timed_u32(scratch+16) ||
        elapsed_ms>=duration) goto done;
    *index=(uint16_t)(elapsed_ms/period);
    *timestamp_ms=(uint32_t)*index*period;
    ok=true;
done:
    fat_CloseFile(&file);
    /* An expired valid schedule is ordinary fallback, not a damaged drive. */
    return ok;
}
