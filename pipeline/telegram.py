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
if __name__ == '__main__':
    cmd = sys.argv[1]; chat = os.environ['TG_CHAT_ID']
    if cmd == 'review':   # review OUTDIR RUN_ID
        meta = json.load(open(os.path.join(sys.argv[2], 'meta.json'))); run = sys.argv[3]
        kb = {'inline_keyboard': [[{'text': '✅ Publish', 'callback_data': f'pub:{run}'}, {'text': '🔁 Redo', 'callback_data': f'redo:{run}'}, {'text': '✖ Reject', 'callback_data': f'rej:{run}'}]]}
        api('sendVideo', {'chat_id': chat, 'caption': (meta['caption'] + '\n\nSOURCES\n' + meta['sources'])[:1020], 'supports_streaming': 'true',
                          'reply_markup': json.dumps(kb)}, {'video': os.path.join(sys.argv[2], 'preview.mp4')})
    else:                 # say "text"
        api('sendMessage', {'chat_id': chat, 'text': sys.argv[2][:4000]})
