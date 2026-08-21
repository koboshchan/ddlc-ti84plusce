#include <fileioc.h>
#include <graphx.h>
#include <keypadc.h>

int main(void)
{
    const char *names[4] = {"SAYORI", "NATSUKI", "YURI", "MONIKA"};
    
    gfx_Begin();
    gfx_ZeroScreen();
    gfx_SetTextFGColor(1);
    gfx_SetTextXY(10, 20);
    gfx_PrintString("Creating DDLC character AppVars...");

    for (int i = 0; i < 4; i++) {
        ti_var_t ch = ti_Open(names[i], "w");
        if (ch) {
            ti_SetArchiveStatus(true, ch);
            ti_Close(ch);
            gfx_SetTextXY(20, 50 + i * 20);
            gfx_PrintString("[OK] Created: ");
            gfx_PrintString(names[i]);
        } else {
            gfx_SetTextXY(20, 50 + i * 20);
            gfx_PrintString("[FAIL] Could not create: ");
            gfx_PrintString(names[i]);
        }
    }

    gfx_SetTextXY(10, 160);
    gfx_PrintString("Done! Press [Clear] or [Enter] to exit.");
    gfx_BlitBuffer();

    while (!kb_IsDown(kb_KeyClear) && !kb_IsDown(kb_KeyEnter)) {
        kb_Scan();
    }

    gfx_End();
    return 0;
}
