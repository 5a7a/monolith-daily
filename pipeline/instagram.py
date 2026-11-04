# Publishes the Reel with the official Instagram API (free). Uses the resumable upload, so the video never needs
# public hosting. Needs a Page access token (Facebook Login for Business) for the Page linked to the IG account.
# Usage: python pipeline/instagram.py OUTDIR
import os, sys, json, time, urllib.request, urllib.parse
G = 'https://graph.facebook.com'; TOK = os.environ['IG_TOKEN']; IG = os.environ['IG_USER_ID']
def call(method, url, data=None, headers=None, body=None):
    h = {'Authorization': f'OAuth {TOK}', **(headers or {})}
    if data is not None: body = urllib.parse.urlencode(data).encode()
    try: return json.load(urllib.request.urlopen(urllib.request.Request(url, body, h, method=method), timeout=300))
    except urllib.error.HTTPError as e: raise SystemExit(f'{method} {url} -> {e.code}: {e.read().decode()[:600]}')
out = sys.argv[1]; meta = json.load(open(os.path.join(out, 'meta.json'))); mp4 = os.path.join(out, 'episode.mp4')
c = call('POST', f'{G}/{IG}/media?upload_type=resumable', {'media_type': 'REELS', 'caption': meta['caption'], 'is_ai_generated': 'true'})
size = os.path.getsize(mp4)
call('POST', c['uri'], headers={'offset': '0', 'file_size': str(size)}, body=open(mp4, 'rb').read())
for _ in range(20):   # Meta recommends polling about once a minute; reels usually finish in 30s-2min
    time.sleep(30); st = call('GET', f"{G}/{c['id']}?fields=status_code,status")
    if st.get('status_code') == 'FINISHED': break
    if st.get('status_code') in ('ERROR', 'EXPIRED'): raise SystemExit(f'processing failed: {st}')
else: raise SystemExit('processing timed out')
pub = call('POST', f'{G}/{IG}/media_publish', {'creation_id': c['id']})
link = call('GET', f"{G}/{pub['id']}?fields=permalink").get('permalink', '')
print(json.dumps({'media_id': pub['id'], 'permalink': link})); open(os.path.join(out, 'published.txt'), 'w').write(link)
