'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { MANUAL_SESSION_PATHS } = require('../Resources/core');
const { captureFirefoxSession, extractBidiWebSocketUrl, validateBidiWebSocketUrl } = require('../Resources/firefox_bidi_support');

const session = { sessionId: 'synthetic-session', bidiUrl: 'ws://127.0.0.1:49200/session/synthetic-session' };
const context = 'synthetic-window';
const origin = 'https://glados.cloud';
const scopeError = { code: 'GLADOS_COOKIE_SCOPE_UNAVAILABLE' };

function cookie(name, value, domain = '.glados.cloud', path = '/') {
  return { name, value: { type: 'string', value }, domain, path, httpOnly: true, secure: true, sameSite: 'lax', size: name.length + value.length };
}

function harness(mutate = () => {}) {
  const state = {
    requests: [], windows: 0, scripts: 0, trees: 0,
    cookies: [cookie('gld:sess', 'synthetic-opaque'), cookie('gld:sess.sig', 'synthetic-sig'), cookie('api_common', 'synthetic-api', 'glados.cloud', '/api/user')],
  };
  const options = {
    expectedHost: 'glados.cloud',
    webdriver: async (method, endpoint) => {
      assert.equal(method, 'GET');
      assert.equal(endpoint, '/session/synthetic-session/window');
      state.windows += 1;
      return { value: state.windows > 1 && state.changedWindow ? 'another-window' : context };
    },
    request: async (websocket, payload) => {
      assert.equal(websocket, session.bidiUrl);
      state.requests.push(payload);
      let result;
      if (payload.method === 'browsingContext.getTree') {
        state.trees += 1;
        assert.deepEqual(payload.params, { root: context, maxDepth: 0 });
        result = { contexts: [{ context, parent: null, url: origin + '/console/checkin', userContext: 'default', children: null }] };
      } else if (payload.method === 'script.evaluate') {
        state.scripts += 1;
        assert.deepEqual(payload.params, {
          expression: 'JSON.stringify({userAgent:navigator.userAgent,pageUrl:location.href})', target: { context }, awaitPromise: false,
        });
        result = { type: 'success', realm: 'synthetic-realm', result: { type: 'string', value: JSON.stringify({
          pageUrl: origin + '/console/checkin', userAgent: state.scripts > 1 && state.changedUA ? 'ChangedSynthetic/1.0' : 'SyntheticFirefox/1.0',
        }) } };
      } else {
        assert.equal(payload.method, 'storage.getCookies');
        assert.deepEqual(payload.params.partition, { type: 'context', context });
        assert.equal(['glados.cloud', '.glados.cloud'].includes(payload.params.filter.domain), true);
        assert.deepEqual(Object.keys(payload.params.filter), ['domain']); // No path filter: read all paths.
        result = { cookies: state.cookies.filter((item) => item.domain === payload.params.filter.domain), partitionKey: { sourceOrigin: origin, userContext: 'default' } };
      }
      const response = { id: payload.id, type: 'success', result };
      mutate(payload, response, state);
      return response;
    },
  };
  return { state, options };
}

test('Firefox requests a session-bound loopback BiDi URL and rejects unsupported or untrusted addresses', () => {
  assert.equal(extractBidiWebSocketUrl({ value: { sessionId: session.sessionId, capabilities: { webSocketUrl: session.bidiUrl } } }, session.sessionId), session.bidiUrl);
  for (const url of [undefined, 'ws://evil.example:49200/session/synthetic-session', 'ws://127.0.0.1:49200/session/another',
    'ws://127.0.0.1:49200/session', 'wss://127.0.0.1:49200/session/synthetic-session',
    'ws://user:pass@127.0.0.1:49200/session/synthetic-session', session.bidiUrl + '?extra=1', session.bidiUrl + '#fragment']) {
    assert.throws(() => validateBidiWebSocketUrl(url, session.sessionId), scopeError);
  }
  assert.throws(() => extractBidiWebSocketUrl({ value: { capabilities: {} } }, session.sessionId), (error) => {
    assert.equal(error.code, scopeError.code);
    assert.match(error.message, /升级.*Edge.*Safari/);
    return true;
  });
});

test('Firefox captures the current context all-path cookie jar and actual UA without navigating or mutating storage', async () => {
  const flow = harness();
  flow.state.cookies.push(cookie('console_only', 'excluded', 'glados.cloud', '/console'));
  flow.state.cookies.push({ ...cookie('expired', 'excluded'), expiry: 1 });
  flow.state.cookies[1].value = { type: 'base64', value: Buffer.from('synthetic-sig').toString('base64') };
  const captured = await captureFirefoxSession(session, flow.options);
  assert.equal(captured.userAgent, 'SyntheticFirefox/1.0');
  assert.equal(captured.host, 'glados.cloud');
  assert.equal(captured.cookieHeader.includes('api_common=synthetic-api'), true);
  assert.equal(captured.cookieHeader.includes('gld:sess.sig=synthetic-sig'), true);
  assert.equal(captured.cookieHeader.includes('excluded'), false);
  assert.equal(Number.isFinite(Date.parse(captured.capturedAt)), true);
  assert.deepEqual(flow.state.requests.map((request) => request.method), [
    'browsingContext.getTree', 'script.evaluate', 'storage.getCookies', 'storage.getCookies', 'script.evaluate', 'browsingContext.getTree',
  ]);
  assert.equal(flow.state.windows, 2);
});

test('Firefox detects API-path-only cookies missed by classic Get All Cookies', async () => {
  for (const path of MANUAL_SESSION_PATHS) {
    const flow = harness();
    flow.state.cookies.push(cookie('endpoint_only', 'synthetic-extra', 'glados.cloud', path));
    await assert.rejects(captureFirefoxSession(session, flow.options), { code: 'GLADOS_COOKIE_SCOPE_MISMATCH' });
  }
});

test('Firefox refuses foreign contexts and origins before reading cookie values', async () => {
  for (const change of [
    (info) => { info.context = 'other-window'; },
    (info) => { info.parent = 'iframe-parent'; },
    (info) => { info.url = 'https://railgun.info/console'; },
    (info) => { delete info.userContext; },
  ]) {
    const flow = harness((payload, response) => { if (payload.method === 'browsingContext.getTree') change(response.result.contexts[0]); });
    await assert.rejects(captureFirefoxSession(session, flow.options));
    assert.equal(flow.state.requests.some((request) => request.method === 'storage.getCookies'), false);
  }
});

test('Firefox refuses wrong storage partitions, unexpected domains, unsupported commands and malformed replies', async () => {
  for (const change of [
    (response) => { response.result.partitionKey.sourceOrigin = 'https://railgun.info'; },
    (response) => { response.result.partitionKey.userContext = 'other-profile'; },
    (response) => { delete response.result.partitionKey; },
    (response) => { response.result.cookies = [cookie('gld:sess', 'synthetic', 'evil.example')]; },
    (response) => { response.type = 'error'; response.error = 'unknown command'; response.message = 'synthetic-private'; },
    (response) => { response.id += 10; },
  ]) {
    const flow = harness((payload, response) => { if (payload.method === 'storage.getCookies') change(response); });
    await assert.rejects(captureFirefoxSession(session, flow.options), (error) => {
      assert.equal(error.code, scopeError.code);
      assert.equal(error.message.includes('synthetic-private'), false);
      return true;
    });
  }
});

test('Firefox rechecks current window, context partition and actual UA before exporting', async () => {
  for (const change of ['changedWindow', 'changedUA', 'changedPartition']) {
    const flow = harness((payload, response, state) => {
      if (change === 'changedPartition' && payload.method === 'browsingContext.getTree' && state.trees === 2) response.result.contexts[0].userContext = 'other-profile';
    });
    flow.state[change] = true;
    await assert.rejects(captureFirefoxSession(session, flow.options), scopeError);
  }
});
