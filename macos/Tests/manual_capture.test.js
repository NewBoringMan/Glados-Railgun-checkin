'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { selectSessionCookies, composeCookieHeader, accountKeyFromSecretName, accountSecretNameFromUserId } = require('../Resources/core');
const { assertSameBrowserContext, buildCapturePayload, expectedCaptureIdentity, normalizeBrowserContext, verifyCookie } = require('../Resources/capture_account');

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

test('identity conflicts, points-only status, and API rejection stop capture after one request', async () => {
  const input = acquired();
  for (const payload of [
    { code: 0, points: 50 },
    { code: -2, data: { userId: 4242, email: 'manual@example.invalid' } },
    { code: 0, data: { userId: 4343, email: 'other@example.invalid' } },
  ]) {
    let calls = 0;
    await assert.rejects(verifyCookie(input.host, input.cookieHeader, input.userAgent, input.parts, {
      expectedAccountKey: accountKeyFromSecretName(accountSecretNameFromUserId(4242)),
      fetchImpl: async () => { calls += 1; return { ok: true, text: async () => JSON.stringify(payload) }; },
    }));
    assert.equal(calls, 1);
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
