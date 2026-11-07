// Cloudflare Worker (free plan)
//  - Episode crons (3 a day): start an episode on GitHub Actions.
//  - Every-5-minutes cron: auto-post any episode nobody answered within AUTO_POST_MINUTES, but only if the AI check passed.
//  - Telegram buttons: Publish / Redo / Reject.
//  - POST /pending (from the GitHub run, with X-Pipe-Secret): remember an episode waiting for an answer (KV binding STATE).
//  - Safety: an episode can only be sent to Instagram once; a GitHub call that fails is retried every 5 minutes for an hour;
//    a Telegram warning when the GitHub key is about to expire.
// Secrets: GH_TOKEN, TG_BOT_TOKEN, TG_SECRET, PIPE_SECRET. Vars: GH_REPO, TG_ADMIN_ID, AUTO_POST_MINUTES (default 30).
const CHECK_CRON = '*/5 * * * *';
export default {
  async scheduled(event, env, ctx) {
    if (event.cron === CHECK_CRON) ctx.waitUntil(autoPost(env).then(() => retryFailed(env)));
    else ctx.waitUntil(dispatch(env, 'make-episode', {}).catch(() => {}));
  },
  async fetch(req, env) {
    const url = new URL(req.url);
    if (req.method === 'POST' && url.pathname === '/pending') {
      if (req.headers.get('X-Pipe-Secret') !== env.PIPE_SECRET) return new Response('forbidden', { status: 403 });
      const p = await req.json(); const mins = Number(env.AUTO_POST_MINUTES || 30);
      if (p.test) return new Response('ok');   // setup check from deploy-worker: secret works, nothing stored
      await env.STATE.put('pending:' + p.run_id, JSON.stringify({ ...p, deadline: Date.now() + mins * 60000 }), { expirationTtl: 3 * 86400 });
      return new Response('ok');
    }
    if (req.method !== 'POST' || req.headers.get('X-Telegram-Bot-Api-Secret-Token') !== env.TG_SECRET) return new Response('forbidden', { status: 403 });
    const u = await req.json(); const q = u.callback_query;
    if (!q) return new Response('ok');
    if (String(q.from.id) !== String(env.TG_ADMIN_ID)) { await tg(env, 'answerCallbackQuery', { callback_query_id: q.id, text: 'Not allowed' }); return new Response('ok'); }
    const [act, run] = q.data.split(':'); if (act === 'done') return new Response('ok');
    const key = 'pending:' + run; const known = await env.STATE.get(key);
    // an episode the timer never heard about (e.g. registration failed) can still be published by its button, but only once
    if (!known && act === 'pub' && await env.STATE.get('posted:' + run)) { await tg(env, 'answerCallbackQuery', { callback_query_id: q.id, text: 'Already posted' }); return new Response('ok'); }
    await env.STATE.delete(key);           // answered -> no auto-post
    let note = 'Rejected';
    if (act === 'pub') note = (await publishOnce(env, run)) ? 'Publishing…' : 'Already posted';
    if (act === 'redo') { await dispatch(env, 'make-episode', {}).catch(() => {}); note = 'Making a new version…'; }
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
      if (!(await publishOnce(env, p.run_id))) continue;
      await tg(env, 'editMessageReplyMarkup', { chat_id: p.chat_id, message_id: p.message_id, reply_markup: { inline_keyboard: [[{ text: 'Auto-posting (no answer)', callback_data: 'done' }]] } });
    } else {
      await tg(env, 'sendMessage', { chat_id: p.chat_id, reply_to_message_id: p.message_id, text: 'No answer, and the AI check found problems, so this one was NOT posted. You can still tap Publish.' });
    }
  }
}
async function publishOnce(env, run) {   // the Publish button and auto-post can't both send the same episode
  const k = 'posted:' + run;
  if (await env.STATE.get(k)) return false;
  await env.STATE.put(k, String(Date.now()), { expirationTtl: 30 * 86400 });
  await dispatch(env, 'publish', { run_id: run }).catch(() => {});   // a failed call is queued and retried
  return true;
}
async function dispatch(env, type, payload, retry = true) {
  let r;
  try {
    r = await fetch(`https://api.github.com/repos/${env.GH_REPO}/dispatches`, {
      method: 'POST', body: JSON.stringify({ event_type: type, client_payload: payload }),
      headers: { Authorization: `Bearer ${env.GH_TOKEN}`, Accept: 'application/vnd.github+json', 'User-Agent': 'monolith-daily-worker' } });
  } catch (e) { r = { ok: false, status: 0, headers: new Headers(), text: async () => String(e) }; }
  await keyExpiryWarning(env, r.headers);
  if (!r.ok) {
    const why = `GitHub ${r.status}: ${(await r.text()).slice(0, 200)}`;
    if (retry) await env.STATE.put('retry:' + Date.now(), JSON.stringify({ type, payload, tries: 0, why }), { expirationTtl: 86400 });
    throw new Error(why);
  }
}
async function retryFailed(env) {   // every 5 minutes, for up to an hour
  const list = await env.STATE.list({ prefix: 'retry:' });
  for (const k of list.keys) {
    const j = JSON.parse(await env.STATE.get(k.name) || 'null'); if (!j) continue;
    try { await dispatch(env, j.type, j.payload, false); await env.STATE.delete(k.name); }
    catch (e) {
      j.tries += 1;
      if (j.tries >= 12) {
        await env.STATE.delete(k.name);
        await tg(env, 'sendMessage', { chat_id: env.TG_ADMIN_ID, text: `⚠️ Couldn't reach GitHub for an hour, so "${j.type}" didn't start. (${String(e.message).slice(0, 200)})` });
      } else await env.STATE.put(k.name, JSON.stringify(j), { expirationTtl: 86400 });
    }
  }
}
async function keyExpiryWarning(env, headers) {   // fine-grained GitHub keys end on a set date; warn 10 days before, once a day
  const exp = headers.get('github-authentication-token-expiration'); if (!exp) return;
  const days = Math.floor((new Date(exp.replace(' UTC', 'Z').replace(' ', 'T')) - Date.now()) / 86400000);
  if (!(days <= 10)) return;
  const k = 'warned:' + new Date().toISOString().slice(0, 10);
  if (await env.STATE.get(k)) return;
  await env.STATE.put(k, '1', { expirationTtl: 2 * 86400 });
  await tg(env, 'sendMessage', { chat_id: env.TG_ADMIN_ID, text: `⚠️ The GitHub key used by the Monolith Worker expires in ${Math.max(days, 0)} day(s). Make a new one and replace GH_TOKEN in Cloudflare, or episodes will stop.` });
}
const tg = (env, m, body) => fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/${m}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
