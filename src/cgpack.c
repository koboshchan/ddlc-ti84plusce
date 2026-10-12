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
    static uint8_t marker[MARKER_SIZE_BLOCKS * 512];
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
/* Exact reflected CRC32 represented as four bytes. Avoids 32-bit shift/XOR
 * helper calls per byte. No RAM table/cache, every fetched frame remains
 * independently integrity-checked including rereads after reconnect. */
static const uint8_t timed_crc_bytes[256][4] = {
{0,0,0,0},
{150,48,7,119},
{44,97,14,238},
{186,81,9,153},
{25,196,109,7},
{143,244,106,112},
{53,165,99,233},
{163,149,100,158},
{50,136,219,14},
{164,184,220,121},
{30,233,213,224},
{136,217,210,151},
{43,76,182,9},
{189,124,177,126},
{7,45,184,231},
{145,29,191,144},
{100,16,183,29},
{242,32,176,106},
{72,113,185,243},
{222,65,190,132},
{125,212,218,26},
{235,228,221,109},
{81,181,212,244},
{199,133,211,131},
{86,152,108,19},
{192,168,107,100},
{122,249,98,253},
{236,201,101,138},
{79,92,1,20},
{217,108,6,99},
{99,61,15,250},
{245,13,8,141},
{200,32,110,59},
{94,16,105,76},
{228,65,96,213},
{114,113,103,162},
{209,228,3,60},
{71,212,4,75},
{253,133,13,210},
{107,181,10,165},
{250,168,181,53},
{108,152,178,66},
{214,201,187,219},
{64,249,188,172},
{227,108,216,50},
{117,92,223,69},
{207,13,214,220},
{89,61,209,171},
{172,48,217,38},
{58,0,222,81},
{128,81,215,200},
{22,97,208,191},
{181,244,180,33},
{35,196,179,86},
{153,149,186,207},
{15,165,189,184},
{158,184,2,40},
{8,136,5,95},
{178,217,12,198},
{36,233,11,177},
{135,124,111,47},
{17,76,104,88},
{171,29,97,193},
{61,45,102,182},
{144,65,220,118},
{6,113,219,1},
{188,32,210,152},
{42,16,213,239},
{137,133,177,113},
{31,181,182,6},
{165,228,191,159},
{51,212,184,232},
{162,201,7,120},
{52,249,0,15},
{142,168,9,150},
{24,152,14,225},
{187,13,106,127},
{45,61,109,8},
{151,108,100,145},
{1,92,99,230},
{244,81,107,107},
{98,97,108,28},
{216,48,101,133},
{78,0,98,242},
{237,149,6,108},
{123,165,1,27},
{193,244,8,130},
{87,196,15,245},
{198,217,176,101},
{80,233,183,18},
{234,184,190,139},
{124,136,185,252},
{223,29,221,98},
{73,45,218,21},
{243,124,211,140},
{101,76,212,251},
{88,97,178,77},
{206,81,181,58},
{116,0,188,163},
{226,48,187,212},
{65,165,223,74},
{215,149,216,61},
{109,196,209,164},
{251,244,214,211},
{106,233,105,67},
{252,217,110,52},
{70,136,103,173},
{208,184,96,218},
{115,45,4,68},
{229,29,3,51},
{95,76,10,170},
{201,124,13,221},
{60,113,5,80},
{170,65,2,39},
{16,16,11,190},
{134,32,12,201},
{37,181,104,87},
{179,133,111,32},
{9,212,102,185},
{159,228,97,206},
{14,249,222,94},
{152,201,217,41},
{34,152,208,176},
{180,168,215,199},
{23,61,179,89},
{129,13,180,46},
{59,92,189,183},
{173,108,186,192},
{32,131,184,237},
{182,179,191,154},
{12,226,182,3},
{154,210,177,116},
{57,71,213,234},
{175,119,210,157},
{21,38,219,4},
{131,22,220,115},
{18,11,99,227},
{132,59,100,148},
{62,106,109,13},
{168,90,106,122},
{11,207,14,228},
{157,255,9,147},
{39,174,0,10},
{177,158,7,125},
{68,147,15,240},
{210,163,8,135},
{104,242,1,30},
{254,194,6,105},
{93,87,98,247},
{203,103,101,128},
{113,54,108,25},
{231,6,107,110},
{118,27,212,254},
{224,43,211,137},
{90,122,218,16},
{204,74,221,103},
{111,223,185,249},
{249,239,190,142},
{67,190,183,23},
{213,142,176,96},
{232,163,214,214},
{126,147,209,161},
{196,194,216,56},
{82,242,223,79},
{241,103,187,209},
{103,87,188,166},
{221,6,181,63},
{75,54,178,72},
{218,43,13,216},
{76,27,10,175},
{246,74,3,54},
{96,122,4,65},
{195,239,96,223},
{85,223,103,168},
{239,142,110,49},
{121,190,105,70},
{140,179,97,203},
{26,131,102,188},
{160,210,111,37},
{54,226,104,82},
{149,119,12,204},
{3,71,11,187},
{185,22,2,34},
{47,38,5,85},
{190,59,186,197},
{40,11,189,178},
{146,90,180,43},
{4,106,179,92},
{167,255,215,194},
{49,207,208,181},
{139,158,217,44},
{29,174,222,91},
{176,194,100,155},
{38,242,99,236},
{156,163,106,117},
{10,147,109,2},
{169,6,9,156},
{63,54,14,235},
{133,103,7,114},
{19,87,0,5},
{130,74,191,149},
{20,122,184,226},
{174,43,177,123},
{56,27,182,12},
{155,142,210,146},
{13,190,213,229},
{183,239,220,124},
{33,223,219,11},
{212,210,211,134},
{66,226,212,241},
{248,179,221,104},
{110,131,218,31},
{205,22,190,129},
{91,38,185,246},
{225,119,176,111},
{119,71,183,24},
{230,90,8,136},
{112,106,15,255},
{202,59,6,102},
{92,11,1,17},
{255,158,101,143},
{105,174,98,248},
{211,255,107,97},
{69,207,108,22},
{120,226,10,160},
{238,210,13,215},
{84,131,4,78},
{194,179,3,57},
{97,38,103,167},
{247,22,96,208},
{77,71,105,73},
{219,119,110,62},
{74,106,209,174},
{220,90,214,217},
{102,11,223,64},
{240,59,216,55},
{83,174,188,169},
{197,158,187,222},
{127,207,178,71},
{233,255,181,48},
{28,242,189,189},
{138,194,186,202},
{48,147,179,83},
{166,163,180,36},
{5,54,208,186},
{147,6,215,205},
{41,87,222,84},
{191,103,217,35},
{46,122,102,179},
{184,74,97,196},
{2,27,104,93},
{148,43,111,42},
{55,190,11,180},
{161,142,12,195},
{27,223,5,90},
{141,239,2,45},
};
static uint32_t timed_crc32(const uint8_t *p, unsigned length) {
 uint8_t a=255,b=255,c=255,d=255;
 while(length--) {
  const uint8_t *t=timed_crc_bytes[(uint8_t)(a^*p++)];
  a=b^t[0];b=c^t[1];c=d^t[2];d=t[3];
 }
 uint8_t out[4]={(uint8_t)~a,(uint8_t)~b,(uint8_t)~c,(uint8_t)~d};
 uint32_t result;memcpy(&result,out,4);return result;
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
