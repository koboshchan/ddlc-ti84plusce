
p='/home/kat/ddlc-opt/scene/render_scene.py'; s=open(p).read()
# slot builder
s=s.replace("def placeholder(canvas, zoom, x, y, alpha=1.0):",'''def _build_slot():
    bg = open_img(IMG / 'bg/class.png', 'RGB')
    l = open_img(IMG / 'sayori/1l.png', 'RGBA'); r = open_img(IMG / 'sayori/1r.png', 'RGBA'); a = open_img(IMG / 'sayori/a.png', 'RGBA')
    spr = Image.new('RGBA', (960, 960), (0, 0, 0, 0))
    for part in (l, r, a): spr = Image.alpha_composite(spr, part)   # sayori 1a = 1l + 1r + a
    spr = spr.resize((768, 768), Image.LANCZOS)                      # t11 / tcommon(640): zoom .80, xcenter 640, ypos 1.03 yanchor 1.0
    bg.paste(spr.convert('RGB'), (640 - 384, int(1.03 * H) - 768), spr.getchannel('A'))
    return bg
SLOT = _build_slot()
_slot_cache = {}
def placeholder(canvas, zoom, x, y, alpha=1.0):
    """SAFE SUBSTITUTE for the hidden CG slot: classroom bg + standing Sayori 1a, same zoom/position/alpha ATL."""
    key = round(zoom, 3)
    if key not in _slot_cache:
        if len(_slot_cache) > 40: _slot_cache.clear()
        _slot_cache[key] = SLOT if key == 1.0 else SLOT.resize((int(W * zoom), int(H * zoom)), Image.BILINEAR)
    blit(canvas, _slot_cache[key], (x, y), alpha)

def placeholder_old(canvas, zoom, x, y, alpha=1.0):''')
s=s.replace("'CG hidden (black placeholder, s_kill_bg2)'","'SAFE SUBSTITUTE classroom+Sayori 1a in slot of original s_kill_bg2'")
s=s.replace("'CG hidden (black placeholder, s_kill_bg)'","'SAFE SUBSTITUTE classroom+Sayori 1a in slot of original s_kill_bg'")
s=s.replace("'s_kill_bg_zoom black placeholder zoom2 dizzy(.25,1)'","'s_kill_bg_zoom slot = SAFE SUBSTITUTE classroom+Sayori 1a, zoom2 dizzy(.25,1)'")
s=s.replace("'s_kill_bg2_zoom black placeholder zoom2 dizzy(.25,1) alpha .2-.35'","'s_kill_bg2_zoom slot = SAFE SUBSTITUTE classroom+Sayori 1a, zoom2 dizzy(.25,1) alpha .2-.35'")
a=s.index("         'Censorship:"); b=s.index("'Approximations:")
s=s[:a]+"""         'Substitution: the original hanging CG identifiers s_kill_bg, s_kill_bg2, s_kill_bg_zoom, s_kill_bg2_zoom keep their tags, order, zoom, dizzy, alpha and timing, but their image slot shows a SAFE SUBSTITUTE: bg/class.png (classroom) with the normal standing Sayori sprite 1a (1l+1r+a) at t11 position. Nothing depicting the hanging is rendered. s_kill, s_kill2, s_kill_zoom, s_kill2_zoom and the L246 alias s_kill_zoom_trans are Null so no foreground covers the menus or popup. Original CG files and CG reference images were never opened.', '',
         """+s[b:]
s=s.replace("""The hanging CG images (s_kill*.png) are NEVER opened:
both background CG layers are black frames labelled "CG (hidden)", foreground""","""The hanging CG images (s_kill*.png) are NEVER opened:
both background CG layers show a safe classroom + standing Sayori 1a substitute, foreground""")
open(p,'w').write(s)
