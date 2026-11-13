# Publishes the Reel with the official Instagram API (free). Uses the resumable upload, so the video never needs
# public hosting. Needs a Page access token (Facebook Login for Business) for the Page linked to the IG account.
# Usage: python pipeline/instagram.py OUTDIR
import os, sys, json, time, urllib.request, urllib.parse, urllib.error
G = 'https://graph.facebook.com'; TOK = os.environ['IG_TOKEN']; IG = os.environ['IG_USER_ID']
class Retry(Exception): pass
def call(method, url, data=None, headers=None, body=None):   # errors raise Retry so a stuck upload can start again
    h = {'Authorization': f'OAuth {TOK}', **(headers or {})}
    if data is not None: body = urllib.parse.urlencode(data).encode()
    try: return json.load(urllib.request.urlopen(urllib.request.Request(url, body, h, method=method), timeout=300))
    except urllib.error.HTTPError as e: raise Retry(f'{method} {url.split("?")[0]} -> {e.code}: {e.read().decode()[:600]}')
    except OSError as e: raise Retry(f'{method} {url.split("?")[0]} -> {e}')

out = sys.argv[1]; meta = json.load(open(os.path.join(out, 'meta.json'))); mp4 = next((os.path.join(out, f) for f in ('episode.mp4', 'manual.mp4') if os.path.exists(os.path.join(out, f))), os.path.join(out, 'episode.mp4'))
def done(link, note=''):
    print(json.dumps({'permalink': link, 'note': note})); open(os.path.join(out, 'published.txt'), 'w').write(link); sys.exit(0)

def already():   # never post the same episode twice: if one of the newest posts has this caption, it's done
    try:
        for m in call('GET', f'{G}/{IG}/media?fields=caption,permalink&limit=5').get('data', []):
            if (m.get('caption') or '').strip()[:150] == meta['caption'].strip()[:150]: done(m.get('permalink', ''), 'already posted')
    except Retry as e: print('could not check recent posts:', e)
if not os.environ.get('IG_DRY'): already()

def post():
    c = call('POST', f'{G}/{IG}/media?upload_type=resumable', {'media_type': 'REELS', 'caption': meta['caption'], 'is_ai_generated': 'true'})
    data = open(mp4, 'rb').read()
    for k in range(3):   # send the bytes again to the same container before giving up on it
        try:
            call('POST', c['uri'], headers={'offset': '0', 'file_size': str(len(data)), 'Content-Type': 'application/octet-stream'}, body=data); break
        except Retry as e:
            print(f'  upload refused ({str(e)[-60:]}), sending again', flush=True)
            if k == 2: raise
            time.sleep(15)
    for _ in range(20):   # Meta recommends polling about once a minute; reels usually finish in 30s-2min
        time.sleep(30); st = call('GET', f"{G}/{c['id']}?fields=status_code,status")
        if st.get('status_code') == 'FINISHED': break
        if st.get('status_code') in ('ERROR', 'EXPIRED'): raise Retry(f'processing failed: {st}')
    else: raise Retry('processing timed out')
    if os.environ.get('IG_DRY'): print('DRY RUN: uploaded and processed fine, not published'); sys.exit(0)
    pub = call('POST', f'{G}/{IG}/media_publish', {'creation_id': c['id']})
    try: link = call('GET', f"{G}/{pub['id']}?fields=permalink").get('permalink', '')
    except Retry: link = ''                                   # it's posted; a missing link must not cause a second upload
    done(link)

TRIES = 8   # Instagram's upload server randomly refuses Reels ('ProcessingFailedError') and accepts the same file on a later try
for attempt in range(TRIES):
    try: post()
    except Retry as e:
        print(f'attempt {attempt + 1} failed: {e}', flush=True)
        if 'OAuth' in str(e) or '190' in str(e): raise SystemExit('Instagram key not accepted (expired or revoked): ' + str(e))
        time.sleep(30); already()                              # it may have gone through after all
raise SystemExit(f'could not post after {TRIES} attempts')
