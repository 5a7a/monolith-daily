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
def verdict(out):
    try: r = json.load(open(os.path.join(out, 'review.json')))
    except Exception: r = {'ok': False, 'checked': False, 'problems': [{'detail': 'no AI check result'}]}
    mins = int(os.environ.get('AUTO_POST_MINUTES', '120'))
    if r.get('ok'):
        head = f"✅ AI check passed. Auto-posts in {mins // 60}h{mins % 60:02d} if you don't answer."
    else:
        head = "⚠️ AI check found problems, so this will NOT auto-post. Tap Publish yourself if it's fine."
    lines = [f"• {p.get('time', '')} {p.get('type', '')} ({p.get('severity', '')}): {p.get('detail', '')}" for p in r.get('problems', [])]
    return r, head, '\n'.join(lines)

if __name__ == '__main__':
    cmd = sys.argv[1]; chat = os.environ['TG_CHAT_ID']
    if cmd == 'review':   # review OUTDIR RUN_ID
        out, run = sys.argv[2], sys.argv[3]; meta = json.load(open(os.path.join(out, 'meta.json'))); r, head, probs = verdict(out)
        kb = {'inline_keyboard': [[{'text': '✅ Publish', 'callback_data': f'pub:{run}'}, {'text': '🔁 Redo', 'callback_data': f'redo:{run}'}, {'text': '✖ Reject', 'callback_data': f'rej:{run}'}]]}
        cap = head + '\n\n' + meta['caption']
        msg = api('sendVideo', {'chat_id': chat, 'caption': cap[:1020], 'supports_streaming': 'true', 'reply_markup': json.dumps(kb)},
                  {'video': os.path.join(out, 'preview.mp4')})['result']
        extra = ('AI CHECK NOTES\n' + probs + '\n\n' if probs else '') + 'SOURCES\n' + meta['sources']
        api('sendMessage', {'chat_id': chat, 'text': extra[:4000], 'reply_to_message_id': msg['message_id']})
        url, secret = os.environ.get('WORKER_URL'), os.environ.get('PIPE_SECRET')
        if url and secret:   # register the episode with the Worker, which auto-posts it later if nobody answers
            body = json.dumps({'run_id': run, 'ai_ok': bool(r.get('ok')), 'message_id': msg['message_id'], 'chat_id': chat}).encode()
            urllib.request.urlopen(urllib.request.Request(url.rstrip('/') + '/pending', body, {'Content-Type': 'application/json', 'X-Pipe-Secret': secret}), timeout=30)
    else:                 # say "text"
        api('sendMessage', {'chat_id': chat, 'text': sys.argv[2][:4000]})
