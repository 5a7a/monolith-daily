# Turns the AI check's problems into fixes for the next build. Usage: python pipeline/autofix.py OUTDIR ROUND
# Exit 0 = at least one fix applied (rebuild + re-check), 1 = nothing fixable left.
import os, sys, re, json
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
out, rnd = sys.argv[1], int(sys.argv[2]); J = lambda n: os.path.join(out, n)
rev = json.load(open(J('review.json'))); S = json.load(open(J('script.json')))
FX = json.load(open(J('fixes.json'))) if os.path.exists(J('fixes.json')) else {}
log = json.load(open(J('fixlog.json'))) if os.path.exists(J('fixlog.json')) else []
lines = json.load(open(J('lines.json'))); psrc = json.load(open(J('photo_src.json'))) if os.path.exists(J('photo_src.json')) else {}
LS = os.path.join(HERE, 'learned_say.json'); learned = json.load(open(LS)) if os.path.exists(LS) else {}
arts = {a['id']: a for a in S.get('_articles', [])}
BANNED = re.compile(r'\b(startup|launch(?:ed|ing)?|free trial|monolith\.news|link in bio|\$\d)', re.I)
FX['round'] = rnd; done = []

def story_line(n):   # spoken line index -> (story index, 'question'|'answer'); line 0 is the intro
    return ((n - 1) // 2, 'question' if n % 2 == 1 else 'answer') if n >= 1 else (None, None)

def facts_ok(story, text):
    try:
        import writer
        a = arts[S['stories'][story]['article_id']]
        v, _ = writer.chat([{'role': 'system', 'content': writer.CHECK},
                            {'role': 'user', 'content': json.dumps([{'story': 1, 'outlet': a.get('source', ''), 'article': a['title'] + '. ' + a['body'], 'answer': text}], ensure_ascii=False)}])
        return bool(v.get('ok'))
    except Exception: return False

def rewrite_line(story, kind, problem):   # ask the writer for a corrected line, using only the article's facts
    try:
        import writer
        a = arts[S['stories'][story]['article_id']]; cur = S['stories'][story][kind]
        rule = ('2 short complete sentences, 18-26 words, facts from the article only, numbers as words, no outlet names'
                if kind == 'answer' else '2-7 words, a question about the story\'s place or subject')
        v, _ = writer.chat([{'role': 'system', 'content': f'Rewrite one line of a news video script. Rules: {rule}. Return JSON {{"text": "..."}}.'},
                            {'role': 'user', 'content': json.dumps({'article': a['title'] + '. ' + a['body'], 'current_line': cur, 'problem': problem}, ensure_ascii=False)}])
        t = str(v.get('text', '')).strip()
        if kind == 'answer' and story == len(S['stories']) - 1 and 'has the details' in cur:   # keep the closing credit
            t = t.rstrip() + ' ' + cur[cur.rindex('.', 0, cur.index('has the details')) + 1:].strip()
        return t
    except Exception: return ''

for p in rev.get('problems', []):
    f = p.get('fix') or {}; act = f.get('action'); what = p.get('detail', '')[:120]
    try:
        if act == 'pronounce' and f.get('word') and f.get('say_as'):
            w, say = f['word'].strip(), f['say_as'].strip()
            FX.setdefault('say', {})[w] = say; learned[w] = say       # remembered for every future episode
            done.append(f'taught the voices to say "{w}" as "{say}"')
        elif act == 'revoice' and isinstance(f.get('line'), int) and 0 <= f['line'] < len(lines):
            n = f['line']; tries = FX.setdefault('revoiced', {}); st, kind = story_line(n)
            if tries.get(str(n)) and st is not None:          # re-recording didn't help: the words themselves are wrong
                t = rewrite_line(st, kind, p.get('detail', ''))
                if t and not re.search(r'\d', t) and not BANNED.search(t) and (kind == 'question' or facts_ok(st, t)):
                    S['stories'][st][kind] = t; done.append(f'rewrote line {n}: "{t}"'); continue
            tries[str(n)] = tries.get(str(n), 0) + 1
            FX.setdefault('revoice', []).append(n); done.append(f"re-recorded line {n} ({what})")
        elif act == 'rewrite' and isinstance(f.get('line'), int):
            st, kind = story_line(f['line']); t = (f.get('text') or '').strip()
            if st is None or not t or re.search(r'\d', t) or BANNED.search(t): continue
            if kind == 'answer' and not facts_ok(st, t): continue    # a rewrite must pass the fact check too
            S['stories'][st][kind] = t; done.append(f"rewrote line {f['line']}: \"{t}\"")
        elif act == 'replace_photo' and isinstance(f.get('story'), int):
            src = (psrc.get(str(f['story'])) or {}).get('src')
            if src: FX.setdefault('skip_urls', {}).setdefault(str(f['story']), []).append(src); done.append(f"swapped the photo for story {f['story'] + 1}")
        elif act == 'fix_text' and isinstance(f.get('story'), int) and f.get('new'):
            s = S['stories'][f['story']]; new = f['new'].strip()[:22]
            if f.get('field') == 'chip':
                for c in s['chips']:
                    if c[0].strip().upper() == (f.get('old') or '').strip().upper(): c[0] = new.upper()
            elif f.get('field') in ('place', 'rundown'): s[f['field']] = new.upper()
            else: continue
            done.append(f"fixed on-screen text in story {f['story'] + 1}: {f.get('old')} → {new}")
        elif act == 'move_pin' and isinstance(f.get('story'), int) and -90 <= float(f['lat']) <= 90 and -180 <= float(f['lon']) <= 180:
            S['stories'][f['story']]['lat'], S['stories'][f['story']]['lon'] = float(f['lat']), float(f['lon']); done.append(f"moved the pin for story {f['story'] + 1}")
        elif act == 'music_quieter':
            FX['music_db'] = FX.get('music_db', 0) - 3; done.append('lowered the music by 3 dB')
    except Exception: continue

json.dump(FX, open(J('fixes.json'), 'w'), ensure_ascii=False, indent=1); json.dump(S, open(J('script.json'), 'w'), ensure_ascii=False, indent=1)
json.dump(learned, open(LS, 'w'), ensure_ascii=False, indent=1)
log += [f'Round {rnd}: {d}' for d in dict.fromkeys(done)]; json.dump(log, open(J('fixlog.json'), 'w'), ensure_ascii=False, indent=1)
print('\n'.join(done) or 'nothing fixable'); sys.exit(0 if done else 1)
