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

window.addEventListener('hashchange', () => { registerBridgeFromLocation(); });
registerBridgeFromLocation();
