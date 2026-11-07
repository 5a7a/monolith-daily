# Approval step: sends the finished episode to your Telegram with Publish / Redo / Reject buttons (free Bot API).
import os, sys, json, urllib.request, uuid
def api(method, fields=None, files=None):
    url = f"https://api.telegram.org/bot{os.environ['TG_BOT_TOKEN']}/{method}"
    if not files:
        req = urllib.request.Request(url, json.dumps(fields or {}).encode(), {'Content-Type': 'application/json'})
    else:
        b = uuid.uuid4().hex; body = b''
        for k, v in (fields or {}).items(): body += f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
        for k, p in files.items(): body += f'--{b}\r\nContent-Disposition: form-data; name="{k}"; filename="{os.path.basename(p)}"\r\nContent-Type: video/mp4\r\n\r\n'.encode() + open(p, 'rb').read() + b'\r\n'
        req = urllib.request.Request(url, body + f'--{b}--\r\n'.encode(), {'Content-Type': f'multipart/form-data; boundary={b}'})
    return json.load(urllib.request.urlopen(req, timeout=300))
def worker_link():
    """Where the Cloudflare Worker lives (worker/url.txt, written by the deploy workflow) and the shared secret.
    The secret is derived from the bot token, so it never has to be copied anywhere by hand."""
    import hashlib
    url = os.environ.get('WORKER_URL')
    if not url:
        f = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'worker', 'url.txt')
        url = open(f).read().strip() if os.path.exists(f) else ''
    secret = os.environ.get('PIPE_SECRET') or hashlib.sha256((os.environ.get('TG_BOT_TOKEN', '') + ':pipe').encode()).hexdigest()[:40]
    return url, secret

def verdict(out):
    try: r = json.load(open(os.path.join(out, 'review.json')))
    except Exception: r = {'ok': False, 'checked': False, 'problems': [{'detail': 'no AI check result'}]}
    try: names = json.load(open(os.path.join(out, 'name_flags.json')))
    except Exception: names = []
    uniq = sorted({n.split('"')[1] if '"' in n else n for n in names})   # each unsure name once
    HOLD_AT = 3                                    # this many unsure names usually means something else went wrong (voice glitch)
    mins = int(os.environ.get('AUTO_POST_MINUTES', '30')); when = f'{mins // 60}h{mins % 60:02d}' if mins >= 60 else f'{mins} min'
    if len(uniq) >= HOLD_AT:
        r = {**r, 'ok': False, 'problems': r.get('problems', []) + [{'type': 'pronunciation', 'severity': 'major', 'detail': f'{len(uniq)} names may be mispronounced: ' + ', '.join(uniq)}]}
    if os.environ.get('TEST_RUN'):
        r = {**r, 'ok': False}
    if os.environ.get('TEST_RUN'): head = '🧪 TEST RUN: this one will not be posted.'
    elif r.get('ok'):
        head = f"✅ AI check passed. Auto-posts in {when} if you don't answer."
        if uniq: head += f"\n⚠️ Name{'s' if len(uniq) > 1 else ''} the voice may have said slightly wrong: {', '.join(uniq)}. Listen if you can; it will still post."
    else:
        head = "⚠️ AI check found problems, so this will NOT auto-post. Tap Publish yourself if it's fine."
    try: fixed = json.load(open(os.path.join(out, 'fixlog.json')))
    except Exception: fixed = []
    if fixed: head += f"\n🔧 Fixed automatically: {len(fixed)} problem{'s' if len(fixed) > 1 else ''}."
    try: learned = json.load(open(os.path.join(out, 'learned_names.json')))
    except Exception: learned = []
    if learned: fixed = fixed + ['Learned how to say: ' + ', '.join(learned) + ' (saved for future episodes)']
    lines = [f"• {p.get('time', '')} {p.get('type', '')} ({p.get('severity', '')}): {p.get('detail', '')}" for p in r.get('problems', [])]
    notes = ('FIXED AUTOMATICALLY\n' + '\n'.join('• ' + x for x in fixed) + '\n\n' if fixed else '') + ('STILL FLAGGED\n' + '\n'.join(lines) if lines else '')
    return r, head, notes.strip()

if __name__ == '__main__':
    cmd = sys.argv[1]; chat = os.environ['TG_CHAT_ID']
    if cmd == 'review':   # review OUTDIR RUN_ID
        out, run = sys.argv[2], sys.argv[3]; meta = json.load(open(os.path.join(out, 'meta.json'))); r, head, probs = verdict(out)
        kb = {'inline_keyboard': [[{'text': '✅ Publish', 'callback_data': f'pub:{run}'}, {'text': '🔁 Redo', 'callback_data': f'redo:{run}'}, {'text': '✖ Reject', 'callback_data': f'rej:{run}'}]]}
        cap = head + '\n\n' + meta['caption']
        msg = api('sendVideo', {'chat_id': chat, 'caption': cap[:1020], 'supports_streaming': 'true', 'reply_markup': json.dumps(kb)},
                  {'video': os.path.join(out, 'preview.mp4')})['result']
        extra = (probs + '\n\n' if probs else '') + 'SOURCES\n' + meta['sources']
        api('sendMessage', {'chat_id': chat, 'text': extra[:4000], 'reply_to_message_id': msg['message_id']})
        url, secret = worker_link()
        if os.environ.get('TEST_RUN'): url = ''                  # test run: never registered for auto-post
        if url and secret:   # register the episode with the Worker, which auto-posts it later if nobody answers
            body = json.dumps({'run_id': run, 'ai_ok': bool(r.get('ok')), 'message_id': msg['message_id'], 'chat_id': chat}).encode()
            for attempt in range(3):
                try: urllib.request.urlopen(urllib.request.Request(url.rstrip('/') + '/pending', body, {'Content-Type': 'application/json', 'X-Pipe-Secret': secret}), timeout=30); break
                except Exception as e:
                    if attempt == 2: api('sendMessage', {'chat_id': chat, 'text': f'⚠️ Couldn\'t reach the auto-post timer ({e}). This episode will only post if you tap Publish.'})
                    import time; time.sleep(10)
    else:                 # say "text"
        api('sendMessage', {'chat_id': chat, 'text': sys.argv[2][:4000]})
