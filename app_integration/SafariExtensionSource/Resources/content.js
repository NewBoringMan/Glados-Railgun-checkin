'use strict';

const api = globalThis.browser || globalThis.chrome;

function parseBridge() {
  const params = new URLSearchParams(location.hash.replace(/^#/, ''));
  const token = params.get('glados-assistant');
  const port = Number(params.get('port'));
  if (!/^[a-f0-9]{64}$/i.test(String(token || ''))) return null;
  if (!Number.isInteger(port) || port < 1024 || port > 65535) return null;
  return { token, port };
}

async function registerBridgeFromLocation() {
  if (window.top !== window) return;
  const bridge = parseBridge();
  if (!bridge) return;
  try {
    const response = await api.runtime.sendMessage({ type: 'REGISTER_PENDING', ...bridge });
    if (response?.ok) {
      history.replaceState(null, '', `${location.pathname}${location.search}`);
    }
  } catch {
    // The popup exposes actionable diagnostics. Account data is never auto-read here.
  }
}

api.runtime.onMessage.addListener((message, sender) => {
  if (message?.type !== 'READ_MANUAL_CONTEXT' || sender?.id !== api.runtime.id || window.top !== window) return undefined;
  if (location.protocol !== 'https:' || message.origin !== location.origin) return Promise.resolve({ ok: false, reason: 'origin_mismatch' });
  // This only reads page context after the popup's explicit send action. No login or Cookie mutation.
  return Promise.resolve({ ok: true, pageUrl: `${location.origin}${location.pathname}`, userAgent: navigator.userAgent });
});

window.addEventListener('hashchange', () => { registerBridgeFromLocation(); });
registerBridgeFromLocation();
