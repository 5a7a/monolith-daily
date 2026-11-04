# Free voices, set per speaker in voices.json. Both run on a normal CPU, much faster than real time.
#   M: Pocket TTS "michael" (Kyutai; code MIT, weights CC-BY 4.0, voice from VCTK, CC-BY 4.0 -> credit "VCTK corpus")
#   F: Kokoro-82M "af_heart" (Apache 2.0) with fixed pronunciations from pronunciations.json
import os, re, json, numpy as np, soundfile as sf
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = lambda *a: os.path.join(ROOT, *a)
CFG = json.load(open(P('pipeline', 'voices.json')))
PRON = {k: v for k, v in json.load(open(P('pipeline', 'pronunciations.json'))).items() if not k.startswith('_')}
SAY = {'Türkiye': 'Turkey', 'Zelenskyy': 'Zelensky', '%': ' percent', '&': ' and '}   # plain-text fixes for engines without phoneme input
_LS = P('pipeline', 'learned_say.json')
LEARNED = json.load(open(_LS)) if os.path.exists(_LS) else {}   # words the AI check taught us: word -> how to say it (both voices)
def learned(text):
    for w, say in LEARNED.items(): text = re.sub(r'\b' + re.escape(w) + r'\b', say, text)
    return text
def clean(t):
    t = re.sub(r'\[[^\]]*\]', '', t)            # sound tags: neither engine performs them
    return re.sub(r'\s+', ' ', t).strip()

_E = {}
def engine(name):
    if name in _E: return _E[name]
    if name == 'pocket':
        import torch
        from pocket_tts import TTSModel
        m = TTSModel.load_model(); states = {}
        def say(text, c, seed):
            for a, b in SAY.items(): text = text.replace(a, b)
            if c['voice'] not in states: states[c['voice']] = m.get_state_for_audio_prompt(c['voice'])
            torch.manual_seed(seed); return m.generate_audio(states[c['voice']], text).numpy().reshape(-1).astype(np.float32), m.sample_rate
    elif name == 'kokoro':
        from kokoro_onnx import Kokoro
        mdir = os.environ.get('KOKORO_DIR', P('models'))
        k = Kokoro(os.path.join(mdir, 'kokoro-v1.0.onnx'), os.path.join(mdir, 'voices-v1.0.bin'))
        def say(text, c, seed):
            ph = k.tokenizer.phonemize(text.replace('%', ' percent').replace('&', ' and '), 'en-us')
            for word, ipa in PRON.items(): ph = ph.replace(k.tokenizer.phonemize(word, 'en-us'), ipa)
            a, sr = k.create(ph, voice=c['voice'], speed=c.get('speed', 1.0) * (1 if seed < 100 else 0.97), lang='en-us', is_phonemes=True)
            return np.asarray(a, np.float32), sr
    else: raise ValueError(f'unknown voice engine {name}')
    _E[name] = say; return say

def synth(lines, outdir, only=None, seed=7):
    files = []
    for i, (who, txt, _) in enumerate(lines):
        f = os.path.join(outdir, f'line_{i:02d}.wav'); files.append(f)
        if only is not None and i not in only: continue
        c = CFG[who]; a, sr = engine(c['engine'])(learned(clean(txt)), c, seed + i); sf.write(f, a, sr)
    return files
