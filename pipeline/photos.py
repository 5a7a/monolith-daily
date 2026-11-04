# Story photo = the article's own share image (og:image), shown with the outlet's name and grade on it.
# Falls back to another outlet covering the same story, then to a plain source card drawn by the renderer.
import re, html, urllib.request, io
from PIL import Image
UA = {'User-Agent': 'Mozilla/5.0 (compatible; MonolithBriefing/1.0)', 'Accept-Language': 'en'}
def og_image(url):
    try:
        page = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20).read(600_000).decode('utf-8', 'ignore')
    except Exception: return None
    for pat in (r'<meta[^>]+property=["\']og:image(?::url)?["\'][^>]+content=["\']([^"\']+)', r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image',
                r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)'):
        m = re.search(pat, page, re.I)
        if m: return html.unescape(m.group(1))
    return None
def fetch(urls, out, skip=()):
    for u in urls:
        if u in skip: continue
        img = og_image(u)
        if not img: continue
        try:
            data = urllib.request.urlopen(urllib.request.Request(img, headers=UA), timeout=20).read(15_000_000)
            im = Image.open(io.BytesIO(data)).convert('RGB')
            if im.width >= 600 and im.height >= 300: im.save(out, quality=92); return out, u
        except Exception: continue
    return None, None

def search_other_outlets(title, n=12):
    """Other outlets covering the same story (GDELT news search: free, no key). Returns their article URLs, best first."""
    import json, urllib.parse
    words = [w for w in re.findall(r"[A-Za-z]{4,}", title)][:6]
    q = urllib.parse.quote(' '.join(words))
    try:
        d = json.load(urllib.request.urlopen(urllib.request.Request(
            f'https://api.gdeltproject.org/api/v2/doc/doc?query={q}&mode=artlist&format=json&maxrecords={n}&timespan=3d&sort=hybridrel', headers=UA), timeout=30))
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
