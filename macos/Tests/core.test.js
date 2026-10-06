'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const {
  ACCOUNT_SECRET_PREFIX,
  accountKeyFromSecretName,
  accountSecretNameFromSession,
  accountSecretNameFromUserId,
  buildManagedWorkflow,
  composeCookieHeader,
  extractGladosUserId,
  isAllowedHost,
  isManagedAccountSecretName,
  isSafeBranch,
  isSafeRepo,
  isSafeSecretName,
  isSafeWorkflow,
  normalizeStatusIdentity,
  normalizeUserAgent,
  parseGitHubRepo,
  redact,
  selectSessionCookies,
  summarizeRunLog,
  validatePinnedPage,
} = require('../Resources/core');

function sessionCookie(payload) {
  return Buffer.from(JSON.stringify(payload), 'utf8').toString('base64').replace(/=+$/g, '');
}

test('allow only GLaDOS and Railgun hosts', () => {
  assert.equal(isAllowedHost('glados.cloud'), true);
  assert.equal(isAllowedHost('sub.glados.cloud'), false);
  assert.equal(isAllowedHost('railgun.info'), true);
  assert.equal(isAllowedHost('evil.example'), false);
  for (const host of ['glados.network', 'glados.rocks', 'glados.one', 'glados.space', 'glados.vip', 'glados-facility.com']) assert.equal(isAllowedHost(host), true);
  for (const host of ['glados.cloud.evil.example', 'glados.cloud.', '.glados.cloud', 'https://glados.cloud']) assert.equal(isAllowedHost(host), false);
});

test('select and compose exact cookies', () => {
  const parts = selectSessionCookies([
    { name: 'koa:sess', value: 'session-value', domain: '.glados.cloud' },
    { name: 'koa:sess.sig', value: 'signature-value', domain: '.glados.cloud' },
    { name: 'other', value: 'preserve', domain: '.glados.cloud' },
  ], 'glados.cloud');
  assert.equal(parts.session, 'session-value');
  assert.equal(parts.signature, 'signature-value');
  assert.equal(parts.sessionKind, 'koa');
  assert.equal(composeCookieHeader(parts), 'koa:sess=session-value; koa:sess.sig=signature-value; other=preserve');
});

test('reject missing session cookie', () => {
  assert.throws(() => selectSessionCookies([
    { name: 'koa:sess', value: 'session-value', domain: '.glados.cloud' },
  ], 'glados.cloud'), /koa:sess\.sig/);
});

function gldCookies(extra = []) {
  return [
    { name: 'gld:sess', value: 'opaque-synthetic-session', domain: '.glados.cloud', path: '/' },
    { name: 'gld:sess.sig', value: 'synthetic-signature', domain: '.glados.cloud', path: '/' },
    ...extra,
  ];
}

test('preserve opaque new session, compatible old cookies, and applicable site cookies', () => {
  const old = sessionCookie({ userId: 4242, _expire: 1 });
  const parts = selectSessionCookies(gldCookies([
    { name: 'koa:sess', value: old, domain: '.glados.cloud' },
    { name: 'koa:sess.sig', value: 'synthetic-legacy-signature', domain: '.glados.cloud' },
    { name: 'site_setting', value: 'synthetic-value', domain: '.glados.cloud', path: '/' },
  ]), 'glados.cloud');
  assert.equal(parts.sessionKind, 'gld');
  assert.equal(parts.cookies.length, 5);
  assert.match(composeCookieHeader(parts), /^gld:sess=opaque-synthetic-session; gld:sess.sig=synthetic-signature;/);
  const verified = normalizeStatusIdentity({ code: 0, data: { userId: 4242, email: 'manual@example.invalid', leftDays: '12.8' } }, parts);
  assert.equal(verified.secretName, accountSecretNameFromUserId(4242));
  assert.equal(verified.leftDays, 12);
});

test('only include cookies applicable to the pinned status request', () => {
  const parts = selectSessionCookies(gldCookies([
    { name: 'other_origin', value: 'exclude', domain: '.railgun.info' },
    { name: 'child_origin', value: 'exclude', domain: 'sub.glados.cloud' },
    { name: 'other_path', value: 'exclude', domain: '.glados.cloud', path: '/console' },
    { name: 'prefix_path', value: 'exclude', domain: '.glados.cloud', path: '/api/user/stat' },
    { name: 'expired', value: 'exclude', domain: '.glados.cloud', expirationDate: 1 },
    { name: 'partitioned', value: 'exclude', domain: '.glados.cloud', partitionKey: { topLevelSite: 'https://example.invalid' } },
    { name: 'api_path', value: 'keep', domain: 'glados.cloud', hostOnly: true, path: '/api/user' },
  ]), 'glados.cloud');
  assert.deepEqual(parts.cookies.map((cookie) => cookie.name), ['gld:sess', 'gld:sess.sig', 'api_path']);
});

test('reject incomplete new sessions, duplicate names, and header injection', () => {
  assert.throws(() => selectSessionCookies(gldCookies().slice(0, 1), 'glados.cloud'), /gld:sess.sig/);
  assert.throws(() => selectSessionCookies(gldCookies([{ name: 'gld:sess', value: 'second', domain: '.glados.cloud' }]), 'glados.cloud'), /重名/);
  for (const value of ['bad; injected=value', 'bad\r\nX-Test: injected', 'bad value']) {
    assert.throws(() => selectSessionCookies(gldCookies([{ name: 'auxiliary', value, domain: '.glados.cloud' }]), 'glados.cloud'), /格式/);
  }
  assert.throws(() => composeCookieHeader({ cookies: [{ name: 'bad\nname', value: 'value' }] }), /名称/);
});

test('expired old browser cookie does not discard a complete new session', () => {
  const parts = selectSessionCookies(gldCookies([
    { name: 'koa:sess', value: sessionCookie({ userId: 4242, _expire: 1 }), domain: '.glados.cloud', expirationDate: 1 },
    { name: 'koa:sess.sig', value: 'legacy-signature', domain: '.glados.cloud' },
  ]), 'glados.cloud');
  assert.equal(parts.sessionKind, 'gld');
  assert.equal(parts.session, '');
  assert.equal(parts.signature, 'legacy-signature');
});

test('status schemas verify explicit identity without requiring leftDays', () => {
  for (const payload of [
    { userId: 4242, email: 'manual@example.invalid' },
    { code: 0, data: { userId: 4242, user: { email: 'manual@example.invalid' } } },
    { code: 0, data: { user: { id: 4242, email: 'manual@example.invalid' } } },
  ]) {
    const result = normalizeStatusIdentity(payload);
    assert.equal(result.secretName, accountSecretNameFromUserId(4242));
    assert.equal(result.accountEmail, 'manual@example.invalid');
    assert.equal(result.leftDays, null);
    assert.equal(result.actualCheckinObserved, undefined);
  }
});

test('missing, rejected, and conflicting API identities cannot be saved', () => {
  for (const payload of [
    { code: 0, points: 30 },
    { code: 0, userId: 4242, points: 30 },
    { code: 0, email: 'manual@example.invalid', id: 4242 },
    { code: -2, userId: 4242, email: 'manual@example.invalid' },
    { code: 0, userId: 4242, email: 'manual@example.invalid', data: { userId: 4343 } },
    { code: 0, userId: 4242, email: 'manual@example.invalid', data: { email: 'different@example.invalid' } },
  ]) assert.throws(() => normalizeStatusIdentity(payload));
  const payload = { code: 0, data: { userId: 4242, email: 'manual@example.invalid' } };
  assert.throws(() => normalizeStatusIdentity(payload, { session: sessionCookie({ userId: 4343 }) }), /身份不一致/);
  assert.throws(() => normalizeStatusIdentity(payload, null, accountKeyFromSecretName(accountSecretNameFromUserId(4343))), /要更新的账号/);
  const expectedKey = accountKeyFromSecretName(accountSecretNameFromUserId(4242));
  assert.equal(normalizeStatusIdentity(payload, null, expectedKey).accountKey, expectedKey);
});

test('actual browser context is validated without inventing a UA or leaking a URL token', () => {
  assert.equal(normalizeUserAgent('SyntheticBrowser/1.0'), 'SyntheticBrowser/1.0');
  for (const value of ['', null, 'SyntheticBrowser\r\nInjected: value']) assert.throws(() => normalizeUserAgent(value));
  assert.deepEqual(validatePinnedPage('https://glados.cloud/console/checkin?private=value#glados-assistant=synthetic', 'glados.cloud'), {
    host: 'glados.cloud', origin: 'https://glados.cloud', pageUrl: 'https://glados.cloud/console/checkin',
  });
  for (const value of ['http://glados.cloud/', 'https://glados.cloud:8443/', 'https://user:pass@glados.cloud/', 'https://railgun.info/']) assert.throws(() => validatePinnedPage(value, 'glados.cloud'));
});

test('parse GitHub repository URL', () => {
  assert.equal(parseGitHubRepo('https://github.com/NewBoringMan/Glados-Railgun-checkin/actions'), 'NewBoringMan/Glados-Railgun-checkin');
  assert.equal(parseGitHubRepo('https://example.com/a/b'), null);
});

test('validate GitHub parameters', () => {
  assert.equal(isSafeRepo('NewBoringMan/Glados-Railgun-checkin'), true);
  assert.equal(isSafeRepo('bad value'), false);
  assert.equal(isSafeBranch('master'), true);
  assert.equal(isSafeBranch('../main'), false);
  assert.equal(isSafeWorkflow('gladosAccounts.yml'), true);
  assert.equal(isSafeSecretName('GLADOS_ACCOUNT_AABBCCDDEEFF0011'), true);
});

test('extract stable user id and generate deterministic account secret', () => {
  const session = sessionCookie({ userId: 734713, _expire: 1809017573320 });
  assert.equal(extractGladosUserId(session), '734713');
  const first = accountSecretNameFromSession(session);
  const second = accountSecretNameFromSession(sessionCookie({ userId: 734713, _expire: 1900000000000 }));
  assert.match(first, new RegExp(`^${ACCOUNT_SECRET_PREFIX}[A-F0-9]{16}$`));
  assert.equal(first, second);
  assert.equal(isManagedAccountSecretName(first), true);
  assert.equal(accountKeyFromSecretName(first), first.slice(ACCOUNT_SECRET_PREFIX.length));
});

test('reject sessions without stable user id', () => {
  assert.throws(() => accountSecretNameFromSession(sessionCookie({ _expire: 123 })), /稳定账号 ID/);
});

test('build independent multi-account workflow', () => {
  const secrets = [
    'GLADOS_ACCOUNT_AABBCCDDEEFF0011',
    'GLADOS_ACCOUNT_1122334455667788',
    'GLADOS_COOKIES',
  ];
  const yaml = buildManagedWorkflow(secrets);
  assert.match(yaml, /name: GLaDOS Multi-Account Check-in/);
  assert.match(yaml, /account_aabbccddeeff0011:/);
  assert.match(yaml, /account_1122334455667788:/);
  assert.match(yaml, /secrets\.GLADOS_ACCOUNT_AABBCCDDEEFF0011/);
  assert.match(yaml, /secrets\.GLADOS_ACCOUNT_1122334455667788/);
  assert.doesNotMatch(yaml, /secrets\.GLADOS_COOKIES/);
  assert.match(yaml, /inputs\.account == 'AABBCCDDEEFF0011'/);
  assert.match(yaml, /cron: '0 5,17 \* \* \*'/);
  assert.match(yaml, /timezone: 'Asia\/Taipei'/);
  assert.match(yaml, /GLADOS_AUTO_EXCHANGE: 'false'/);
});

test('redact cookie and GitHub token', () => {
  const syntheticToken = 'ghp_' + 'a'.repeat(36);
  const result = redact(`koa:sess=abc; koa:sess.sig=def; token=${syntheticToken}`);
  assert.equal(result.includes('abc'), false);
  assert.equal(result.includes('def'), false);
  assert.equal(result.includes('ghp_'), false);
});

test('summarize run log', () => {
  const result = summarizeRunLog('共加载了 1 个 Cookie 用于签到。\n签到成功');
  assert.equal(result.loadedCookies, 1);
  assert.equal(result.actualCheckinObserved, true);
  assert.equal(result.explicitFailure, false);
});
