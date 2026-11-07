# Script writing + fact check with free LLM APIs (OpenAI-compatible endpoints). Tries providers in order.
import os, re, json, datetime as dt, urllib.request
PROVIDERS = [  # (env key, base url, model env, default model)
    ('GEMINI_API_KEY', 'https://generativelanguage.googleapis.com/v1beta/openai', 'GEMINI_MODEL', 'gemini-3.8-flash'),
    ('GROQ_API_KEY', 'https://api.groq.com/openai/v1', 'GROQ_MODEL', 'openai/gpt-oss-120b'),
    ('CF_AI_TOKEN', f"https://api.cloudflare.com/client/v4/accounts/{os.environ.get('CF_ACCOUNT_ID', '')}/ai/v1", 'CF_MODEL', '@cf/openai/gpt-oss-120b'),   # same free Cloudflare account as the illustrations
    ('CEREBRAS_API_KEY', 'https://api.cerebras.ai/v1', 'CEREBRAS_MODEL', 'llama-3.3-70b'),
    ('OPENROUTER_API_KEY', 'https://openrouter.ai/api/v1', 'OPENROUTER_MODEL', 'nvidia/nemotron-3-ultra-550b-a55b:free'),
]
BANNED = re.compile(r'\b(startup|launch(?:ed|ing)?|free trial|pro plan|monolith\.news|link in bio|subscribe|\$\d)', re.I)
N_STORIES = 4

UA = 'monolith-daily/1.0 (+https://github.com/5a7a/monolith-daily)'   # some APIs block the default Python user agent
FALLBACK = {'GEMINI_API_KEY': ['gemini-3.5-flash-lite'], 'OPENROUTER_API_KEY': ['google/gemma-4-31b-it:free'], 'CF_AI_TOKEN': ['@cf/meta/llama-3.3-70b-instruct-fp8-fast']}                    # tried when the main model is overloaded
PREFER = ['gpt-oss-120b', 'kimi-k2', 'nemotron-3-ultra', 'llama-4-maverick', 'qwen3-235b', 'gemma-4-31b', 'llama-3.3-70b', 'qwen3-32b', 'gpt-oss-20b', 'llama']   # best first

def _available(base, key):
    try:
        r = json.load(urllib.request.urlopen(urllib.request.Request(base + '/models', headers={'Authorization': 'Bearer ' + key, 'User-Agent': UA}), timeout=30))
        ids = [m['id'] for m in r.get('data', []) if not re.search(r'whisper|tts|guard|embed|vision|audio|image', m['id'], re.I)]
        if 'openrouter' in base: ids = [i for i in ids if i.endswith(':free')]   # never a paid model
        for p in PREFER:
            for i in ids:
                if p in i.lower(): return i
        return ids[0] if ids else None
    except Exception: return None

def _gemini_extras(base, key, tried):   # other Gemini models: each has its own capacity when one is overloaded
    try:
        r = json.load(urllib.request.urlopen(urllib.request.Request(base + '/models', headers={'Authorization': 'Bearer ' + key, 'User-Agent': UA}), timeout=30))
        ids = [m['id'].split('/')[-1] for m in r.get('data', [])]
        ids = [i for i in ids if re.search(r'gemini.*(flash|pro)', i) and not re.search(r'tts|image|embed|live|audio|preview-tts|robotics', i) and i not in tried]
        return sorted(ids, key=lambda i: [float(x) for x in re.findall(r'\d+(?:\.\d+)?', i)[:1]] or [0], reverse=True)[:4]
    except Exception: return []

def _call(base, key, model, messages, strict=True):
    q = {'model': model, 'messages': messages, 'temperature': 0.5, 'max_tokens': 4096}
    if 'gpt-oss' in model: q['reasoning_effort'] = 'low'   # less hidden thinking: fewer tokens, no empty replies
    if strict: q['response_format'] = {'type': 'json_object'}
    body = json.dumps(q).encode()
    req = urllib.request.Request(base + '/chat/completions', body, {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json', 'User-Agent': UA})
    r = json.load(urllib.request.urlopen(req, timeout=100)); txt = r['choices'][0]['message'].get('content') or ''
    if '{' not in txt: raise ValueError('empty reply')
    return json.JSONDecoder().raw_decode(txt[txt.find('{'):])[0]   # the first JSON object, ignoring anything after it

DOWN = set()   # models that hung or were overloaded earlier in this run: skipped on the first pass of later calls
def chat(messages, skip=None, passes=2):
    import time, urllib.error
    err = []
    for p in range(passes):                       # every provider busy at once: wait a minute, go round again
        if p: time.sleep(60)
        for key, base, menv, mdef in PROVIDERS:
            if not os.environ.get(key) or key == skip: continue
            models = [os.environ.get(menv, mdef)] + FALLBACK.get(key, []); tried = set(); extras = key != 'GEMINI_API_KEY'
            while models or not extras:
                if not models: extras = True; models = _gemini_extras(base, os.environ[key], tried); continue
                model = models.pop(0)
                if model in tried or not model: continue
                if p == 0 and model in DOWN: continue
                tried.add(model); strict = True
                for attempt in range(3):
                    t0 = time.time()
                    try:
                        res = _call(base, os.environ[key], model, messages, strict)
                        print(f'    ai: {model} answered in {time.time() - t0:.0f}s', flush=True); return res, key
                    except urllib.error.HTTPError as e:
                        detail = e.read().decode('utf-8', 'ignore')[:200].replace('\n', ' ')
                        err.append(f'{key} {model}: HTTP {e.code} {detail}'); print(f'    ai: {model} HTTP {e.code} after {time.time() - t0:.0f}s', flush=True)
                        if e.code == 429:                                   # limit hit: a short per-minute wait is worth it, a used-up quota is not
                            m = re.search(r'try again in ([\d.]+)s', detail)
                            if m and float(m.group(1)) <= 20: time.sleep(float(m.group(1)) + 2); continue
                            if p == 0: break
                            time.sleep(30); continue
                        if e.code in (500, 502, 503, 504):                 # busy: first time round move straight on to the next AI
                            if p == 0: DOWN.add(model); break
                            time.sleep(15 * (attempt + 1)); continue
                        if e.code == 400 and strict: strict = False; continue          # its JSON mode choked or isn't supported: ask plainly
                        if e.code in (400, 404) and 'model' in detail.lower() and not models:              # model retired: ask what exists
                            alt = _available(base, os.environ[key]); models += [alt] if alt and alt not in tried else []
                        break
                    except Exception as e:
                        err.append(f'{key} {model}: {e}'); print(f'    ai: {model} failed after {time.time() - t0:.0f}s: {str(e)[:80]}', flush=True)
                        if p == 0 and time.time() - t0 > 60: DOWN.add(model); break   # hung / too slow: move on, and skip it for the rest of this run
                        strict = False; time.sleep(5)
    raise RuntimeError('no LLM provider worked: ' + ' | '.join(err[-6:]))

SYSTEM = """You write the "Monolith Briefing", a ~55 second vertical news video. A male host asks one short question
per story, a female anchor answers with the facts. The intro line and the outro are added automatically - don't write them.
RULES
- Pick exactly 4 stories from the candidates: the most important for an international audience of professionals,
  varied topics and regions (at most 2 stories about the same country or the same conflict; cover at least 3 world regions when the candidates allow), each tied to a real place. Prefer world events, geopolitics and conflict, business and markets,
  economy, tech and AI, science, climate. AVOID: local crime and court cases about private people, minor local or
  state-level elections, celebrity gossip, sports results, opinion pieces, local accidents with few casualties (a collapsed roof,
  a road crash), stories about one little-known person unless it matters internationally. A story that matters to people in
  many countries beats a local incident even if the local one is newer.
- question: male host, 2-7 words, natural and varied, e.g. "What happened in Spain?", "And in Belgrade?", "What about Yemen?".
- answer: female anchor, 2 short sentences, each clear on its own (no vague filler like 'The fire burned.'), 18-26 words (hard limit), ONLY facts stated in that article's text. No outside knowledge,
  no guesses, no added numbers. Do NOT name any news outlet (the source credit is added automatically).
- Write every number as spoken words ("thirty-one", "six point three billion dollars"). No digits anywhere in question/answer.
- Calm, serious tone for deaths and war. Never: website names, "link in bio", prices, "startup", "launch", "free trial".
- topic: one of WORLD, CONFLICT, POLITICS, DIPLOMACY, BUSINESS, ECONOMY, TECH, SCIENCE, CLIMATE, HEALTH, DEFENCE, ENERGY.
- chips: 2-3 on-screen fact labels per story, written with digits (e.g. 42 KILLED); a number in a chip must be the same number the answer says, CAPS, max 18 characters, e.g. ["31 KILLED","thirty-one"], ["TALKS RESUME","talks"].
  Second item = the word in the answer where the chip pops. Chips with numbers start with the digits.
- place: WHERE THE EVENT HAPPENS according to the article ("CITY, COUNTRY" or "REGION, COUNTRY", caps, max 22 chars), never
  the outlet's home country. A WHO decision -> "GENEVA, SWITZERLAND"; an outbreak in Congo -> "DR CONGO". lat/lon = that place.
- question: asks about the event's own place or subject ("What about the Ebola outbreak?"), never the outlet's country.
- rundown: "COUNTRY  ·  2-4 WORD SUMMARY" in caps, e.g. "UKRAINE  ·  STRIKE ON AID HUB".
- keys: 1-3 words from the answer to highlight (numbers and the key noun).
- caption_line: one line for the Instagram caption about THIS story, 50-90 characters, written for search: the key names, place and the
  fact (digits allowed here, e.g. "Russian strike on Komyshuvakha: death toll rises to 53"). Only facts from the article.
- hook (top level): the caption's opening line, 40-80 characters, about the most important story of this episode, specific, no clickbait.
- viewer_question (top level): one neutral, open-ended question about this episode's news that invites real discussion, under 100
  characters (e.g. "How should airports respond to a third wave of threats in a week?"). Never ask people to comment a word or emoji,
  to tag, like, share or follow, and never take a side on a political question.\n- image_prompt: one sentence describing a simple symbolic scene for the story (objects and setting only, no real people, no text).
Return JSON:
{"stories":[{"article_id":"<id>","question":"...","answer":"...","place":"...","lat":0.0,"lon":0.0,
  "topic":"...","chips":[["LABEL","word"]],"rundown":"...","keys":["..."],"caption_line":"...","image_prompt":"..."}, ... 4 items],
 "hook":"...","viewer_question":"...?",
 "hashtags":["5 specific hashtags, no #reels/#explore"]}"""

CHECK = """You are a strict fact checker. Different spellings of the same name (Chernobyl/Chornobyl, Kyiv/Kiev) are not errors. For each story, list every factual claim in its answer and its caption_line (numbers, names, places, causes,
who said what). Each claim must be directly supported by THAT story's article text. Also check that "place" fits the story: it may be
inferred (a WHO decision -> Geneva) and need not be named in the text, but flag it if it is clearly unrelated to where the
events happen (e.g. just the outlet's home country). Check the question fits the story. Naming the story's own outlet as the
source ("according to <outlet>", "<outlet> has the details", "<outlet> reports") is always allowed, even if the article text
doesn't contain the outlet's name. Numbers written as words are the same as digits. Return JSON
{"ok": true|false, "problems": ["story N: claim - why unsupported", ...]}."""

def write(cands, tries=8, covered=()):
    cands = ([a for a in cands if len(a.get('body') or '') >= 150] or cands)[:10]     # enough text to state real facts; 10 keeps the request small
    short = {f'a{i + 1}': a['id'] for i, a in enumerate(cands)}; real2short = {v: k for k, v in short.items()}
    arts = [{'id': real2short[a['id']], 'source': a['source'], 'title': a['title'], 'body': _lead(a['body']), 'topic': a['topic']} for a in cands]
    byid = {k: next(a for a in cands if a['id'] == v) for k, v in short.items()}
    scands = [{'id': k} for k in short]
    told = ('ALREADY COVERED in the last 24 hours (do not pick these events again unless the candidate reports a major new development, '
            'e.g. a much higher death toll or a decision). If fewer than 4 candidates are new, fill the rest with the newest development of a covered event:\n' + '\n'.join('- ' + c for c in covered) + '\n\n') if covered else ''
    msgs = [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': told + 'CANDIDATES:\n' + json.dumps(arts, ensure_ascii=False)}]
    last, fails = None, {}
    for attempt in range(tries):
        if last: msgs.append({'role': 'user', 'content': last})
        s, used = chat(msgs[:2] + msgs[-2:] if len(msgs) > 4 else msgs)   # only the latest draft + feedback: keeps requests small
        for x in s.get('stories', []):                    # label lengths are fixed in code, at a word break
            x['article_id'] = str(x.get('article_id', '')).strip()
            for f in ('question', 'answer', 'place', 'rundown', 'caption_line'):          # house spellings (Chornobyl -> Chernobyl)
                if isinstance(x.get(f), str): x[f] = house(x[f])
            x['chips'] = [[house(str(c[0])), *c[1:]] for c in x.get('chips', []) if c]
            for f in ('hook', 'viewer_question'):
                if isinstance(s.get(f), str): s[f] = house(s[f])
            x['place'], x['rundown'] = _place(str(x.get('place', ''))), _trim(str(x.get('rundown', '')), 40)
            x['chips'] = [[_chip_digits(str(c[0])), *c[1:]] for c in x.get('chips', []) if c]
        probs = validate(s, scands, spread=attempt < 2)   # region spread is wanted, but never worth losing the episode
        if probs:
            print('  writer: format problems ->', probs, flush=True)
            last = 'Fix these problems and return the full JSON again:\n' + '\n'.join(probs)
            for i in {int(n) - 1 for q in probs for n in re.findall(r'story (\d)', q)}:
                if i < len(s.get('stories', [])):
                    aid = s['stories'][i].get('article_id'); fails[aid] = fails.get(aid, 0) + 1
                    if fails[aid] >= 2: last += f'\nStory {i + 1} keeps failing: replace it with a different candidate (not {aid}).'
        else:
            nprob = [f"story {n + 1}: caption_line uses {v}, which is not in the article" for n, x in enumerate(s['stories'])
                     for v in re.findall(r'\d[\d,.]*\d|\d', str(x.get('caption_line', ''))) if not 1900 <= float(v.replace(',', '')) <= 2100 and not _in_article(v.replace(',', ''), byid[x['article_id']]['title'] + ' ' + _lead(byid[x['article_id']]['body']))]
            nprob += [f"story {n + 1}: the answer says {v}, but that number is not in the article"
                     for n, x in enumerate(s['stories']) for v in _numbers(x['answer'])
                     if not _in_article(v, byid[x['article_id']]['title'] + ' ' + _lead(byid[x['article_id']]['body']))]
            if nprob:                                    # every number has to come from the article: checked in code, not by an AI
                print('  writer: numbers ->', nprob, flush=True); last = ('Fix these: ' + '; '.join(nprob) +
                    '. Use only numbers stated in the article text. Return the full JSON again.'); msgs.append({'role': 'assistant', 'content': json.dumps(s, ensure_ascii=False)}); continue
            pack = [{'story': n + 1, 'outlet': byid[x['article_id']]['source'], 'article': byid[x['article_id']]['title'] + '. ' + _lead(byid[x['article_id']]['body']),
                     'question': x['question'], 'answer': x['answer'], 'caption_line': x.get('caption_line', ''), 'place': x['place']} for n, x in enumerate(s['stories'])]
            cm = [{'role': 'system', 'content': CHECK}, {'role': 'user', 'content': json.dumps(pack, ensure_ascii=False)}]
            try: fc, _ = chat(cm, skip=used)            # a different AI checks the writer's work
            except RuntimeError: fc, _ = chat(cm)        # none other available: same provider, fresh conversation
            if fc.get('ok'):
                last_src = byid[s['stories'][-1]['article_id']]['source']
                s['stories'][-1]['answer'] = s['stories'][-1]['answer'].rstrip() + f' {spoken_outlet(last_src)} has the details.'
                for x in s['stories']: x['article_id'] = short[x['article_id']]   # back to the feed's real ids
                return s
            probs = fc.get('problems') or ['fact check failed']
            print('  writer: fact check ->', probs, flush=True)
            bad = sorted({int(n) - 1 for p in probs for n in re.findall(r'story\s*(\d)', str(p), re.I) if 0 < int(n) <= len(s['stories'])})
            swap = []
            for i in bad:
                aid = s['stories'][i]['article_id']; fails[aid] = fails.get(aid, 0) + 1
                if fails[aid] >= 2: swap.append(i + 1)
            last = ('The fact checker rejected these claims:\n' + '\n'.join(map(str, probs)) +
                    '\nRewrite ONLY the stories named, using only facts stated word-for-word in their article text. Delete any claim that is not in the text; do not replace it with new information. Keep the other stories exactly the same.' +
                    (f"\nStories {swap} failed twice: replace them with different candidates that have clear, concrete facts. Do not use these article ids again: {[k for k, v in fails.items() if v >= 2]}." if swap else '') +
                    '\nReturn the full JSON again.')
        msgs.append({'role': 'assistant', 'content': json.dumps(s, ensure_ascii=False)})
    raise RuntimeError(f'script rejected after {tries} tries: ' + (last or '; '.join(map(str, probs))))

SPOKEN = {'nyt': 'The New York Times', 'new york times': 'The New York Times', 'bbc': 'The BBC', 'guardian': 'The Guardian',
          'wsj': 'The Wall Street Journal', 'wall street journal': 'The Wall Street Journal', 'ap ': 'The Associated Press',
          'associated press': 'The Associated Press', 'reuters': 'Reuters', 'bloomberg': 'Bloomberg', 'cnn': 'CNN', 'npr': 'NPR',
          'al jazeera': 'Al Jazeera', 'scmp': 'The South China Morning Post', 'south china morning post': 'The South China Morning Post',
          'financial times': 'The Financial Times', 'ft ': 'The Financial Times', 'economist': 'The Economist', 'the hill': 'The Hill'}
def spoken_outlet(src):
    low = (src or '').lower() + ' '
    for k, v in SPOKEN.items():
        if k in low: return v
    name = re.sub(r'\s+(World|International|Tech|Technology|Business|Middle East|Europe|Asia|Africa|Americas|Politics|Health|Science|Top Stories|News Feed)$', '', src or '').strip()
    return re.sub(r'\.(org|com|net|io|co)\b', r' dot \1', name)   # 'Phys.org' is said 'Phys dot org'

ONES = 'zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen'.split()
TENS = 'twenty thirty forty fifty sixty seventy eighty ninety'.split()
def _words(n):   # 42 -> 'forty two'
    if n < 20: return ONES[n]
    if n < 100: return TENS[n // 10 - 2] + ('' if n % 10 == 0 else ' ' + ONES[n % 10])
    if n < 1000: return ONES[n // 100] + ' hundred' + ('' if n % 100 == 0 else ' ' + _words(n % 100))
    return _words(n // 1000) + ' thousand' + ('' if n % 1000 == 0 else ' ' + _words(n % 1000))

def _says(text, n):   # does the text say this exact number in words? ('forty' must not match 'forty two')
    t = ' ' + re.sub(r'[^a-z ]', ' ', text.lower().replace('-', ' ')) + ' '
    return bool(re.search(r' ' + _words(n) + r' (?!(?:' + '|'.join(ONES[1:10]) + r'|hundred|thousand) )', re.sub(r'\s+', ' ', t)))

VAL = {w: i for i, w in enumerate(ONES)} | {w: 20 + 10 * i for i, w in enumerate(TENS)}
def _numbers(text):
    """Numbers a line says in words, as they'd be written: 'forty-two' -> '42', 'one thousand six hundred' -> '1600',
    'sixteen point seven billion' -> '16.7'. Single small numbers (one..ten) are skipped: 'one of the deadliest'."""
    t = re.sub(r'[^a-z ]', ' ', text.lower().replace('-', ' ')).split() + ['.']; out = []; cur = tot = 0; dec = ''; on = False; n_words = 0
    for w in t:
        if w in VAL and on and dec == '' and cur in (19, 20) and tot == 0 and n_words == 1 and VAL[w] >= 10: cur = cur * 100 + VAL[w]; n_words += 1   # 'twenty twenty-six' = 2026
        elif w in VAL and dec == '': cur += VAL[w]; on = True; n_words += 1
        elif w in VAL and on and dec != '': dec += str(VAL[w]); n_words += 1
        elif w == 'hundred' and on: cur = max(cur, 1) * 100; n_words += 1
        elif w == 'thousand' and on: tot += max(cur, 1) * 1000; cur = 0; n_words += 1
        elif w == 'point' and on: dec = '.'
        elif w in ('million', 'billion', 'trillion') and on: n_words += 1; v = str(tot + cur) + (dec if len(dec) > 1 else ''); out.append(v); cur = tot = 0; dec = ''; on = False; n_words = 0
        elif on:
            v = tot + cur
            if v > 10 or n_words > 1: out.append(str(v) + (dec if len(dec) > 1 else ''))
            cur = tot = 0; dec = ''; on = False; n_words = 0
    return out
def _in_article(v, art):
    a = art.replace(',', ''); low = ' ' + re.sub(r'[^a-z ]', ' ', art.lower().replace('-', ' ')) + ' '
    if re.search(r'(?<![\d.])' + re.escape(v) + r'(?![\d])', a): return True
    if '.' not in v and int(v) < 1000000 and (' ' + _words(int(v)) + ' ') in re.sub(r'\s+', ' ', low): return True
    if v.endswith('00') and re.search(r'(?<![\d.])' + re.escape(str(int(v) / 1000).rstrip('0').rstrip('.')) + r'\s*(k|thousand)\b', a, re.I): return True
    return False

def _chip_digits(t):   # chips show digits: 'FIFTY KILLED' -> '50 KILLED', 'SEVEN PERCENT' -> '7%', 'OVER EIGHT' -> 'OVER 8'
    words = t.split(); out = []; i = 0
    while i < len(words):
        j = i; seq = []
        while j < len(words) and (words[j].lower().strip(',') in VAL or words[j].lower() in ('hundred', 'thousand', 'point') or '-' in words[j] and all(w in VAL for w in words[j].lower().split('-'))):
            seq.append(words[j].replace('-', ' ')); j += 1
        nums = _numbers(' '.join(seq)) if seq else []
        if seq and (nums or (len(seq) == 1 and seq[0].lower() in VAL)):
            out.append(nums[0] if nums else str(VAL[seq[0].lower()])); i = j
        else: out.append(words[i]); i += 1
    return re.sub(r'(\d) PERCENT\b', r'\1%', ' '.join(out), flags=re.I)

_SP = {k: v for k, v in json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'spellings.json'))).items() if not k.startswith('_')}
def house(t):   # our spelling of names, whatever the outlet wrote
    for a, b in _SP.items(): t = re.sub(r'\b' + re.escape(a) + r'\b', b, t)
    return t

def _country(x):   # 'ZAPORIZHZHIA, UKRAINE' -> 'UKRAINE'; rundown 'UKRAINE · ...' as a fallback
    return (str(x.get('place', '')).split(',')[-1] or str(x.get('rundown', '')).split('·')[0]).strip().upper()

def _lead(t, n=600):   # first sentences of an article, up to ~n characters
    t = (t or '').strip()
    if len(t) <= n: return t
    cut = t[:n]; m = list(re.finditer(r'[.!?]["\u201d\']?(?=\s)', cut))
    return cut[:m[-1].end()] if m else cut.rsplit(' ', 1)[0]

SHORT_C = {'UNITED STATES': 'USA', 'UNITED KINGDOM': 'UK', 'UNITED ARAB EMIRATES': 'UAE', 'DEMOCRATIC REPUBLIC OF THE CONGO': 'DR CONGO',
           'CENTRAL AFRICAN REPUBLIC': 'CAR', 'BOSNIA AND HERZEGOVINA': 'BOSNIA', 'SAUDI ARABIA': 'SAUDI ARABIA', 'SOUTH KOREA': 'S. KOREA', 'NORTH KOREA': 'N. KOREA'}
def _place(t, n=22):   # 'WASHINGTON, UNITED STATES' -> 'WASHINGTON, USA'; too long -> drop the city, never cut the country
    t = (t or '').strip().upper()
    for a, b in SHORT_C.items(): t = re.sub(r'\b' + a + r'\b', b, t)
    if len(t) <= n: return t
    city, _, country = t.rpartition(',')
    return country.strip()[:n] if country.strip() else t[:n]

def _trim(t, n):
    t = (t or '').strip()
    if len(t) <= n: return t
    cut = t[:n + 1].rsplit(' ', 1)[0].rstrip(' ·,-')
    return cut if len(cut) >= n // 2 else t[:n]

def validate(s, cands, spread=True):
    p = []; ids = {a['id'] for a in cands}
    try:
        st = s['stories']
        if len(st) != N_STORIES: p.append(f'need exactly {N_STORIES} stories (if there are not enough new ones, add a covered event with its newest development)')
        for n, x in enumerate(st, 1):
            if x['article_id'] not in ids: p.append(f"story {n}: unknown article_id")
            for f in ('question', 'answer'):
                if re.search(r'\d', x[f]): p.append(f'story {n}: write numbers as words in the {f}')
                m = BANNED.search(x[f])
                if m: p.append(f'story {n}: the {f} uses the banned word "{m.group(0)}" - rephrase without it (e.g. "fired", "started", "opened", "unveiled")')
            if not 2 <= len(x['question'].split()) <= 8: p.append(f'story {n}: question must be 2-7 words')
            for m_, y in enumerate(st[:n - 1], 1):   # the same event twice in one episode (two outlets, one story)
                same_place = str(y.get('place', '')).split(',')[0].strip().upper() == str(x.get('place', '')).split(',')[0].strip().upper()
                kx = set(re.findall(r'[a-z]{5,}', x['answer'].lower())); ky = set(re.findall(r'[a-z]{5,}', y['answer'].lower()))
                if y.get('article_id') == x.get('article_id') or (same_place and len(kx & ky) / max(1, min(len(kx), len(ky))) >= 0.3):
                    p.append(f'story {n}: same event as story {m_} - replace it with a different candidate')
            if spread and sum(1 for y in st if _country(y) == _country(x)) > 2: p.append(f'story {n}: more than 2 stories from {_country(x)} - swap one for a different region')
            if ',' not in str(x.get('place', '')) or re.search(r'\b(CYBERSPACE|ONLINE|GLOBAL|WORLDWIDE|INTERNET)\b', str(x.get('place', '')).upper()):
                p.append(f"story {n}: place must be a real location as 'CITY, COUNTRY' or 'REGION, COUNTRY' (for an online/company story use the company's home city)")
            if n == 1:
                vq = str(s.get('viewer_question', '')).strip()
                if vq and (not vq.endswith('?') or len(vq) > 110 or re.search(r'\b(comment|tag|share|like|follow|below|emoji|type)\b', vq, re.I)):
                    p.append('viewer_question must be one open question under 100 characters, without asking people to comment, tag, share, like or follow')
            cl = str(x.get('caption_line', '')).strip()
            if not 30 <= len(cl) <= 110: p.append(f'story {n}: caption_line must be one line of 50-90 characters about this story')
            if not x['answer'].rstrip().endswith(('.', '!', '?')): p.append(f'story {n}: the answer must end with a full sentence')
            if not 15 <= len(x["answer"].split()) <= 30: p.append(f'story {n}: answer must be 20-30 words, it has {len(x["answer"].split())}')
            if not (-90 <= float(x['lat']) <= 90 and -180 <= float(x['lon']) <= 180): p.append(f'story {n}: bad coordinates')
            for c in x['chips']:
                for num in re.findall(r'\d[\d,]*', str(c[0])):
                    n_ = int(num.replace(',', ''))
                    if n_ < 1000000 and not _says(x['answer'], n_): p.append(f"story {n}: chip '{c[0]}' says {n_} but the answer doesn't say {_words(n_)}")
            if not 2 <= len(x['chips']) <= 3 or any(len(c[0]) > 20 for c in x['chips']): p.append(f'story {n}: 2-3 chips of max 18 chars')

    except Exception as e: p.append(f'bad JSON structure: {e}')
    return p
