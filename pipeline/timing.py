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
        out.append({'file': f, 'dur': len(a) / sr, 'words': align(txt, ws)})
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
