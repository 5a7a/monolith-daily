# Word timestamps for karaoke captions (faster-whisper, MIT), a check that every line was spoken right, and alignment
# so captions show the script's spelling of names while numbers show as digits (as transcribed).
import re, difflib, soundfile as sf, scipy.signal as sg
_M = None
def _model():
    global _M
    if _M is None:
        from faster_whisper import WhisperModel
        _M = WhisperModel('base.en', device='cpu', compute_type='int8')
    return _M
def norm(x): return re.sub(r'[^a-z]', '', re.sub(r'\[[^\]]+\]', '', x.lower()))
def words(files, lines):
    out = []; bad = []
    for f, (_, txt, _) in zip(files, lines):
        a, sr = sf.read(f, dtype='float32'); a = a.mean(1) if a.ndim > 1 else a
        segs, _ = _model().transcribe(sg.resample_poly(a, 16000, sr).astype('float32'), beam_size=5, word_timestamps=True)
        ws = [[w.word.strip(), round(w.start, 2), round(w.end, 2)] for s in segs for w in s.words]
        heard = ' '.join(w[0] for w in ws)
        if difflib.SequenceMatcher(None, norm(txt), norm(heard)).ratio() < 0.7: bad.append(f)
        out.append({'file': f, 'dur': len(a) / sr, 'words': align(txt, ws), 'heard': heard})
    return out, bad
def _merge(ws):
    """Glue whisper's split tokens back together: 'U' '.S.' -> 'U.S.', 'sell' '-off', '100' ',000'."""
    out = []
    for w in ws:
        if out and re.match(r"^[.,\-'][^\s]", w[0]): out[-1] = [out[-1][0] + w[0], out[-1][1], w[2]]
        else: out.append(list(w))
    return out
def align(script, ws):
    """Keep whisper timings; show the script's words (correct spelling of names), except where whisper wrote digits."""
    ws = _merge(ws); sw = re.sub(r'\[[^\]]+\]', '', script).split()
    key = lambda w: re.sub(r'[^a-z0-9]', '', w.lower())
    a = [key(w) for w in sw]; b = [key(w[0]) for w in ws]; out = [list(w) for w in ws]; res = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == 'equal' or (op == 'replace' and i2 - i1 == j2 - j1):
            for k in range(j2 - j1):
                w = out[j1 + k]; res.append([w[0] if re.search(r'\d', w[0]) else sw[i1 + k], w[1], w[2]])
        elif op == 'replace' and not any(re.search(r'\d', w[0]) for w in out[j1:j2]):
            # different word split ("USAID" vs "US aid"): spread the script words over the same time span
            t0, t1 = out[j1][1], out[j2 - 1][2]; L = sum(len(x) + 1 for x in sw[i1:i2]); c = 0
            for x in sw[i1:i2]:
                res.append([x, round(t0 + (t1 - t0) * c / L, 2), round(t0 + (t1 - t0) * (c + len(x)) / L, 2)]); c += len(x) + 1
        elif op in ('replace', 'insert'): res += out[j1:j2]
    return res

# ---- names: a whole-line score hides one wrong name ("Medina" heard as "Medna" still scores 96%), so check each name
COMMON = set('the a an and but or in on at of to for with from by as is are was were be been it its this that these those there their they he she his her we our you your what who where when why how which after before during over under about into than then also both each other some most many more new old first last one two three four five six seven eight nine ten hundred thousand million billion monday tuesday wednesday thursday friday saturday sunday january february march april may june july august september october november december briefing monolith officials authorities experts critics police government president prime minister has have had will would could should can may might not no yes so if while because says said report reports details news'.split())
def names(text):
    """Proper names in a line: capitalised words, not common words. 'STAT News has the details.' -> ['STAT']"""
    out = []
    for w in re.findall(r"[A-Za-z\u00C0-\u024F][\w\u00C0-\u024F'\-]*", re.sub(r'\[[^\]]+\]', '', text)):
        if w[0].isupper() and w.lower().strip("'") not in COMMON and len(w) >= 3 and w not in out: out.append(w)
    return out
def _syl(x): return len(re.findall(r'[aeiouy]+', x))
_S = None
def _second(f):   # bigger model: only to tell a dropped word from a missing one ('Ukraine' left out by base.en)
    global _S
    if _S is None:
        from faster_whisper import WhisperModel
        _S = WhisperModel('small.en', device='cpu', compute_type='int8')
    a, sr = sf.read(f, dtype='float32'); a = a.mean(1) if a.ndim > 1 else a
    segs, _ = _S.transcribe(sg.resample_poly(a, 16000, sr).astype('float32'), beam_size=5)
    return ' '.join(x.text for x in segs)
def _match(k, heard):
    """Find the run of 1-3 heard words that best matches a name (fewest words on a tie). It counts as said right only
    if that best match is close AND has the same number of syllables ('Medina' heard as 'Medna' is close but lost one)."""
    t = [norm(x) for x in heard.split()]; t = [x for x in t if x]; best, got = 0, ''
    for n in (1, 2, 3):
        for i in range(len(t) - n + 1):
            c = ''.join(t[i:i + n])
            for c in ([c, c[:len(k)], c[-len(k):]] if n == 1 and len(c) > len(k) + 2 else [c]):   # name glued to the next word: 'Yanbu port' -> 'yambuport'
                r = difflib.SequenceMatcher(None, k, c).ratio()
                if r > best + 1e-9: best, got = r, c
    return best >= 0.75 and _syl(k) == _syl(got), best, got
def name_check(text, heard, file=None):
    """Names that were not clearly said: [(name, what was heard there)]. Close but with a lost or extra syllable
    ('Medina' heard as 'Medna') counts as wrong. base.en is used because bigger models 'correct' a wrong name back
    to the right spelling; only when a name seems missing altogether does small.en get a say."""
    h = norm(heard); bad = []; h2 = None   # h: everything heard, run together
    for n in names(text):
        k = norm(n)
        if not k or k in h: continue
        ok, best, got = _match(k, heard)
        if ok: continue
        dropped = len(heard.split()) < len(re.sub(r'\[[^\]]+\]', '', text).split())
        if best < 0.6 and dropped and file:                      # base.en left words out: second opinion
            if h2 is None: h2 = _second(file)
            if k in norm(h2) or _match(k, h2)[0]: continue
        bad.append((n, got))
    return bad
