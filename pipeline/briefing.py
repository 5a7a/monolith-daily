# Monolith briefing renderer - the approved 'Evening Briefing' loop design (Oct 29), driven by an episode.json.
import sys, json, math, re, subprocess, os
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EP_PATH = os.environ.get('EPISODE_JSON') or (sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].endswith('.json') else None)
E = json.load(open(EP_PATH))
W, H, FPS = 1080, 1920, 30
FB = os.path.join(ROOT, 'assets', 'fonts')
FILES = {'i4': 'inter-latin-400-normal.woff', 'i6': 'inter-latin-600-normal.woff', 'i8': 'inter-latin-800-normal.woff',
         'm4': 'jetbrains-mono-latin-400-normal.woff', 'm7': 'jetbrains-mono-latin-700-normal.woff'}
_fc = {}
def F(k, s):
    if (k, s) not in _fc: _fc[(k, s)] = ImageFont.truetype(f'{FB}/{FILES[k]}', s)
    return _fc[(k, s)]
def hx(h): h = h.lstrip('#'); return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
BG = hx('020B12'); PANEL = hx('0A1520'); CARD = hx('060E16'); OX = hx('7B1D2E'); OXH = hx('B8344E')
TXT = hx('F2E8DC'); MUT = hx('8A7E74'); GRN = hx('22C55E'); RED = hx('DC2626'); SLT = hx('5B7A8C'); LINE = hx('14232F')
def clamp(x, a=0.0, b=1.0): return max(a, min(b, x))
def lerp(a, b, t): return a + (b - a) * t
def mix(c1, c2, t): t = clamp(t); return tuple(int(round(lerp(c1[i], c2[i], t))) for i in range(3))
def prog(t, a, b): return clamp((t - a) / (b - a)) if b > a else float(t >= a)
def eo3(x): x = clamp(x); return 1 - (1 - x) ** 3
def eio(x): x = clamp(x); return 3 * x * x - 2 * x ** 3
def back(x):
    x = clamp(x); c1 = 1.9; c3 = c1 + 1
    return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2

WORDS = E['words']; NS = len(E['stories'])
# monotone, min spacing inside a segment
for i in range(1, len(WORDS)):
    a, b = WORDS[i - 1], WORDS[i]
    if a['seg'] == b['seg'] and b['t0'] < a['t0'] + 0.09: b['t0'] = a['t0'] + 0.09
for i, x in enumerate(WORDS):
    nx = WORDS[i + 1] if i + 1 < len(WORDS) else None
    x['t1'] = (nx['t0'] if nx and nx['seg'] == x['seg'] else x['t1'])

# ------------------------------------------------------------------ geometry
PX0, PX1, PY0, PY1 = 56, 1024, 150, 1150
PH = (68, 162, 1012, 672)         # photo slot
CAP_TAG_Y, CAP_Y, CAP_LH = 694, 732, 76
GCX, GCY, GR = 540, 1710, 525     # globe
PINY = 1370

STORIES = E['stories']
for s in STORIES: s['loc'] = tuple(s['loc']); s['chips'] = [tuple(c) for c in s['chips']]; s.setdefault('focus', (0.5, 0.45))
def norm(w): return re.sub(r'[^a-z0-9\',.\-?]', '', w.lower())
def core_w(w): return re.sub(r'[^a-z0-9]', '', w.lower())
KEYS = {core_w(k) for k in E['keys']}

# story timing: question start (male first word), question end, dot time (first female word)
def story_times():
    out = []
    for i in range(NS):
        fw = [x for x in WORDS if x['story'] == i and x['spk'] == 'female']
        mw = [x for x in WORDS if x['spk'] == 'male' and x.get('q') == i]
        td = fw[0]['t0']; qs = mw[0]['t0']; qe = mw[-1]['t1']
        out.append(dict(td=td, qs=qs, qe=qe, fs=qs - 0.05, fe=min(td - 0.1, qe + 0.8), end=fw[-1]['t1']))
    return out
ST = story_times()
TO = ST[NS - 1]['end'] + 0.35      # outro start: return to the intro state
TF = TO + 1.7                # globe starts flying back to the intro view
TOTAL = TO + 3.5             # last frame == one step before frame 0 (seamless loop)
def chip_time(sw, label, trig):
    digits = re.sub(r'[^0-9]', '', label)
    if digits:                                   # numbers: match the spoken number however it was transcribed
        m = [x for x in sw if digits and re.sub(r'[^0-9]', '', x['w']) == digits]
        if m: return m[0]['t0']
    m = [x for x in sw if core_w(x['w']) == core_w(trig)]
    if m: return m[0]['t0']
    m = [x for x in sw if core_w(trig) and core_w(trig)[:5] in core_w(x['w'])]
    return m[0]['t0'] if m else None
for i in range(NS):
    sw = [x for x in WORDS if x['story'] == i and x['spk'] == 'female']
    ts = []
    for k, (label, trig) in enumerate(STORIES[i]['chips']):
        tc = chip_time(sw, label, trig)
        ts.append(tc if tc is not None else sw[0]['t0'] + 1.0 + 1.2 * k)
    STORIES[i]['chip_t'] = sorted(ts)
# ------------------------------------------------------------------ assets
def load_photo(s):
    if not s.get('img') or not os.path.exists(s['img']): return None
    im = Image.open(s['img']).convert('RGB')
    pw, ph = PH[2] - PH[0], PH[3] - PH[1]
    sc = max((pw + 80) / im.width, ph / im.height)
    im = im.resize((int(im.width * sc) + 1, int(im.height * sc) + 1), Image.LANCZOS)
    return im
for s in STORIES: s['photo'] = load_photo(s)

yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
VIG = (1 - 0.38 * (((xx - W / 2) / (W * 0.72)) ** 2 + ((yy - H / 2) / (H * 0.72)) ** 2)).clip(0.55, 1)[..., None]
rng = np.random.default_rng(7)
NOISE = [(rng.standard_normal((H, W, 1)) * 2.6).astype(np.float32) for _ in range(6)]

_land = None
def landpts():
    global _land
    if _land is None:
        polys = json.load(open(os.path.join(ROOT, 'assets', 'land.json'))); out = []
        for p in polys:
            if len(p) < 12: continue
            q = p[::4] + [p[0]]
            lo = np.radians([v[0] for v in q]); la = np.radians([v[1] for v in q])
            out.append(np.stack([np.cos(la) * np.sin(lo), np.sin(la), np.cos(la) * np.cos(lo)], 1))
        _land = out
    return _land
def rot(v, lon0, lat0):
    lo, la = math.radians(lon0), math.radians(lat0)
    x = v[..., 0] * math.cos(lo) - v[..., 2] * math.sin(lo); z = v[..., 0] * math.sin(lo) + v[..., 2] * math.cos(lo); y = v[..., 1]
    y2 = y * math.cos(la) - z * math.sin(la); z2 = y * math.sin(la) + z * math.cos(la)
    return np.stack([x, y2, z2], -1)
def ll(lat, lon):
    la, lo = math.radians(lat), math.radians(lon); return np.array([math.cos(la) * math.sin(lo), math.sin(la), math.cos(la) * math.cos(lo)])
def gpoint(lat, lon, cx, cy, R, lon0, lat0):
    r = rot(ll(lat, lon), lon0, lat0); return float(cx + R * r[0]), float(cy - R * r[1]), float(r[2])

def globe(img, cx, cy, R, lon0, lat0, pins=()):
    x0 = max(0, int(cx - R - 90)); y0 = max(0, int(cy - R - 90)); x1 = min(W, int(cx + R + 90)); y1 = min(H, int(cy + R + 90))
    S = 2
    sub = img.crop((x0, y0, x1, y1)).resize(((x1 - x0) * S, (y1 - y0) * S), Image.BILINEAR)
    d = ImageDraw.Draw(sub)
    scx, scy = (cx - x0) * S, (cy - y0) * S; sR = R * S
    for k in range(8, 0, -1):
        rr = sR + k * 14; d.ellipse((scx - rr, scy - rr, scx + rr, scy + rr), outline=mix(BG, OX, 0.05 * (9 - k)), width=14)
    d.ellipse((scx - sR, scy - sR, scx + sR, scy + sR), fill=hx('07131D'))
    gl = hx('132634')
    def poly_lines(pts):
        r = rot(pts, lon0, lat0)
        for i in range(len(r) - 1):
            if r[i, 2] > 0 and r[i + 1, 2] > 0:
                d.line((scx + sR * r[i, 0], scy - sR * r[i, 1], scx + sR * r[i + 1, 0], scy - sR * r[i + 1, 1]), fill=gl, width=2)
    for lon in range(-180, 180, 20): poly_lines(np.array([ll(la, lon) for la in range(-90, 91, 4)]))
    for lat in range(-60, 61, 20): poly_lines(np.array([ll(lat, lo) for lo in range(-180, 181, 4)]))
    lf = hx('17303F'); lo_ = hx('3C6278')
    for p in landpts():
        r = rot(p, lon0, lat0)
        if r[:, 2].max() <= 0: continue
        X = r[:, 0].copy(); Y = r[:, 1].copy(); bk = r[:, 2] <= 0
        if bk.any():
            n = np.sqrt(X[bk] ** 2 + Y[bk] ** 2) + 1e-9; X[bk] /= n; Y[bk] /= n
        d.polygon(list(zip(scx + sR * X, scy - sR * Y)), fill=lf)
        for i in range(len(r) - 1):
            if r[i, 2] > 0 and r[i + 1, 2] > 0:
                d.line((scx + sR * r[i, 0], scy - sR * r[i, 1], scx + sR * r[i + 1, 0], scy - sR * r[i + 1, 1]), fill=lo_, width=2)
    d.ellipse((scx - sR, scy - sR, scx + sR, scy + sR), outline=hx('2A4558'), width=4)
    d.arc((scx - sR, scy - sR, scx + sR, scy + sR), 200, 340, fill=hx('4B7389'), width=6)
    img.paste(sub.reduce(S), (x0, y0))

# ------------------------------------------------------------------ camera
def target(i): lat, lon = STORIES[i]['loc']; return (lon, lat - 38.0)
def hold_view(i, t):
    lon, lat = target(i); return (lon + 1.1 * (t - ST[i]['fe']), lat)
def intro_view(t): return (-35 + 5.0 * t, 16.0)
def view(t):
    if t >= TF:
        u = eio((t - TF) / (TOTAL - TF))
        v0 = hold_view(NS - 1, TF); v1 = intro_view(0.0)
        dl = ((v1[0] - v0[0] + 180) % 360) - 180
        return (v0[0] + dl * u, lerp(v0[1], v1[1], u)), GR * (1 - 0.07 * math.sin(math.pi * u))
    if t < ST[0]['fs']: return intro_view(t), GR
    for i in range(NS):
        a = ST[i]['fs']; b = ST[i]['fe']
        if a <= t < b:
            u = eio((t - a) / (b - a))
            v0 = intro_view(a) if i == 0 else hold_view(i - 1, a)
            v1 = hold_view(i, b)
            dl = ((v1[0] - v0[0] + 180) % 360) - 180
            v = (v0[0] + dl * u, lerp(v0[1], v1[1], u))
            return v, GR * (1 - 0.07 * math.sin(math.pi * u))
        nxt = ST[i + 1]['fs'] if i < NS - 1 else 1e9
        if b <= t < nxt: return hold_view(i, t), GR
    return hold_view(NS - 1, t), GR

def cur_story(t):
    if t >= TO: return -1
    c = -1
    for i in range(NS):
        if t >= ST[i]['fs']: c = i
    return c

# ------------------------------------------------------------------ drawing helpers
def text(d, xy, s, font, fill, alpha=1.0, bg=BG, anchor='la', track=0):
    if alpha <= 0.01: return
    c = mix(bg, fill, alpha)
    if track:
        x, y = xy
        tw = sum(font.getlength(ch) + track for ch in s) - track
        if anchor[0] == 'm': x -= tw / 2
        elif anchor[0] == 'r': x -= tw
        for ch in s:
            d.text((x, y), ch, font=font, fill=c, anchor='l' + anchor[1]); x += font.getlength(ch) + track
    else: d.text(xy, s, font=font, fill=c, anchor=anchor)
def rrect(d, box, r, fill=None, outline=None, width=1): d.rounded_rectangle(box, radius=r, fill=fill, outline=outline, width=width)
def wrap(s, font, width):
    out, cur = [], ''
    for w in s.split(' '):
        t = (cur + ' ' + w).strip()
        if font.getlength(t) <= width or not cur: cur = t
        else: out.append(cur); cur = w
    if cur: out.append(cur)
    return out

def draw_slab(d, cx, cy, h, a=1.0, bg=BG):
    w = h * 0.45
    rrect(d, (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2), 3, fill=mix(bg, hx('1B2A36'), a), outline=mix(bg, hx('2D4252'), a), width=2)
    d.rectangle((cx - w * 0.07, cy - h * 0.34, cx + w * 0.07, cy + h * 0.34), fill=mix(bg, OXH, a))

# ------------------------------------------------------------------ captions
def n_lines(ws):   # how many caption lines these words take (same layout as draw_caption)
    male = ws[0]['spk'] == 'male'; font = F('i6' if male else 'i8', 56 if male else 68)
    n, cw, sp = 1, 0, font.getlength(' ')
    for x in ws:
        wl = font.getlength(x['w'])
        if cw + wl > 880 and cw: n += 1; cw = 0
        cw += wl + sp
    return n

def chunks():
    out = []; cur = []
    for i, x in enumerate(WORDS):
        if cur and n_lines(cur + [x]) > 2: out.append(cur); cur = []   # never a third line: it would run into the headline below
        cur.append(x)
        nx = WORDS[i + 1] if i + 1 < len(WORDS) else None
        endp = x['w'].endswith(('.', '?', '!')) or (x['w'].endswith(',') and len(cur) >= 3)
        brk = (nx is None) or nx['seg'] != x['seg'] or endp or len(cur) >= 5 or (nx['t0'] - x['t1'] > 0.45 and len(cur) >= 2)
        if brk:
            out.append(cur); cur = []
    return out
CH = chunks()
for k, c in enumerate(CH):
    c0 = c[0]['t0'] - 0.04
    nxt = CH[k + 1][0] if k + 1 < len(CH) else None
    if nxt and nxt['seg'] == c[-1]['seg']: c1 = nxt['t0'] - 0.02
    else: c1 = c[-1]['t1'] + 0.30
    c[0]['_win'] = (c0, c1)
ENTR = ['rise', 'drop', 'slide', 'zoom']

def draw_caption(img, t):
    d = ImageDraw.Draw(img)
    cur = None
    for k, c in enumerate(CH):
        a, b = c[0]['_win']
        if a <= t < b: cur = (k, c, a, b); break
    if cur is None: return
    k, c, a, b = cur
    spk = c[0]['spk']; male = spk == 'male'
    u = eo3((t - a) / 0.16)
    kind = ENTR[k % 4]
    dx = dy = 0; sc = 1.0
    if kind == 'rise': dy = (1 - u) * 26
    elif kind == 'drop': dy = -(1 - u) * 26
    elif kind == 'slide': dx = -(1 - u) * 44
    else: sc = 0.94 + 0.06 * u
    alpha = clamp((t - a) / 0.10) * (1 - clamp((t - (b - 0.10)) / 0.10))
    fs = 56 if male else 68
    font = F('i6' if male else 'i8', fs)
    base = MUT if male else TXT
    # layout words
    maxw = 880; lines = [[]]; cw = 0; sp = font.getlength(' ')
    for x in c:
        wl = font.getlength(x['w'])
        if cw + wl > maxw and lines[-1]: lines.append([]); cw = 0
        lines[-1].append((x, cw)); cw += wl + sp
    # tag
    tag = 'ASKS' if male else 'ANSWERS'
    tf = F('m7', 19)
    text(d, (84 + dx, CAP_TAG_Y + dy), tag, tf, SLT if male else OXH, alpha * 0.95, PANEL, track=3)
    ty0 = CAP_Y + dy
    lh = CAP_LH if not male else 68
    for li, ln in enumerate(lines):
        y = ty0 + li * lh
        for x, wx in ln:
            xpos = 84 + dx + wx
            wl = font.getlength(x['w'])
            key = core_w(x['w']) in KEYS and not male
            spoken = t >= x['t0']
            iscur = x['t0'] <= t < x['t1']
            if key and spoken:
                p = eo3((t - x['t0']) / 0.14)
                d.rectangle((xpos - 8, y + 6, xpos - 8 + (wl + 16) * p, y + fs + 10), fill=mix(PANEL, OX, alpha))
            if not spoken: col = mix(PANEL, base, 0.28 * alpha)
            elif iscur and not male: col = mix(PANEL, (255, 255, 255), alpha) if not key else mix(PANEL, TXT, alpha)
            else: col = mix(PANEL, base, alpha)
            yy_ = y
            if iscur: yy_ = y - 3 * math.sin(math.pi * clamp((t - x['t0']) / max(0.12, x['t1'] - x['t0'])))
            d.text((xpos, yy_), x['w'], font=font, fill=col)
            if iscur and not key and not male:
                d.rectangle((xpos, y + fs + 14, xpos + wl, y + fs + 17), fill=mix(PANEL, OXH, alpha))

# ------------------------------------------------------------------ panel
def count_label(label, p):
    m = re.match(r'^([~]?)(\d[\d,]*)(\+?)(.*)$', label)
    if not m: return label
    pre, num, plus, rest = m.groups()
    n = int(num.replace(',', ''))
    v = int(round(n * p))
    s = f'{v:,}' if ',' in num else str(v)
    return f'{pre}{s}{plus}{rest}'

def draw_panel_frame(img, t, ci):
    d = ImageDraw.Draw(img)
    rrect(d, (PX0, PY0, PX1, PY1), 22, fill=PANEL, outline=hx('1D3140'), width=2)
    # corner ticks
    for (x, y, sx, sy) in ((PX0, PY0, 1, 1), (PX1, PY0, -1, 1), (PX0, PY1, 1, -1), (PX1, PY1, -1, -1)):
        d.line((x, y + sy * 34, x, y, x + sx * 34, y), fill=OXH, width=3)

def draw_slot_intro(img, t, a=1.0):
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = PH
    d.rectangle((x0, y0, x1, y1), fill=CARD)
    for gx in range(x0, x1, 48): d.line((gx, y0, gx, y1), fill=hx('0C1A25'))
    for gy in range(y0, y1, 48): d.line((x0, gy, x1, gy), fill=hx('0C1A25'))
    draw_slab(d, x0 + 120, (y0 + y1) / 2, 150)
    text(d, (x0 + 230, (y0 + y1) / 2 - 62), E['slot'], F('i8', 84), TXT, 1, CARD, anchor='lm')
    text(d, (x0 + 230, (y0 + y1) / 2 + 22), 'BRIEFING', F('i8', 84), TXT, 1, CARD, anchor='lm')
    text(d, (x0 + 232, (y0 + y1) / 2 + 100), f"{E['date_long']}  ·  {NS} STORIES", F('m7', 24), OXH, 1, CARD, anchor='lm', track=3)

def draw_slot_wait(img, t, place):
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = PH
    d.rectangle((x0, y0, x1, y1), fill=CARD)
    for gx in range(x0, x1, 48): d.line((gx, y0, gx, y1), fill=hx('0C1A25'))
    for gy in range(y0, y1, 48): d.line((x0, gy, x1, gy), fill=hx('0C1A25'))
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    d.line((cx - 40, cy, cx + 40, cy), fill=hx('2A4558'), width=2); d.line((cx, cy - 40, cx, cy + 40), fill=hx('2A4558'), width=2)
    sy = y0 + ((t * 300) % (y1 - y0)); d.line((x0, sy, x1, sy), fill=hx('1D3140'), width=2)
    text(d, (cx, cy + 70), 'ACQUIRING SOURCE', F('m7', 22), SLT, 1, CARD, anchor='mm', track=4)

def draw_photo(img, s, t, p, ts):
    # p: reveal progress 0..1 (left-to-right with blinds)
    x0, y0, x1, y1 = PH
    ph = s['photo']; pw_, ph_ = x1 - x0, y1 - y0
    if ph is None:
        ph = Image.new('RGB', (pw_ + 80, ph_), CARD); dd = ImageDraw.Draw(ph)
        for gx in range(0, pw_ + 80, 48): dd.line((gx, 0, gx, ph_), fill=hx('0C1A25'))
        for gy in range(0, ph_, 48): dd.line((0, gy, pw_ + 80, gy), fill=hx('0C1A25'))
        f = F('i8', 64); ln = wrap(s['outlet'], f, pw_ - 120)
        for k, t_ in enumerate(ln[:2]): dd.text((60, ph_ / 2 - 40 * len(ln[:2]) + k * 78), t_, font=f, fill=TXT, anchor='lm')
        dd.text((62, ph_ / 2 + 60), f"SOURCE GRADE {s['grade']}  ·  {s['topic']}", font=F('m7', 26), fill=OXH, anchor='lm')
        s['photo'] = ph
    pan = int(clamp((t - ts) / 20.0) * 60)
    ox = int((ph.width - pw_ - 80) * s['focus'][0]) + pan
    oy = int((ph.height - ph_) * s['focus'][1])
    crop = ph.crop((ox, oy, ox + pw_, oy + ph_))
    # slight treatment: darken bottom
    ov = Image.new('RGB', (pw_, ph_), CARD)
    g = np.linspace(0, 1, ph_)[:, None, None]
    arr = np.asarray(crop).astype(np.float32) * (1 - 0.35 * g ** 2.2)
    crop = Image.fromarray(arr.clip(0, 255).astype(np.uint8))
    rw = int(pw_ * eio(p))
    if rw > 0:
        img.paste(crop.crop((0, 0, rw, ph_)), (x0, y0))
        if p < 1: ImageDraw.Draw(img).rectangle((x0 + rw - 3, y0, x0 + rw, y1), fill=OXH)
    # overlay chips
    if p > 0.6:
        a = clamp((p - 0.6) / 0.4)
        d = ImageDraw.Draw(img)
        f = F('m7', 22); label = ('PHOTO  ' + s['photo_by']) if s.get('photo_by') else s['outlet']   # photo from another outlet: credit that one
        w = f.getlength(label) + 34 - (58 if s.get('photo_by') else 0)
        rrect(d, (x0 + 18, y0 + 18, x0 + 18 + w + 54, y0 + 62), 10, fill=mix(CARD, BG, 1) if a > .5 else CARD, outline=hx('2A4558'))
        text(d, (x0 + 18 + 17, y0 + 40), label, f, TXT, a, BG, anchor='lm', track=1)
        gx = x0 + 18 + w + 4
        if not s.get('photo_by'):
            rrect(d, (gx, y0 + 24, gx + 44, y0 + 56), 7, fill=mix(BG, GRN, 0.9))
            text(d, (gx + 22, y0 + 41), s['grade'], F('m7', 21), (2, 11, 18), 1, GRN, anchor='mm')
        if s.get('ai_image'):   # never pass a generated picture off as a news photo
            lab = 'AI ILLUSTRATION'; lw = F('m7', 20).getlength(lab) + 30
            rrect(d, (x1 - 18 - lw, y0 + 18, x1 - 18, y0 + 58), 10, fill=CARD, outline=OXH)
            text(d, (x1 - 18 - lw / 2, y0 + 38), lab, F('m7', 20), TXT, a, CARD, anchor='mm', track=1)

def draw_details(img, s, t, ts, i):
    d = ImageDraw.Draw(img)
    a = eo3((t - ts) / 0.3)
    if a <= 0.01: return
    hf = F('i6', 31)
    lines = wrap(s['headline'], hf, 900)[:2]
    for li, ln in enumerate(lines): text(d, (84, 912 + li * 40), ln, hf, TXT, a * 0.92, PANEL)
    # chips
    x = 84; y = 1012
    cf = F('m7', 23)
    for (label, trig), tc in zip(s['chips'], s['chip_t']):
        p = eo3((t - tc) / 0.22)
        if p <= 0.01: continue
        nump = clamp((t - tc) / 0.7)
        lab = count_label(label, eo3(nump)) if re.match(r'^[~]?\d', label) else label
        wfull = cf.getlength(label) + 36
        rrect(d, (x, y, x + wfull, y + 46), 10, fill=mix(PANEL, OX, 0.55 * p + 0.45 * p * (1 - clamp((t - tc - 0.3) / 0.5))), outline=mix(PANEL, OXH, p))
        text(d, (x + 18, y + 23), lab, cf, TXT, p, OX, anchor='lm')
        x += wfull + 14
    text(d, (84, 1080), s['note'], F('m4', 19), MUT, a, PANEL, track=1)
    text(d, (PX1 - 28, 1128), f"{s['topic']}  ·  {s['pub']}", F('m4', 18), SLT, a, PANEL, anchor='rm', track=1)

def draw_slot_cta(img, t):
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = PH
    d.rectangle((x0, y0, x1, y1), fill=CARD)
    for gx in range(x0, x1, 48): d.line((gx, y0, gx, y1), fill=hx('0C1A25'))
    for gy in range(y0, y1, 48): d.line((x0, gy, x1, gy), fill=hx('0C1A25'))
    draw_slab(d, x0 + 120, (y0 + y1) / 2, 150)
    text(d, (x0 + 230, (y0 + y1) / 2 - 62), 'FOLLOW FOR THE', F('i8', 62), TXT, 1, CARD, anchor='lm')
    text(d, (x0 + 230, (y0 + y1) / 2 + 12), 'NEXT BRIEFING', F('i8', 62), TXT, 1, CARD, anchor='lm')
    text(d, (x0 + 232, (y0 + y1) / 2 + 84), 'NEW STORIES THROUGH THE DAY', F('m7', 24), OXH, 1, CARD, anchor='lm', track=3)
    text(d, (84, 930), 'LIVE COVERAGE', F('m7', 20), SLT, 1, PANEL, track=4)
    text(d, (84, 990), 'Everything happening in the world.', F('i6', 34), TXT, 1, PANEL)
    text(d, (84, 1038), 'One screen.', F('i6', 34), MUT, 1, PANEL)
    text(d, (84, 1100), 'MONOLITH.NEWS', F('m7', 28), OXH, 1, PANEL, track=4)

RUND = [s['rundown'] for s in STORIES]
def draw_rundown(img, t, ci):
    d = ImageDraw.Draw(img)
    text(d, (84, 930), 'TONIGHT' if E['slot'] == 'EVENING' else 'TODAY', F('m7', 20), SLT, 1, PANEL, track=4)
    for k, r in enumerate(RUND):
        y = 984 + k * 36 + 6
        act = (k == ci); done = k < ci
        col = TXT if act else (MUT if not done else SLT)
        text(d, (84, y), f'0{k + 1}', F('m7', 22), OXH if act else SLT, 1, PANEL, anchor='lm')
        text(d, (140, y), r, F('i6', 27), col, 1 if act else 0.75, PANEL, anchor='lm')

# ------------------------------------------------------------------ frame
def frame_core(t, ci_force=None, slot=None):
    img = Image.new('RGB', (W, H), BG)
    d = ImageDraw.Draw(img)
    (lon0, lat0), R = view(t)
    globe(img, GCX, GCY, R, lon0, lat0)
    ci = cur_story(t) if ci_force is None else ci_force
    # header
    draw_slab(d, 84, 80, 52)
    text(d, (118, 80), 'MONOLITH', F('i8', 38), TXT, 1, BG, anchor='lm', track=4)
    text(d, (PX1, 80), f"{E['slot']} BRIEFING  ·  {E['date_short']}", F('m4', 21), MUT, 1, BG, anchor='rm', track=1)
    # progress
    sw_ = (PX1 - PX0 + 12) / NS
    for i in range(NS):
        x0 = PX0 + i * sw_; x1 = x0 + sw_ - 12
        d.rectangle((x0, 122, x1, 126), fill=LINE)
        if ci >= i or (i == 0 and 4.5 < t < TO):
            fe = ST[i]['end'] if i <= ci else 0
            pr = 1.0 if i < ci else clamp((t - ST[i]['fs']) / max(0.1, ST[i]['end'] - ST[i]['fs']))
            if i == 0 and ci < 0: pr = 0
            d.rectangle((x0, 122, x0 + (sw_ - 12) * pr, 126), fill=OXH)
    # pins
    ov = Image.new('RGBA', (W, H), (0, 0, 0, 0)); od = ImageDraw.Draw(ov)
    line_info = None
    for i in range(NS):
        td = ST[i]['td']
        if i != ci or t < td: continue
        s = STORIES[i]
        px, py, pz = gpoint(s['loc'][0], s['loc'][1], GCX, GCY, R, lon0, lat0)
        u = t - td
        r = 12 * back(u / 0.28)
        for k in range(3):
            q = ((u * 0.9 - k * 0.33) % 1.0) if u * 0.9 - k * 0.33 > 0 else None
            if q is None: continue
            rr = 14 + 120 * q
            od.ellipse((px - rr, py - rr, px + rr, py + rr), outline=OXH + (int(210 * (1 - q) ** 1.6),), width=3)
        od.ellipse((px - r - 10, py - r - 10, px + r + 10, py + r + 10), fill=OXH + (70,))
        od.ellipse((px - r, py - r, px + r, py + r), fill=OXH + (255,), outline=(255, 235, 235, 255), width=2)
        line_info = (i, px, py, u)
    img = Image.alpha_composite(img.convert('RGBA'), ov).convert('RGB')
    d = ImageDraw.Draw(img)
    # panel frame (always)
    draw_panel_frame(img, t, ci)
    # connector line
    if line_info:
        i, px, py, u = line_info
        lp = eo3((u - 0.12) / 0.5)
        tx = min(max(px, PX0 + 80), PX1 - 80)
        top = PY1 + 0
        ey = py + (top - py) * lp
        if lp > 0:
            pts = [(px, py - 12), (px, lerp(py - 12, top, 0.55 * lp + 0.0)), (lerp(px, tx, 1.0), ey)] if abs(px - tx) > 1 else [(px, py - 12), (px, ey)]
            od2 = ImageDraw.Draw(img)
            od2.line(pts, fill=OXH, width=4)
            # glow packet
            if lp < 1:
                od2.ellipse((pts[-1][0] - 8, pts[-1][1] - 8, pts[-1][0] + 8, pts[-1][1] + 8), fill=(255, 220, 225))
            else:
                od2.rectangle((tx - 9, PY1 - 5, tx + 9, PY1 + 5), fill=OXH)
        # place label
        la = eo3((u - 0.25) / 0.25)
        s = STORIES[i]
        f = F('m7', 22)
        lw = f.getlength(s['place']) + 30
        lx = px + 26 if px < 760 else px - 26 - lw
        ly = py + 18
        rrect(d, (lx, ly, lx + lw, ly + 40), 10, fill=mix(BG, CARD, la), outline=mix(BG, OXH, la))
        text(d, (lx + 15, ly + 20), s['place'], f, TXT, la, CARD, anchor='lm', track=1)
    # slot content
    x0, y0, x1, y1 = PH
    if slot == 'cta':
        draw_slot_cta(img, t)
    elif slot == 'intro' or ci < 0 or t < ST[0]['fs'] + 0.0:
        draw_slot_intro(img, t); draw_rundown(img, t, -1)
    else:
        i = ci; s = STORIES[i]; td = ST[i]['td']
        p = (t - (td + 0.45)) / 0.55
        if p <= 0:
            draw_slot_wait(img, t, s['place']); draw_rundown(img, t, ci)
        else:
            draw_slot_wait(img, t, s['place']) if p < 1 else None
            draw_photo(img, s, t, p, td + 0.45)
            draw_details(img, s, t, td + 0.9, i)
    draw_caption(img, t)
    # coords
    if ci >= 0:
        s = STORIES[ci]; la, lo = s['loc']
        d = ImageDraw.Draw(img)
        text(d, (PX0, 1868), f"{abs(la):.2f}°{'N' if la >= 0 else 'S'}  {abs(lo):.2f}°{'E' if lo >= 0 else 'W'}", F('m4', 20), SLT, 1, BG, track=2)
    # post
    arr = np.asarray(img).astype(np.float32) * VIG + NOISE[int(t * FPS) % 6]
    return arr.clip(0, 255).astype(np.uint8)

def frame(t):
    def bl(a, b, u):
        return (a.astype(np.float32) * (1 - u) + b.astype(np.float32) * u).clip(0, 255).astype(np.uint8)
    if t < TO: return frame_core(t)
    if t < TO + 0.45: return bl(frame_core(t, NS - 1), frame_core(t, -1, 'cta'), eio((t - TO) / 0.45))
    if t < TF: return frame_core(t, -1, 'cta')
    if t < TF + 0.45: return bl(frame_core(t, -1, 'cta'), frame_core(t, -1, 'intro'), eio((t - TF) / 0.45))
    return frame_core(t, -1, 'intro')

if __name__ == '__main__':
    # python briefing.py episode.json still T... | render A B out.mp4
    if sys.argv[2] == 'still':
        for t in map(float, sys.argv[3:]):
            Image.fromarray(frame(t)).save(os.path.join(os.path.dirname(EP_PATH), f'still_{t:06.2f}.png'))
    else:
        a, b, out = int(sys.argv[3]), int(sys.argv[4]), sys.argv[5]
        p = subprocess.Popen(['ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{W}x{H}', '-r', str(FPS), '-i', '-',
                              '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '16', '-pix_fmt', 'yuv420p', out], stdin=subprocess.PIPE)
        for n in range(a, b):
            p.stdin.write(frame(n / FPS).tobytes())
        p.stdin.close(); p.wait()
