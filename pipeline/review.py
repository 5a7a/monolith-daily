# AI quality check of the FINISHED episode (Gemini watches and listens to the mp4; free tier, same GEMINI_API_KEY).
# Writes review.json: {"ok": bool, "problems": [...], "checked": bool}. Auto-posting only happens when ok is true.
# Usage: python pipeline/review.py OUTDIR
import os, sys, json, time, re, urllib.request, urllib.error
KEY = os.environ.get('GEMINI_API_KEY'); MODEL = os.environ.get('REVIEW_MODEL', os.environ.get('GEMINI_MODEL', 'gemini-3.8-flash'))
G = 'https://generativelanguage.googleapis.com'

def req(url, data=None, headers=None, method=None, raw=False):
    h = {'x-goog-api-key': KEY, 'User-Agent': 'monolith-daily/1.0', **(headers or {})}
    r = urllib.request.urlopen(urllib.request.Request(url, data, h, method=method), timeout=300)
    return r if raw else json.load(r)

def upload(path):
    size = os.path.getsize(path)
    start = req(f'{G}/upload/v1beta/files', json.dumps({'file': {'display_name': 'episode'}}).encode(),
                {'X-Goog-Upload-Protocol': 'resumable', 'X-Goog-Upload-Command': 'start', 'X-Goog-Upload-Header-Content-Length': str(size),
                 'X-Goog-Upload-Header-Content-Type': 'video/mp4', 'Content-Type': 'application/json'}, raw=True)
    url = start.headers['x-goog-upload-url']
    f = req(url, open(path, 'rb').read(), {'Content-Length': str(size), 'X-Goog-Upload-Offset': '0', 'X-Goog-Upload-Command': 'upload, finalize'})['file']
    for _ in range(60):
        st = req(f"{G}/v1beta/{f['name']}")
        if st.get('state') == 'ACTIVE': return st
        if st.get('state') == 'FAILED': raise RuntimeError('video processing failed')
        time.sleep(5)
    raise RuntimeError('video processing timed out')

def ask(fileinfo, prompt):
    body = {'contents': [{'parts': [{'file_data': {'mime_type': 'video/mp4', 'file_uri': fileinfo['uri']}}, {'text': prompt}]}],
            'generationConfig': {'responseMimeType': 'application/json', 'temperature': 0.1}}
    txt = None
    for model in (MODEL, 'gemini-3.5-flash-lite'):          # main model, then a lighter one if Google is overloaded
        for attempt in range(3):
            try:
                r = req(f'{G}/v1beta/models/{model}:generateContent', json.dumps(body).encode(), {'Content-Type': 'application/json'})
                txt = r['candidates'][0]['content']['parts'][0]['text']; break
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504): time.sleep(20 * (attempt + 1)); continue
                break
        if txt: break
    if not txt:   # newer Interactions endpoint
        body = {'model': MODEL, 'input': [{'type': 'video', 'uri': fileinfo['uri'], 'mime_type': 'video/mp4'}, {'type': 'text', 'text': prompt}]}
        txt = json.dumps(req(f'{G}/v1beta/interactions', json.dumps(body).encode(), {'Content-Type': 'application/json'}))
        txt = txt.encode().decode('unicode_escape', 'ignore')
    m = re.search(r'\{[^{}]*"ok"\s*:\s*(true|false)[\s\S]*?\]\s*\}', txt)
    return json.loads(m.group(0) if m else txt[txt.find('{'): txt.rfind('}') + 1])

PROMPT = """You are the final quality checker for "Monolith Briefing", a vertical news Reel that may be posted automatically.
Watch AND listen to the whole video carefully. Male host asks, female anchor answers. Below is exactly what should be
said and shown. Report only real mistakes a viewer would notice:
- AUDIO: a word mispronounced badly enough to confuse (especially names/places), garbled or skipped words, a line cut off,
  speech that doesn't match the script, voices overlapping, clipping/distortion, silence gaps, music drowning the voice.
- VISUAL: typos, text overlapping or cut off (including words cut mid-word), captions not matching what is said, a broken/blank/black
  frame, a photo that is unrelated to its story or is graphic/gory/inappropriate, a photo showing an identifiable private person
  in a crime, court or death story (always major: could be a victim or wrongly identified), a map pin clearly in the wrong place, glitches.
- FACTS: anything on screen or spoken that contradicts the script or the article facts given below.
Ignore matters of taste (style, voice choice, music choice). Minor = noticeable but harmless; major = misleading,
embarrassing, or clearly broken. ok must be false if there is ANY major problem.
For EVERY problem also give the fix, using exactly one of these actions (line = index in spoken_lines, story = index in stories):
  {"action": "pronounce", "word": "<word as written>", "say_as": "<respelling a text-to-speech voice reads right, e.g. AHN-kuh-ruh>"}
  {"action": "revoice", "line": N}                         (garbled, skipped or cut-off words in that line)
  {"action": "rewrite", "line": N, "text": "<corrected line, numbers as words, facts only from article_facts>"}
  {"action": "replace_photo", "story": N}                   (photo wrong, broken, unrelated or graphic)
  {"action": "fix_text", "story": N, "field": "chip|place|rundown", "old": "<text on screen>", "new": "<corrected>"}
  {"action": "move_pin", "story": N, "lat": 0.0, "lon": 0.0}
  {"action": "music_quieter"}
  {"action": "none"}                                         (cannot be fixed by any action above)
Return JSON only: {"ok": true|false, "problems": [{"time": "m:ss", "type": "audio|visual|facts", "severity": "minor|major", "detail": "...", "fix": {...}}]}

EXPECTED CONTENT:
"""

if __name__ == '__main__':
    out = sys.argv[1]; res = {'ok': False, 'checked': False, 'problems': []}
    try:
        if not KEY: raise RuntimeError('GEMINI_API_KEY missing')
        script = json.load(open(os.path.join(out, 'script.json'))); ep = json.load(open(os.path.join(out, 'episode.json')))
        expect = {'spoken_lines': [dict(index=i, **L) for i, L in enumerate(json.load(open(os.path.join(out, 'lines.json'))))],
                  'stories': [{'outlet': s['outlet'], 'grade': s['grade'], 'headline': s['headline'], 'chips': [c[0] for c in s['chips']],
                               'place': s['place'], 'pin_lat_lon': s['loc'], 'has_photo': bool(s.get('img')), 'ai_illustration': s.get('ai_image', False)}
                              for s in ep['stories']],
                  'story_index_note': 'stories[0] is story 1 on screen',
                  'article_facts': [{'outlet': s['outlet'], 'text': a.get('title', '') + '. ' + a.get('body', '')} for s, a in
                                    zip(ep['stories'], json.load(open(os.path.join(out, 'articles.json'))))]}
        f = upload(os.path.join(out, 'episode.mp4'))
        v = ask(f, PROMPT + json.dumps(expect, ensure_ascii=False))
        probs = v.get('problems') or []
        res = {'checked': True, 'problems': probs, 'ok': bool(v.get('ok')) and not any(p.get('severity') == 'major' for p in probs)}
    except Exception as e:
        res['problems'] = [{'time': '-', 'type': 'check', 'severity': 'major', 'detail': f'AI check could not run: {e}'[:300]}]
    json.dump(res, open(os.path.join(out, 'review.json'), 'w'), ensure_ascii=False, indent=1)
    print(json.dumps(res, ensure_ascii=False)[:2000])
