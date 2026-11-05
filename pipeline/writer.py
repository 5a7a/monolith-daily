# Script writing + fact check with free LLM APIs (OpenAI-compatible endpoints). Tries providers in order.
import os, re, json, datetime as dt, urllib.request
PROVIDERS = [  # (env key, base url, model env, default model)
    ('GEMINI_API_KEY', 'https://generativelanguage.googleapis.com/v1beta/openai', 'GEMINI_MODEL', 'gemini-3.8-flash'),
    ('GROQ_API_KEY', 'https://api.groq.com/openai/v1', 'GROQ_MODEL', 'llama-3.3-70b-versatile'),
    ('CEREBRAS_API_KEY', 'https://api.cerebras.ai/v1', 'CEREBRAS_MODEL', 'llama-3.3-70b'),
    ('OPENROUTER_API_KEY', 'https://openrouter.ai/api/v1', 'OPENROUTER_MODEL', 'meta-llama/llama-3.3-70b-instruct:free'),
]
BANNED = re.compile(r'\b(startup|launch(?:ed|ing)?|free trial|pro plan|monolith\.news|link in bio|subscribe|\$\d)', re.I)
N_STORIES = 4

UA = 'monolith-daily/1.0 (+https://github.com/5a7a/monolith-daily)'   # some APIs block the default Python user agent
FALLBACK = {'GEMINI_API_KEY': ['gemini-3.5-flash-lite']}                    # tried when the main model is overloaded

def _call(base, key, model, messages):
    body = json.dumps({'model': model, 'messages': messages, 'temperature': 0.5, 'response_format': {'type': 'json_object'}}).encode()
    req = urllib.request.Request(base + '/chat/completions', body, {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json', 'User-Agent': UA})
    r = json.load(urllib.request.urlopen(req, timeout=180)); txt = r['choices'][0]['message']['content']
    return json.loads(txt[txt.find('{'): txt.rfind('}') + 1])

def chat(messages, skip=None):
    import time, urllib.error
    err = []
    for key, base, menv, mdef in PROVIDERS:
        if not os.environ.get(key) or key == skip: continue
        for model in [os.environ.get(menv, mdef)] + FALLBACK.get(key, []):
            for attempt in range(3):
                try: return _call(base, os.environ[key], model, messages), key
                except urllib.error.HTTPError as e:
                    detail = e.read().decode('utf-8', 'ignore')[:200].replace('\n', ' ')
                    err.append(f'{key} {model}: HTTP {e.code} {detail}')
                    if e.code in (429, 500, 502, 503, 504): time.sleep(15 * (attempt + 1)); continue   # busy: wait and retry
                    break                                                                              # bad key/model: next option
                except Exception as e:
                    err.append(f'{key} {model}: {e}'); time.sleep(5)
    raise RuntimeError('no LLM provider worked: ' + ' | '.join(err[-6:]))

SYSTEM = """You write the "Monolith Briefing", a ~55 second vertical news video. A male host asks one short question
per story, a female anchor answers with the facts. The intro line and the outro are added automatically - don't write them.
RULES
- Pick exactly 4 stories from the candidates: the most important, varied topics, each tied to a real place.
- question: male host, 2-7 words, natural and varied, e.g. "What happened in Spain?", "And in Belgrade?", "What about Yemen?".
- answer: female anchor, 2-3 short sentences, 22-32 words, ONLY facts stated in that article's text. No outside knowledge,
  no guesses, no added numbers. Mention the outlet once in the first answer ("according to The New York Times").
  The LAST answer ends with "<Outlet> has the details."
- Write every number as spoken words ("thirty-one", "six point three billion dollars"). No digits anywhere in question/answer.
- Calm, serious tone for deaths and war. Never: website names, "link in bio", prices, "startup", "launch", "free trial".
- chips: 2-3 on-screen fact labels per story, CAPS, max 18 characters, e.g. ["31 KILLED","thirty-one"], ["TALKS RESUME","talks"].
  Second item = the word in the answer where the chip pops. Chips with numbers start with the digits.
- place: "CITY, COUNTRY" or "REGION, COUNTRY" in caps, max 22 chars; lat/lon = real coordinates of that place.
- rundown: "COUNTRY  ·  SHORT SUMMARY" in caps, max 40 chars.
- keys: 1-3 words from the answer to highlight (numbers and the key noun).\n- image_prompt: one sentence describing a simple symbolic scene for the story (objects and setting only, no real people, no text).
Return JSON:
{"stories":[{"article_id":"<id>","question":"...","answer":"...","place":"...","lat":0.0,"lon":0.0,
  "chips":[["LABEL","word"]],"rundown":"...","keys":["..."],"image_prompt":"..."}, ... 4 items],
 "caption":"Instagram caption: first line with the key search words, one short line per story, then 'Send this to someone who needs to know.'",
 "hashtags":["5 specific hashtags, no #reels/#explore"]}"""

CHECK = """You are a strict fact checker. For each story, list every factual claim in its answer (numbers, names, places, causes,
who said what). Each claim must be directly supported by THAT story's article text. Naming the story's own outlet as the
source ("according to <outlet>", "<outlet> has the details", "<outlet> reports") is always allowed, even if the article text
doesn't contain the outlet's name. Numbers written as words are the same as digits. Return JSON
{"ok": true|false, "problems": ["story N: claim - why unsupported", ...]}."""

def write(cands):
    arts = [{k: a[k] for k in ('id', 'source', 'title', 'body', 'topic', 'countries')} for a in cands]
    msgs = [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': 'CANDIDATES:\n' + json.dumps(arts, ensure_ascii=False)}]
    last = None
    for attempt in range(3):
        if last: msgs.append({'role': 'user', 'content': 'Fix these problems and return the full JSON again:\n' + '\n'.join(last)})
        s, used = chat(msgs); last = validate(s, cands)
        if not last:
            byid = {a['id']: a for a in cands}
            pack = [{'story': n + 1, 'outlet': byid[x['article_id']]['source'], 'article': byid[x['article_id']]['title'] + '. ' + byid[x['article_id']]['body'], 'answer': x['answer']} for n, x in enumerate(s['stories'])]
            fc, _ = chat([{'role': 'system', 'content': CHECK}, {'role': 'user', 'content': json.dumps(pack, ensure_ascii=False)}], skip=used if attempt == 0 else None)
            if fc.get('ok'): return s
            last = fc.get('problems') or ['fact check failed']
        msgs.append({'role': 'assistant', 'content': json.dumps(s, ensure_ascii=False)})
    raise RuntimeError('script rejected after 3 tries: ' + '; '.join(last))

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
            if not 16 <= len(x['answer'].split()) <= 36: p.append(f'story {n}: answer must be 22-32 words')
            if not (-90 <= float(x['lat']) <= 90 and -180 <= float(x['lon']) <= 180): p.append(f'story {n}: bad coordinates')
            if not 1 <= len(x['chips']) <= 3 or any(len(c[0]) > 20 for c in x['chips']): p.append(f'story {n}: 2-3 chips of max 18 chars')
            if len(x['place']) > 24 or len(x['rundown']) > 44: p.append(f'story {n}: place max 22 chars, rundown max 40')
    except Exception as e: p.append(f'bad JSON structure: {e}')
    return p
