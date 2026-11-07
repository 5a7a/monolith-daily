# AI quality check of the FINISHED episode (Gemini watches and listens to the mp4; free tier, same GEMINI_API_KEY).
# Writes review.json: {"ok": bool, "problems": [...], "checked": bool}. Auto-posting only happens when ok is true.
# Usage: python pipeline/review.py OUTDIR
import os, re, sys, json, time, re, urllib.request, urllib.error
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
    for model in dict.fromkeys((MODEL, 'gemini-3.5-flash', 'gemini-3.5-flash-lite')):   # main model, then lighter ones if Google is overloaded
        for attempt in range(2):
            try:
                r = req(f'{G}/v1beta/models/{model}:generateContent', json.dumps(body).encode(), {'Content-Type': 'application/json'})
                txt = r['candidates'][0]['content']['parts'][0]['text']; break
            except urllib.error.HTTPError as e:
                print(f'  review: {model} HTTP {e.code}', flush=True)
                if e.code in (429, 500, 502, 503, 504) and attempt == 0: time.sleep(20); continue
                break
            except Exception as e:                       # timed out (busy): don't retry the same model, try the next one
                print(f'  review: {model} {str(e)[:60]}', flush=True); break
        if txt: break
    if not txt: raise RuntimeError('no Gemini model could watch the video')
    if False:   # (old Interactions endpoint fallback, unused)
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
Think it through before answering, but put only your FINAL conclusions in the JSON: "detail" is one short sentence (max 25 words),
never your working-out. If you check something and it turns out fine, leave it out. Note: amounts can differ only by currency
(e.g. A$29m = US$20m) - that is not an error. Set "certain": true only if you are sure it is really wrong.
Return JSON only: {"ok": true|false, "problems": [{"time": "m:ss", "type": "audio|visual|facts", "severity": "minor|major", "certain": true|false, "detail": "...", "fix": {...}}]}

EXPECTED CONTENT:
"""

def frames_check(out, expect):
    """Backup when no Gemini model can watch the video: another AI looks at 8 still frames against the expected content.
    Speech was already checked word by word (Whisper + the name check) while the voices were made."""
    import base64, subprocess, writer
    dur = float(subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', os.path.join(out, 'episode.mp4')],
                               capture_output=True, text=True).stdout.strip() or 60)
    imgs = []
    for k in range(8):
        f = os.path.join(out, f'chk{k}.jpg')
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', f'{dur * (k + 0.5) / 8:.2f}', '-i', os.path.join(out, 'episode.mp4'), '-frames:v', '1', '-vf', 'scale=540:-1', f], check=True)
        imgs.append(base64.b64encode(open(f, 'rb').read()).decode())
    ask_ = ('These are 4 still frames, in order, from a vertical news video. Expected on-screen content: ' + json.dumps(expect['stories'], ensure_ascii=False) +
            '. Report only clear VISUAL problems: text overlapping or cut off, a label/number that contradicts the expected chips, a map pin label that '
            "doesn't match the expected place, a graphic or gory image, broken or blank frames. Return JSON {\"problems\": [{\"frame\": n, \"severity\": \"minor|major\", \"detail\": \"one short sentence\"}]} "
            '(empty list if fine).')
    probs = []
    for half in (imgs[:4], imgs[4:]):
        msg = [{'role': 'user', 'content': [{'type': 'text', 'text': ask_}] + [{'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + b}} for b in half]}]
        v = None
        for key, model in (('GROQ_API_KEY', 'qwen/qwen3.8-27b'), ('OPENROUTER_API_KEY', 'google/gemma-4-31b-it:free'), ('GEMINI_API_KEY', 'gemini-3.5-flash-lite')):
            prov = next((p for p in writer.PROVIDERS if p[0] == key), None)
            if not prov or not os.environ.get(key): continue
            for attempt in range(2):
                try: v = writer._call(prov[1], os.environ[key], model, msg); break
                except Exception as e: print(f'  backup check: {model} {str(e)[:60]}', flush=True); time.sleep(15)
            if v is not None: break
        if v is None: raise RuntimeError('no AI could look at the frames either')
        probs += [{'time': '-', 'type': 'visual', 'severity': p.get('severity', 'minor'), 'detail': str(p.get('detail', ''))[:220]} for p in v.get('problems', [])]
    return probs

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
        probs = []
        for p_ in v.get('problems') or []:
            d = str(p_.get('detail', ''))
            if re.search(r'everything is fine|no (actual )?(error|mismatch|problem)|is consistent|that is correct|not an error', d, re.I): continue   # it talked itself out of it
            if p_.get('severity') == 'major' and p_.get('certain') is False: p_['severity'] = 'minor'                            # unsure -> not a blocker
            p_['detail'] = re.split(r'\s*\((?:wait|actually|let\'s)', d, flags=re.I)[0].strip()[:220]                          # conclusion only, never the working-out
            probs.append(p_)
        res = {'checked': True, 'problems': probs, 'ok': bool(v.get('ok')) and not any(p.get('severity') == 'major' for p in probs)}
    except Exception as e:
        print('  review: video check failed -', str(e)[:120], '- trying the backup frame check', flush=True)
        try:
            probs = frames_check(out, expect)
            res = {'checked': True, 'backup': True, 'problems': probs, 'ok': not any(p.get('severity') == 'major' for p in probs)}
        except Exception as e2:
            res['problems'] = [{'time': '-', 'type': 'check', 'severity': 'major', 'detail': f'AI check could not run: {e} / backup: {e2}'[:300]}]
    json.dump(res, open(os.path.join(out, 'review.json'), 'w'), ensure_ascii=False, indent=1)
    print(json.dumps(res, ensure_ascii=False)[:2000])
