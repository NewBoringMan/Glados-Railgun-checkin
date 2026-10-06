'use strict';

const api = globalThis.browser || globalThis.chrome;
const ALLOWED_HOSTS = ['glados.cloud', 'railgun.info'];
const PENDING_KEY = 'gladosAssistantManualPendingV1';
const NATIVE_APP_ID = 'com.enoch.glados-account-center.safari-bridge';
const PENDING_TTL_MS = 30 * 60 * 1000;

function allowedHost(host) {
  const value = String(host || '').toLowerCase();
  if (!/^[a-z0-9.-]+$/.test(value) || value.startsWith('.') || value.endsWith('.') || value.includes('..')) return false;
  return ALLOWED_HOSTS.includes(value);
}

function allowedPage(value) {
  try {
    const page = new URL(value);
    return page.protocol === 'https:' && !page.username && !page.password && !page.port && allowedHost(page.hostname) ? page : null;
  } catch { return null; }
}

function pendingStore() { return api.storage.session || api.storage.local; }

async function getPending() {
  const result = await pendingStore().get(PENDING_KEY);
  return result?.[PENDING_KEY] || null;
}

async function setPending(value) {
  await pendingStore().set({ [PENDING_KEY]: value });
}

async function clearPending() {
  await pendingStore().remove(PENDING_KEY);
}

function validatePending(pending) {
  if (!pending || !/^[a-f0-9]{64}$/i.test(String(pending.token || ''))) return 'no_pending';
  const port = Number(pending.port);
  if (!Number.isInteger(port) || port < 1024 || port > 65535) return 'invalid_pending';
  if (!allowedHost(pending.host) || !Number.isInteger(pending.tabId) || pending.tabId < 0) return 'invalid_pending';
  const age = Date.now() - Number(pending.createdAt);
  if (!Number.isFinite(age) || age < 0 || age > PENDING_TTL_MS) return 'expired_pending';
  return null;
}

async function currentTab() {
  const tabs = await api.tabs.query({ active: true, currentWindow: true });
  return tabs?.[0] || null;
}

function pendingFromTabUrl(tab) {
  if (!tab?.url) return null;
  const parsed = allowedPage(tab.url);
  if (!parsed) return null;
  const params = new URLSearchParams(parsed.hash.replace(/^#/, ''));
  const token = params.get('glados-assistant');
  const port = Number(params.get('port'));
  const candidate = { token: String(token || ''), port, host: parsed.hostname, tabId: tab.id, createdAt: Date.now() };
  return validatePending(candidate) ? null : candidate;
}

async function captureCurrentAccount() {
  const tab = await currentTab();
  let pending = await getPending();
  let pendingError = validatePending(pending);
  if (pendingError) {
    if (pendingError === 'expired_pending') await clearPending();
    const fallback = pendingFromTabUrl(tab);
    if (fallback) {
      pending = fallback;
      pendingError = null;
      await setPending(fallback);
    }
  }
  if (pendingError) return { ok: false, reason: pendingError };


  if (!tab?.url) return { ok: false, reason: 'no_active_tab' };
  const pageUrl = allowedPage(tab.url);
  if (!pageUrl) return { ok: false, reason: 'not_glados_page' };
  if (pageUrl.hostname !== pending.host || tab.id !== pending.tabId) return { ok: false, reason: 'origin_mismatch' };

  const readContext = async () => {
    const context = await api.tabs.sendMessage(tab.id, { type: 'READ_MANUAL_CONTEXT', origin: pageUrl.origin }, { frameId: 0 });
    const contextPage = allowedPage(context?.pageUrl);
    if (!context?.ok || !contextPage || contextPage.origin !== pageUrl.origin) throw new Error('origin_mismatch');
    if (typeof context.userAgent !== 'string' || !context.userAgent.trim() || context.userAgent.length > 2048 || /[^\x20-\x7e]/.test(context.userAgent)) throw new Error('invalid_user_agent');
    return { pageUrl: `${contextPage.origin}${contextPage.pathname}`, userAgent: context.userAgent.trim() };
  };
  let context;
  let cookies;
  try {
    const before = await readContext();
    const allCookies = await api.cookies.getAll({ url: `${pageUrl.origin}/api/user/status` });
    const after = await readContext();
    if (before.userAgent !== after.userAgent) return { ok: false, reason: 'context_changed' };
    context = after;
    cookies = allCookies.filter((cookie) => !cookie.partitionKey && !cookie.partitioned).map((cookie) => ({
      name: cookie.name, value: cookie.value, domain: cookie.domain, path: cookie.path,
      hostOnly: cookie.hostOnly, secure: cookie.secure, httpOnly: cookie.httpOnly,
      session: cookie.session, expirationDate: cookie.expirationDate,
    }));
    const values = new Map(cookies.map((cookie) => [cookie.name, cookie.value]));
    if (values.size !== cookies.length) return { ok: false, reason: 'ambiguous_cookies' };
    const hasGld = values.get('gld:sess') && values.get('gld:sess.sig');
    const hasKoa = values.get('koa:sess') && values.get('koa:sess.sig');
    if (!hasGld && !hasKoa) return { ok: false, reason: 'not_logged_in' };
  } catch (error) {
    return { ok: false, reason: ['origin_mismatch', 'invalid_user_agent'].includes(error?.message) ? error.message : 'context_unavailable' };
  }

  let nativeResponse;
  try {
    nativeResponse = await api.runtime.sendNativeMessage(NATIVE_APP_ID, {
      type: 'CAPTURE_ACCOUNT',
      token: pending.token,
      port: Number(pending.port),
      host: pageUrl.hostname,
      pageUrl: context.pageUrl,
      userAgent: context.userAgent,
      cookies,
    });
  } catch {
    return { ok: false, reason: 'native_messaging_failed' };
  }

  if (!nativeResponse?.ok) return { ok: false, reason: 'native_bridge_rejected' };
  await clearPending();
  return { ok: true };
}

let captureInProgress = false;

api.runtime.onMessage.addListener((message, sender) => {
  return (async () => {
    if (sender?.id !== api.runtime.id) return { ok: false, reason: 'invalid_sender' };
    if (message?.type === 'REGISTER_PENDING') {
      const parsed = allowedPage(sender?.tab?.url);
      if (!parsed || sender.frameId !== 0 || !Number.isInteger(sender?.tab?.id)) return { ok: false, reason: 'invalid_sender' };
      const bridge = new URLSearchParams(parsed.hash.replace(/^#/, ''));
      if (message.token !== bridge.get('glados-assistant') || Number(message.port) !== Number(bridge.get('port'))) return { ok: false, reason: 'invalid_pending' };
      const pending = {
        token: String(message.token || ''),
        port: Number(message.port),
        createdAt: Date.now(),
        host: parsed.hostname,
        tabId: sender.tab.id,
      };
      const error = validatePending(pending);
      if (error) return { ok: false, reason: error };
      await setPending(pending);
      return { ok: true };
    }
    const fromPopup = !sender.tab && sender.url === api.runtime.getURL('popup.html');
    if (!fromPopup) return { ok: false, reason: 'invalid_sender' };
    if (message?.type === 'MANUAL_CAPTURE') {
      if (captureInProgress) return { ok: false, reason: 'capture_in_progress' };
      captureInProgress = true;
      try { return await captureCurrentAccount(); } finally { captureInProgress = false; }
    }
    if (message?.type === 'GET_STATUS') {
      const pending = await getPending();
      return { ok: true, pending: !validatePending(pending) };
    }
    return { ok: false, reason: 'unsupported' };
  })();
});
