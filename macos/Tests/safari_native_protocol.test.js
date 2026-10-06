'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const {
  isValidBridgePort,
  isValidBridgeToken,
  normalizeSafariNativeCapture,
} = require('../Resources/safari_native_protocol');

const token = 'a'.repeat(64);
const port = 42317;

function payload() {
  return {
    type: 'CAPTURE_ACCOUNT', token, port, host: 'glados.cloud',
    pageUrl: 'https://glados.cloud/console/checkin', userAgent: 'SyntheticSafari/1.0',
    cookies: [
      { name: 'gld:sess', value: 'synthetic-opaque-session', domain: '.glados.cloud', path: '/' },
      { name: 'gld:sess.sig', value: 'synthetic-signature', domain: '.glados.cloud', path: '/' },
      { name: 'site_setting', value: 'synthetic-site-cookie', domain: '.glados.cloud', path: '/' },
    ],
  };
}

test('accept only a 256-bit hexadecimal token and valid user port', () => {
  assert.equal(isValidBridgeToken(token), true);
  assert.equal(isValidBridgeToken('short'), false);
  assert.equal(isValidBridgePort(port), true);
  assert.equal(isValidBridgePort(80), false);
  assert.equal(isValidBridgePort(70000), false);
  assert.equal(isValidBridgePort(42317.5), false);
  assert.equal(isValidBridgePort([42317]), false);
});

test('normalize an allowed native Safari capture', () => {
  const result = normalizeSafariNativeCapture(payload(), token, port, 'glados.cloud');
  assert.equal(result.parts.sessionKind, 'gld');
  assert.equal(result.cookieHeader, 'gld:sess=synthetic-opaque-session; gld:sess.sig=synthetic-signature; site_setting=synthetic-site-cookie');
  assert.equal(result.userAgent, 'SyntheticSafari/1.0');
  assert.equal(Number.isFinite(Date.parse(result.capturedAt)), true);
  assert.equal(result.token, undefined);
});

test('reject wrong token, port, host, and missing cookie', () => {
  const base = payload();
  assert.throws(() => normalizeSafariNativeCapture({ ...base, token: 'b'.repeat(64) }, token, port, 'glados.cloud'), /令牌/);
  assert.throws(() => normalizeSafariNativeCapture({ ...base, port: port + 1 }, token, port, 'glados.cloud'), /端口/);
  assert.throws(() => normalizeSafariNativeCapture({ ...base, host: 'evil.example', pageUrl: 'https://evil.example/' }, token, port, 'glados.cloud'), /白名单/);
  assert.throws(() => normalizeSafariNativeCapture({ ...base, cookies: base.cookies.slice(0, 1) }, token, port, 'glados.cloud'), /gld:sess\.sig/);
  assert.throws(() => normalizeSafariNativeCapture({ ...base, userAgent: '' }, token, port, 'glados.cloud'), /User-Agent/);
  assert.throws(() => normalizeSafariNativeCapture(base, token, port, 'railgun.info'), /原域名/);
  assert.throws(() => normalizeSafariNativeCapture(base, token, port), /白名单/);
  assert.throws(() => normalizeSafariNativeCapture({ ...base, host: 'glados.network', pageUrl: 'https://glados.network/' }, token, port, 'glados.network'), /白名单/);
});

test('strip handshake data from native capture output', () => {
  const base = payload();
  base.pageUrl += `?private=value#glados-assistant=${token}&port=${port}`;
  const result = normalizeSafariNativeCapture(base, token, port, 'glados.cloud');
  assert.equal(result.pageUrl, 'https://glados.cloud/console/checkin');
  assert.equal(JSON.stringify(result).includes(token), false);
});
