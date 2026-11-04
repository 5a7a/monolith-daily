# Pulls Monolith's own feed and picks well-sourced, recent candidates for the episode.
import json, time, urllib.request, datetime as dt
URL = 'https://api.monolith.news/api/feed'
GOOD = {'A', 'A-', 'B+'}
def candidates(max_age_h=12, n=14):
    d = json.load(urllib.request.urlopen(urllib.request.Request(URL, headers={'User-Agent': 'monolith-daily'}), timeout=30))
    now = dt.datetime.now(dt.timezone.utc); seen = set(); out = []
    for a in d['articles']:
        try: age = (now - dt.datetime.fromisoformat(a['pubDate'].replace('Z', '+00:00'))).total_seconds() / 3600
        except Exception: continue
        key = ' '.join(a['title'].lower().split()[:5])
        if age > max_age_h or a.get('trust') not in GOOD or len(a.get('body') or '') < 80 or key in seen: continue
        seen.add(key)
        out.append({k: a.get(k) for k in ('id', 'source', 'trust', 'title', 'body', 'pubDate', 'topic', 'countries', 'url', 'alertScore')})
    out.sort(key=lambda a: a['pubDate'], reverse=True); out.sort(key=lambda a: -(a.get('alertScore') or 0))   # most urgent, then newest
    return out[:n]
