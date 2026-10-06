'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { selectSessionCookies, composeCookieHeader, accountKeyFromSecretName, accountSecretNameFromUserId } = require('../Resources/core');
const { assertSameBrowserContext, browserProfileDirectory, buildCapturePayload, captureFailureCode, expectedCaptureIdentity, normalizeBrowserContext, verifyCookie } = require('../Resources/capture_account');

function acquired() {
  const parts = selectSessionCookies([
    { name: 'gld:sess', value: 'synthetic-opaque-session', domain: 'glados.cloud', path: '/', hostOnly: true },
    { name: 'gld:sess.sig', value: 'synthetic-signature', domain: 'glados.cloud', path: '/', hostOnly: true },
    { name: 'site_setting', value: 'synthetic-site-cookie', domain: 'glados.cloud', path: '/', hostOnly: true },
  ], 'glados.cloud');
  return { parts, cookieHeader: composeCookieHeader(parts), host: 'glados.cloud', pageUrl: 'https://glados.cloud/console/checkin', userAgent: 'SyntheticBrowser/1.0', capturedAt: '2026-10-06T00:00:00.000Z' };
}

test('manual capture uses the actual UA and one pinned origin, ignores Set-Cookie, and returns the original complete string', async () => {
  const input = acquired();
  const requests = [];
  const fetchImpl = async (url, options) => {
    requests.push({ url, options });
    return {
      ok: true, status: 200,
      headers: { get() { throw new Error('response Cookie must not be read or saved'); } },
      text: async () => JSON.stringify({ code: 0, data: { userId: 4242, email: 'manual@example.invalid' } }),
    };
  };
  const verified = await verifyCookie(input.host, input.cookieHeader, input.userAgent, input.parts, { fetchImpl });
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, 'https://glados.cloud/api/user/status');
  assert.equal(requests[0].options.headers['user-agent'], input.userAgent);
  assert.equal(requests[0].options.headers.cookie, input.cookieHeader);
  assert.equal(requests[0].options.redirect, 'error');
  assert.equal(requests[0].options.method, 'GET');
  const result = buildCapturePayload(input, verified, 'Synthetic Browser');
  assert.equal(result.cookieHeader, input.cookieHeader);
  assert.equal(typeof result.cookieHeader, 'string');
  assert.equal(result.userAgent, input.userAgent);
  assert.equal(result.capturedAt, input.capturedAt);
  assert.equal(result.daysLeft, null);
  assert.equal(result.accountKey, accountKeyFromSecretName(accountSecretNameFromUserId(4242)));
  assert.equal(result.session, undefined);
  assert.equal(result.signature, undefined);
});

test('identity conflicts and API rejection stop capture without a session request', async () => {
  const input = acquired();
  for (const payload of [
    { code: -2, data: { userId: 4242, email: 'manual@example.invalid' } },
    { code: 0, data: { userId: 4343, email: 'other@example.invalid' } },
    { code: 0, userId: 4242, data: { userId: 4343 } },
    { code: 0, userId: 4343 },
  ]) {
    let calls = 0;
    await assert.rejects(verifyCookie(input.host, input.cookieHeader, input.userAgent, input.parts, {
      expectedAccountKey: accountKeyFromSecretName(accountSecretNameFromUserId(4242)),
      fetchImpl: async () => { calls += 1; return { ok: true, text: async () => JSON.stringify(payload) }; },
    }));
    assert.equal(calls, 1);
  }
});

test('the known session endpoint may supply missing identity using the same captured session', async () => {
  const input = acquired();
  const requests = [];
  const verified = await verifyCookie(input.host, input.cookieHeader, input.userAgent, input.parts, {
    fetchImpl: async (url, options) => {
      requests.push({ url, options });
      const payload = url.endsWith('/status')
        ? { code: 0, data: { email: 'manual@example.invalid', leftDays: '12.8' } }
        : { code: 0, data: { userId: 4242, email: 'manual@example.invalid' } };
      return {
        ok: true,
        headers: { get() { throw new Error('must not read or save Set-Cookie'); } },
        text: async () => JSON.stringify(payload),
      };
    },
  });
  assert.deepEqual(requests.map((request) => request.url), ['https://glados.cloud/api/user/status', 'https://glados.cloud/api/user/session']);
  for (const request of requests) {
    assert.equal(request.options.headers.cookie, input.cookieHeader);
    assert.equal(request.options.headers['user-agent'], input.userAgent);
    assert.equal(request.options.redirect, 'error');
    assert.equal(request.options.method, 'GET');
  }
  assert.equal(verified.userId, '4242');
  assert.equal(verified.leftDays, 12);
  assert.equal(verified.accountEmail, 'manual@example.invalid');
});

test('session fallback cannot turn points, guessed ids, rejection, or conflicting account data into identity', async () => {
  const input = acquired();
  const statuses = [
    [{ code: 0, points: 50 }, { code: 0, points: 50 }],
    [{ code: 0, email: 'manual@example.invalid' }, { code: 0, id: 4242 }],
    [{ code: 0, email: 'manual@example.invalid' }, { userId: 4242 }],
    [{ code: 0, email: 'manual@example.invalid' }, { code: -2, userId: 4242 }],
    [{ code: 0, email: 'manual@example.invalid' }, { code: 0, data: { userId: 4242, email: 'other@example.invalid' } }],
    [{ code: 0, userId: 4242 }, { code: 0, data: { userId: 4343, email: 'manual@example.invalid' } }],
  ];
  for (const payloads of statuses) {
    let calls = 0;
    await assert.rejects(verifyCookie(input.host, input.cookieHeader, input.userAgent, input.parts, {
      fetchImpl: async () => ({ ok: true, text: async () => JSON.stringify(payloads[calls++]) }),
    }));
    assert.equal(calls, 2);
  }
});

test('origin and capture context mismatches fail before any request', async () => {
  const input = acquired();
  const fetchImpl = async () => { assert.fail('must not make a request'); };
  await assert.rejects(verifyCookie(input.host, input.cookieHeader, input.userAgent, input.parts, { expectedHost: 'railgun.info', fetchImpl }));
  await assert.rejects(verifyCookie(input.host, input.cookieHeader, '', input.parts, { fetchImpl }));
  await assert.rejects(verifyCookie(input.host, input.cookieHeader + '; extra=value', input.userAgent, input.parts, { fetchImpl }));
  const before = normalizeBrowserContext({ pageUrl: input.pageUrl, userAgent: input.userAgent }, input.host);
  assert.throws(() => normalizeBrowserContext({ pageUrl: 'https://railgun.info/', userAgent: input.userAgent }, input.host));
  assert.throws(() => assertSameBrowserContext(before, { ...before, userAgent: 'AnotherSyntheticBrowser/1.0' }));
  assert.throws(() => expectedCaptureIdentity({ GLADOS_EXPECTED_ACCOUNT_KEY: 'invalid' }));
  assert.throws(() => expectedCaptureIdentity({ GLADOS_EXPECTED_HOST: 'glados.cloud.evil.example' }));
});

test('existing account captures reuse the native account-specific browser profile path', () => {
  const key = 'AABBCCDDEEFF0011';
  const profile = browserProfileDirectory('edge', { GLADOS_EXPECTED_ACCOUNT_KEY: key });
  assert.equal(profile.endsWith(`/BrowserProfiles/accounts/${key}/edge`), true);
  assert.equal(browserProfileDirectory('edge', {}).endsWith('/BrowserProfiles/edge'), true);
  assert.throws(() => browserProfileDirectory('../edge', {}));
  assert.throws(() => browserProfileDirectory('edge', { GLADOS_EXPECTED_ACCOUNT_KEY: '../invalid' }));
});

test('capture failures expose only fixed safe codes to the native app', () => {
  assert.equal(captureFailureCode({ code: 'GLADOS_BROWSER_CONNECTION', message: 'synthetic-secret' }), 'browser_connection');
  assert.equal(captureFailureCode({ code: 'INCOMPLETE_API_IDENTITY' }), 'missing_identity');
  assert.equal(captureFailureCode(new Error('新旧会话的账号身份不一致，已停止保存。')), 'identity_mismatch');
  assert.equal(captureFailureCode(new Error('当前登录账号与要更新的账号不一致，已停止保存。')), 'identity_mismatch');
  assert.equal(captureFailureCode(new Error('状态接口拒绝会话认证，请手动重新登录并读取。')), 'verification_required');
  assert.equal(captureFailureCode(new Error('应用内嵌的 Safari 扩展组件缺失。')), 'safari_extension_unavailable');
  assert.equal(captureFailureCode(new Error('synthetic-secret / unexpected transport response')), 'invalid_capture');
});
