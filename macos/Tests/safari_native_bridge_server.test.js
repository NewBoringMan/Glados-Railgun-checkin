'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { PassThrough } = require('node:stream');
const { createCaptureReceiver, readBridgeConfig, validateBridgeConfig } = require('../Resources/safari_native_bridge_server');

const token = 'a'.repeat(64);
const host = 'glados.cloud';
const port = 42317;
const payload = {
  type: 'CAPTURE_ACCOUNT', token, port, host,
  pageUrl: 'https://glados.cloud/console/checkin', userAgent: 'SyntheticSafari/1.0',
  cookies: [
    { name: 'gld:sess', value: 'synthetic-session', domain: '.glados.cloud', path: '/' },
    { name: 'gld:sess.sig', value: 'synthetic-signature', domain: '.glados.cloud', path: '/' },
  ],
};

test('bridge accepts only one valid pinned capture without a network listener', () => {
  const receive = createCaptureReceiver({ token, host }, () => port);
  assert.equal(receive(JSON.stringify({ ...payload, token: 'b'.repeat(64) })).response.ok, false);
  assert.equal(receive(JSON.stringify({ ...payload, host: 'railgun.info', pageUrl: 'https://railgun.info/' })).response.ok, false);
  const accepted = receive(JSON.stringify(payload));
  assert.deepEqual(accepted.response, { ok: true });
  assert.equal(accepted.capture.cookieHeader, 'gld:sess=synthetic-session; gld:sess.sig=synthetic-signature');
  assert.equal(accepted.capture.userAgent, payload.userAgent);
  assert.equal(JSON.stringify(accepted.capture).includes(token), false);
  assert.deepEqual(receive(JSON.stringify(payload)), { response: { ok: false, reason: 'already_captured' } });
});

test('bridge rejects malformed and oversized data without echoing secrets', () => {
  const receive = createCaptureReceiver({ token, host }, () => port);
  for (const line of [token, '{"cookies":"synthetic-secret"}', 'x'.repeat(64 * 1024 + 1)]) {
    const result = receive(line);
    assert.equal(result.response.ok, false);
    assert.equal(result.capture, undefined);
    assert.equal(JSON.stringify(result).includes(token), false);
    assert.equal(JSON.stringify(result).includes('synthetic-secret'), false);
  }
});

test('bridge receives initialization on a private stream, with no token argv', async () => {
  const input = new PassThrough();
  const result = readBridgeConfig(input);
  input.end(`${JSON.stringify({ token, host })}\n`);
  assert.deepEqual(await result, { token, host });
  assert.throws(() => validateBridgeConfig({ token, host: 'glados.network' }), /INVALID_BRIDGE_CONFIG/);
  const invalidInput = new PassThrough();
  const invalid = readBridgeConfig(invalidInput);
  invalidInput.end('invalid\n');
  await assert.rejects(invalid, /INVALID_BRIDGE_CONFIG/);
});
