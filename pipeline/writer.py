# Script writing + fact check with free LLM APIs (OpenAI-compatible endpoints). Tries providers in order.
import os, re, json, datetime as dt, urllib.request
PROVIDERS = [  # (env key, base url, model env, default model)
    ('GEMINI_API_KEY', 'https://generativelanguage.googleapis.com/v1beta/openai', 'GEMINI_MODEL', 'gemini-3.8-flash'),
    ('GROQ_API_KEY', 'https://api.groq.com/openai/v1', 'GROQ_MODEL', 'openai/gpt-oss-120b'),
    ('CEREBRAS_API_KEY', 'https://api.cerebras.ai/v1', 'CEREBRAS_MODEL', 'llama-3.3-70b'),
    ('OPENROUTER_API_KEY', 'https://openrouter.ai/api/v1', 'OPENROUTER_MODEL', 'meta-llama/llama-3.3-70b-instruct:free'),
]
BANNED = re.compile(r'\b(startup|launch(?:ed|ing)?|free trial|pro plan|monolith\.news|link in bio|subscribe|\$\d)', re.I)
N_STORIES = 4

UA = 'monolith-daily/1.0 (+https://github.com/5a7a/monolith-daily)'   # some APIs block the default Python user agent
FALLBACK = {'GEMINI_API_KEY': ['gemini-3.5-flash-lite']}                    # tried when the main model is overloaded
PREFER = ['gpt-oss-120b', 'kimi-k2', 'llama-4-maverick', 'qwen3-235b', 'llama-3.3-70b', 'qwen3-32b', 'gpt-oss-20b', 'llama']   # best first

def _available(base, key):
    try:
        r = json.load(urllib.request.urlopen(urllib.request.Request(base + '/models', headers={'Authorization': 'Bearer ' + key, 'User-Agent': UA}), timeout=30))
        ids = [m['id'] for m in r.get('data', []) if not re.search(r'whisper|tts|guard|embed|vision|audio|image', m['id'], re.I)]
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
    r = json.load(urllib.request.urlopen(req, timeout=180)); txt = r['choices'][0]['message'].get('content') or ''
    if '{' not in txt: raise ValueError('empty reply')
    return json.loads(txt[txt.find('{'): txt.rfind('}') + 1])

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
                tried.add(model); strict = True
                for attempt in range(3):
                    try: return _call(base, os.environ[key], model, messages, strict), key
                    except urllib.error.HTTPError as e:
                        detail = e.read().decode('utf-8', 'ignore')[:200].replace('\n', ' ')
                        err.append(f'{key} {model}: HTTP {e.code} {detail}')
                        if e.code == 429:                                   # per-minute limit: wait it out
                            m = re.search(r'try again in ([\d.]+)s', detail); time.sleep(min(65, float(m.group(1)) + 2) if m else 30 * (attempt + 1)); continue
                        if e.code in (500, 502, 503, 504): time.sleep(15 * (attempt + 1)); continue   # busy: wait and retry
                        if e.code == 400 and 'json' in detail.lower(): strict = False; continue          # its JSON mode choked: ask plainly
                        if e.code in (400, 404) and 'model' in detail.lower() and not models:              # model retired: ask what exists
                            alt = _available(base, os.environ[key]); models += [alt] if alt and alt not in tried else []
                        break
                    except Exception as e:
                        err.append(f'{key} {model}: {e}'); strict = False; time.sleep(5)
    raise RuntimeError('no LLM provider worked: ' + ' | '.join(err[-6:]))

SYSTEM = """You write the "Monolith Briefing", a ~55 second vertical news video. A male host asks one short question
per story, a female anchor answers with the facts. The intro line and the outro are added automatically - don't write them.
RULES
- Pick exactly 4 stories from the candidates: the most important for an international audience of professionals,
  varied topics, each tied to a real place. Prefer world events, geopolitics and conflict, business and markets,
  economy, tech and AI, science, climate. AVOID: local crime and court cases about private people, minor local or
  state-level elections, celebrity gossip, sports results, opinion pieces.
- question: male host, 2-7 words, natural and varied, e.g. "What happened in Spain?", "And in Belgrade?", "What about Yemen?".
- answer: female anchor, 2 short sentences, 18-26 words (hard limit), ONLY facts stated in that article's text. No outside knowledge,
  no guesses, no added numbers. Do NOT name any news outlet (the source credit is added automatically).
- Write every number as spoken words ("thirty-one", "six point three billion dollars"). No digits anywhere in question/answer.
- Calm, serious tone for deaths and war. Never: website names, "link in bio", prices, "startup", "launch", "free trial".
- chips: 2-3 on-screen fact labels per story, CAPS, max 18 characters, e.g. ["31 KILLED","thirty-one"], ["TALKS RESUME","talks"].
  Second item = the word in the answer where the chip pops. Chips with numbers start with the digits.
- place: WHERE THE EVENT HAPPENS according to the article ("CITY, COUNTRY" or "REGION, COUNTRY", caps, max 22 chars), never
  the outlet's home country. A WHO decision -> "GENEVA, SWITZERLAND"; an outbreak in Congo -> "DR CONGO". lat/lon = that place.
- question: asks about the event's own place or subject ("What about the Ebola outbreak?"), never the outlet's country.
- rundown: "COUNTRY  ·  2-4 WORD SUMMARY" in caps, e.g. "UKRAINE  ·  STRIKE ON AID HUB".
- keys: 1-3 words from the answer to highlight (numbers and the key noun).\n- image_prompt: one sentence describing a simple symbolic scene for the story (objects and setting only, no real people, no text).
Return JSON:
{"stories":[{"article_id":"<id>","question":"...","answer":"...","place":"...","lat":0.0,"lon":0.0,
  "chips":[["LABEL","word"]],"rundown":"...","keys":["..."],"image_prompt":"..."}, ... 4 items],
 "caption":"Instagram caption: first line with the key search words, one short line per story, then 'Send this to someone who needs to know.'",
 "hashtags":["5 specific hashtags, no #reels/#explore"]}"""

CHECK = """You are a strict fact checker. For each story, list every factual claim in its answer (numbers, names, places, causes,
who said what). Each claim must be directly supported by THAT story's article text. Also check that "place" fits the story: it may be
inferred (a WHO decision -> Geneva) and need not be named in the text, but flag it if it is clearly unrelated to where the
events happen (e.g. just the outlet's home country). Check the question fits the story. Naming the story's own outlet as the
source ("according to <outlet>", "<outlet> has the details", "<outlet> reports") is always allowed, even if the article text
doesn't contain the outlet's name. Numbers written as words are the same as digits. Return JSON
{"ok": true|false, "problems": ["story N: claim - why unsupported", ...]}."""

def write(cands, tries=5):
    cands = ([a for a in cands if len(a.get('body') or '') >= 150] or cands)[:10]     # enough text to state real facts; 10 keeps the request small
    short = {f'a{i + 1}': a['id'] for i, a in enumerate(cands)}; real2short = {v: k for k, v in short.items()}
    arts = [{'id': real2short[a['id']], 'source': a['source'], 'title': a['title'], 'body': _lead(a['body']), 'topic': a['topic']} for a in cands]
    byid = {k: next(a for a in cands if a['id'] == v) for k, v in short.items()}
    scands = [{'id': k} for k in short]
    msgs = [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': 'CANDIDATES:\n' + json.dumps(arts, ensure_ascii=False)}]
    last, fails = None, {}
    for attempt in range(tries):
        if last: msgs.append({'role': 'user', 'content': last})
        s, used = chat(msgs)
        for x in s.get('stories', []):                    # label lengths are fixed in code, at a word break
            x['article_id'] = str(x.get('article_id', '')).strip()
            x['place'], x['rundown'] = _trim(str(x.get('place', '')), 22), _trim(str(x.get('rundown', '')), 40)
        probs = validate(s, scands)
        if probs:
            print('  writer: format problems ->', probs, flush=True)
            last = 'Fix these problems and return the full JSON again:\n' + '\n'.join(probs)
        else:
            pack = [{'story': n + 1, 'outlet': byid[x['article_id']]['source'], 'article': byid[x['article_id']]['title'] + '. ' + _lead(byid[x['article_id']]['body']),
                     'question': x['question'], 'answer': x['answer'], 'place': x['place']} for n, x in enumerate(s['stories'])]
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
    raise RuntimeError(f'script rejected after {tries} tries: ' + '; '.join(map(str, probs)))

SPOKEN = {'nyt': 'The New York Times', 'new york times': 'The New York Times', 'bbc': 'The BBC', 'guardian': 'The Guardian',
          'wsj': 'The Wall Street Journal', 'wall street journal': 'The Wall Street Journal', 'ap ': 'The Associated Press',
          'associated press': 'The Associated Press', 'reuters': 'Reuters', 'bloomberg': 'Bloomberg', 'cnn': 'CNN', 'npr': 'NPR',
          'al jazeera': 'Al Jazeera', 'scmp': 'The South China Morning Post', 'south china morning post': 'The South China Morning Post',
          'financial times': 'The Financial Times', 'ft ': 'The Financial Times', 'economist': 'The Economist', 'the hill': 'The Hill'}
def spoken_outlet(src):
    low = (src or '').lower() + ' '
    for k, v in SPOKEN.items():
        if k in low: return v
    return re.sub(r'\s+(World|International|Tech|Technology|Business|Middle East|Europe|Asia|Africa|Americas|Politics|Health|Science|Top Stories|News Feed)$', '', src or '').strip()

def _lead(t, n=600):   # first sentences of an article, up to ~n characters
    t = (t or '').strip()
    if len(t) <= n: return t
    cut = t[:n]; m = list(re.finditer(r'[.!?]["\u201d\']?(?=\s)', cut))
    return cut[:m[-1].end()] if m else cut.rsplit(' ', 1)[0]

def _trim(t, n):
    t = (t or '').strip()
    if len(t) <= n: return t
    cut = t[:n + 1].rsplit(' ', 1)[0].rstrip(' ·,-')
    return cut if len(cut) >= n // 2 else t[:n]

def validate(s, cands):
    p = []; ids = {a['id'] for a in cands}
    try:
        st = s['stories']
        if len(st) != N_STORIES: p.append(f'need exactly {N_STORIES} stories')
        for n, x in enumerate(st, 1):
            if x['article_id'] not in ids: p.append(f"story {n}: unknown article_id")
            for f in ('question', 'answer'):
                if re.search(r'\d', x[f]): p.append(f'story {n}: write numbers as words in the {f}')
                if BANNED.search(x[f]): p.append(f'story {n}: banned phrase in the {f}')
            if not 2 <= len(x['question'].split()) <= 8: p.append(f'story {n}: question must be 2-7 words')
            if not x['answer'].rstrip().endswith(('.', '!', '?')): p.append(f'story {n}: the answer must end with a full sentence')
            if not 15 <= len(x["answer"].split()) <= 30: p.append(f'story {n}: answer must be 20-30 words, it has {len(x["answer"].split())}')
            if not (-90 <= float(x['lat']) <= 90 and -180 <= float(x['lon']) <= 180): p.append(f'story {n}: bad coordinates')
            if not 1 <= len(x['chips']) <= 3 or any(len(c[0]) > 20 for c in x['chips']): p.append(f'story {n}: 2-3 chips of max 18 chars')

    except Exception as e: p.append(f'bad JSON structure: {e}')
    return p
