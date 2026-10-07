'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const {
  appleScript, captureFailureCode, captureFailureLine, capturePreparedBrowser,
  confirm, handleUnhandledCaptureError, verifyCookie,
} = require('../Resources/capture_account');
const { composeCookieHeader, selectReusableSessionCookies } = require('../Resources/core');

const PRIVATE_FIXTURE = 'synthetic-private-value@example.invalid; gld:sess=synthetic-only';
const browser = { id: 'edge', label: 'Synthetic Edge', loginPersistence: 'profile' };
const session = { kind: 'cdp', port: 43123, reused: true };

function fixture(overrides = {}) {
  const calls = [];
  const stages = [];
  const parts = selectReusableSessionCookies([
    { name: 'gld:sess', value: 'synthetic-session', domain: 'glados.cloud', path: '/', hostOnly: true },
    { name: 'gld:sess.sig', value: 'synthetic-signature', domain: 'glados.cloud', path: '/', hostOnly: true },
  ], 'glados.cloud');
  const acquired = {
    host: 'glados.cloud', pageUrl: 'https://glados.cloud/console/subscription',
    userAgent: 'SyntheticBrowser/1.0', parts, cookieHeader: composeCookieHeader(parts),
  };
  const verified = { accountKey: 'AABBCCDDEEFF0011', accountEmail: 'fixture@example.invalid' };
  const payload = { fixture: true };
  const options = {
    onStage(stage) { stages.push(stage); },
    confirm(message, label) {
      calls.push('confirm');
      assert.equal(label, '读取账号');
      assert.match(message, /已连接 Synthetic Edge 中原来打开的专用/);
      return true;
    },
    async acquireCookie(actualSession) {
      calls.push('acquire'); assert.equal(actualSession, session); return acquired;
    },
    onAcquired(value) {
      calls.push('register-cleanup'); assert.equal(value, acquired);
    },
    expectedCaptureIdentity() {
      calls.push('expected'); return { accountKey: verified.accountKey, host: acquired.host };
    },
    async verifyCookie(host, header, agent, actualParts, expected) {
      calls.push('verify');
      assert.equal(host, acquired.host); assert.equal(header, acquired.cookieHeader);
      assert.equal(agent, acquired.userAgent); assert.equal(actualParts, parts);
      assert.deepEqual(expected, { expectedAccountKey: verified.accountKey, expectedHost: acquired.host });
      return verified;
    },
    buildCapturePayload(value, identity, label) {
      calls.push('build'); assert.equal(value, acquired); assert.equal(identity, verified);
      assert.equal(label, browser.label); return payload;
    },
    ...overrides,
  };
  return { calls, stages, acquired, verified, payload, options };
}

test('the production capture sequence confirms, reads, registers cleanup, verifies and builds once', async () => {
  const flow = fixture();
  assert.deepEqual(await capturePreparedBrowser(browser, session, flow.options), {
    acquired: flow.acquired, verified: flow.verified, capturePayload: flow.payload,
  });
  assert.deepEqual(flow.calls, ['confirm', 'acquire', 'register-cleanup', 'expected', 'verify', 'build']);
  assert.deepEqual(flow.stages, ['waiting_confirmation', 'reading_browser', 'verifying_session', 'validating_capture']);
});

test('explicit confirmation cancellation performs no browser read, identity request or result build', async () => {
  const flow = fixture({ confirm: () => false });
  assert.equal(await capturePreparedBrowser(browser, session, flow.options), null);
  assert.deepEqual(flow.calls, []);
  assert.deepEqual(flow.stages, ['waiting_confirmation']);
});

for (const [dependency, stage, code, calls] of [
  ['confirm', 'waiting_confirmation', 'dialog_failed', []],
  ['acquireCookie', 'reading_browser', 'page_read_failed', ['confirm']],
  ['verifyCookie', 'verifying_session', 'verification_failed', ['confirm', 'acquire', 'register-cleanup', 'expected']],
  ['buildCapturePayload', 'validating_capture', 'invalid_capture', ['confirm', 'acquire', 'register-cleanup', 'expected', 'verify']],
]) {
  test(`${dependency} failure stops later production steps and emits only its fixed code`, async () => {
    const failure = new Error(PRIVATE_FIXTURE);
    const flow = fixture({ [dependency]: () => { throw failure; } });
    await assert.rejects(capturePreparedBrowser(browser, session, flow.options), (error) => {
      assert.equal(error, failure);
      assert.equal(flow.stages.at(-1), stage);
      assert.equal(captureFailureLine(error, stage), `GLADOS_CAPTURE_ERROR_CODE=${code}\n`);
      return true;
    });
    assert.deepEqual(flow.calls, calls);
  });
}

test('a nested page-selection dialog failure retains its dialog code during browser reading', async () => {
  const failure = Object.assign(new Error(PRIVATE_FIXTURE), { code: 'GLADOS_CAPTURE_DIALOG_FAILED' });
  const flow = fixture({ acquireCookie: () => { throw failure; } });
  await assert.rejects(capturePreparedBrowser(browser, session, flow.options), (error) => {
    assert.equal(flow.stages.at(-1), 'reading_browser');
    assert.equal(captureFailureCode(error, flow.stages.at(-1)), 'dialog_failed');
    return true;
  });
  assert.deepEqual(flow.calls, ['confirm']);
});

test('known failure codes take precedence over every stage and never expose exception values', () => {
  for (const [code, expected] of [
    ['GLADOS_COOKIE_SCOPE_MISMATCH', 'cookie_scope_mismatch'],
    ['GLADOS_COOKIE_SCOPE_UNAVAILABLE', 'cookie_scope_unavailable'],
    ['INCOMPLETE_API_IDENTITY', 'missing_identity'],
    ['GLADOS_BROWSER_CONNECTION', 'browser_connection'],
    ['GLADOS_CAPTURE_DIALOG_FAILED', 'dialog_failed'],
    ['GLADOS_CAPTURE_CANCELLED', 'cancelled'],
  ]) {
    for (const stage of ['preparing_browser', 'waiting_confirmation', 'reading_browser', 'verifying_session', 'validating_capture', PRIVATE_FIXTURE]) {
      assert.equal(captureFailureLine({ code, message: PRIVATE_FIXTURE }, stage), `GLADOS_CAPTURE_ERROR_CODE=${expected}\n`);
    }
  }
  assert.equal(captureFailureCode(new Error('当前登录账号与要更新的账号不一致。'), 'verifying_session'), 'identity_mismatch');
  assert.equal(captureFailureCode(new Error(PRIVATE_FIXTURE), PRIVATE_FIXTURE), 'invalid_capture');
});

test('AppleScript execution failures become fixed dialog errors without retaining stderr', () => {
  const executions = [
    () => ({ status: 1, stderr: PRIVATE_FIXTURE, stdout: '' }),
    () => ({ status: null, stderr: '', error: new Error(PRIVATE_FIXTURE) }),
    () => { throw new Error(PRIVATE_FIXTURE); },
  ];
  for (const execute of executions) {
    assert.throws(() => appleScript(['display dialog "fixture"'], [], execute), (error) => {
      assert.equal(error.code, 'GLADOS_CAPTURE_DIALOG_FAILED');
      assert.equal(error.message.includes(PRIVATE_FIXTURE), false);
      return true;
    });
  }
});

test('dialog cancellation remains cancellation while unexpected dialog output is an error', () => {
  const cancelled = appleScript(['display dialog "fixture"'], [], () => ({ status: 1, stderr: 'User canceled. (-128)' }));
  assert.equal(cancelled, null);
  assert.equal(confirm('fixture', '读取账号', () => cancelled), false);
  assert.equal(confirm('fixture', '读取账号', () => '取消'), false);
  assert.equal(confirm('fixture', '读取账号', () => '读取账号'), true);
  assert.throws(() => confirm('fixture', '读取账号', () => PRIVATE_FIXTURE), { code: 'GLADOS_CAPTURE_DIALOG_FAILED' });
});

test('non-dialog Safari AppleScript failures retain their surrounding stage', () => {
  assert.throws(() => appleScript(['tell application "Safari" to get URL of current tab of front window'], [],
    () => ({ status: 1, stderr: PRIVATE_FIXTURE })), (error) => {
    assert.equal(captureFailureCode(error, 'reading_browser'), 'page_read_failed');
    assert.equal(error.message.includes(PRIVATE_FIXTURE), false);
    return true;
  });
});

for (const [name, fetchImpl] of [
  ['request rejection', async () => { throw new Error(PRIVATE_FIXTURE); }],
  ['HTTP rejection', async () => ({ ok: false, status: 503, text: async () => PRIVATE_FIXTURE })],
  ['invalid JSON', async () => ({ ok: true, text: async () => PRIVATE_FIXTURE })],
  ['invalid identity fields', async () => ({ ok: true, text: async () => JSON.stringify({ code: 0, data: { userId: {}, email: 'fixture@example.invalid' } }) })],
]) {
  test(`the real identity verifier classifies ${name} at verification and does not build a result`, async () => {
    const flow = fixture({
      verifyCookie: (host, header, agent, parts, options) => verifyCookie(host, header, agent, parts, { ...options, fetchImpl }),
    });
    await assert.rejects(capturePreparedBrowser(browser, session, flow.options), (error) => {
      assert.equal(flow.stages.at(-1), 'verifying_session');
      assert.equal(captureFailureLine(error, flow.stages.at(-1)), 'GLADOS_CAPTURE_ERROR_CODE=verification_failed\n');
      return true;
    });
    assert.deepEqual(flow.calls, ['confirm', 'acquire', 'register-cleanup', 'expected']);
  });
}

test('capture-only global exception handling emits a fixed code and never opens a raw-error dialog', () => {
  for (const stage of ['waiting_confirmation', 'reading_browser', 'verifying_session', PRIVATE_FIXTURE]) {
    const output = [];
    const exits = [];
    handleUnhandledCaptureError(new Error(PRIVATE_FIXTURE), {
      captureOnly: true, stage,
      write: (line) => output.push(line), setExitCode: (code) => exits.push(code),
      alert: () => { throw new Error('capture-only failures must not open a dialog'); },
    });
    assert.deepEqual(exits, [1]);
    assert.equal(output.length, 1);
    assert.match(output[0], /^GLADOS_CAPTURE_ERROR_CODE=(dialog_failed|page_read_failed|verification_failed|invalid_capture)\n$/);
    assert.equal(output[0].includes(PRIVATE_FIXTURE), false);
  }
});

test('standalone global exception handling retains the existing alert route', () => {
  const alerts = [];
  handleUnhandledCaptureError(new Error('synthetic standalone failure'), {
    captureOnly: false, alert: (...args) => alerts.push(args),
    write: () => { throw new Error('standalone handler must use its existing alert'); },
    setExitCode: () => { throw new Error('standalone exit behavior must remain unchanged'); },
  });
  assert.equal(alerts.length, 1);
  assert.equal(alerts[0][1], 'stop');
});
