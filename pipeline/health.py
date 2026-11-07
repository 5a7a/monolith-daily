# Weekly health check: is every free service and key still working? Sends one short Telegram message.
import os, sys, json, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import writer, feed
ok, bad = [], []
def check(name, fn):
    try: r = fn(); ok.append(name) if r is not False else bad.append(f'{name}: not working')
    except Exception as e: bad.append(f'{name}: {str(e)[:160]}')
msg = [{'role': 'user', 'content': 'Return JSON {"ok": true}'}]
for key, base, menv, mdef in writer.PROVIDERS:
    if not os.environ.get(key): continue
    def one(p=(key, base, menv, mdef)):
        keep = writer.PROVIDERS; writer.PROVIDERS = [p]
        try: return writer.chat(msg, passes=1)[0].get('ok') is True
        finally: writer.PROVIDERS = keep
    check(key.replace('_API_KEY', '').replace('_AI_TOKEN', ' AI').title(), one)
check('Monolith feed', lambda: len(feed.candidates(24, 10)) > 0)
check('Kokoro voice files', lambda: urllib.request.urlopen(urllib.request.Request(
    'https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin', method='HEAD'), timeout=30).status == 200)
if os.environ.get('IG_TOKEN'):
    def ig():
        tok = os.environ['IG_TOKEN']; G = 'https://graph.facebook.com'
        me = json.load(urllib.request.urlopen(urllib.request.Request(f"{G}/{os.environ['IG_USER_ID']}?fields=id,username", headers={'Authorization': 'OAuth ' + tok}), timeout=30))
        d = json.load(urllib.request.urlopen(f"{G}/debug_token?input_token={tok}&access_token={tok}", timeout=30)).get('data', {})
        exp = d.get('expires_at', -1); print('Instagram:', '@' + me.get('username', '?'), '| key type:', d.get('type'), '| expires:', 'never' if exp == 0 else exp,
                                                '| can post:', 'instagram_content_publish' in d.get('scopes', []))
        if exp not in (0, None): raise RuntimeError('this key expires - it should be the Page key (Expires: Never)')
        if 'instagram_content_publish' not in d.get('scopes', []): raise RuntimeError('key is missing the instagram_content_publish permission')
        return True
    check('Instagram key', ig)
import telegram
_wurl, _ = telegram.worker_link()
if _wurl:
    def worker():
        try: urllib.request.urlopen(_wurl, timeout=30)
        except urllib.error.HTTPError as e: return e.code == 403      # it answers (and refuses strangers) = alive
        return True
    check('Cloudflare Worker', worker)
text = ('⚠️ Weekly check: something needs attention\n' + '\n'.join('• ' + b for b in bad) + '\n\nWorking: ' + ', '.join(ok)) if bad \
       else '✅ Weekly check: everything works (' + ', '.join(ok) + ')'
print(text)
tg = os.environ.get('TG_BOT_TOKEN')
if tg: urllib.request.urlopen(urllib.request.Request(f'https://api.telegram.org/bot{tg}/sendMessage',
        json.dumps({'chat_id': os.environ['TG_CHAT_ID'], 'text': text}).encode(), {'Content-Type': 'application/json'}), timeout=30)
