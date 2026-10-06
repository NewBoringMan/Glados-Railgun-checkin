'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { normalizeSafariNativeCapture } = require('../Resources/safari_native_protocol');
const { buildCapturePayload, verifyCookie } = require('../Resources/capture_account');
const { MANUAL_SESSION_PATHS } = require('../Resources/core');

const source = fs.readFileSync(path.join(__dirname, '..', '..', 'app_integration', 'SafariExtensionSource', 'Resources', 'background.js'), 'utf8');
const token = 'c'.repeat(64);
const port = 42317;
const extensionId = 'synthetic-safari-extension';
const popupUrl = 'safari-web-extension://synthetic/popup.html';

function harness() {
  const state = {
    tab: { id: 7, url: `https://glados.cloud/console/checkin#glados-assistant=${token}&port=${port}` },
    store: {}, cookieReads: 0, cookiePaths: [], contextReads: 0, nativeSends: [], capture: null,
    userAgent: 'SyntheticSafari/1.0', contextOrigin: 'https://glados.cloud',
    cookies: [
      { name: 'gld:sess', value: 'synthetic-opaque-session', domain: '.glados.cloud', path: '/', hostOnly: false, session: true },
      { name: 'gld:sess.sig', value: 'synthetic-signature', domain: '.glados.cloud', path: '/', hostOnly: false, session: true },
      { name: 'koa:sess', value: Buffer.from(JSON.stringify({ userId: 4242, _expire: 1 })).toString('base64'), domain: '.glados.cloud', path: '/' },
      { name: 'koa:sess.sig', value: 'synthetic-legacy-signature', domain: '.glados.cloud', path: '/' },
      { name: 'site_setting', value: 'synthetic-site-cookie', domain: '.glados.cloud', path: '/' },
    ],
  };
  const storage = {
    get: async (key) => ({ [key]: state.store[key] }),
    set: async (value) => Object.assign(state.store, value),
    remove: async (key) => { delete state.store[key]; },
  };
  let listener;
  const api = {
    storage: { session: storage, local: storage },
    tabs: {
      query: async () => [state.tab],
      sendMessage: async (tabId, message) => {
        assert.equal(tabId, state.tab.id);
        assert.equal(message.type, 'READ_MANUAL_CONTEXT');
        state.contextReads += 1;
        return { ok: true, pageUrl: `${state.contextOrigin}/console/checkin`, userAgent: state.userAgent };
      },
    },
    cookies: {
      getAll: async (filter) => {
        state.cookieReads += 1;
        const url = new URL(filter.url);
        assert.equal(url.origin, 'https://glados.cloud');
        assert.equal(MANUAL_SESSION_PATHS.includes(url.pathname), true);
        state.cookiePaths.push(url.pathname);
        return state.cookies.filter((cookie) => {
          const path = cookie.path || '/';
          return url.pathname === path || (url.pathname.startsWith(path) && (path.endsWith('/') || url.pathname[path.length] === '/'));
        });
      },
    },
    runtime: {
      id: extensionId, getURL: () => popupUrl,
      onMessage: { addListener(value) { listener = value; } },
      sendNativeMessage: async (appId, payload) => {
        assert.equal(appId, 'com.enoch.glados-account-center.safari-bridge');
        const copied = JSON.parse(JSON.stringify(payload));
        state.nativeSends.push(copied);
        state.capture = normalizeSafariNativeCapture(copied, token, port, 'glados.cloud');
        return { ok: true };
      },
    },
  };
  vm.runInNewContext(source, { browser: api, URL, URLSearchParams, Date }, { filename: 'background.js' });
  const popupSender = { id: extensionId, url: popupUrl };
  return {
    state,
    send: (message, sender = popupSender) => listener(message, sender),
    register: () => listener({ type: 'REGISTER_PENDING', token, port }, { id: extensionId, frameId: 0, tab: { ...state.tab } }),
  };
}

test('Safari only transports complete cookies and actual UA after an explicit popup action', async () => {
  const flow = harness();
  assert.equal(flow.state.cookieReads, 0);
  assert.equal((await flow.register()).ok, true);
  assert.equal(flow.state.cookieReads, 0);
  assert.equal(flow.state.contextReads, 0);
  flow.state.tab.url = 'https://glados.cloud/console/checkin';
  assert.equal((await flow.send({ type: 'GET_STATUS' })).pending, true);
  assert.equal(flow.state.cookieReads, 0);
  assert.equal((await flow.send({ type: 'MANUAL_CAPTURE' })).ok, true);
  assert.equal(flow.state.cookieReads, 5);
  assert.deepEqual(flow.state.cookiePaths, [...MANUAL_SESSION_PATHS]);
  assert.equal(flow.state.contextReads, 2);
  assert.equal(flow.state.nativeSends.length, 1);
  const sent = flow.state.nativeSends[0];
  assert.equal(sent.cookies.length, 5);
  assert.equal(sent.userAgent, flow.state.userAgent);
  assert.equal(sent.pageUrl.includes(token), false);
  assert.deepEqual(flow.state.store, {});
  const captured = flow.state.capture;
  const verified = await verifyCookie(captured.host, captured.cookieHeader, captured.userAgent, captured.parts, {
    fetchImpl: async () => ({ ok: true, text: async () => JSON.stringify({ code: 0, userId: 4242, email: 'manual@example.invalid', leftDays: 12 }) }),
  });
  const result = buildCapturePayload(captured, verified, 'Synthetic Safari');
  assert.equal(result.cookieHeader.split('; ').length, 5);
  assert.equal(result.email, 'manual@example.invalid');
  assert.equal(result.userAgent, flow.state.userAgent);
  assert.equal(JSON.stringify(result).includes(token), false);
});

test('content scripts and foreign extension senders cannot trigger manual capture', async () => {
  const flow = harness();
  await flow.register();
  for (const sender of [
    { id: extensionId, tab: flow.state.tab, frameId: 0 },
    { id: 'foreign-extension', url: popupUrl },
    { id: extensionId, url: 'https://glados.cloud/' },
  ]) assert.equal((await flow.send({ type: 'MANUAL_CAPTURE' }, sender)).ok, false);
  assert.equal(flow.state.cookieReads, 0);
  assert.equal(flow.state.nativeSends.length, 0);
});

test('origin and tab changes stop before reading cookies', async () => {
  for (const tab of [{ id: 7, url: 'https://railgun.info/console/checkin' }, { id: 8, url: 'https://glados.cloud/console/checkin' }]) {
    const flow = harness();
    await flow.register();
    flow.state.tab = tab;
    assert.equal((await flow.send({ type: 'MANUAL_CAPTURE' })).reason, 'origin_mismatch');
    assert.equal(flow.state.cookieReads, 0);
  }
});

test('missing actual UA, context changes, and ambiguous cookies stop transport', async () => {
  for (const setup of [
    (state) => { state.userAgent = ''; },
    (state) => { state.contextOrigin = 'https://railgun.info'; },
    (state) => { state.cookies.push({ ...state.cookies[0], value: 'different-synthetic-session' }); },
  ]) {
    const flow = harness();
    await flow.register();
    setup(flow.state);
    assert.equal((await flow.send({ type: 'MANUAL_CAPTURE' })).ok, false);
    assert.equal(flow.state.nativeSends.length, 0);
  }
});

test('Safari refuses endpoint-only cookies before Native Messaging and never combines endpoint headers', async () => {
  for (const path of MANUAL_SESSION_PATHS) {
    const flow = harness();
    await flow.register();
    flow.state.cookies.push({ name: 'endpoint_only', value: 'synthetic-private', domain: '.glados.cloud', path });
    const result = await flow.send({ type: 'MANUAL_CAPTURE' });
    assert.equal(result.ok, false);
    assert.equal(result.reason, 'cookie_scope_mismatch');
    assert.equal(flow.state.nativeSends.length, 0);
    assert.equal(flow.state.capture, null);
  }
});

test('Safari retains common API cookies and excludes console-only cookies', async () => {
  const flow = harness();
  await flow.register();
  flow.state.cookies.push({ name: 'api_common', value: 'synthetic', domain: '.glados.cloud', path: '/api/user' });
  flow.state.cookies.push({ name: 'console_only', value: 'excluded', domain: '.glados.cloud', path: '/console' });
  assert.equal((await flow.send({ type: 'MANUAL_CAPTURE' })).ok, true);
  assert.equal(flow.state.capture.cookieHeader.includes('api_common=synthetic'), true);
  assert.equal(flow.state.capture.cookieHeader.includes('console_only'), false);
});
