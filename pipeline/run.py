# One briefing, end to end: feed -> 4 stories (free LLM + fact check) -> voices -> captions -> photos ->
# approved Evening Briefing render -> music + SFX mix -> Instagram-ready mp4.
# Usage: python pipeline/run.py OUTDIR [--script script.json]
import os, sys, re, json, time, subprocess, datetime as dt
from concurrent.futures import ThreadPoolExecutor
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE); sys.path.insert(0, HERE)
out = os.path.abspath(sys.argv[1]); os.makedirs(out, exist_ok=True); T = time.time()
def log(*a): print(f'[{time.time() - T:6.1f}s]', *a, flush=True)
import feed
cands = feed.candidates(max_age_h=12, n=20)
if '--script' in sys.argv:
    S = json.load(open(sys.argv[sys.argv.index('--script') + 1]))
    have = {a['id'] for a in cands}; cands += [a for a in S.get('_articles', []) if a['id'] not in have]
else:
    import writer
    S = writer.write(cands)
byid = {a['id']: a for a in cands}
json.dump(S, open(os.path.join(out, 'script.json'), 'w'), ensure_ascii=False, indent=1); log('script ok')

# ---- date / slot (Istanbul time) and the spoken intro
now = dt.datetime.now(dt.timezone(dt.timedelta(hours=3)))
slot = 'MORNING' if now.hour < 11 else 'MIDDAY' if now.hour < 17 else 'EVENING'
ORD = ['first', 'second', 'third', 'fourth', 'fifth', 'sixth', 'seventh', 'eighth', 'ninth', 'tenth', 'eleventh', 'twelfth', 'thirteenth',
       'fourteenth', 'fifteenth', 'sixteenth', 'seventeenth', 'eighteenth', 'nineteenth', 'twentieth', 'twenty-first', 'twenty-second',
       'twenty-third', 'twenty-fourth', 'twenty-fifth', 'twenty-sixth', 'twenty-seventh', 'twenty-eighth', 'twenty-ninth', 'thirtieth', 'thirty-first']
intro = f"This is the Monolith {slot.lower()} briefing, {now:%A}, {now:%B} {ORD[now.day - 1]}."
lines = [('M', intro, 'intro')]
for i, st in enumerate(S['stories']): lines += [('M', st['question'], f'q{i}'), ('F', st['answer'], f'a{i}')]

# ---- voices + timings
import voices, timing
if '--reuse' in sys.argv and all(os.path.exists(os.path.join(out, f'line_{i:02d}.wav')) for i in range(len(lines))):
    files = [os.path.join(out, f'line_{i:02d}.wav') for i in range(len(lines))]
else: files = voices.synth(lines, out)
log('voices ok')
W, bad = timing.words(files, lines)
for retry in range(2):
    if not bad: break
    idx = [files.index(f) for f in bad]; log('re-voicing lines', idx)
    voices.synth(lines, out, only=idx, seed=100 * (retry + 1)); W, bad = timing.words(files, lines)
if bad: raise SystemExit('voice check failed on: ' + ', '.join(bad))
log('timings ok')

# ---- place lines on the timeline (gaps as in the approved episode)
words, t, place = [], 0.3, []
for k, ((who, txt, tag), w) in enumerate(zip(lines, W)):
    if k: t += 0.35 if tag.startswith('q') and k == 1 else 0.3 if tag.startswith('a') else 0.45
    place.append((t, w['file'], who))
    story = int(tag[1:]) if tag.startswith('a') else -1
    for x in w['words']:
        e = {'w': x[0], 't0': round(t + x[1], 3), 't1': round(t + x[2], 3), 'spk': 'male' if who == 'M' else 'female', 'story': story, 'seg': f'L{k}'}
        if tag.startswith('q'): e['q'] = int(tag[1:])
        words.append(e)
    t += w['dur']

# ---- per-story card data (source, grade, time, topic come from the feed, never from the AI)
import photos
def related(a):
    sig = lambda s: {w for w in re.findall(r'[a-z]{4,}', s.lower())} - {'with', 'after', 'from', 'that', 'this', 'over', 'says', 'will'}
    me = sig(a['title']); outl = []
    for b in cands:
        if b['id'] != a['id'] and b['source'] != a['source'] and len(me & sig(b['title'])) >= 3 and b['source'] not in outl: outl.append(b['source'])
    return outl
stories, keys = [], []
for i, st in enumerate(S['stories']):
    a = byid[st['article_id']]; rel = related(a)
    dst = os.path.join(out, f'photo_{i}.jpg'); ai = False
    img = photos.fetch([a['url']] + [b['url'] for b in cands if b['source'] in rel], dst)          # 1. the outlet, 2. feed outlets on the same story
    if not img: img = photos.fetch(photos.search_other_outlets(a['title']), dst)                     # 3. any other outlet's cover photo
    if not img: img = photos.illustrate(st.get('image_prompt') or a['title'], dst); ai = bool(img)  # 4. AI illustration (labelled)
    topic = re.sub(r'[^A-Za-z ]', '', a.get('topic') or 'WORLD').strip().upper() or 'WORLD'
    stories.append({'img': img, 'outlet': a['source'].upper()[:22], 'grade': a['trust'], 'topic': topic,
                    'pub': dt.datetime.fromisoformat(a['pubDate'].replace('Z', '+00:00')).strftime('%H:%M UTC'),
                    'headline': a['title'], 'note': ('ALSO REPORTED BY  ' + '  ·  '.join(r.upper() for r in rel[:2])) if rel else f"SOURCE  {a['source'].upper()}",
                    'loc': [float(st['lat']), float(st['lon'])], 'place': st['place'].upper(), 'chips': st['chips'][:3], 'rundown': st['rundown'].upper(),
                    'url': a['url'], 'ai_image': ai})
    keys += st.get('keys', []) + [re.sub(r'[^0-9]', '', c[0]) for c in st['chips'] if re.search(r'\d', c[0])]
log('photos', sum(1 for s in stories if s['img']), 'of', len(stories))
E = {'slot': slot, 'date_long': now.strftime('%a %-d %b %Y').upper(), 'date_short': now.strftime('%-d %b %Y').upper(),
     'stories': stories, 'keys': [k for k in keys if k], 'words': words}
ep = os.path.join(out, 'episode.json'); json.dump(E, open(ep, 'w'), ensure_ascii=False)

# ---- render (parallel chunks of the approved renderer)
env = dict(os.environ, EPISODE_JSON=ep); sys.argv = [sys.argv[0], ep]
import briefing as B
n = round(B.TOTAL * B.FPS); jobs = os.cpu_count() or 2; cuts = [round(n * k / jobs) for k in range(jobs + 1)]
def chunk(k): subprocess.run([sys.executable, os.path.join(HERE, 'briefing.py'), ep, 'render', str(cuts[k]), str(cuts[k + 1]), os.path.join(out, f'v{k}.mp4')], env=env, check=True)
with ThreadPoolExecutor(jobs) as ex: list(ex.map(chunk, range(jobs)))
open(os.path.join(out, 'v.txt'), 'w').writelines(f"file 'v{k}.mp4'\n" for k in range(jobs)); log('video ok', round(B.TOTAL, 1), 's')

# ---- audio: voices on the timeline + approved music bed + kit SFX cues (ducked under the voice)
import numpy as np, soundfile as sf, scipy.signal as sg
SR = 48000; N = int(round(B.TOTAL * SR))
def load(p):
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', p, '-f', 's16le', '-ac', '1', '-ar', str(SR), '-'], capture_output=True).stdout
    return np.frombuffer(raw, np.int16).astype(np.float32) / 32768
voice = np.zeros(N, np.float32)
for t0, f, who in place:
    a = load(f); a *= 0.11 / (np.sqrt((a[np.abs(a) > 0.01] ** 2).mean()) + 1e-9); i = int(t0 * SR); voice[i:i + len(a)] += a[:N - i]
w = int(0.06 * SR); env_ = np.sqrt(np.convolve(voice ** 2, np.ones(w) / w, 'same')); env_ = np.clip(np.convolve(env_, np.ones(int(0.25 * SR)) / int(0.25 * SR), 'same') / 0.07, 0, 1)
m = load(os.path.join(ROOT, 'assets', 'music.mp3')); off = int(8 * SR); bed = np.resize(m[off:], N).copy()
fi = int(0.5 * SR); bed[:fi] *= np.linspace(0, 1, fi); bed[-fi:] *= np.linspace(1, 0, fi)
bed *= (10 ** (-30 / 20)) / (np.sqrt((bed ** 2).mean()) + 1e-9); bed *= (1 - 0.55 * env_)
sfx = np.zeros(N, np.float32)
def put(name, t, g=1.0):
    x = load(os.path.join(ROOT, 'assets', 'sfx', name + '.mp3')); i = int(t * SR)
    if 0 <= i < N: x = x[:N - i]; sfx[i:i + len(x)] += x * g
put('confirm_chime_1', 0.25, 0.5); put('whoosh_soft_1', B.TF, 0.9); put('ui_blip_1', B.TO + 0.1, 0.8); put('riser_1', B.TOTAL - 1.5, 0.45)
for i, s in enumerate(B.STORIES):
    st = B.ST[i]; put('whoosh_soft_1', st['fs'], 0.9); put('ui_blip_1', st['td'], 0.9); put('pen_line_1', st['td'] + 0.12, 0.8); put('paper_slide_1', st['td'] + 0.45, 0.8)
    for tc in s['chip_t']: put('ui_tick_1', tc, 0.8)
put('impact_small_1', B.STORIES[0]['chip_t'][0], 0.8)
sfx *= 0.55 * (1 - 0.5 * env_)
mix = voice + bed + sfx; mix = mix / (np.abs(mix).max() + 1e-9) * 0.89
sf.write(os.path.join(out, 'mix.wav'), mix, SR); log('audio ok')
mp4 = os.path.join(out, 'episode.mp4')   # Instagram Reels spec: H.264 high, 30fps, AAC 48k, moov at front
subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', os.path.join(out, 'v.txt'), '-i', os.path.join(out, 'mix.wav'),
                '-map', '0:v', '-map', '1:a', '-c:v', 'libx264', '-preset', 'medium', '-crf', '20', '-pix_fmt', 'yuv420p', '-profile:v', 'high',
                '-r', '30', '-g', '60', '-c:a', 'aac', '-b:a', '128k', '-ar', '48000', '-shortest', '-movflags', '+faststart', mp4], check=True)
subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', mp4, '-vf', 'scale=720:1280', '-c:v', 'libx264', '-preset', 'medium', '-crf', '26',
                '-c:a', 'aac', '-b:a', '96k', '-movflags', '+faststart', os.path.join(out, 'preview.mp4')], check=True)   # Telegram bots can send max 50 MB
caption = S['caption'].strip() + '\n\n' + ' '.join('#' + h.lstrip('#') for h in S['hashtags'][:5])
srcs = '\n'.join(f"• {s['outlet']}: {s['headline']}" for s in stories)
json.dump({'caption': caption, 'sources': srcs, 'seconds': round(B.TOTAL, 1)}, open(os.path.join(out, 'meta.json'), 'w'), ensure_ascii=False)
log('done', mp4)
