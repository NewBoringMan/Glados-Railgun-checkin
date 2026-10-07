'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const {
  cleanNativeEnvironment,
  connectControlledBrowser,
  inspectBrowserProcess,
  ownsBrowserListener,
  parseListenerReport,
  parseProcessInfo,
  validateCDPWebSocket,
} = require('../Resources/browser_connection');

// Every external dependency below is injected. These fixtures never launch a
// process, open a browser, inspect a real profile, or access a network endpoint.
const EXECUTABLE = '/fixture/Microsoft Edge.app/Contents/MacOS/Microsoft Edge';
const PROFILE = '/fixture/BrowserProfiles/accounts/ABCDEF0123456789/edge';
const PROCESS = Object.freeze({ pid: 7711, port: 43123, startedAt: '1800000000:123456' });
const UID = 501;
const PRIVATE_OUTPUT = 'PRIVATE_PROCESS_STDOUT_SENTINEL';
const ERROR_MESSAGE = '无法核实该账号的浏览器读取连接。请保留原网页登录，回到应用后重新读取。';

function safeFailure(error) {
  assert.equal(error.code, 'GLADOS_BROWSER_CONNECTION');
  assert.equal(error.message, ERROR_MESSAGE);
  assert.equal(String(error).includes(PRIVATE_OUTPUT), false);
  return true;
}

function processJSON(overrides = {}) {
  return JSON.stringify({ schema: 'glados.browser-process', version: 1, state: 'found', ...PROCESS, ...overrides });
}

function listenerReport({ pid = PROCESS.pid, uid = UID, files } = {}) {
  const rows = files || [{ name: '127.0.0.1:' + PROCESS.port, state: 'LISTEN' }];
  return [`p${pid}`, `u${uid}`].join('\0') + '\0' + rows.map((file, index) => (
    [`f${index + 15}`, `n${file.name}`, `TST=${file.state}`].join('\0') + '\0\n'
  )).join('');
}

function harness({ running = true } = {}) {
  const state = { current: running ? { ...PROCESS } : null, clock: 0, socket: 'fixture-browser' };
  const calls = { inspect: 0, allocate: 0, launch: [], listener: [], version: [], sleep: [] };
  const options = {
    executable: EXECUTABLE,
    profileDir: PROFILE,
    timeoutMs: 1800,
    inspect() {
      calls.inspect += 1;
      return state.current && { ...state.current };
    },
    allocatePort: async () => { calls.allocate += 1; return PROCESS.port; },
    launchBrowser(port, profile) {
      calls.launch.push({ port, profile });
      state.current = { ...PROCESS };
    },
    ownsListener(snapshot) { calls.listener.push({ ...snapshot }); return true; },
    async getVersion(port) {
      calls.version.push(port);
      return { webSocketDebuggerUrl: `ws://127.0.0.1:${port}/devtools/browser/${state.socket}` };
    },
    now: () => state.clock,
    async sleep(ms) { calls.sleep.push(ms); state.clock += ms; },
  };
  return { state, calls, options };
}

test('reuses an existing verified process without allocating a port or opening a browser', async () => {
  const { calls, options } = harness();
  const connection = await connectControlledBrowser(options);
  assert.equal(connection.reused, true);
  assert.equal(connection.port, PROCESS.port);
  assert.equal(calls.allocate, 0);
  assert.deepEqual(calls.launch, []);
  assert.deepEqual(calls.version, [PROCESS.port]);
  assert.ok(calls.listener.length >= 3);
  assert.ok(calls.listener.every((snapshot) => JSON.stringify(snapshot) === JSON.stringify(PROCESS)));
  await connection.close();
  assert.equal(calls.allocate, 0);
  assert.deepEqual(calls.launch, []);
});

test('starts a missing process once with the same dedicated profile and waits for readiness', async () => {
  const { state, calls, options } = harness({ running: false });
  options.launchBrowser = (port, profile) => calls.launch.push({ port, profile });
  options.sleep = async (ms) => {
    calls.sleep.push(ms); state.clock += ms;
    if (calls.sleep.length === 2) state.current = { ...PROCESS };
  };
  const connection = await connectControlledBrowser(options);
  assert.equal(connection.reused, false);
  assert.equal(calls.allocate, 1);
  assert.deepEqual(calls.launch, [{ port: PROCESS.port, profile: PROFILE }]);
  assert.deepEqual(calls.sleep, [600, 600]);
  assert.deepEqual(calls.version, [PROCESS.port]);
});

test('transient version HTTP failure recovers after one launch and again after manual waiting', async () => {
  const { state, calls, options } = harness({ running: false });
  const getVersion = options.getVersion;
  let failuresRemaining = 1;
  options.getVersion = async (port) => {
    const result = await getVersion(port);
    if (failuresRemaining > 0) {
      failuresRemaining -= 1;
      throw new Error(PRIVATE_OUTPUT);
    }
    return result;
  };

  const connection = await connectControlledBrowser(options);
  assert.equal(connection.reused, false);
  assert.deepEqual(calls.version, [PROCESS.port, PROCESS.port]);
  assert.deepEqual(calls.sleep, [600]);

  // The human wait exceeds the original budget. Revalidation gets a new,
  // bounded budget while retaining the original process and browser socket.
  state.clock += 120000;
  const beforeRevalidation = state.clock;
  failuresRemaining = 1;
  await connection.verifyConnection();
  assert.equal(state.clock - beforeRevalidation, 600);
  assert.deepEqual(calls.version, Array(4).fill(PROCESS.port));
  assert.deepEqual(calls.sleep, [600, 600]);
  assert.equal(calls.allocate, 1);
  assert.deepEqual(calls.launch, [{ port: PROCESS.port, profile: PROFILE }]);
});

test('permanent version HTTP failure stays within the original budget and exposes only a safe error', async () => {
  const { state, calls, options } = harness({ running: false });
  options.timeoutMs = 1000;
  const launchBrowser = options.launchBrowser;
  options.launchBrowser = (port, profile) => {
    launchBrowser(port, profile);
    state.clock += 400;
  };
  options.getVersion = async (port) => {
    calls.version.push(port);
    throw new Error(PRIVATE_OUTPUT);
  };

  await assert.rejects(connectControlledBrowser(options), safeFailure);
  assert.equal(state.clock, options.timeoutMs, 'launch time must consume the same connection budget');
  assert.deepEqual(calls.sleep, [600]);
  assert.deepEqual(calls.version, [PROCESS.port, PROCESS.port]);
  assert.equal(calls.allocate, 1);
  assert.deepEqual(calls.launch, [{ port: PROCESS.port, profile: PROFILE }]);
});

test('process replacement during a failed HTTP request rejects immediately without another attempt', async () => {
  const { state, calls, options } = harness({ running: false });
  options.getVersion = async (port) => {
    calls.version.push(port);
    state.current = { ...PROCESS, pid: PROCESS.pid + 1 };
    throw new Error(PRIVATE_OUTPUT);
  };

  await assert.rejects(connectControlledBrowser(options), safeFailure);
  assert.deepEqual(calls.version, [PROCESS.port]);
  assert.deepEqual(calls.sleep, []);
  assert.equal(calls.allocate, 1);
  assert.deepEqual(calls.launch, [{ port: PROCESS.port, profile: PROFILE }]);
});

test('a missing listener times out without relaunching or changing profile', async () => {
  const { calls, options } = harness({ running: false });
  options.ownsListener = () => false;
  await assert.rejects(connectControlledBrowser(options), safeFailure);
  assert.equal(calls.allocate, 1);
  assert.deepEqual(calls.launch, [{ port: PROCESS.port, profile: PROFILE }]);
  assert.deepEqual(calls.version, []);
});

test('blocked or failed native inspection never falls back to another port or browser', async (t) => {
  for (const [name, result] of [
    ['blocked', { status: 0, stderr: '', stdout: processJSON({ state: 'blocked' }) }],
    ['malformed stdout', { status: 0, stderr: '', stdout: PRIVATE_OUTPUT }],
    ['nonzero exit', { status: 1, stderr: '', stdout: PRIVATE_OUTPUT }],
    ['stderr present', { status: 0, stderr: PRIVATE_OUTPUT, stdout: processJSON() }],
    ['process read error', { status: null, stderr: '', stdout: PRIVATE_OUTPUT, error: new Error(PRIVATE_OUTPUT) }],
  ]) {
    await t.test(name, async () => {
      const { calls, options } = harness();
      options.inspect = () => inspectBrowserProcess(EXECUTABLE, PROFILE, { run: () => result });
      await assert.rejects(connectControlledBrowser(options), safeFailure);
      assert.equal(calls.allocate, 0);
      assert.deepEqual(calls.launch, []);
      assert.deepEqual(calls.version, []);
    });
  }
});

test('native process response accepts only the documented process identity shape', () => {
  assert.deepEqual(parseProcessInfo(processJSON()), PROCESS);
  assert.equal(parseProcessInfo(processJSON({ state: 'none' })), null);
  for (const value of [
    '{}', '[]', PRIVATE_OUTPUT, 'x'.repeat(4097),
    processJSON({ schema: 'other' }), processJSON({ version: 2 }),
    processJSON({ pid: 0 }), processJSON({ pid: Number.MAX_SAFE_INTEGER + 1 }),
    processJSON({ port: 1023 }), processJSON({ port: 65536 }), processJSON({ port: '43123' }),
    processJSON({ startedAt: 'unknown' }), processJSON({ startedAt: '1800000000:1234567' }),
  ]) assert.throws(() => parseProcessInfo(value), safeFailure);
});

test('PID, process start time, port changes or disappearance before connection reject reuse', async (t) => {
  for (const [name, replacement] of [
    ['PID', { ...PROCESS, pid: PROCESS.pid + 1 }],
    ['start time', { ...PROCESS, startedAt: '1800000001:123456' }],
    ['port', { ...PROCESS, port: PROCESS.port + 1 }],
    ['disappearance', null],
  ]) {
    await t.test(name, async () => {
      const { calls, options } = harness();
      let reads = 0;
      options.inspect = () => (++reads === 1 ? { ...PROCESS } : replacement);
      await assert.rejects(connectControlledBrowser(options), safeFailure);
      assert.equal(calls.allocate, 0);
      assert.deepEqual(calls.launch, []);
      assert.deepEqual(calls.version, []);
    });
  }
});

test('a newly discovered process must own the allocated port', async () => {
  const { state, calls, options } = harness({ running: false });
  options.launchBrowser = (port, profile) => {
    calls.launch.push({ port, profile });
    state.current = { ...PROCESS, port: PROCESS.port + 1 };
  };
  await assert.rejects(connectControlledBrowser(options), safeFailure);
  assert.equal(calls.allocate, 1);
  assert.equal(calls.launch.length, 1);
  assert.deepEqual(calls.version, []);
});

test('listener reports require the exact PID and UID with an IPv4 loopback LISTEN socket', () => {
  const expected = { ...PROCESS, uid: UID };
  assert.equal(parseListenerReport(listenerReport(), expected), true);
  assert.equal(parseListenerReport(listenerReport({ files: [
    { name: '127.0.0.1:' + PROCESS.port, state: 'LISTEN' },
    { name: '[::1]:' + PROCESS.port, state: 'LISTEN' },
  ] }), expected), true);
  assert.equal(parseListenerReport('', expected), false);
  assert.equal(parseListenerReport(listenerReport({ files: [] }), expected), false);
  assert.equal(parseListenerReport(listenerReport({ files: [
    { name: '[::1]:' + PROCESS.port, state: 'LISTEN' },
  ] }), expected), false, 'IPv6 alone cannot serve the pinned 127.0.0.1 client');

  for (const report of [
    listenerReport({ pid: PROCESS.pid + 1 }),
    listenerReport({ uid: UID + 1 }),
    listenerReport({ files: [{ name: '*:' + PROCESS.port, state: 'LISTEN' }] }),
    listenerReport({ files: [{ name: '0.0.0.0:' + PROCESS.port, state: 'LISTEN' }] }),
    listenerReport({ files: [{ name: '[::]:' + PROCESS.port, state: 'LISTEN' }] }),
    listenerReport({ files: [{ name: 'localhost:' + PROCESS.port, state: 'LISTEN' }] }),
    listenerReport({ files: [{ name: '192.0.2.1:' + PROCESS.port, state: 'LISTEN' }] }),
    listenerReport({ files: [{ name: '127.0.0.1:' + (PROCESS.port + 1), state: 'LISTEN' }] }),
    listenerReport({ files: [{ name: '127.0.0.1:' + PROCESS.port, state: 'ESTABLISHED' }] }),
    listenerReport() + `p${PROCESS.pid + 1}\0u${UID}\0`,
    listenerReport().replace('TST=LISTEN\0', ''),
    listenerReport().replace('n127.0.0.1:', 'n' + PRIVATE_OUTPUT + ':'),
    listenerReport() + 'xunexpected\0',
    'x'.repeat(16385),
  ]) assert.throws(() => parseListenerReport(report, expected), safeFailure);
});

test('one valid listener never hides an additional wildcard or foreign listener', () => {
  const expected = { ...PROCESS, uid: UID };
  const report = listenerReport({ files: [
    { name: '127.0.0.1:' + PROCESS.port, state: 'LISTEN' },
    { name: '*:' + PROCESS.port, state: 'LISTEN' },
  ] });
  assert.throws(() => parseListenerReport(report, expected), safeFailure);
});

test('lsof invocation intersects PID, UID, port and LISTEN filters and requests machine-readable fields', () => {
  let called = 0;
  assert.equal(ownsBrowserListener(PROCESS, { uid: UID, run(executable, args, options) {
    called += 1;
    assert.equal(executable, '/usr/sbin/lsof');
    assert.deepEqual(args, ['-nP', '-a', '-p', String(PROCESS.pid), '-u', String(UID),
      '-iTCP:' + PROCESS.port, '-sTCP:LISTEN', '-F0pufnT']);
    assert.equal(options.encoding, 'utf8');
    assert.equal(options.timeout, 5000);
    assert.equal(options.maxBuffer, 16384);
    return { status: 0, stderr: '', stdout: listenerReport() };
  } }), true);
  assert.equal(called, 1);
});

test('lsof failures expose only a fixed safe error and an empty no-match is not ownership', () => {
  assert.equal(ownsBrowserListener(PROCESS, { uid: UID, run: () => ({ status: 1, stderr: '', stdout: '' }) }), false);
  for (const result of [
    { status: 1, stderr: '', stdout: PRIVATE_OUTPUT },
    { status: 0, stderr: PRIVATE_OUTPUT, stdout: listenerReport() },
    { status: 0, stderr: '', stdout: PRIVATE_OUTPUT },
    { status: null, stderr: '', stdout: PRIVATE_OUTPUT, error: new Error(PRIVATE_OUTPUT) },
  ]) assert.throws(() => ownsBrowserListener(PROCESS, { uid: UID, run: () => result }), safeFailure);
});

test('browser and page websocket URLs pin their endpoint kind and port without credentials or external hosts', () => {
  for (const kind of ['browser', 'page']) {
    const suffix = `/devtools/${kind}/fixture-0123`;
    assert.equal(validateCDPWebSocket(`ws://localhost:${PROCESS.port}${suffix}`, PROCESS.port, kind),
      `ws://127.0.0.1:${PROCESS.port}${suffix}`);
    assert.equal(validateCDPWebSocket(`ws://127.0.0.1:${PROCESS.port}${suffix}`, PROCESS.port, kind),
      `ws://127.0.0.1:${PROCESS.port}${suffix}`);
    for (const value of [
      `ws://127.0.0.1:${PROCESS.port + 1}${suffix}`,
      `ws://example.invalid:${PROCESS.port}${suffix}`,
      `ws://localhost.example.invalid:${PROCESS.port}${suffix}`,
      `ws://192.0.2.1:${PROCESS.port}${suffix}`,
      `ws://[::1]:${PROCESS.port}${suffix}`,
      `wss://127.0.0.1:${PROCESS.port}${suffix}`,
      `http://127.0.0.1:${PROCESS.port}${suffix}`,
      `ws://user:fixture@127.0.0.1:${PROCESS.port}${suffix}`,
      `ws://user@127.0.0.1:${PROCESS.port}${suffix}`,
      `ws://127.0.0.1:${PROCESS.port}${suffix}?fixture=1`,
      `ws://127.0.0.1:${PROCESS.port}${suffix}#fixture`,
      `ws://127.0.0.1:${PROCESS.port}/devtools/${kind === 'browser' ? 'page' : 'browser'}/fixture`,
      `ws://127.0.0.1:${PROCESS.port}${suffix}/extra`,
      `ws://127.0.0.1:${PROCESS.port}/devtools/${kind}/`,
      PRIVATE_OUTPUT,
    ]) assert.throws(() => validateCDPWebSocket(value, PROCESS.port, kind), safeFailure);
  }
});

test('empty URL userinfo, query and fragment delimiters cannot bypass websocket validation', () => {
  for (const kind of ['browser', 'page']) {
    const suffix = `/devtools/${kind}/fixture`;
    for (const value of [
      `ws://@127.0.0.1:${PROCESS.port}${suffix}`,
      `ws://127.0.0.1:${PROCESS.port}${suffix}?`,
      `ws://127.0.0.1:${PROCESS.port}${suffix}#`,
    ]) assert.throws(() => validateCDPWebSocket(value, PROCESS.port, kind), safeFailure);
  }
});

test('an invalid version websocket fails before exposing a reusable connection', async (t) => {
  for (const url of [
    `ws://127.0.0.1:${PROCESS.port + 1}/devtools/browser/fixture`,
    `ws://example.invalid:${PROCESS.port}/devtools/browser/fixture`,
    `ws://127.0.0.1:${PROCESS.port}/devtools/page/fixture`,
  ]) {
    await t.test(url, async () => {
      const { calls, options } = harness();
      options.getVersion = async () => ({ webSocketDebuggerUrl: url });
      await assert.rejects(connectControlledBrowser(options), safeFailure);
      assert.equal(calls.allocate, 0);
      assert.deepEqual(calls.launch, []);
    });
  }
});

test('ownership is checked again if the process changes during version retrieval', async () => {
  const { state, options } = harness();
  const getVersion = options.getVersion;
  options.getVersion = async (port) => {
    const value = await getVersion(port);
    state.current = { ...PROCESS, startedAt: '1800000002:123456' };
    return value;
  };
  await assert.rejects(connectControlledBrowser(options), safeFailure);
});

test('a process replacement during listener ownership inspection is rejected before version retrieval', async () => {
  const { state, calls, options } = harness();
  let listenerReads = 0;
  options.ownsListener = () => {
    listenerReads += 1;
    if (listenerReads === 2) state.current.startedAt = '1800000004:123456';
    return true;
  };
  await assert.rejects(connectControlledBrowser(options), safeFailure);
  assert.deepEqual(calls.version, []);
  assert.equal(calls.allocate, 0);
  assert.deepEqual(calls.launch, []);
});

test('after manual waiting verifyConnection retrieves and compares the live browser websocket again', async () => {
  const { state, calls, options } = harness();
  const connection = await connectControlledBrowser(options);
  state.clock += 120000;
  await connection.verifyConnection();
  assert.deepEqual(calls.version, [PROCESS.port, PROCESS.port]);
  state.socket = 'replacement-browser';
  await assert.rejects(connection.verifyConnection(), safeFailure);
  assert.deepEqual(calls.version, [PROCESS.port, PROCESS.port, PROCESS.port]);
  assert.equal(calls.allocate, 0);
  assert.deepEqual(calls.launch, []);
});

test('after manual waiting process identity or listener loss fails before another version request', async (t) => {
  for (const [name, change] of [
    ['PID changed', (state) => { state.current.pid += 1; }],
    ['start time changed', (state) => { state.current.startedAt = '1800000003:123456'; }],
    ['port changed', (state) => { state.current.port += 1; }],
    ['process gone', (state) => { state.current = null; }],
  ]) {
    await t.test(name, async () => {
      const { state, calls, options } = harness();
      const connection = await connectControlledBrowser(options);
      change(state);
      await assert.rejects(connection.verifyConnection(), safeFailure);
      assert.deepEqual(calls.version, [PROCESS.port]);
      assert.equal(calls.allocate, 0);
      assert.deepEqual(calls.launch, []);
    });
  }
  const { calls, options } = harness();
  let listening = true;
  options.ownsListener = () => listening;
  const connection = await connectControlledBrowser(options);
  listening = false;
  await assert.rejects(connection.verifyConnection(), safeFailure);
  assert.deepEqual(calls.version, [PROCESS.port]);
});

test('ownership is checked on both sides of the post-wait version request', async () => {
  const { state, calls, options } = harness();
  const getVersion = options.getVersion;
  options.getVersion = async (port) => {
    const result = await getVersion(port);
    if (calls.version.length === 2) state.current.pid += 1;
    return result;
  };
  const connection = await connectControlledBrowser(options);
  await assert.rejects(connection.verifyConnection(), safeFailure);
  assert.equal(calls.version.length, 2);
});

test('native metadata and lsof subprocesses strip DYLD injection without mutating the parent environment', () => {
  const environment = {
    PATH: '/fixture/bin', HOME: '/fixture/home', GLADOS_EXPECTED_ACCOUNT_KEY: 'ABCDEF0123456789',
    DYLD_INSERT_LIBRARIES: '/fixture/menu-plugin.dylib',
    DYLD_LIBRARY_PATH: '/fixture/lib', DYLD_FRAMEWORK_PATH: '/fixture/frameworks',
    DYLD_PRINT_LIBRARIES: '1',
  };
  const before = { ...environment };
  const clean = cleanNativeEnvironment(environment);
  assert.deepEqual(clean, {
    PATH: '/fixture/bin', HOME: '/fixture/home', GLADOS_EXPECTED_ACCOUNT_KEY: 'ABCDEF0123456789',
  });
  assert.deepEqual(environment, before);
  assert.deepEqual(inspectBrowserProcess(EXECUTABLE, PROFILE, {
    helper: '/fixture/GLaDOSAccountCenter', env: environment,
    run(executable, args, options) {
      assert.equal(executable, '/fixture/GLaDOSAccountCenter');
      assert.deepEqual(args, ['--browser-process-info', EXECUTABLE, PROFILE]);
      assert.deepEqual(options.env, clean);
      assert.equal(options.timeout, 5000);
      assert.equal(options.maxBuffer, 4096);
      return { status: 0, stderr: '', stdout: processJSON() };
    },
  }), PROCESS);
  assert.equal(ownsBrowserListener(PROCESS, { uid: UID, env: environment,
    run(_executable, _args, options) {
      assert.deepEqual(options.env, clean);
      return { status: 0, stderr: '', stdout: listenerReport() };
    },
  }), true);
  assert.deepEqual(environment, before);
});
