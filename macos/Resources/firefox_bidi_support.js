'use strict';

const { composeCookieHeader, normalizeUserAgent, selectReusableSessionCookies, validatePinnedPage } = require('./core');

// Standard read-only BiDi commands; no navigation, cookie writes, or profile access.
// https://w3c.github.io/webdriver-bidi/#command-storage-getCookies
// https://developer.mozilla.org/en-US/docs/Web/WebDriver/Reference/Capabilities/webSocketUrl
const READ_METHODS = new Set(['browsingContext.getTree', 'script.evaluate', 'storage.getCookies']);
const CONTEXT_EXPRESSION = 'JSON.stringify({userAgent:navigator.userAgent,pageUrl:location.href})';

function unavailable() {
  const error = new Error('当前 Firefox 或 geckodriver 不支持所需的只读 Cookie 接口，或无法核实浏览器上下文。请升级后重试，或手动使用 Edge / Safari。');
  error.code = 'GLADOS_COOKIE_SCOPE_UNAVAILABLE';
  return error;
}

function validateBidiWebSocketUrl(value, sessionId) {
  let url;
  try { url = new URL(value); } catch { throw unavailable(); }
  if (typeof sessionId !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(sessionId) ||
      url.protocol !== 'ws:' || !['127.0.0.1', 'localhost'].includes(url.hostname) ||
      url.username || url.password || url.search || url.hash ||
      !/^\d+$/.test(url.port) || Number(url.port) < 1024 || Number(url.port) > 65535 ||
      url.pathname !== `/session/${sessionId}`) throw unavailable();
  return url.href;
}

function extractBidiWebSocketUrl(response, sessionId) {
  const value = response?.value ?? response;
  return validateBidiWebSocketUrl(value?.capabilities?.webSocketUrl, sessionId);
}

function decodeCookie(cookie, host) {
  if (!cookie || ![host, `.${host}`].includes(cookie.domain) ||
      typeof cookie.path !== 'string' || !cookie.path.startsWith('/') ||
      typeof cookie.httpOnly !== 'boolean' || typeof cookie.secure !== 'boolean' ||
      typeof cookie.value?.value !== 'string') throw unavailable();
  let value = cookie.value.value;
  if (value.length > 24 * 1024) throw unavailable();
  if (cookie.value.type === 'base64') {
    const bytes = Buffer.from(value, 'base64');
    if (bytes.toString('base64') !== value) throw unavailable();
    value = bytes.toString('latin1');
  } else if (cookie.value.type !== 'string') throw unavailable();
  if (cookie.expiry !== undefined && (!Number.isSafeInteger(cookie.expiry) || cookie.expiry < 0)) throw unavailable();
  return {
    name: cookie.name, value, domain: cookie.domain, path: cookie.path,
    hostOnly: cookie.domain === host, httpOnly: cookie.httpOnly, secure: cookie.secure,
    session: cookie.expiry === undefined, expiry: cookie.expiry,
  };
}

async function captureFirefoxSession(session, options) {
  const websocket = validateBidiWebSocketUrl(session.bidiUrl, session.sessionId);
  const endpoint = `/session/${encodeURIComponent(session.sessionId)}/window`;
  let nextId = 1;
  const call = async (method, params) => {
    if (!READ_METHODS.has(method)) throw unavailable();
    const id = nextId++;
    let response;
    try { response = await options.request(websocket, { id, method, params }); }
    catch { throw unavailable(); }
    if (response?.id !== id || response.type !== 'success' || !response.result || typeof response.result !== 'object') throw unavailable();
    return response.result;
  };
  const readWindow = async () => {
    let response;
    try { response = await options.webdriver('GET', endpoint); } catch { throw unavailable(); }
    const value = response?.value ?? response;
    if (typeof value !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(value)) throw unavailable();
    return value;
  };
  const context = await readWindow();
  const readTree = async () => {
    const result = await call('browsingContext.getTree', { root: context, maxDepth: 0 });
    const info = result.contexts?.[0];
    if (!Array.isArray(result.contexts) || result.contexts.length !== 1 || info?.context !== context ||
        (info.parent !== undefined && info.parent !== null) || typeof info.userContext !== 'string' || !info.userContext) throw unavailable();
    const page = validatePinnedPage(info.url, options.expectedHost || '');
    return { ...page, userContext: info.userContext };
  };
  const beforeTree = await readTree();
  const readContext = async () => {
    const evaluated = await call('script.evaluate', {
      expression: CONTEXT_EXPRESSION, target: { context }, awaitPromise: false,
    });
    if (evaluated.type !== 'success' || evaluated.result?.type !== 'string') throw unavailable();
    let value;
    try { value = JSON.parse(evaluated.result.value); } catch { throw unavailable(); }
    return { ...validatePinnedPage(value?.pageUrl, beforeTree.host), userAgent: normalizeUserAgent(value?.userAgent) };
  };
  const before = await readContext();
  const cookies = [];
  // BiDi domain filters match the stored domain exactly. Query both legal forms
  // inside this window's partition, retaining all paths but no unrelated domains.
  for (const domain of [before.host, `.${before.host}`]) {
    const result = await call('storage.getCookies', { filter: { domain }, partition: { type: 'context', context } });
    if (result.partitionKey?.sourceOrigin !== before.origin || result.partitionKey?.userContext !== beforeTree.userContext ||
        !Array.isArray(result.cookies) || cookies.length + result.cookies.length > 256) throw unavailable();
    for (const cookie of result.cookies) {
      if (cookie?.domain !== domain) throw unavailable();
      cookies.push(decodeCookie(cookie, before.host));
    }
  }
  const after = await readContext();
  const afterTree = await readTree();
  if (await readWindow() !== context || before.origin !== after.origin || before.userAgent !== after.userAgent ||
      beforeTree.origin !== afterTree.origin || beforeTree.userContext !== afterTree.userContext) throw unavailable();
  const parts = selectReusableSessionCookies(cookies, before.host);
  return { parts, cookieHeader: composeCookieHeader(parts), ...after, capturedAt: new Date().toISOString() };
}

module.exports = { bidiUnavailableError: unavailable, captureFirefoxSession, extractBidiWebSocketUrl, validateBidiWebSocketUrl };
