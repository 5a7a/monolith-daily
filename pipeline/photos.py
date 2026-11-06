# Story photo = the article's own share image (og:image), shown with the outlet's name and grade on it.
# Falls back to another outlet covering the same story, then to a plain source card drawn by the renderer.
import os, re, html, urllib.request, io
from PIL import Image
UA = {'User-Agent': 'Mozilla/5.0 (compatible; MonolithBriefing/1.0)', 'Accept-Language': 'en'}
KNOWN = {}   # article url -> its image url, when the feed or the search already told us
def og_image(url):
    if KNOWN.get(url): return KNOWN[url]
    try:
        page = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20).read(600_000).decode('utf-8', 'ignore')
    except Exception: return None
    for pat in (r'<meta[^>]+property=["\']og:image(?::url)?["\'][^>]+content=["\']([^"\']+)', r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image',
                r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)'):
        m = re.search(pat, page, re.I)
        if m: return html.unescape(m.group(1))
    return None
def fetch(urls, out, skip=(), check=None):
    for u in urls:
        if u in skip: continue
        img = og_image(u)
        if not img: continue
        try:
            data = urllib.request.urlopen(urllib.request.Request(img, headers=UA), timeout=20).read(15_000_000)
            im = Image.open(io.BytesIO(data)).convert('RGB')
            if im.width >= 600 and im.height >= 300:
                im.save(out, quality=92)
                if check and not check(out): os.remove(out); continue   # the AI looked at it and said no: try the next photo
                return out, u
        except Exception: continue
    return None, None

VISION = [('GEMINI_API_KEY', None), ('GROQ_API_KEY', 'qwen/qwen3.8-27b'), ('OPENROUTER_API_KEY', 'google/gemma-4-31b-it:free')]
_SEEN = set()
def _vision_model(base, key):
    import json
    try:
        r = json.load(urllib.request.urlopen(urllib.request.Request(base + '/models', headers={'Authorization': 'Bearer ' + key, 'User-Agent': UA['User-Agent']}), timeout=30))
        ids = [m['id'] for m in r.get('data', [])]
        if 'openrouter' in base: ids = [i for i in ids if i.endswith(':free')]
        for pat in ('qwen3', 'llama-4-maverick', 'llama-4-scout', 'gemma-4', 'vision', 'vl'):
            hit = [i for i in ids if pat in i.lower() and 'guard' not in i.lower()]
            if hit: return hit[0]
    except Exception: pass
    return None

def screen(path, headline, sensitive=False):
    """An AI looks at the photo before it's used. False = don't use it: graphic (bodies, body bags, blood, injuries),
    doesn't fit the story (logo, placeholder, unrelated), mostly text, or an identifiable private person in a sensitive story.
    If no AI can look right now, the photo is kept (the video check still sees it)."""
    import base64, json, writer
    im = Image.open(path); im.thumbnail((768, 768)); b = io.BytesIO(); im.convert('RGB').save(b, 'JPEG', quality=80)
    msg = [{'role': 'user', 'content': [
        {'type': 'text', 'text': f'News story headline: "{headline}". Look at this photo that would illustrate it in a news video on Instagram. '
                                 'Answer JSON {"graphic": bool, "fits": bool, "mostly_text": bool, "private_person": bool, "why": "few words"}. '
                                 'graphic = shows dead bodies, body bags, corpses, blood or visible injuries. fits = plausibly shows this story '
                                 '(place, people, event or subject), not a logo, placeholder, chart or unrelated stock image. mostly_text = mainly '
                                 'words/graphics. private_person = an identifiable ordinary (non-public) person is the main subject.'},
        {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + base64.b64encode(b.getvalue()).decode()}}]}]
    for key, model in VISION:
        prov = next((p for p in writer.PROVIDERS if p[0] == key), None)
        if not prov or not os.environ.get(key): continue
        m = model or os.environ.get(prov[2], prov[3])
        try: v = writer._call(prov[1], os.environ[key], m, msg)
        except Exception as e:
            print('    photo check:', key, str(e)[:100], flush=True)
            if '404' in str(e) and key not in _SEEN:          # that vision model is gone: find one this key can use
                _SEEN.add(key); alt = _vision_model(prov[1], os.environ[key])
                if alt:
                    VISION[[k for k, _ in VISION].index(key)] = (key, alt)
                    try: v = writer._call(prov[1], os.environ[key], alt, msg)
                    except Exception: continue
                else: continue
            else: continue
        bad = v.get('graphic') or v.get('fits') is False or v.get('mostly_text') or (sensitive and v.get('private_person'))
        print(f"    photo check: {'REJECTED' if bad else 'ok'} ({v.get('why', '')})", flush=True)
        return not bad
    return True

def search_other_outlets(title, n=12):
    """Other outlets covering the same story (GDELT news search: free, no key). Returns their article URLs, best first."""
    import json, urllib.parse
    words = [w for w in re.findall(r"[A-Za-z]{4,}", title)][:6]
    q = urllib.parse.quote(' '.join(words))
    try:
        d = json.load(urllib.request.urlopen(urllib.request.Request(
            f'https://api.gdeltproject.org/api/v2/doc/doc?query={q}&mode=artlist&format=json&maxrecords={n}&timespan=3d&sort=hybridrel', headers=UA), timeout=30))
        for a in d.get('articles', []):
            if a.get('socialimage'): KNOWN[a['url']] = a['socialimage']
        return [a['url'] for a in d.get('articles', []) if a.get('socialimage')]
    except Exception: return []

def illustrate(prompt, out):
    """No photo anywhere: a non-realistic editorial illustration from FLUX.1 schnell (Apache-2.0) on Cloudflare Workers AI,
    free tier (10,000 neurons/day = roughly 200 images). Needs CF_ACCOUNT_ID + CF_AI_TOKEN. Returns None if unavailable."""
    import os, json, base64
    acc, tok = os.environ.get('CF_ACCOUNT_ID'), os.environ.get('CF_AI_TOKEN')
    if not (acc and tok): return None
    style = ('flat vector editorial illustration, bold simple shapes, limited palette of deep navy, slate blue and crimson red, '
             'subtle grain, no text, no letters, no logos, no real people, no faces, not photorealistic. Scene: ')
    body = json.dumps({'prompt': style + prompt, 'steps': 6}).encode()
    try:
        r = json.load(urllib.request.urlopen(urllib.request.Request(
            f'https://api.cloudflare.com/client/v4/accounts/{acc}/ai/run/@cf/black-forest-labs/flux-1-schnell', body,
            {'Authorization': f'Bearer {tok}', 'Content-Type': 'application/json'}), timeout=120))
        im = Image.open(io.BytesIO(base64.b64decode(r['result']['image']))).convert('RGB'); im.save(out, quality=92); return out
    except Exception: return None
