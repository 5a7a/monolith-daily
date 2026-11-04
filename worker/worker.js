// Cloudflare Worker (free plan): 1) exact-time schedule -> starts an episode on GitHub Actions,
// 2) Telegram button presses -> Publish / Redo / Reject. Secrets: GH_TOKEN, TG_BOT_TOKEN, TG_SECRET. Vars: GH_REPO, TG_ADMIN_ID.
export default {
  async scheduled(event, env, ctx) { ctx.waitUntil(dispatch(env, 'make-episode', {})); },
  async fetch(req, env) {
    if (req.method !== 'POST' || req.headers.get('X-Telegram-Bot-Api-Secret-Token') !== env.TG_SECRET) return new Response('forbidden', { status: 403 });
    const u = await req.json(); const q = u.callback_query;
    if (!q) return new Response('ok');
    if (String(q.from.id) !== String(env.TG_ADMIN_ID)) { await tg(env, 'answerCallbackQuery', { callback_query_id: q.id, text: 'Not allowed' }); return new Response('ok'); }
    const [act, run] = q.data.split(':'); let note = 'Rejected';
    if (act === 'pub') { await dispatch(env, 'publish', { run_id: run }); note = 'Publishing…'; }
    if (act === 'redo') { await dispatch(env, 'make-episode', {}); note = 'Making a new version…'; }
    await tg(env, 'answerCallbackQuery', { callback_query_id: q.id, text: note });
    await tg(env, 'editMessageReplyMarkup', { chat_id: q.message.chat.id, message_id: q.message.message_id, reply_markup: { inline_keyboard: [[{ text: note, callback_data: 'done' }]] } });
    return new Response('ok');
  },
};
async function dispatch(env, type, payload) {
  const r = await fetch(`https://api.github.com/repos/${env.GH_REPO}/dispatches`, {
    method: 'POST', body: JSON.stringify({ event_type: type, client_payload: payload }),
    headers: { Authorization: `Bearer ${env.GH_TOKEN}`, Accept: 'application/vnd.github+json', 'User-Agent': 'monolith-daily-worker' } });
  if (!r.ok) throw new Error(`GitHub dispatch ${r.status}: ${await r.text()}`);
}
const tg = (env, m, body) => fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/${m}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
