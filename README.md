# Monolith Daily — free automatic Reels

Three times a day this repo builds a ~60 s "Monolith Daily" Reel from Monolith's own feed, sends it to your Telegram
for approval, and posts it to Instagram when you tap **Publish**. Every part runs on free tiers. Cost: $0.

```
Cloudflare Worker cron ─► GitHub Actions: make-episode
   feed.py     Monolith feed → recent A/A-/B+ stories
   writer.py   free LLM (Gemini → Groq → Cerebras → OpenRouter) writes the M/F dialogue, 2nd LLM fact-checks it
   voices.py   Chatterbox Turbo (MIT, CPU) speaks it; voices cloned from assets/voices/*.wav
   timing.py   faster-whisper word timings → karaoke captions + checks every line was spoken right
   episode.py  renders 1080×1920 video (globe, graded source cards, slam text, chrome MONOLITH loop outro)
   music.py    own synthesized bed + ducking + seamless loop (no licensed music)
 └─► Telegram: video + caption + sources, buttons [Publish] [Redo] [Reject]
Publish ─► Worker ─► GitHub Actions: publish → instagram.py (official API, resumable upload) → Telegram "Posted ✅"
```

## One-time setup (≈1 hour)

1. **GitHub** – create a private repo, push this folder. (Free plan: ~2,000 Actions minutes/month; one episode ≈ 10 min.)
2. **LLM keys (free, no card)** – Google AI Studio → API key → secret `GEMINI_API_KEY`. Groq console → API key →
   secret `GROQ_API_KEY` (used as fact-checker and fallback). Optional: `CEREBRAS_API_KEY`, `OPENROUTER_API_KEY`.
3. **Telegram** – talk to @BotFather → `/newbot` → token → secret `TG_BOT_TOKEN`. Send your bot any message, open
   `https://api.telegram.org/bot<TOKEN>/getUpdates`, copy `chat.id` → secret `TG_CHAT_ID` (also your `TG_ADMIN_ID`).
4. **Instagram** (official API, free):
   - Instagram account must be Professional (Business/Creator) and linked to a Facebook Page.
   - developers.facebook.com → Create app (Business) → add *Instagram* / *Facebook Login for Business*.
   - Graph API Explorer → your app → get a User token with `instagram_basic`, `instagram_content_publish`,
     `pages_show_list`, `pages_read_engagement`, `business_management`.
   - Exchange it for a long-lived token, then `GET /me/accounts` → the Page's `access_token` (doesn't expire)
     → secret `IG_TOKEN`. `GET /<PAGE_ID>?fields=instagram_business_account` → id → secret `IG_USER_ID`.
   - Your own account has a role on the app, so Standard Access is enough — no App Review.
5. **Cloudflare Worker (free)** – `npm i -g wrangler`, `cd worker`, edit `wrangler.toml` (repo + your Telegram id),
   `wrangler secret put GH_TOKEN` (fine-grained GitHub token: this repo, Contents read/write + Actions read/write),
   `wrangler secret put TG_BOT_TOKEN`, `wrangler secret put TG_SECRET` (any random string), `wrangler deploy`.
   Then point Telegram at it:
   `https://api.telegram.org/bot<TOKEN>/setWebhook?url=<WORKER_URL>&secret_token=<TG_SECRET>&allowed_updates=["callback_query"]`
6. **Test** – Actions → make-episode → Run workflow. The video arrives in Telegram in ~10 min.

## Voices
`assets/voices/male.wav` and `female.wav` are 7–8 s reference clips (made with Kokoro, Apache-2.0). Replace them with
10 s clean recordings of your own voices to get a unique Monolith sound — Chatterbox clones any clip you own.

## Limits worth knowing
- Gemini free tier: per-project daily caps that Google changes; Groq/Cerebras/OpenRouter keep it running if Gemini is capped.
- Instagram: 100 API posts per 24 h. Reels: H.264/AAC 48 kHz, 3 s–15 min, ≤300 MB (pipeline output ≈10 MB).
- GitHub cron is unreliable, so the schedule lives in the Worker (exact times) and only *dispatches* the workflow.
- Posts are flagged `is_ai_generated` (AI voices).

## Run locally
`pip install torch --index-url https://download.pytorch.org/whl/cpu && pip install -r requirements.txt` then `python pipeline/run.py out` (needs an LLM key) or
`python pipeline/run.py out --script demo_script.json` (no key; uses the included demo script).
