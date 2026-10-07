'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { Controller, safeMessage } = require('../src/controller.cjs');
const { saveState, loadState } = require('../src/state.cjs');

const accountKey = 'ABCDEF1234567890';
const credential = { cookie: 'gld:sess=private-session-example; gld:sess.sig=private-signature-example', userAgent: 'Real Browser 1.0', origin: 'https://glados.cloud', email: 'person@example.com', accountKey, browser: 'embedded' };
function fixture(overrides = {}) {
  const calls = [];
  let saved;
  const github = {
    whoami: async () => ({ login: 'example' }),
    login: async () => { calls.push('login'); return { login: 'example' }; },
    deploy: async args => { calls.push(['deploy', args.credential]); return { repository: 'example/glados-quick-deploy', accountKey, runId: 42, runUrl: 'https://github.com/example/glados-quick-deploy/actions/runs/42', conclusion: 'success', result: { pointsAdded: 12 } }; },
    refresh: async () => ({ repository: 'example/glados-quick-deploy', runId: 42, conclusion: 'success', result: { outcome: 'already' } }),
    pause: async () => {}, ...overrides.github,
  };
  const controller = new Controller({ github, discoverBrowsers: async () => [{ id: 'embedded', available: true }], captureLogin: async () => ({ ...credential }), save: state => { saved = JSON.parse(JSON.stringify(state)); }, openExternal: async () => {}, ...overrides, github });
  return { controller, calls, saved: () => saved };
}
test('single click performs required GitHub login then verifies an account without exposing credentials', async () => {
  const f = fixture({ github: { whoami: async () => null } });
  await f.controller.initialize();
  const state = await f.controller.action('startDeploy', { browserId: 'embedded' });
  assert.equal(f.calls[0], 'login');
  assert.equal(state.accounts.length, 1);
  assert.equal(state.accounts[0].pointsAdded, 12);
  assert.equal(state.busy, false);
  assert.ok(!JSON.stringify(state).includes('private-session-example'));
  assert.ok(!JSON.stringify(f.saved()).includes('private-signature-example'));
});
test('cancelling during browser login does not deploy or retain the secret', async () => {
  const f = fixture({ captureLogin: ({ signal }) => new Promise((_resolve, reject) => signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true })) });
  await f.controller.initialize();
  const task = f.controller.action('startDeploy', { browserId: 'embedded' });
  await new Promise(resolve => setImmediate(resolve));
  await f.controller.action('cancel');
  const state = await task;
  assert.equal(state.stage, 'cancelled');
  assert.equal(f.calls.length, 0);
  assert.equal(state.busy, false);
});
test('a run queued in GitHub is retained for read-only refresh without re-dispatch', async () => {
  const f = fixture({ github: { deploy: async () => ({ repository: 'example/glados-quick-deploy', accountKey, runId: 42, runUrl: 'https://github.com/example/glados-quick-deploy/actions/runs/42', status: 'queued' }) } });
  await f.controller.initialize();
  await f.controller.action('startDeploy', { browserId: 'embedded' });
  assert.equal(f.controller.state.accounts[0].conclusion, 'queued');
  await f.controller.refreshPending();
  assert.equal(f.controller.state.accounts[0].conclusion, 'already_checked_in');
});
test('unexpected errors are scrubbed before reaching renderer state', async () => {
  const f = fixture({ github: { deploy: async () => { throw new Error('Header gld:sess=private-session-example; failure github_pat_secret123456789'); } } });
  await f.controller.initialize();
  const state = await f.controller.action('startDeploy', { browserId: 'embedded' });
  assert.equal(state.stage, 'error');
  assert.ok(!state.error.includes('private-session-example'));
  assert.ok(!state.error.includes('github_pat_secret'));
  assert.equal(f.controller.secrets.length, 0);
});
test('state file stores a field whitelist and excludes cookies, user agents and authentication codes', () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'qd-state-test-'));
  try {
    saveState(directory, { settings: {}, selectedBrowser: 'embedded', cookie: credential.cookie, authCode: { code: 'ABCD-EFGH' }, accounts: [{ ...credential, repository: 'example/glados-quick-deploy' }] });
    const text = fs.readFileSync(path.join(directory, 'deployment-state.json'), 'utf8');
    assert.ok(!text.includes('private-session-example'));
    assert.ok(!text.includes('Real Browser'));
    assert.ok(!text.includes('ABCD-EFGH'));
    assert.equal(loadState(directory).accounts[0].accountKey, accountKey);
  } finally { fs.rmSync(directory, { recursive: true, force: true }); }
});
test('invalid settings fail before any login, network or browser operation', async () => {
  const f = fixture(); await f.controller.initialize();
  const state = await f.controller.action('startDeploy', { browserId: 'embedded', time: '25:99' });
  assert.equal(state.stage, 'error'); assert.equal(f.calls.length, 0);
});
test('redactor handles both cookie families and Authorization headers', () => {
  const result = safeMessage('koa:sess=aaa123456; Authorization: Bearer xyz123456 ghp_example123456');
  assert.ok(!result.includes('aaa123456')); assert.ok(!result.includes('xyz123456')); assert.ok(!result.includes('ghp_example'));
});

test('explicit reconnect invokes official authorization even if an existing identity works', async () => {
  const f = fixture(); await f.controller.initialize();
  await f.controller.action('connectGithub');
  assert.deepEqual(f.calls, ['login']);
});
test('missing workflow permission requests authorization and resumes the same captured login once', async () => {
  let attempts = 0; let captures = 0;
  const f = fixture({ captureLogin: async () => { captures++; return { ...credential }; }, github: {
    deploy: async () => {
      if (++attempts === 1) { const error = new Error('workflow authorization required'); error.code = 'WORKFLOW_AUTH_REQUIRED'; throw error; }
      return { repository: 'example/glados-quick-deploy', accountKey, runId: 50, status: 'completed', conclusion: 'success', result: { accounts: [{ accountKey, outcome: 'already_checked' }] } };
    },
  } });
  await f.controller.initialize();
  const state = await f.controller.action('startDeploy', { browserId: 'embedded' });
  assert.equal(captures, 1); assert.equal(attempts, 2); assert.deepEqual(f.calls, ['login']);
  assert.equal(state.accounts[0].conclusion, 'already_checked_in');
});
test('aggregate results preserve cancelled run status even when this account has accepted check-in evidence', () => {
  const f = fixture();
  f.controller.recordResult({ accountKey, repository: 'example/glados-quick-deploy', runId: 71, status: 'completed', conclusion: 'cancelled', result: { accounts: [{ accountKey: '0000000000000000', outcome: 'checked', pointsAdded: 99 }, { accountKey, outcome: 'already_checked', pointsAdded: 2 }] } });
  assert.equal(f.controller.state.accounts[0].pointsAdded, 2);
  assert.equal(f.controller.state.accounts[0].conclusion, 'cancelled');
});
test('manual refresh requests latest applicable account run instead of the initial saved run', async () => {
  let args;
  const f = fixture({ github: { refresh: async input => { args = input; return { repository: input.repository, runId: 99, status: 'completed', conclusion: 'success', result: { accounts: [{ accountKey, outcome: 'already_checked' }] } }; } } });
  f.controller.upsert({ accountKey, repository: 'example/glados-quick-deploy', runId: 42 });
  await f.controller.action('refreshRun', { accountKey });
  assert.equal(args.runId, undefined); assert.equal(args.accountKey, accountKey);
  assert.equal(f.controller.state.accounts[0].runId, 99);
});
test('a delayed background read is cancelled and cannot overwrite a new foreground deployment', async () => {
  let release; let started; const hasStarted = new Promise(resolve => { started = resolve; });
  const f = fixture({ github: { refresh: args => new Promise(resolve => { release = () => resolve({ repository: args.repository, runId: 41, status: 'completed', conclusion: 'failure' }); started(); }) } });
  await f.controller.initialize();
  f.controller.upsert({ accountKey, repository: 'example/glados-quick-deploy', runId: 41, status: 'queued', conclusion: 'queued' });
  const background = f.controller.refreshPending();
  await hasStarted;
  assert.equal(f.controller.refreshPending(), background);
  const foreground = f.controller.action('startDeploy', { browserId: 'embedded' });
  release(); await background; await foreground;
  assert.equal(f.controller.state.currentRun.runId, 42);
  assert.equal(f.controller.state.accounts[0].conclusion, 'success');
});
test('an old running task does not mark a newly logged-in account as deployed', () => {
  const f = fixture(); f.controller.pendingAccount = { accountKey, email: 'person@example.com' };
  f.controller.onGitHubEvent({ type: 'run', repository: 'example/glados-quick-deploy', runId: 42, credentialUpdated: false });
  assert.equal(f.controller.state.accounts.length, 0);
});
test('shutdown waits for asynchronous login cleanup after cancellation', async () => {
  let cleaned = false;
  const f = fixture({ captureLogin: ({ signal }) => new Promise((_resolve, reject) => signal.addEventListener('abort', () => {
    setTimeout(() => { cleaned = true; reject(new DOMException('Aborted', 'AbortError')); }, 5);
  }, { once: true })) });
  await f.controller.initialize();
  const action = f.controller.action('startDeploy', { browserId: 'embedded' });
  await new Promise(resolve => setImmediate(resolve));
  await f.controller.shutdown(); await action;
  assert.equal(cleaned, true); assert.equal(f.controller.secrets.length, 0);
});
