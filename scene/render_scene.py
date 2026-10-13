#!/usr/bin/env python3
"""Offline code-rendered censored reference of DDLC script-ch5.rpy L210-310.
No game execution / screen recording. Composites real DDLC assets (extracted to
~/ddlc-opt/assets/raw) using timings + ATL read from compiled .rpyc via the
allowlist AST reader. The hanging CG images (s_kill*.png) are NEVER opened:
both background CG layers show a safe classroom + standing Sayori 1a substitute, foreground
CG layers are omitted (Null) so nothing opaque covers menus/popup.
Usage: python3 render_scene.py [outdir]
"""
import sys, math, random, subprocess, os, re
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageChops
sys.path.insert(0, '/home/kat/ddlc-opt/tools')
from rpyc_ast import load_rpyc, flatten, kind

RAW = Path('/home/kat/ddlc-opt/assets/raw')
IMG = RAW / 'images'
WORK = Path('/home/kat/ddlc-opt/scene/work')
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/home/kat/ti/ddlc-scene')
W, H, FPS = 1280, 720, 30
ORDINARY = 2.5          # hold for ordinary dialogue (original waits for input)
DISSOLVE_CG = 0.75
PLAYER = 'kobosh'
LABEL = 'CG (hidden)'
FORBIDDEN = re.compile(r's_kill')   # asset names we refuse to open

def open_img(p, mode=None):
    assert not FORBIDDEN.search(Path(p).name), 'refusing to open hidden CG asset ' + str(p)
    im = Image.open(p)
    im.load()
    return im.convert(mode) if mode else im

# ----------------------------------------------------------------- assets
A = {}
A['bg'] = open_img(IMG / 'bg/residential.png', 'RGB')
A['noise'] = [open_img(IMG / f'bg/noise{i}.jpg', 'RGB') for i in range(1, 5)]
A['vignette'] = open_img(IMG / 'bg/vignette.png', 'RGBA')
A['splash'] = open_img(IMG / 'bg/splash-glitch.png', 'RGBA')
A['menu_bg'] = open_img(RAW / 'gui/menu_bg.png', 'RGB')
def half(p):
    im = open_img(p, 'RGBA'); return im.resize((im.width // 2, im.height // 2), Image.LANCZOS)
A['art_m'] = half(RAW / 'gui/menu_art_m.png')
A['art_n'] = half(RAW / 'gui/menu_art_n.png')
A['art_y'] = half(RAW / 'gui/menu_art_y.png')
A['textbox'] = open_img(RAW / 'gui/textbox.png', 'RGBA').resize((W, 182), Image.BILINEAR)
A['namebox'] = open_img(RAW / 'gui/namebox.png', 'RGBA')
F_TEXT = ImageFont.truetype(str(RAW / 'gui/font/Aller_Rg.ttf'), 24)
F_NAME = ImageFont.truetype(str(RAW / 'gui/font/RifficFree-Bold.ttf'), 24)
DEJAVU = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'   # Ren'Py _default style font
F_EX1 = ImageFont.truetype(DEJAVU, 40)
F_EX2 = ImageFont.truetype(DEJAVU, 20)

# ----------------------------------------------------------------- easing
def ease(x): return 0.5 - math.cos(math.pi * x) / 2
def easein(x): return math.cos((1 - x) * math.pi / 2)
def easeout(x): return 1 - math.cos(x * math.pi / 2)
def clamp01(x): return max(0.0, min(1.0, x))

def seq_eval(t, vals, durs, fns):
    """piecewise: vals[i] -> vals[i+1] over durs[i] with easing fns[i]; t within one loop."""
    for i, d in enumerate(durs):
        if t < d:
            return vals[i] + (vals[i + 1] - vals[i]) * fns[i](t / d)
        t -= d
    return vals[-1]

lin = lambda x: x
def dizzy(t, m, T):
    """transform dizzy(m, t) from cgs.rpy L247 (two looping parallel blocks)."""
    xv = [0, 10 * m, 5 * m, -5 * m, -3 * m, -10 * m, 0, 5 * m, 0]
    xd = [0.75 * T] * 8
    yv = [0, 5 * m, -5 * m, 0]
    yd = [1.0 * T, 2.0 * T, 1.0 * T]
    tx = t % sum(xd); ty = t % sum(yd)
    return (seq_eval(tx, xv, xd, [ease] * 8), seq_eval(ty, yv, yd, [ease, ease, easein]))

# ----------------------------------------------------------------- draw helpers
def solid_rgb(canvas, box, rgb, alpha=1.0):
    x0, y0, x1, y1 = [int(round(v)) for v in box]
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
    if x1 <= x0 or y1 <= y0: return
    canvas.paste(rgb, (x0, y0, x1, y1), Image.new('L', (x1 - x0, y1 - y0), int(round(255 * alpha))) if alpha < 1 else None)

def blit(canvas, im, pos, alpha=1.0):
    pos = (int(round(pos[0])), int(round(pos[1])))
    if pos[0] >= W or pos[1] >= H or pos[0] + im.width <= 0 or pos[1] + im.height <= 0: return
    if im.mode == 'RGBA':
        mask = im.getchannel('A')
        if alpha < 1: mask = mask.point([int(i * alpha) for i in range(256)])
    else:
        mask = Image.new('L', im.size, int(round(255 * alpha))) if alpha < 1 else None
    canvas.paste(im.convert('RGB') if im.mode == 'RGBA' else im, pos, mask)

_label_cache = {}
def label_img(size):
    size = max(4, int(round(size)))
    if size not in _label_cache:
        f = ImageFont.truetype(DEJAVU, size)
        bb = f.getbbox(LABEL)
        im = Image.new('RGBA', (bb[2] + 4, bb[3] + 4), (0, 0, 0, 0))
        ImageDraw.Draw(im).text((2, 2), LABEL, font=f, fill=(128, 128, 128, 255))
        _label_cache[size] = im
    return _label_cache[size]

def _build_slot():
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

def placeholder_old(canvas, zoom, x, y, alpha=1.0):
    """black CG (hidden) frame; (x, y) is the top-left of the zoomed 1280x720 frame."""
    w, h = W * zoom, H * zoom
    solid_rgb(canvas, (x, y, x + w, y + h), (0, 0, 0), alpha)
    lb = label_img(32 * zoom)
    blit(canvas, lb, (x + (w - lb.width) / 2, y + (h - lb.height) / 2), alpha)

# ----------------------------------------------------------------- displayables
def d_bg(c, tl): blit(c, A['bg'], (0, 0))

def d_white(c, tl): solid_rgb(c, (0, 0, W, H), (255, 255, 255))

def d_skill_bg_plain(c, tl): placeholder(c, 1.0, 0, 0)

def d_skill_bg_start(c, tl):
    # transform s_kill_bg_start: truecenter, zoom 1.10 -> linear 4 -> 1.00
    z = 1.10 + (1.00 - 1.10) * clamp01(tl / 4.0)
    placeholder(c, z, (W - W * z) / 2, (H - H * z) / 2)

def d_skill_bg_zoom(c, tl):   # xalign 0.2 yalign 0.3 zoom 2.0 + dizzy(0.25, 1.0)
    dx, dy = dizzy(tl, 0.25, 1.0)
    placeholder(c, 2.0, 0.2 * (W - 2 * W) + dx, 0.3 * (H - 2 * H) + dy)

BG2_ALPHA = [.2, .2, .25, .2, .3, .25, .35, .3, .35, .2]
def d_skill_bg2_zoom(c, tl):
    dx, dy = dizzy(tl, 0.25, 1.0)
    a = seq_eval(tl % 2.25, BG2_ALPHA, [0.25] * 9, [lin] * 9)
    placeholder(c, 2.0, 0.2 * (W - 2 * W) + dx, 0.3 * (H - 2 * H) + dy, a)

def d_noise(c, tl):
    a = 0.25 * clamp01(tl / 3.0)
    cyc = int(tl / 0.1) % 16
    im = A['noise'][cyc % 4]
    blk = cyc // 4
    if blk in (1, 2): im = im.transpose(Image.FLIP_LEFT_RIGHT)
    if blk in (2, 3): im = im.transpose(Image.FLIP_TOP_BOTTOM)
    blit(c, im, (0, 0), a)

def d_vignette(c, tl): blit(c, A['vignette'], (0, 0), 0.75 * clamp01(tl / 3.0))

def d_splash_glitch(c, tl):
    if tl < 0.5: return
    if tl < 4.0:
        a = clamp01((tl - 0.5) / 0.5) if tl < 1.0 else (1.0 if tl < 3.5 else 1 - clamp01((tl - 3.5) / 0.5))
        blit(c, A['splash'], (0, 0), a); return
    u = tl - 4.0
    a = clamp01(u / 0.5)
    off = -100 * ((u % 0.25) / 0.25)
    ypos = 0 if u < 1.0 else -500 * easeout(clamp01(u - 1.0))
    blit(c, A['menu_bg'], (off, ypos + off), a)

def d_splash_glitch2(c, tl):
    off = -100 * ((tl % 0.05) / 0.05)
    blit(c, A['menu_bg'], (off, off))

def d_glitch_m(c, tl):
    if tl >= 0.6: return                      # parallel #1: pause .5 (starts at .1) then alpha 0
    if tl < 0.1: blit(c, A['art_m'], (0.5 * W, 0.5 * H)); return
    u = (tl - 0.1) % 0.08
    ypos = 1.2 + (0.1 - 1.2) * (u / 0.08)
    blit(c, A['art_m'], (0.3 * W, ypos * H))

N_STEPS = [(0.8, 0.8), (0.2, 0.7), (0.4, 0.2), (0.7, 1.2), (0.1, 1.0), (0.2, 0.6), (0.9, 0.4)]
def d_glitch_n(c, tl):
    if tl < 0.2: blit(c, A['art_n'], (0, 0)); return          # no pos set yet -> topleft
    i = int((tl - 0.2) / 0.05)
    if i >= len(N_STEPS): return                              # alpha 0
    px, py = N_STEPS[i]
    blit(c, A['art_n'], (px * W, py * H))

def d_glitch_y(c, tl):
    # ypos 1.3 is never changed in script-ch5 L70-80 so it sits below the screen (xpos jitters .85/.81 every .02s)
    px = 0.85 if int(tl / 0.02) % 2 == 0 else 0.81
    blit(c, A['art_y'], (px * W, 1.3 * H))

def d_exc_bg(c, tl): solid_rgb(c, (0, 0, W, H), (0xda, 0xda, 0xda))

def text_layer(c, txt, font, pos):
    d = ImageDraw.Draw(c)
    d.multiline_text(pos, txt, font=font, fill=(255, 255, 255), spacing=0)   # _default style color is white
def d_exc1(c, tl): text_layer(c, 'An exception has occurred.', F_EX1, (0.1 * W, 0.05 * H))
def d_exc2(c, tl): text_layer(c, 'File "game/script-ch5.rpy", line 307\nSee traceback.txt for details.', F_EX2, (0.1 * W, 0.15 * H))

def d_null(c, tl): pass

DISPLAY = {
    ('black',): ('black', None),
    ('bg', 'residential_day'): ('bg residential_day', d_bg),
    ('s_kill_bg2',): ('SAFE SUBSTITUTE classroom+Sayori 1a in slot of original s_kill_bg2', d_skill_bg_plain),
    ('s_kill_bg',): ('SAFE SUBSTITUTE classroom+Sayori 1a in slot of original s_kill_bg', d_skill_bg_plain),
    ('s_kill2',): ('Null (s_kill2 foreground CG omitted)', d_null),
    ('s_kill',): ('Null (s_kill foreground CG omitted)', d_null),
    ('s_kill_bg_zoom',): ('s_kill_bg_zoom slot = SAFE SUBSTITUTE classroom+Sayori 1a, zoom2 dizzy(.25,1)', d_skill_bg_zoom),
    ('s_kill_bg2_zoom',): ('s_kill_bg2_zoom slot = SAFE SUBSTITUTE classroom+Sayori 1a, zoom2 dizzy(.25,1) alpha .2-.35', d_skill_bg2_zoom),
    ('s_kill_zoom',): ('Null (s_kill_zoom foreground CG omitted)', d_null),
    ('s_kill2_zoom',): ('Null (s_kill2_zoom foreground CG omitted)', d_null),
    ('noise',): ('noise (alpha 0->.25 over 3s)', d_noise),
    ('vignette',): ('vignette (alpha 0->.75 over 3s)', d_vignette),
    ('white',): ('white solid', d_white),
    ('splash_glitch',): ('splash_glitch (splash-glitch.png fade, then scrolling menu_bg)', d_splash_glitch),
    ('splash_glitch2',): ('splash_glitch2 (menu_bg jitter 20Hz)', d_splash_glitch2),
    ('splash_glitch_m',): ('splash_glitch_m (menu_art_m)', d_glitch_m),
    ('splash_glitch_n',): ('splash_glitch_n (menu_art_n)', d_glitch_n),
    ('splash_glitch_y',): ('splash_glitch_y (menu_art_y, ypos 1.3 offscreen)', d_glitch_y),
    ('exception_bg',): ('exception_bg #dadada', d_exc_bg),
    ('fake_exception',): ('fake_exception text', d_exc1),
    ('fake_exception2',): ('fake_exception2 text', d_exc2),
}
# Per-tag transform applied on top of the displayable (name -> fn replacing the drawing for that tag while the tag is live)
TAG_TRANSFORM = {'s_kill_bg': ('s_kill_bg_start', d_skill_bg_start)}
# s_kill as s_kill_zoom_trans: foreground CG with alpha .5 zoom 2 dizzy(1,1) -> Null here (alias tag kept distinct)

# ----------------------------------------------------------------- timeline from the real AST
def strip_tags(s):
    return re.sub(r'\{/?[a-z]+(=[^}]*)?\}', '', s)

def build_timeline():
    _, top = load_rpyc(RAW / 'script-ch5.rpyc')
    nodes = [n for n in flatten(top) if 210 <= getattr(n, 'linenumber', 0) <= 310]
    layers = {}; order = [0]
    snaps = []        # (time, snapshot) layers: tag -> (key, z, order, t0, transform_key)
    events = {'say': [], 'tear': [], 'sfx': [], 'music': [], 'dissolve': None}
    rows = []
    t = 0.0
    textbox = {'say': None}
    def snap(): snaps.append((t, {k: dict(v) for k, v in layers.items()}, textbox['say']))
    layers['bg'] = dict(key=('bg', 'residential_day'), z=0, order=0, t0=0.0, tf=None); order[0] += 1
    snap()
    def desc_layers():
        out = []
        for tag, v in sorted(layers.items(), key=lambda kv: (kv[1]['z'], kv[1]['order'])):
            out.append(f"{tag}={DISPLAY[v['key']][0]}" + (f" [{v['tf']}]" if v['tf'] else '') + f" z{v['z']}")
        return out
    def row(l, t0, t1, d, hold=''):
        rows.append((l, t0, t1, d, hold, desc_layers(), textbox['say']))
    for n in nodes:
        k = kind(n); l = n.linenumber
        if k == 'Say':
            who = 'mc' if n.who == 'mc' else None
            dur = len('.......Sayo--') / 30 if l == 218 else ORDINARY
            textbox['say'] = (who, n.what, t, l == 218)
            snap()
            events['say'].append((t, t + dur, who, n.what, l == 218))
            row(l, t, t + dur, 'dialogue ' + repr(n.what) + (' [speaker %s]' % PLAYER if who else ' [narrator]'),
                'cps=30 + nw (types out in %.3fs, then auto-advances)' % dur if l == 218 else 'capture hold %.1fs (original waits for input)' % dur)
            t += dur
        elif k in ('Show', 'Hide', 'Scene'):
            im = n.imspec; key = tuple(im[0]); tag = im[2] or key[0]; z = int(im[5]) if im[5] else 0
            if k == 'Scene':
                if l == 309:
                    events['dissolve'] = (t, t + DISSOLVE_CG, {kk: dict(v) for kk, v in layers.items()})
                    layers.clear()
                    layers['black'] = dict(key=('black',), z=0, order=order[0], t0=t, tf=None); order[0] += 1
                    textbox['say'] = None; snap()
                    row(l, t, t + DISSOLVE_CG, 'scene black + with dissolve_cg (Dissolve 0.75s, blocks)', 'transition 0.75s')
                    t += DISSOLVE_CG
                    snaps.append((t, {kk: dict(v) for kk, v in layers.items()}, None))
                continue
            if k == 'Hide':
                layers.pop(tag, None); textbox['say'] = textbox['say'] if False else textbox['say']
                snap(); row(l, t, t, 'hide ' + tag); continue
            if key == ('s_kill',) and im[2] == 's_kill_zoom_trans':
                dk = ('s_kill',)
            else:
                dk = key
            tfnames = im[3]
            tf = tfnames[0] if tfnames else None
            if tag in layers:
                v = layers[tag]; v['key'] = dk; v['z'] = z or v['z']
                if tf: v['tf'] = tf; v['t0'] = t
            else:
                layers[tag] = dict(key=dk, z=z, order=order[0], t0=t, tf=tf); order[0] += 1
            snap(); row(l, t, t, 'show %s%s%s' % (' '.join(key), (' as ' + im[2]) if im[2] else '', (' at ' + ', '.join(tfnames)) if tfnames else ''))
        elif k == 'UserStatement':
            ln = n.line
            if ln.startswith('window hide'):
                textbox['say'] = None; snap(); row(l, t, t, ln)
            elif ln == 'play music td':
                events['music'].append(t); row(l, t, t, ln + ' (td = <loop 36.782>bgm/d.ogg)')
            elif ln.startswith('show screen tear'):
                events['tear'].append([t, None]); layers['screen tear'] = dict(key=('tear',), z=99, order=order[0], t0=t, tf=None); order[0] += 1
                DISPLAY[('tear',)] = ('tear screen (frozen frame sliced into 21 shifting strips)', d_null)
                snap(); row(l, t, t, ln)
            elif ln.startswith('play sound'):
                events['sfx'].append([t, None]); row(l, t, t, ln)
            elif ln == 'stop sound':
                events['sfx'][-1][1] = t; row(l, t, t, ln)
            elif ln == 'hide screen tear':
                events['tear'][-1][1] = t; layers.pop('screen tear', None); snap(); row(l, t, t, ln)
            else:
                row(l, t, t, ln)
        elif k == 'Python':
            src = n.code._state[1]
            if src.startswith('pause('):
                d = float(src[6:-1]); row(l, t, t + d, src, 'hard pause %.2fs' % d); t += d
            elif l == 223:
                row(l, t, t, 'in_sayori_kill = True (no visual)')
            elif l == 224 or 'window hide' in src:
                pass
            else:
                row(l, t, t, 'side effect not rendered: ' + src.replace('\n', ' ')[:80])
        elif k == 'With':
            if l != 309: row(l, t, t, 'with ' + n.expr)
    total = t
    return snaps, events, rows, total

# ----------------------------------------------------------------- textbox
def wrap(draw, text, font, width):
    words = text.split(' '); lines = []; cur = ''
    for w in words:
        trial = (cur + ' ' + w).strip()
        if draw.textlength(trial, font=font) <= width: cur = trial
        else: lines.append(cur); cur = w
    lines.append(cur); return lines

def draw_textbox(canvas, say, t):
    who, what, st, typing = say
    canvas.paste(A['textbox'].convert('RGB'), (0, 532), A['textbox'].getchannel('A'))
    txt = strip_tags(what)
    italic = '{i}' in what
    if typing:
        txt = txt[:int((t - st) * 30) + 1]
    layer = Image.new('RGBA', (W, 182), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    y = 62
    for ln in wrap(d, txt, F_TEXT, 744):
        d.text((268, y), ln, font=F_TEXT, fill=(255, 255, 255, 255), stroke_width=2, stroke_fill=(0xbb, 0x55, 0x99, 255))
        y += 30
    if italic:
        layer = layer.transform(layer.size, Image.AFFINE, (1, 0.2, -0.2 * 100, 0, 1, 0), Image.BILINEAR)
    canvas.paste(layer.convert('RGB'), (0, 532), layer.getchannel('A'))
    if who:
        nb = A['namebox']
        canvas.paste(nb.convert('RGB'), (350 - nb.width // 2, 529), nb.getchannel('A'))
        nl = Image.new('RGBA', (nb.width, nb.height), (0, 0, 0, 0))
        nd = ImageDraw.Draw(nl)
        tw = nd.textlength(PLAYER, font=F_NAME)
        nd.text(((nb.width - tw) / 2, 4), PLAYER, font=F_NAME, fill=(255, 255, 255, 255), stroke_width=2, stroke_fill=(0xbb, 0x55, 0x99, 255))
        canvas.paste(nl.convert('RGB'), (350 - nb.width // 2, 529), nl.getchannel('A'))

# ----------------------------------------------------------------- compositor
class Scene:
    def __init__(self):
        self.snaps, self.events, self.rows, self.total = build_timeline()
        self.snap_t = [s[0] for s in self.snaps]
        self.tears = {}
        rnd = random.Random(1010)
        for i, (t0, t1) in enumerate(self.events['tear']):
            pts = [0, H] + [rnd.randint(10, H - 10) for _ in range(20)]; pts.sort()
            pieces = [dict(sy=pts[j], ey=pts[j + 1], off_t=(rnd.random() * .2 + .2) * .1, on_t=(rnd.random() * .2 + .2) * .1, off=0) for j in range(21)]
            self.tears[i] = dict(t0=t0, t1=t1, pieces=pieces, rnd=rnd, frozen=None)
    def state_at(self, t):
        import bisect
        i = bisect.bisect_right(self.snap_t, t + 1e-9) - 1
        return self.snaps[max(i, 0)]
    def draw_layers(self, layers, t):
        c = Image.new('RGB', (W, H), (0, 0, 0))
        for tag, v in sorted(layers.items(), key=lambda kv: (kv[1]['z'], kv[1]['order'])):
            if v['key'] == ('black',) : continue
            if v['key'] == ('tear',): continue
            fn = DISPLAY[v['key']][1]
            if v['tf'] and tag in TAG_TRANSFORM and v['tf'] == TAG_TRANSFORM[tag][0] and fn is d_skill_bg_plain:
                fn = TAG_TRANSFORM[tag][1]
            fn(c, t - v['t0'])
        return c
    def base_frame(self, t):
        _, layers, say = self.state_at(t)
        d = self.events['dissolve']
        if d and d[0] <= t < d[1]:
            old = self.draw_layers(d[2], t)
            a = clamp01((t - d[0]) / DISSOLVE_CG)
            c = Image.blend(old, Image.new('RGB', (W, H), (0, 0, 0)), a)
        else:
            c = self.draw_layers(layers, t)
        if say: draw_textbox(c, say, t)
        return c
    def frame(self, t):
        for i, tr in self.tears.items():
            if tr['t0'] <= t < tr['t1']:
                if tr['frozen'] is None:
                    tr['frozen'] = self.base_frame(tr['t0'] - 1e-4)
                    for p in tr['pieces']: p['off'] = 0
                src = tr['frozen']; out = src.copy(); st = t - tr['t0']
                for p in tr['pieces']:
                    cyc = (st % (p['off_t'] + p['on_t']))
                    if cyc > p['off_t'] and p['off'] == 0: p['off'] = tr['rnd'].randint(0, 40)
                    elif cyc <= p['off_t'] and p['off'] != 0: p['off'] = 0
                    if p['off']:
                        y0 = max(0, p['sy'] - 1); h = max(0, p['ey'] - p['sy'])
                        out.paste(src.crop((0, y0, W, y0 + h)), (p['off'], p['sy']))
                return out
        return self.base_frame(t)

# ----------------------------------------------------------------- audio
def build_audio(sc, dur, path):
    ev = sc.events
    mt = ev['music'][0]
    ins = ['-i', str(WORK / 'td.ogg')] + sum([['-i', str(WORK / 'glitch.ogg')] for _ in ev['sfx']], [])
    fc = [f"[0:a]atrim=0:{dur - mt:.3f},asetpts=PTS-STARTPTS,aformat=sample_rates=48000:channel_layouts=stereo,adelay={int(mt * 1000)}|{int(mt * 1000)}[m]"]
    labels = ['[m]']
    for i, (a, b) in enumerate(ev['sfx']):
        ln = (b - a)
        fc.append(f"[{i + 1}:a]atrim=0:{ln:.3f},asetpts=PTS-STARTPTS,afade=t=out:st={max(ln - 0.01, 0):.3f}:d=0.01,aformat=sample_rates=48000:channel_layouts=stereo,adelay={int(a * 1000)}|{int(a * 1000)}[s{i}]")
        labels.append(f'[s{i}]')
    fc.append(''.join(labels) + f"amix=inputs={len(labels)}:normalize=0:duration=longest,atrim=0:{dur:.3f}[a]")
    subprocess.run(['ffmpeg', '-y', '-v', 'error'] + ins + ['-filter_complex', ';'.join(fc), '-map', '[a]', '-c:a', 'pcm_s16le', str(path)], check=True)

# ----------------------------------------------------------------- timeline.md
def write_timeline(sc, path, dur):
    L = ['Code-rendered censored reference of DDLC ch5 L210-310 (Sayori hanging scene). No game execution and no screen recording. Real DDLC assets and script timing; nominal MP4 timestamps below (video t=0 is the first dialogue line, 30fps, %.3fs total).' % dur, '',
         'Ordinary dialogue holds %.1fs (original waits for a click). Hard script pauses are original: 3.75, .01, .25, 2, 1.5, 1.5, .2, 4, .2, .75, 6. L218 types at cps=30 then {nw}. dissolve_cg adds a blocking 0.75s transition before L310.' % ORDINARY, '',
         'Substitution: the original hanging CG identifiers s_kill_bg, s_kill_bg2, s_kill_bg_zoom, s_kill_bg2_zoom keep their tags, order, zoom, dizzy, alpha and timing, but their image slot shows a SAFE SUBSTITUTE: bg/class.png (classroom) with the normal standing Sayori sprite 1a (1l+1r+a) at t11 position. Nothing depicting the hanging is rendered. s_kill, s_kill2, s_kill_zoom, s_kill2_zoom and the L246 alias s_kill_zoom_trans are Null so no foreground covers the menus or popup. Original CG files and CG reference images were never opened.', '',
         'Approximations: textbox/namebox placement and outline colour (#b59) follow gui.rpy values but are not pixel exact; tear screen is a re-implementation of Tear() in effects.rpy on a frozen frame (21 strips, offsets 0-40, seeded random); Ren\'Py bilinear filtering/vsync not reproduced; Python side effects and report_exception(...) message (L290) are not rendered; audio before L226 omitted (previous bgm unknown); td = bgm/d.ogg looped from 36.782 as defined; all music/sfx extracted from audio.rpa via a restricted RPA parser. menu_art_y is placed at ypos 1.3 in the original ATL, so it is offscreen. "Layers" lists tag=displayable z-order (low to high); tags replaced via "show X as tag" keep their slot and transform.', '']
    for (l, t0, t1, d, hold, lays, say) in sc.rows:
        rng = f'{t0:.3f}-{t1:.3f}s' if t1 != t0 else f'{t0:.3f}s'
        L.append(f'L{l} {rng} {d}' + (f' | hold: {hold}' if hold else '') + (' | textbox: ' + ('shown' if say else 'hidden')) + ' | layers: ' + ', '.join(lays))
        L.append('')
    Path(path).write_text('\n'.join(L))

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    sc = Scene()
    dur = sc.total
    n = int(round(dur * FPS))
    build_audio(sc, dur, WORK / 'audio.wav')
    write_timeline(sc, OUT / 'timeline.md', dur)
    out = OUT / 'ch5-scene.mp4'
    ff = subprocess.Popen(['ffmpeg', '-y', '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{W}x{H}', '-r', str(FPS), '-i', '-',
                           '-i', str(WORK / 'audio.wav'), '-c:v', 'libx264', '-preset', 'medium', '-crf', '22', '-maxrate', '2200k', '-bufsize', '4400k',
                           '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '128k', '-shortest', '-movflags', '+faststart', str(out)], stdin=subprocess.PIPE)
    sheet = []
    for i in range(n):
        t = i / FPS
        fr = sc.frame(t)
        ff.stdin.write(fr.tobytes())
        if i % int(FPS * 2.5) == 0: sheet.append((t, fr.resize((320, 180))))
    ff.stdin.close(); ff.wait()
    cols = 6; rows_ = (len(sheet) + cols - 1) // cols
    cs = Image.new('RGB', (cols * 320, rows_ * 200), (20, 20, 20)); d = ImageDraw.Draw(cs)
    for k, (t, im) in enumerate(sheet):
        x, y = (k % cols) * 320, (k // cols) * 200
        cs.paste(im, (x, y)); d.text((x + 4, y + 182), f'{t:.1f}s', fill=(255, 255, 255))
    cs.save(WORK / 'contact.jpg', quality=85)
    print('done', n, 'frames', dur)

if __name__ == '__main__':
    main()
