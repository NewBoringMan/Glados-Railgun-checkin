'use strict';

const childProcess = require('child_process');
const path = require('path');

function connectionError() {
  const error = new Error('无法核实该账号的浏览器读取连接。请保留原网页登录，回到应用后重新读取。');
  error.code = 'GLADOS_BROWSER_CONNECTION';
  return error;
}

function cleanNativeEnvironment(source = process.env) {
  // The App's menu plugin belongs only to its UI process. A metadata query
  // must never load an injected AppKit library or create a window.
  return Object.fromEntries(Object.entries(source).filter(([name]) => !name.startsWith('DYLD_')));
}

function parseProcessInfo(text) {
  if (typeof text !== 'string' || Buffer.byteLength(text) > 4096) throw connectionError();
  let value;
  try { value = JSON.parse(text); } catch { throw connectionError(); }
  if (!value || value.schema !== 'glados.browser-process' || value.version !== 1) throw connectionError();
  if (value.state === 'none') return null;
  if (value.state !== 'found' || !Number.isSafeInteger(value.pid) || value.pid <= 0
      || !Number.isInteger(value.port) || value.port < 1024 || value.port > 65535
      || typeof value.startedAt !== 'string' || !/^[0-9]{1,20}:[0-9]{1,6}$/.test(value.startedAt)) {
    throw connectionError();
  }
  return { pid: value.pid, port: value.port, startedAt: value.startedAt };
}

function inspectBrowserProcess(executable, profileDir, options = {}) {
  const run = options.run || childProcess.spawnSync;
  const helper = options.helper || path.resolve(__dirname, '..', 'MacOS', 'GLaDOSAccountCenter');
  const result = run(helper, ['--browser-process-info', executable, profileDir], {
    encoding: 'utf8', timeout: 5000, maxBuffer: 4096,
    env: cleanNativeEnvironment(options.env),
  });
  if (result.error || result.status !== 0 || result.stderr) throw connectionError();
  return parseProcessInfo(result.stdout);
}

function sameProcess(actual, expected) {
  return actual !== null && expected !== null && actual.pid === expected.pid
    && actual.port === expected.port && actual.startedAt === expected.startedAt;
}

function parseListenerReport(text, expected) {
  if (typeof text !== 'string' || Buffer.byteLength(text) > 16384) throw connectionError();
  let pid = null;
  let uid = null;
  let file = null;
  let ipv4 = false;
  let count = 0;
  const finishFile = () => {
    if (!file) return;
    if (pid !== expected.pid || uid !== expected.uid || file.state !== 'LISTEN'
        || !file.name || !["127.0.0.1:" + expected.port, "[::1]:" + expected.port].includes(file.name)) {
      throw connectionError();
    }
    if (file.name === '127.0.0.1:' + expected.port) ipv4 = true;
    count += 1;
  };
  for (const raw of text.split('\0')) {
    const field = raw.replace(/^\n+|\n+$/g, '');
    if (!field) continue;
    const kind = field[0];
    const value = field.slice(1);
    if (kind === 'p') {
      finishFile(); file = null;
      if (!/^[0-9]+$/.test(value) || Number(value) !== expected.pid || pid !== null) throw connectionError();
      pid = Number(value); uid = null;
    } else if (kind === 'u') {
      if (!/^[0-9]+$/.test(value) || Number(value) !== expected.uid || uid !== null || file) throw connectionError();
      uid = Number(value);
    } else if (kind === 'f') {
      finishFile();
      if (pid !== expected.pid || uid !== expected.uid || !/^[0-9]+$/.test(value)) throw connectionError();
      file = { name: null, state: null };
    } else if (kind === 'n') {
      if (!file || file.name !== null) throw connectionError();
      file.name = value;
    } else if (kind === 'T') {
      if (!file) throw connectionError();
      if (value.startsWith('ST=')) {
        if (file.state !== null) throw connectionError();
        file.state = value.slice(3);
      }
    } else {
      throw connectionError();
    }
  }
  finishFile();
  // Existing requests use 127.0.0.1; never resolve a returned host via DNS.
  return count > 0 && ipv4;
}

function ownsBrowserListener(snapshot, options = {}) {
  const uid = options.uid === undefined ? process.getuid() : options.uid;
  if (!Number.isInteger(uid) || uid < 0) throw connectionError();
  const run = options.run || childProcess.spawnSync;
  const result = run('/usr/sbin/lsof', [
    '-nP', '-a', '-p', String(snapshot.pid), '-u', String(uid),
    '-iTCP:' + snapshot.port, '-sTCP:LISTEN', '-F0pufnT',
  ], { encoding: 'utf8', timeout: 5000, maxBuffer: 16384, env: cleanNativeEnvironment(options.env) });
  if (result.error || result.stderr) throw connectionError();
  if (result.status === 1 && !result.stdout) return false;
  if (result.status !== 0) throw connectionError();
  return parseListenerReport(result.stdout, { ...snapshot, uid });
}

function validateCDPWebSocket(value, port, kind) {
  // URL normalization removes empty userinfo/query/fragment delimiters and
  // accepts alternate numeric host spellings. Require the literal loopback
  // endpoint shape before parsing so none of those forms can change identity.
  if (typeof value !== 'string' || !['browser', 'page'].includes(kind)
      || !Number.isInteger(port) || port < 1024 || port > 65535
      || !new RegExp('^ws://(?:127\\.0\\.0\\.1|localhost):' + port
        + '/devtools/' + kind + '/[A-Za-z0-9._-]{1,200}$').test(value)) {
    throw connectionError();
  }
  let url;
  try { url = new URL(value); } catch { throw connectionError(); }
  if (!['browser', 'page'].includes(kind) || !Number.isInteger(port) || port < 1024 || port > 65535
      || url.protocol !== 'ws:' || !['127.0.0.1', 'localhost'].includes(url.hostname)
      || url.port !== String(port) || url.username || url.password || url.search || url.hash
      || !new RegExp('^/devtools/' + kind + '/[A-Za-z0-9._-]{1,200}$').test(url.pathname)) {
    throw connectionError();
  }
  url.hostname = '127.0.0.1';
  return url.toString();
}

async function connectControlledBrowser(options) {
  const { executable, profileDir, allocatePort, launchBrowser, getVersion } = options;
  const inspect = options.inspect || (() => inspectBrowserProcess(executable, profileDir));
  const ownsListener = options.ownsListener || ownsBrowserListener;
  const now = options.now || Date.now;
  const sleep = options.sleep || ((ms) => new Promise((resolve) => setTimeout(resolve, ms)));
  const deadline = now() + (options.timeoutMs === undefined ? 45000 : options.timeoutMs);
  let snapshot = inspect();
  const reused = snapshot !== null;
  const port = reused ? snapshot.port : await allocatePort();
  if (!Number.isInteger(port) || port < 1024 || port > 65535) throw connectionError();
  if (!reused) launchBrowser(port, profileDir);

  while (true) {
    const current = inspect();
    if (snapshot && !sameProcess(current, snapshot)) throw connectionError();
    if (current) {
      if (current.port !== port) throw connectionError();
      snapshot = current;
      if (ownsListener(snapshot)) break;
    }
    if (now() >= deadline) throw connectionError();
    await sleep(600);
  }

  const verifyOwnership = () => {
    if (!sameProcess(inspect(), snapshot) || !ownsListener(snapshot)
        || !sameProcess(inspect(), snapshot)) throw connectionError();
  };
  const readBrowserSocket = async (until) => {
    while (true) {
      verifyOwnership();
      let version;
      try { version = await getVersion(port); }
      catch {
        // A listening socket can precede a ready HTTP endpoint. Retry only
        // the same verified process, within the original connection budget.
        verifyOwnership();
        if (now() >= until) throw connectionError();
        await sleep(Math.min(600, until - now()));
        continue;
      }
      const websocket = validateCDPWebSocket(version?.webSocketDebuggerUrl, port, 'browser');
      verifyOwnership();
      return websocket;
    }
  };
  const browserSocket = await readBrowserSocket(deadline);

  const verifyConnection = async () => {
    const current = await readBrowserSocket(now() + (options.timeoutMs === undefined ? 45000 : options.timeoutMs));
    if (current !== browserSocket) throw connectionError();
  };
  return { port, reused, verifyOwnership, verifyConnection, close: async () => {} };
}

module.exports = {
  cleanNativeEnvironment, connectionError, connectControlledBrowser,
  inspectBrowserProcess, ownsBrowserListener, parseListenerReport,
  parseProcessInfo, sameProcess, validateCDPWebSocket,
};
