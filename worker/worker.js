// Cloudflare Worker (free plan)
//  - Episode crons (3 a day): start an episode on GitHub Actions.
//  - Every-5-minutes cron: auto-post any episode nobody answered within AUTO_POST_MINUTES, but only if the AI check passed.
//  - Telegram buttons: Publish / Redo / Reject.
//  - POST /pending (from the GitHub run, with X-Pipe-Secret): remember an episode waiting for an answer (KV binding STATE).
// Secrets: GH_TOKEN, TG_BOT_TOKEN, TG_SECRET, PIPE_SECRET. Vars: GH_REPO, TG_ADMIN_ID, AUTO_POST_MINUTES (default 30).
const CHECK_CRON = '*/5 * * * *';
export default {
  async scheduled(event, env, ctx) {
    if (event.cron === CHECK_CRON) ctx.waitUntil(autoPost(env));
    else ctx.waitUntil(dispatch(env, 'make-episode', {}));
  },
  async fetch(req, env) {
    const url = new URL(req.url);
    if (req.method === 'POST' && url.pathname === '/pending') {
      if (req.headers.get('X-Pipe-Secret') !== env.PIPE_SECRET) return new Response('forbidden', { status: 403 });
      const p = await req.json(); const mins = Number(env.AUTO_POST_MINUTES || 30);
      await env.STATE.put('pending:' + p.run_id, JSON.stringify({ ...p, deadline: Date.now() + mins * 60000 }), { expirationTtl: 3 * 86400 });
      return new Response('ok');
    }
    if (req.method !== 'POST' || req.headers.get('X-Telegram-Bot-Api-Secret-Token') !== env.TG_SECRET) return new Response('forbidden', { status: 403 });
    const u = await req.json(); const q = u.callback_query;
    if (!q) return new Response('ok');
    if (String(q.from.id) !== String(env.TG_ADMIN_ID)) { await tg(env, 'answerCallbackQuery', { callback_query_id: q.id, text: 'Not allowed' }); return new Response('ok'); }
    const [act, run] = q.data.split(':'); if (act === 'done') return new Response('ok');
    const key = 'pending:' + run; const known = await env.STATE.get(key);
    if (!known && act === 'pub') { await tg(env, 'answerCallbackQuery', { callback_query_id: q.id, text: 'Already handled' }); return new Response('ok'); }
    await env.STATE.delete(key);           // answered -> no auto-post
    let note = 'Rejected';
    if (act === 'pub') { await dispatch(env, 'publish', { run_id: run }); note = 'Publishing…'; }
    if (act === 'redo') { await dispatch(env, 'make-episode', {}); note = 'Making a new version…'; }
    await tg(env, 'answerCallbackQuery', { callback_query_id: q.id, text: note });
    await tg(env, 'editMessageReplyMarkup', { chat_id: q.message.chat.id, message_id: q.message.message_id, reply_markup: { inline_keyboard: [[{ text: note, callback_data: 'done' }]] } });
    return new Response('ok');
  },
};
async function autoPost(env) {
  const list = await env.STATE.list({ prefix: 'pending:' });
  for (const k of list.keys) {
    const p = JSON.parse(await env.STATE.get(k.name) || 'null'); if (!p || Date.now() < p.deadline) continue;
    await env.STATE.delete(k.name);
    if (p.ai_ok) {
      await dispatch(env, 'publish', { run_id: p.run_id });
      await tg(env, 'editMessageReplyMarkup', { chat_id: p.chat_id, message_id: p.message_id, reply_markup: { inline_keyboard: [[{ text: 'Auto-posting (no answer)', callback_data: 'done' }]] } });
    } else {
      await tg(env, 'sendMessage', { chat_id: p.chat_id, reply_to_message_id: p.message_id, text: 'No answer, and the AI check found problems, so this one was NOT posted. You can still tap Publish.' });
    }
  }
}
async function dispatch(env, type, payload) {
  const r = await fetch(`https://api.github.com/repos/${env.GH_REPO}/dispatches`, {
    method: 'POST', body: JSON.stringify({ event_type: type, client_payload: payload }),
    headers: { Authorization: `Bearer ${env.GH_TOKEN}`, Accept: 'application/vnd.github+json', 'User-Agent': 'monolith-daily-worker' } });
  if (!r.ok) throw new Error(`GitHub dispatch ${r.status}: ${await r.text()}`);
}
const tg = (env, m, body) => fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/${m}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
