# One briefing, end to end: feed -> 4 stories (free LLM + fact check) -> voices -> captions -> photos ->
# approved Evening Briefing render -> music + SFX mix -> Instagram-ready mp4.
# Usage: python pipeline/run.py OUTDIR [--script script.json] [--fixes fixes.json]
import os, sys, re, json, time, subprocess, datetime as dt
from concurrent.futures import ThreadPoolExecutor
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE); sys.path.insert(0, HERE)
out = os.path.abspath(sys.argv[1]); os.makedirs(out, exist_ok=True); T = time.time()
def log(*a): print(f'[{time.time() - T:6.1f}s]', *a, flush=True)
import feed
cands = feed.candidates(max_age_h=12, n=20)
if len(cands) < 8: cands = feed.candidates(max_age_h=24, n=20)       # slow news hours: look back a whole day

LOCAL = re.compile(r'\b(bus|truck|lorry|car|van|road|motorway|highway|train)\b.{0,40}\b(crash\w*|collid\w*|collision|accident|overturn\w*)\b|'
                   r'\b(crash\w*|collision|accident)\b.{0,40}\b(bus|truck|car|road|motorway|highway)\b|\broof\b.{0,20}\bcollaps|'
                   r'\bshot dead\b|\bgunmen\b|\bstabb\w*|\bbears?\b|\b(grizzly|shark|dog) attack|\bmurder\w*|\bhouse fire\b|\bdrown\w*', re.I)
def big_toll(t): return any(int(n) >= 20 for n in re.findall(r'\b(\d{2,4})\s+(?:people\s+)?(?:killed|dead|die|died)|\bkill\w*\s+(?:at least\s+)?(\d{2,4})', t or '') for n in n if n)
keep_c = [a for a in cands if not LOCAL.search(a['title']) or big_toll(a['title'])]
if len(keep_c) >= 6: cands = keep_c                                  # local accidents and local crime are not world news

# ---- stories covered in the last 24h (pipeline/history.json, committed after every episode): don't repeat them
HIST = os.path.join(HERE, 'history.json')
hist = json.load(open(HIST)) if os.path.exists(HIST) else []
cut = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=24)
recent = [h for h in hist if dt.datetime.fromisoformat(h['t']) > cut]
kw = lambda t: {w for w in re.findall(r'[a-z]{4,}', (t or '').lower())} - {'says', 'said', 'after', 'over', 'with', 'from', 'that', 'this', 'more', 'have', 'will', 'into', 'amid'}
def same_story(a):
    k = kw(a['title'])
    return any(len(k & kw(h['title'])) / max(1, min(len(k), len(kw(h['title'])))) >= 0.4 for h in recent)
fresh = [a for a in cands if not same_story(a)]
if len(fresh) < 6:                                                    # not enough new stories in 12h: look back a whole day
    more = [a for a in feed.candidates(max_age_h=24, n=30) if not same_story(a) and a['id'] not in {b['id'] for b in fresh}]
    fresh += [a for a in more if not LOCAL.search(a['title']) or big_toll(a['title'])]
if len(fresh) >= 6: cands = fresh                                     # only if enough is left to choose from
else: cands = fresh + [a for a in cands if a not in fresh][:8 - len(fresh)]   # very slow news: covered stories may come back with their newest update

if '--script' in sys.argv:
    S = json.load(open(sys.argv[sys.argv.index('--script') + 1]))
    have = {a['id'] for a in cands}; cands += [a for a in S.get('_articles', []) if a['id'] not in have]
else:
    import writer
    S = writer.write(cands, covered=[h['title'] + (' (' + h['place'] + ')' if h.get('place') else '') for h in recent])
byid = {a['id']: a for a in cands}
FX = json.load(open(sys.argv[sys.argv.index('--fixes') + 1])) if '--fixes' in sys.argv else {}
S['_articles'] = [byid[s['article_id']] for s in S['stories']]   # keep the article texts with the script (re-runs, fixes)
json.dump(S, open(os.path.join(out, 'script.json'), 'w'), ensure_ascii=False, indent=1); log('script ok')
if '--script' not in sys.argv:   # remember what this episode covers (kept 48h)
    keep = [h for h in hist if dt.datetime.fromisoformat(h['t']) > cut - dt.timedelta(hours=24)]
    keep += [{'t': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'), 'title': byid[x['article_id']]['title'], 'place': x.get('place', '')} for x in S['stories']]
    json.dump(keep, open(HIST, 'w'), ensure_ascii=False, indent=1)
json.dump([{k: byid[s['article_id']].get(k) for k in ('id', 'source', 'title', 'body', 'url')} for s in S['stories']], open(os.path.join(out, 'articles.json'), 'w'), ensure_ascii=False)

# ---- date / slot (Istanbul time) and the spoken intro
now = dt.datetime.now(dt.timezone(dt.timedelta(hours=3)))
slot = 'MORNING' if now.hour < 11 else 'MIDDAY' if now.hour < 17 else 'EVENING'
ORD = ['first', 'second', 'third', 'fourth', 'fifth', 'sixth', 'seventh', 'eighth', 'ninth', 'tenth', 'eleventh', 'twelfth', 'thirteenth',
       'fourteenth', 'fifteenth', 'sixteenth', 'seventeenth', 'eighteenth', 'nineteenth', 'twentieth', 'twenty-first', 'twenty-second',
       'twenty-third', 'twenty-fourth', 'twenty-fifth', 'twenty-sixth', 'twenty-seventh', 'twenty-eighth', 'twenty-ninth', 'thirtieth', 'thirty-first']
intro = f"This is the Monolith {slot.lower()} briefing, {now:%A}, {now:%B} {ORD[now.day - 1]}."
lines = [('M', intro, 'intro')]
for i, st in enumerate(S['stories']): lines += [('M', st['question'], f'q{i}'), ('F', st['answer'], f'a{i}')]
json.dump([{'speaker': 'male host' if w == 'M' else 'female anchor', 'text': t} for w, t, _ in lines], open(os.path.join(out, 'lines.json'), 'w'), ensure_ascii=False)

# ---- voices + timings
import voices, timing
import hashlib
hp = os.path.join(out, 'line_hash.json'); old = json.load(open(hp)) if os.path.exists(hp) else {}
voices.LEARNED.update(FX.get('say', {}))
key = lambda i: hashlib.md5((lines[i][0] + voices.learned(voices.clean(lines[i][1]))).encode()).hexdigest()   # changes only if what's spoken changes
todo = [i for i in range(len(lines)) if old.get(str(i)) != key(i) or not os.path.exists(os.path.join(out, f'line_{i:02d}.wav'))]
redo = [i for i in FX.get('revoice', []) if i not in todo]
if todo: voices.synth(lines, out, only=todo)
if redo: voices.synth(lines, out, only=redo, seed=100 * FX.get('round', 1))     # fresh take for lines the AI check flagged
files = [os.path.join(out, f'line_{i:02d}.wav') for i in range(len(lines))]
json.dump({str(i): key(i) for i in range(len(lines))}, open(hp, 'w'))
log('voices ok', f'({len(todo) + len(redo)} recorded)')
W, bad = timing.words(files, lines)
for retry in range(2):
    if not bad: break
    idx = [files.index(f) for f in bad]; log('re-voicing lines', idx)
    voices.synth(lines, out, only=idx, seed=100 * (retry + 1)); W, bad = timing.words(files, lines)
if bad: raise SystemExit('voice check failed on: ' + ', '.join(bad))

def ask_pron(name, heard, engine):   # the AI writes how to say a name: IPA for Kokoro, a plain respelling for Pocket TTS
    import writer
    ex = {k: v for k, v in list(voices.PRON.items())[:4]}
    if engine == 'kokoro':
        q = (f'Give the standard American English news-anchor pronunciation of the name "{name}" in IPA, in the same style as these '
             f'(stress mark before the stressed syllable, no slashes): {json.dumps(ex, ensure_ascii=False)}. Return JSON {{"ipa": "..."}}.')
    else:
        q = (f'A text-to-speech voice mispronounces the name "{name}". Write it as plain English respelling that a TTS will read correctly, '
             f'syllables joined by hyphens, stressed syllable in capitals (e.g. Medina -> meh-DEE-nah). Return JSON {{"say": "..."}}.')
    v, _ = writer.chat([{'role': 'user', 'content': q}])
    return str(v.get('ipa') or v.get('say') or '').strip().strip('/')

def said_right(name, heard):   # speech-to-text often spells a correctly said name as a common word: Kashmir -> "cashmere"
    import writer
    try:
        v, _ = writer.chat([{'role': 'user', 'content':
            f'A text-to-speech voice read the name "{name}" in an English news sentence. A speech-to-text model wrote what it heard as '
            f'"{heard}". Is that consistent with the name being pronounced correctly (its usual English news pronunciation) - for example '
            f'a homophone or a normal spelling of the same sounds? A missing or extra syllable, wrong vowels or different sounds mean false. '
            f'Return JSON {{"correct": true or false, "why": "few words"}}.'}])
        log('  sounds check', f'"{name}" vs "{heard}": {v.get("correct")} ({v.get("why", "")})'); return v.get('correct') is True
    except Exception: return False

name_flags = []; learned_now = []
MANUAL = set(json.load(open(os.path.join(HERE, 'pronunciations.json')))) | set(voices.LEARNED)   # as they were before this episode
for i, (who, txt, _) in enumerate(lines):
    wrong = timing.name_check(txt, W[i]['heard'], files[i])
    if not wrong: continue
    eng = voices.CFG[who]['engine']
    for name, heard in wrong:
        log('name said wrong?', f'line {i}: "{name}" heard as "{heard}"')
        if said_right(name, heard): continue                # just spelled differently: nothing to fix
        fixed = False
        for attempt in range(2):                      # 1: the AI's pronunciation, 2: ask again
            try: how = ask_pron(name, heard, eng)
            except Exception as e: log('  could not ask the AI', e); break
            if not how: continue
            keep_p, keep_s = dict(voices.PRON), dict(voices.LEARNED)
            if eng == 'kokoro': voices.PRON[name] = how
            else: voices.LEARNED[name] = how
            voices.synth(lines, out, only=[i]); w1, _ = timing.words([files[i]], [lines[i]])
            still = dict(timing.name_check(txt, w1[0]['heard'], files[i]))
            if name not in still or said_right(name, still[name]):
                W[i] = w1[0]; fixed = True; log('  fixed with', how)
                if name in MANUAL: pass                                    # a pronunciation set by hand is never overwritten
                elif eng == 'kokoro': voices.save_pron(name, how); learned_now.append(f'{name} ({"female" if eng == "kokoro" else "male"} voice)')
                else: voices.save_say(name, how); learned_now.append(f'{name} → "{how}" (male voice)')
                break
            voices.PRON.clear(); voices.PRON.update(keep_p); voices.LEARNED.clear(); voices.LEARNED.update(keep_s)
        if not fixed:
            voices.synth(lines, out, only=[i]); W[i] = timing.words([files[i]], [lines[i]])[0][0]
            name_flags.append(f'"{name}" may be mispronounced (heard as "{heard}")')
json.dump(name_flags, open(os.path.join(out, 'name_flags.json'), 'w'))
old_l = json.load(open(os.path.join(out, 'learned_names.json'))) if os.path.exists(os.path.join(out, 'learned_names.json')) else []
json.dump(old_l + learned_now, open(os.path.join(out, 'learned_names.json'), 'w'), ensure_ascii=False)
json.dump({str(i): key(i) for i in range(len(lines))}, open(hp, 'w'))
log('timings ok', f'({len(name_flags)} name(s) still flagged)' if name_flags else '')

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
stories, keys, photo_src = [], [], {}
photos.KNOWN.update({b['url']: b['image'] for b in cands + S.get('_articles', []) if b.get('image')})   # the feed's own cover photos come first
for i, st in enumerate(S['stories']):
    a = byid[st['article_id']]; rel = related(a)
    dst = os.path.join(out, f'photo_{i}.jpg'); ai = False; skip = set(FX.get('skip_urls', {}).get(str(i), []))
    prev = json.load(open(os.path.join(out, 'photo_src.json'))).get(str(i)) if os.path.exists(os.path.join(out, 'photo_src.json')) else None
    sensitive = bool(re.search(r'crime|law', a.get('topic') or '', re.I) or re.search(
        r'\b(murder\w*|manslaughter|killing of|court|convicted|sentenced|jury|police|arrested|stabbing|rape|assault|accused|shooting|shot|gunm[ae]n|massacre|terror\w*|victims?|died|dies|dead|killed|attack\w*)\b', a['title'], re.I))
    if sensitive: skip |= {u for u in [a['url']] + [b['url'] for b in cands]}   # crime/court stories: never a real person's photo
    if prev and prev['src'] not in skip and os.path.exists(dst): img, src, ai = dst, prev['src'], prev['ai']   # keep a photo that passed
    else:
        grim = sensitive or bool(re.search(r'\b(shooting|shot|gunm[ae]n|massacre|terror\w*|victims?|died|dies|dead|killed|attack\w*)\b', a['title'], re.I))
        ok = lambda p, a=a, grim=grim: photos.screen(p, a['title'], grim)   # in death/attack stories a private person's photo is refused                          # an AI looks at each photo first
        img, src = photos.fetch([a['url']] + [b['url'] for b in cands if b['source'] in rel], dst, skip, ok)    # 1. the outlet, 2. feed outlets on the same story
        if not img and not sensitive: img, src = photos.fetch(photos.search_other_outlets(a['title']), dst, skip, ok)  # 3. any other outlet's cover photo
        if not img and 'AI' not in skip:                                                                        # 4. AI illustration (labelled)
            img = photos.illustrate(st.get('image_prompt') or a['title'], dst); src = 'AI' if img else None; ai = bool(img)
    photo_src[str(i)] = {'src': src, 'ai': ai}
    topic = re.sub(r'[^A-Za-z ]', '', st.get('topic') or a.get('topic') or 'WORLD').strip().upper() or 'WORLD'   # the writer's label; the feed's can be wrong
    dom = lambda u: re.sub(r'^www\.', '', (u or '').split('/')[2]) if '//' in (u or '') else ''
    photo_by = dom(src).upper()[:24] if img and not ai and src and dom(src) != dom(a['url']) else ''
    stories.append({'img': img, 'outlet': a['source'].upper()[:22], 'grade': a['trust'], 'topic': topic,
                    'pub': dt.datetime.fromisoformat(a['pubDate'].replace('Z', '+00:00')).strftime('%H:%M UTC'),
                    'headline': re.sub(r'^[A-Z][A-Za-z]*\+:\s*', '', a['title']),   # drop paywall tags like 'STAT+:'
                    'note': ('ALSO REPORTED BY  ' + '  ·  '.join(r.upper() for r in rel[:2])) if rel else f"SOURCE  {a['source'].upper()}",
                    'loc': [float(st['lat']), float(st['lon'])], 'place': st['place'].upper(), 'chips': st['chips'][:3], 'rundown': st['rundown'].upper(),
                    'url': a['url'], 'ai_image': ai, 'photo_by': photo_by})
    keys += st.get('keys', []) + [re.sub(r'[^0-9]', '', c[0]) for c in st['chips'] if re.search(r'\d', c[0])]
json.dump(photo_src, open(os.path.join(out, 'photo_src.json'), 'w'))
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
bed *= (10 ** ((-30 + FX.get('music_db', 0)) / 20)) / (np.sqrt((bed ** 2).mean()) + 1e-9); bed *= (1 - 0.55 * env_)
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
caption = S['caption'].strip() + '\n\n' + ' '.join('#' + h.lstrip('#') for h in S['hashtags'][:5]) + '\n\nAI voices · VCTK corpus'   # credit the voice licence requires
srcs = '\n'.join(f"• {s['outlet']}: {s['headline']}" for s in stories)
json.dump({'caption': caption, 'sources': srcs, 'seconds': round(B.TOTAL, 1)}, open(os.path.join(out, 'meta.json'), 'w'), ensure_ascii=False)
log('done', mp4)
