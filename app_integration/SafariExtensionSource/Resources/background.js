'use strict';

const api = globalThis.browser || globalThis.chrome;
const ALLOWED_HOSTS = ['glados.cloud', 'railgun.info'];
const PENDING_KEY = 'gladosAssistantPendingV14';
const NATIVE_APP_ID = 'com.enoch.glados-account-center.safari-bridge';
const PENDING_TTL_MS = 30 * 60 * 1000;

function allowedHost(host) {
  const value = String(host || '').toLowerCase().replace(/^\.+|\.+$/g, '');
  return ALLOWED_HOSTS.some((base) => value === base || value.endsWith(`.${base}`));
}

async function getPending() {
  const result = await api.storage.local.get(PENDING_KEY);
  return result?.[PENDING_KEY] || null;
}

async function setPending(value) {
  await api.storage.local.set({ [PENDING_KEY]: value });
}

async function clearPending() {
  await api.storage.local.remove(PENDING_KEY);
}

function validatePending(pending) {
  if (!pending || !/^[a-f0-9]{64}$/i.test(String(pending.token || ''))) return 'no_pending';
  const port = Number(pending.port);
  if (!Number.isInteger(port) || port < 1024 || port > 65535) return 'invalid_pending';
  if (!Number.isFinite(Number(pending.createdAt)) || Date.now() - Number(pending.createdAt) > PENDING_TTL_MS) return 'expired_pending';
  return null;
}

async function currentTab() {
  const tabs = await api.tabs.query({ active: true, currentWindow: true });
  return tabs?.[0] || null;
}

function pendingFromTabUrl(tab) {
  if (!tab?.url) return null;
  let parsed;
  try { parsed = new URL(tab.url); } catch { return null; }
  if (parsed.protocol !== 'https:' || !allowedHost(parsed.hostname)) return null;
  const params = new URLSearchParams(parsed.hash.replace(/^#/, ''));
  const token = params.get('glados-assistant');
  const port = Number(params.get('port'));
  const candidate = { token: String(token || ''), port, createdAt: Date.now() };
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
  let pageUrl;
  try { pageUrl = new URL(tab.url); } catch { return { ok: false, reason: 'invalid_page_url' }; }
  if (pageUrl.protocol !== 'https:' || !allowedHost(pageUrl.hostname)) return { ok: false, reason: 'not_glados_page' };

  const cookies = await api.cookies.getAll({ url: `${pageUrl.protocol}//${pageUrl.host}/` });
  const session = cookies.find((item) => item.name === 'koa:sess')?.value || '';
  const signature = cookies.find((item) => item.name === 'koa:sess.sig')?.value || '';
  if (!session || !signature) return { ok: false, reason: 'not_logged_in' };

  let nativeResponse;
  try {
    nativeResponse = await api.runtime.sendNativeMessage(NATIVE_APP_ID, {
      type: 'CAPTURE_ACCOUNT',
      token: pending.token,
      port: Number(pending.port),
      host: pageUrl.hostname,
      pageUrl: pageUrl.toString(),
      cookies: { session, signature }
    });
  } catch (error) {
    return { ok: false, reason: `native_messaging_failed: ${error?.message || error}` };
  }

  if (!nativeResponse?.ok) return { ok: false, reason: nativeResponse?.reason || 'native_bridge_rejected' };
  await clearPending();
  return { ok: true };
}

api.runtime.onMessage.addListener((message, sender) => {
  return (async () => {
    if (message?.type === 'REGISTER_PENDING') {
      const tabUrl = sender?.tab?.url;
      let parsed;
      try { parsed = new URL(String(tabUrl || '')); } catch { return { ok: false, reason: 'invalid_sender' }; }
      if (parsed.protocol !== 'https:' || !allowedHost(parsed.hostname)) return { ok: false, reason: 'invalid_sender' };
      const pending = {
        token: String(message.token || ''),
        port: Number(message.port),
        createdAt: Date.now(),
      };
      const error = validatePending(pending);
      if (error) return { ok: false, reason: error };
      await setPending(pending);
      return { ok: true };
    }
    if (message?.type === 'MANUAL_CAPTURE') return captureCurrentAccount();
    if (message?.type === 'GET_STATUS') {
      const pending = await getPending();
      return { ok: true, pending: !validatePending(pending) };
    }
    return { ok: false, reason: 'unsupported' };
  })();
});
