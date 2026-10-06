'use strict';

const childProcess = require('child_process');
const fs = require('fs');
const http = require('http');
const net = require('net');
const os = require('os');
const path = require('path');
const crypto = require('crypto');
const {
  ACCOUNT_SECRET_PREFIX,
  MANAGED_WORKFLOW_NAME,
  accountKeyFromSecretName,
  buildManagedWorkflow,
  composeCookieHeader,
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
} = require('./core');
const {
  buildBrowserCatalog,
  extractSessionId,
  unwrapWebDriverValue,
  webdriverCapabilities,
} = require('./browser_support');

const APP_TITLE = 'GLaDOS Account Center';
const DEFAULT_REPO = 'NewBoringMan/Glados-Railgun-checkin';
const DEFAULT_BRANCH = 'master';
const LEGACY_WORKFLOW = 'gladosCheck.yml';
const LEGACY_SECRET = 'GLADOS_COOKIES';
const MANAGED_WORKFLOW_PATH = `.github/workflows/${MANAGED_WORKFLOW_NAME}`;
const REPOSITORY_SECRET_LIMIT = 100;
const SUPPORT_DIR = path.join(os.homedir(), 'Library', 'Application Support', 'GLaDOS Account Center');
const SAFARI_NATIVE_BRIDGE_SERVER = path.join(__dirname, 'safari_native_bridge_server.js');
const SAFARI_EMBEDDED_EXTENSION = path.resolve(__dirname, '..', 'PlugIns', 'GLaDOS Safari Bridge Extension.appex');

const BROWSERS = buildBrowserCatalog(os.homedir());

function expectedCaptureIdentity(env = process.env) {
  const accountKey = String(env.GLADOS_EXPECTED_ACCOUNT_KEY || '').trim();
  const host = String(env.GLADOS_EXPECTED_HOST || '').trim().toLowerCase();
  if (accountKey && !/^[A-F0-9]{16}$/.test(accountKey)) throw new Error('待更新的账号标识无效。');
  if (host && !isAllowedHost(host)) throw new Error('原账号的登录域名无效。');
  return { accountKey, host };
}

function initialLoginUrl() {
  return `https://${expectedCaptureIdentity().host || 'glados.cloud'}/console/checkin`;
}

function run(command, args, options = {}) {
  return childProcess.spawnSync(command, args, {
    encoding: options.encoding || 'utf8',
    input: options.input,
    timeout: options.timeout || 120000,
    env: options.env || process.env,
    maxBuffer: 20 * 1024 * 1024,
  });
}

function appleScript(lines, argv = []) {
  const args = [];
  for (const line of lines) args.push('-e', line);
  args.push('--', ...argv.map(String));
  const result = run('/usr/bin/osascript', args, { timeout: 3600000 });
  if (result.status !== 0) {
    const errorText = `${result.stderr || ''}`.trim();
    if (/User canceled|(-128)/i.test(errorText)) return null;
    throw new Error(errorText || 'macOS 对话框执行失败。');
  }
  return String(result.stdout || '').trim();
}

function alert(message, icon = 'note') {
  appleScript([
    'on run argv',
    'display dialog (item 1 of argv) with title "GLaDOS Account Center" buttons {"确定"} default button "确定" with icon ' + icon,
    'end run',
  ], [message]);
}

function confirm(message, okLabel = '继续') {
  const result = appleScript([
    'on run argv',
    'set answer to display dialog (item 1 of argv) with title "GLaDOS Account Center" buttons {"取消", item 2 of argv} default button (item 2 of argv) cancel button "取消" with icon caution',
    'return button returned of answer',
    'end run',
  ], [message, okLabel]);
  return result === okLabel;
}

function choose(items, prompt, title = APP_TITLE) {
  if (!items.length) return null;
  const separator = String.fromCharCode(30);
  const result = appleScript([
    'on run argv',
    'set AppleScript\'s text item delimiters to character id 30',
    'set choiceList to text items of (item 1 of argv)',
    'set picked to choose from list choiceList with title (item 2 of argv) with prompt (item 3 of argv) default items {item 1 of choiceList} OK button name "选择" cancel button name "取消"',
    'if picked is false then return ""',
    'return item 1 of picked',
    'end run',
  ], [items.join(separator), title, prompt]);
  return result || null;
}

function promptText(message, defaultValue) {
  const result = appleScript([
    'on run argv',
    'set answer to display dialog (item 1 of argv) with title "GLaDOS Account Center" default answer (item 2 of argv) buttons {"取消", "确定"} default button "确定" cancel button "取消"',
    'return text returned of answer',
    'end run',
  ], [message, defaultValue]);
  return result;
}

function notify(message) {
  try {
    appleScript([
      'on run argv',
      'display notification (item 1 of argv) with title "GLaDOS Account Center"',
      'end run',
    ], [message]);
  } catch {
    // Notification is best effort only.
  }
}

function installedBrowsers() {
  return BROWSERS.filter((browser) => browser.paths.some((candidate) => fs.existsSync(candidate)));
}

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
}

function launchControlledBrowser(browser, port) {
  ensureDir(SUPPORT_DIR);
  const profileDir = path.join(SUPPORT_DIR, 'BrowserProfiles', browser.id);
  ensureDir(profileDir);
  const args = [
    '-na', browser.appName,
    '--args',
    `--remote-debugging-port=${port}`,
    `--user-data-dir=${profileDir}`,
    '--no-first-run',
    '--no-default-browser-check',
    initialLoginUrl(),
  ];
  const result = run('/usr/bin/open', args, { timeout: 30000 });
  if (result.status !== 0) throw new Error(redact(result.stderr || '无法启动所选浏览器。'));
  return profileDir;
}

function httpJson(url, timeoutMs = 10000) {
  return new Promise((resolve, reject) => {
    const request = http.get(url, { timeout: timeoutMs }, (response) => {
      const chunks = [];
      response.on('data', (chunk) => chunks.push(chunk));
      response.on('end', () => {
        const body = Buffer.concat(chunks).toString('utf8');
        if (response.statusCode < 200 || response.statusCode >= 300) {
          reject(new Error(`调试接口返回 HTTP ${response.statusCode}`));
          return;
        }
        try { resolve(JSON.parse(body)); } catch { reject(new Error('调试接口返回了无法解析的数据。')); }
      });
    });
    request.on('timeout', () => request.destroy(new Error('连接浏览器调试接口超时。')));
    request.on('error', reject);
  });
}


function localJsonRequest(port, method, requestPath, data = null, timeoutMs = 15000) {
  return new Promise((resolve, reject) => {
    const payload = data === null ? null : Buffer.from(JSON.stringify(data), 'utf8');
    const request = http.request({
      hostname: '127.0.0.1',
      port,
      path: requestPath,
      method,
      timeout: timeoutMs,
      headers: payload ? {
        'Content-Type': 'application/json; charset=utf-8',
        'Content-Length': payload.length,
      } : {},
    }, (response) => {
      const chunks = [];
      response.on('data', (chunk) => chunks.push(chunk));
      response.on('end', () => {
        const text = Buffer.concat(chunks).toString('utf8');
        let parsed = null;
        if (text) {
          try { parsed = JSON.parse(text); } catch {
            reject(new Error(`本机浏览器驱动返回了无法解析的数据（HTTP ${response.statusCode}）。`));
            return;
          }
        }
        if (response.statusCode < 200 || response.statusCode >= 300) {
          const detail = parsed?.value?.message || parsed?.message || text || `HTTP ${response.statusCode}`;
          reject(new Error(redact(String(detail))));
          return;
        }
        resolve(parsed || {});
      });
    });
    request.on('timeout', () => request.destroy(new Error('本机浏览器驱动请求超时。')));
    request.on('error', reject);
    if (payload) request.write(payload);
    request.end();
  });
}

function spawnLocalDriver(command, args) {
  const child = childProcess.spawn(command, args, {
    stdio: ['ignore', 'pipe', 'pipe'],
    env: process.env,
  });
  let diagnostics = '';
  const capture = (chunk) => {
    diagnostics += String(chunk || '');
    if (diagnostics.length > 16000) diagnostics = diagnostics.slice(-16000);
  };
  child.stdout?.on('data', capture);
  child.stderr?.on('data', capture);
  return {
    child,
    diagnostics: () => redact(diagnostics.trim()),
    stop: () => {
      if (child.exitCode === null && !child.killed) {
        try { child.kill('SIGTERM'); } catch {}
      }
    },
  };
}

async function waitForWebDriver(port, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  let lastError;
  while (Date.now() < deadline) {
    try {
      const response = await localJsonRequest(port, 'GET', '/status', null, 2500);
      const value = unwrapWebDriverValue(response);
      if (value && (value.ready === true || value.message || typeof value === 'object')) return response;
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(`未能连接本机浏览器驱动。${lastError ? ` ${lastError.message}` : ''}`);
}

function firstExistingPath(paths) {
  return (Array.isArray(paths) ? paths : []).find((candidate) => fs.existsSync(candidate)) || null;
}

async function startWebDriverBrowser(browser) {
  const browserBinary = firstExistingPath(browser.paths);
  let driverPath = firstExistingPath(browser.driverPaths);
  if (!driverPath && browser.driver) driverPath = findExecutable(browser.driver);
  if (!driverPath) {
    if (browser.id === 'firefox') {
      throw new Error('未找到 geckodriver。请运行“安装依赖.command”后再使用 Firefox。');
    }
    throw new Error(`未找到 ${browser.label} 的浏览器驱动。`);
  }
  const port = browser.preferredPort;
  const driverArgs = typeof browser.driverArgs === 'function' ? browser.driverArgs(port) : ['--port', String(port)];
  const driverProcess = spawnLocalDriver(driverPath, driverArgs);
  let sessionId = null;
  try {
    await waitForWebDriver(port, 30000);
    const response = await localJsonRequest(
      port,
      'POST',
      '/session',
      webdriverCapabilities(browser, browserBinary),
      45000,
    );
    sessionId = extractSessionId(response);
    if (!sessionId) throw new Error('浏览器驱动没有返回有效的自动化会话。');
    await localJsonRequest(
      port,
      'POST',
      `/session/${encodeURIComponent(sessionId)}/url`,
      { url: initialLoginUrl() },
      45000,
    );
    return {
      kind: 'webdriver',
      browser,
      port,
      sessionId,
      close: async () => {
        try {
          if (sessionId) {
            await localJsonRequest(port, 'DELETE', `/session/${encodeURIComponent(sessionId)}`, null, 10000);
          }
        } catch {}
        driverProcess.stop();
      },
    };
  } catch (error) {
    const diagnostics = driverProcess.diagnostics();
    driverProcess.stop();
    throw new Error(`${error.message}${diagnostics ? `\n${diagnostics}` : ''}`);
  }
}

function createLineReader(stream, onLine) {
  let buffer = '';
  stream.setEncoding('utf8');
  stream.on('data', (chunk) => {
    buffer += chunk;
    while (true) {
      const index = buffer.indexOf('\n');
      if (index < 0) break;
      const line = buffer.slice(0, index).trim();
      buffer = buffer.slice(index + 1);
      if (line) onLine(line);
    }
  });
}

function safariGladosTabs() {
  const separator = String.fromCharCode(30);
  const recordSeparator = String.fromCharCode(29);
  const script = [
    'on run argv',
    'set fieldSeparator to character id 30',
    'set recordSeparator to character id 29',
    'set outputText to ""',
    'tell application "Safari"',
    'repeat with windowIndex from 1 to count of windows',
    'set currentWindow to window windowIndex',
    'repeat with tabIndex from 1 to count of tabs of currentWindow',
    'set currentTab to tab tabIndex of currentWindow',
    'set currentURL to URL of currentTab',
    'if currentURL is not missing value then',
    'if currentURL starts with "https://glados.cloud/" or currentURL starts with "https://railgun.info/" then',
    'set currentTitle to name of currentTab',
    'set outputText to outputText & windowIndex & fieldSeparator & tabIndex & fieldSeparator & currentTitle & fieldSeparator & currentURL & recordSeparator',
    'end if',
    'end if',
    'end repeat',
    'end repeat',
    'end tell',
    'return outputText',
    'end run',
  ];
  try {
    const output = appleScript(script);
    return String(output || '').split(recordSeparator).filter(Boolean).map((record) => {
      const [windowIndex, tabIndex, title, url] = record.split(separator);
      return { windowIndex: Number(windowIndex), tabIndex: Number(tabIndex), title, url };
    }).filter((item) => Number.isInteger(item.windowIndex) && Number.isInteger(item.tabIndex) && item.url);
  } catch {
    return [];
  }
}

function activateSafariTab(tab, bridgeUrl) {
  appleScript([
    'on run argv',
    'set windowIndex to item 1 of argv as integer',
    'set tabIndex to item 2 of argv as integer',
    'set targetURL to item 3 of argv',
    'tell application "Safari"',
    'activate',
    'set current tab of window windowIndex to tab tabIndex of window windowIndex',
    'set index of window windowIndex to 1',
    'set URL of tab tabIndex of window windowIndex to targetURL',
    'end tell',
    'end run',
  ], [tab.windowIndex, tab.tabIndex, bridgeUrl]);
}

function selectSafariTarget() {
  const expectedHost = expectedCaptureIdentity().host;
  if (expectedHost && !['glados.cloud', 'railgun.info'].includes(expectedHost)) {
    throw new Error('Safari 扩展尚未获准读取此账号的原域名，请手动选择支持该域名的原登录浏览器。');
  }
  const tabs = safariGladosTabs().filter((tab) => {
    try { validatePinnedPage(tab.url, expectedHost); return true; } catch { return false; }
  });
  if (!tabs.length) return { mode: 'new', url: initialLoginUrl() };
  const labels = tabs.map((tab, index) => `${index + 1}. ${tab.title || 'GLaDOS'} — ${tab.url}`);
  labels.push('打开新的普通 Safari GLaDOS 页面');
  const selected = choose(labels, '选择已经打开并登录的 GLaDOS 页面；也可以打开新页面：');
  if (!selected) throw new Error('已取消选择 Safari GLaDOS 页面。');
  if (selected === labels[labels.length - 1]) {
    return { mode: 'new', url: initialLoginUrl() };
  }
  const tab = tabs[labels.indexOf(selected)];
  const current = new URL(tab.url);
  current.hash = '';
  return { mode: 'existing', tab, url: current.toString() };
}

async function startSafariExtensionSession(browser) {
  if (!fs.existsSync(SAFARI_NATIVE_BRIDGE_SERVER)) throw new Error('Safari Native Messaging 桥接组件缺失。请重新安装应用。');
  if (!fs.existsSync(SAFARI_EMBEDDED_EXTENSION)) {
    throw new Error('应用内嵌的 Safari 扩展组件缺失。请重新安装完整的 GLaDOS Control Center 应用。');
  }

  const target = selectSafariTarget();
  const pinnedPage = validatePinnedPage(target.url, expectedCaptureIdentity().host);
  const token = crypto.randomBytes(32).toString('hex');
  const child = childProcess.spawn(process.execPath, [SAFARI_NATIVE_BRIDGE_SERVER], {
    stdio: ['pipe', 'pipe', 'pipe'],
    env: process.env,
  });

  let readyResolve;
  let readyReject;
  const readyPromise = new Promise((resolve, reject) => {
    readyResolve = resolve;
    readyReject = reject;
  });
  let captureResolve;
  let captureReject;
  let captured = null;
  const capturePromise = new Promise((resolve, reject) => {
    captureResolve = resolve;
    captureReject = reject;
  });
  capturePromise.catch(() => {});
  let diagnostics = '';
  let ready = false;

  createLineReader(child.stdout, (line) => {
    if (line.startsWith('READY ')) {
      const port = Number(line.slice(6));
      if (Number.isInteger(port) && port > 0 && port <= 65535) {
        ready = true;
        readyResolve(port);
      }
      return;
    }
    if (line.startsWith('CAPTURE ')) {
      try {
        captured = JSON.parse(line.slice(8));
        captureResolve(captured);
      } catch {
        captureReject(new Error('Safari Native Messaging 桥接返回了无法解析的数据。'));
      }
    }
  });
  child.stderr.setEncoding('utf8');
  child.stderr.on('data', (chunk) => {
    diagnostics += String(chunk || '');
    if (diagnostics.length > 8000) diagnostics = diagnostics.slice(-8000);
  });
  child.on('error', (error) => {
    if (!ready) readyReject(error);
    captureReject(error);
  });
  child.on('exit', (code) => {
    if (!ready) readyReject(new Error(`Safari Native Messaging 桥接启动失败（${code ?? 'unknown'}）。${diagnostics ? `\n${redact(diagnostics)}` : ''}`));
    if (!captured && code !== 0) captureReject(new Error(`Safari Native Messaging 桥接已退出（${code ?? 'unknown'}）。`));
  });
  child.stdin.on('error', () => {});
  child.stdin.end(`${JSON.stringify({ token, host: pinnedPage.host })}\n`);

  const port = await Promise.race([
    readyPromise,
    new Promise((_, reject) => setTimeout(() => reject(new Error('Safari Native Messaging 桥接启动超时。')), 10000)),
  ]);
  const bridgeUrl = new URL(target.url);
  bridgeUrl.hash = `glados-assistant=${token}&port=${port}`;
  try {
    if (target.mode === 'existing') activateSafariTab(target.tab, bridgeUrl.toString());
    else {
      const opened = run('/usr/bin/open', ['-a', 'Safari', bridgeUrl.toString()], { timeout: 30000 });
      if (opened.status !== 0) throw new Error(redact(opened.stderr || '无法打开普通 Safari 窗口。'));
    }
  } catch (error) {
    try { child.kill('SIGTERM'); } catch {}
    throw error;
  }

  return {
    kind: 'safari-extension',
    browser,
    port,
    host: pinnedPage.host,
    targetMode: target.mode,
    waitForCapture: async (timeoutMs = 1500) => Promise.race([
      capturePromise,
      new Promise((resolve) => setTimeout(() => resolve(null), timeoutMs)),
    ]),
    close: async () => {
      if (child.exitCode === null && !child.killed) {
        try { child.kill('SIGTERM'); } catch {}
      }
    },
  };
}

function openSafariExtensionSetup() {
  if (!fs.existsSync(SAFARI_EMBEDDED_EXTENSION)) throw new Error('应用内嵌 Safari 扩展缺失，请重新安装完整应用。');
  run('/usr/bin/open', ['-a', 'Safari'], { timeout: 10000 });
  alert([
    'Safari Web Extension 已嵌入当前应用，通过 Native Messaging 接收你手动发送的登录信息。',
    '',
    '请确认“GLaDOS Account Center Safari 扩展”已在 Safari → 设置 → 扩展中启用，并将 glados.cloud 与 railgun.info 设为允许。',
    '',
    '旧版临时 Safari 扩展可以解除安装；当前版本不需要开启 Safari 远程自动化。',
  ].join('\n'), 'caution');
}

async function startBrowserSession(browser) {
  if (browser.kind === 'safari-extension') return startSafariExtensionSession(browser);
  if (browser.kind === 'cdp') {
    const port = browser.preferredPort;
    launchControlledBrowser(browser, port);
    await waitForCDP(port, 45000);
    return {
      kind: 'cdp',
      browser,
      port,
      close: async () => {},
    };
  }
  if (browser.kind === 'webdriver') return startWebDriverBrowser(browser);
  throw new Error(`不支持的浏览器连接方式：${browser.kind}`);
}

async function waitForCDP(port, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  let lastError;
  while (Date.now() < deadline) {
    try {
      const version = await httpJson(`http://127.0.0.1:${port}/json/version`, 3000);
      if (version.webSocketDebuggerUrl) return version;
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 600));
  }
  throw new Error(`未能连接浏览器调试接口。${lastError ? ` ${lastError.message}` : ''}`);
}

class MinimalWebSocket {
  constructor(urlText) {
    this.url = new URL(urlText);
    if (this.url.protocol !== 'ws:') throw new Error('仅允许连接本机 ws:// 调试接口。');
    if (!['127.0.0.1', 'localhost', '::1'].includes(this.url.hostname)) throw new Error('拒绝连接非本机 WebSocket。');
  }

  request(payload, timeoutMs = 10000) {
    return new Promise((resolve, reject) => {
      const key = crypto.randomBytes(16).toString('base64');
      const socket = net.createConnection({ host: this.url.hostname, port: Number(this.url.port) });
      let buffer = Buffer.alloc(0);
      let upgraded = false;
      let settled = false;
      const timer = setTimeout(() => finish(new Error('WebSocket 请求超时。')), timeoutMs);
      const finish = (error, value) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        socket.destroy();
        if (error) reject(error); else resolve(value);
      };
      socket.on('connect', () => {
        const target = `${this.url.pathname}${this.url.search}`;
        socket.write([
          `GET ${target} HTTP/1.1`,
          `Host: ${this.url.host}`,
          'Upgrade: websocket',
          'Connection: Upgrade',
          `Sec-WebSocket-Key: ${key}`,
          'Sec-WebSocket-Version: 13',
          '\r\n',
        ].join('\r\n'));
      });
      socket.on('data', (chunk) => {
        buffer = Buffer.concat([buffer, chunk]);
        if (!upgraded) {
          const headerEnd = buffer.indexOf('\r\n\r\n');
          if (headerEnd < 0) return;
          const header = buffer.subarray(0, headerEnd).toString('utf8');
          if (!/^HTTP\/1\.1 101\b/m.test(header)) {
            finish(new Error('浏览器拒绝 WebSocket 调试连接。'));
            return;
          }
          buffer = buffer.subarray(headerEnd + 4);
          upgraded = true;
          socket.write(MinimalWebSocket.encodeClientText(JSON.stringify(payload)));
        }
        while (upgraded) {
          const frame = MinimalWebSocket.decodeServerFrame(buffer);
          if (!frame) break;
          buffer = buffer.subarray(frame.consumed);
          if (frame.opcode === 0x8) {
            finish(new Error('浏览器关闭了调试连接。'));
            return;
          }
          if (frame.opcode === 0x1) {
            try {
              const parsed = JSON.parse(frame.payload.toString('utf8'));
              if (parsed.id === payload.id) {
                finish(null, parsed);
                return;
              }
            } catch {
              // Ignore unrelated malformed frames.
            }
          }
        }
      });
      socket.on('error', (error) => finish(error));
      socket.on('end', () => finish(new Error('浏览器调试连接已断开。')));
    });
  }

  static encodeClientText(text) {
    const payload = Buffer.from(text, 'utf8');
    const mask = crypto.randomBytes(4);
    let header;
    if (payload.length < 126) {
      header = Buffer.from([0x81, 0x80 | payload.length]);
    } else if (payload.length <= 0xffff) {
      header = Buffer.alloc(4);
      header[0] = 0x81;
      header[1] = 0x80 | 126;
      header.writeUInt16BE(payload.length, 2);
    } else {
      header = Buffer.alloc(10);
      header[0] = 0x81;
      header[1] = 0x80 | 127;
      header.writeBigUInt64BE(BigInt(payload.length), 2);
    }
    const masked = Buffer.alloc(payload.length);
    for (let i = 0; i < payload.length; i += 1) masked[i] = payload[i] ^ mask[i % 4];
    return Buffer.concat([header, mask, masked]);
  }

  static decodeServerFrame(buffer) {
    if (buffer.length < 2) return null;
    const opcode = buffer[0] & 0x0f;
    const masked = Boolean(buffer[1] & 0x80);
    let length = buffer[1] & 0x7f;
    let offset = 2;
    if (length === 126) {
      if (buffer.length < 4) return null;
      length = buffer.readUInt16BE(2);
      offset = 4;
    } else if (length === 127) {
      if (buffer.length < 10) return null;
      const big = buffer.readBigUInt64BE(2);
      if (big > BigInt(Number.MAX_SAFE_INTEGER)) throw new Error('WebSocket 帧过大。');
      length = Number(big);
      offset = 10;
    }
    let mask;
    if (masked) {
      if (buffer.length < offset + 4) return null;
      mask = buffer.subarray(offset, offset + 4);
      offset += 4;
    }
    if (buffer.length < offset + length) return null;
    const payload = Buffer.from(buffer.subarray(offset, offset + length));
    if (masked) for (let i = 0; i < payload.length; i += 1) payload[i] ^= mask[i % 4];
    return { opcode, payload, consumed: offset + length };
  }
}

async function cdpCall(wsUrl, method, params = {}) {
  const payload = { id: Math.floor(Math.random() * 1_000_000) + 1, method, params };
  if (typeof WebSocket === 'function') {
    return new Promise((resolve, reject) => {
      const socket = new WebSocket(wsUrl);
      const timer = setTimeout(() => {
        try { socket.close(); } catch {}
        reject(new Error(`CDP ${method} 请求超时。`));
      }, 12000);
      socket.addEventListener('open', () => socket.send(JSON.stringify(payload)));
      socket.addEventListener('message', (event) => {
        try {
          const data = JSON.parse(String(event.data));
          if (data.id !== payload.id) return;
          clearTimeout(timer);
          socket.close();
          if (data.error) reject(new Error(data.error.message || `CDP ${method} 失败。`));
          else resolve(data.result || {});
        } catch (error) {
          clearTimeout(timer);
          try { socket.close(); } catch {}
          reject(error);
        }
      });
      socket.addEventListener('error', () => {
        clearTimeout(timer);
        reject(new Error(`CDP ${method} WebSocket 连接失败。`));
      });
    });
  }
  const response = await new MinimalWebSocket(wsUrl).request(payload);
  if (response.error) throw new Error(response.error.message || `CDP ${method} 失败。`);
  return response.result || {};
}

async function listTabs(port) {
  const targets = await httpJson(`http://127.0.0.1:${port}/json/list`);
  return targets.filter((target) => target.type === 'page' && /^https?:/i.test(target.url || ''));
}

async function acquireCookieFromCDP(session) {
  await waitForCDP(session.port);
  const tabs = await listTabs(session.port);
  const gladosTabs = tabs.filter((tab) => {
    try { validatePinnedPage(tab.url, expectedCaptureIdentity().host); return true; } catch { return false; }
  });
  if (!gladosTabs.length) throw new Error('没有找到 GLaDOS 标签页。请在专用浏览器窗口中打开签到页面。');
  const labels = gladosTabs.map((tab, index) => `${index + 1}. ${tab.title || 'GLaDOS'} — ${tab.url}`);
  const selectedLabel = gladosTabs.length === 1 ? labels[0] : choose(labels, '选择需要读取登录信息的 GLaDOS 标签页：');
  if (!selectedLabel) throw new Error('已取消选择 GLaDOS 标签页。');
  const selectedIndex = labels.indexOf(selectedLabel);
  const selectedTab = gladosTabs[selectedIndex];
  const page = validatePinnedPage(selectedTab.url, expectedCaptureIdentity().host);
  const readContext = async () => {
    const response = await cdpCall(selectedTab.webSocketDebuggerUrl, 'Runtime.evaluate', {
      expression: '({userAgent:navigator.userAgent,pageUrl:location.href})',
      returnByValue: true,
    });
    if (response.exceptionDetails) throw new Error('无法读取当前登录页面的浏览器信息。');
    return normalizeBrowserContext(response.result?.value, page.host);
  };
  const before = await readContext();
  const result = await cdpCall(selectedTab.webSocketDebuggerUrl, 'Network.getCookies', { urls: [`${page.origin}/api/user/status`] });
  const after = await readContext();
  assertSameBrowserContext(before, after);
  const parts = selectSessionCookies(result.cookies, page.host);
  const cookieHeader = composeCookieHeader(parts);
  return { cookieHeader, parts, ...after, capturedAt: new Date().toISOString() };
}

async function acquireCookieFromWebDriver(session) {
  const encoded = encodeURIComponent(session.sessionId);
  const currentUrlResponse = await localJsonRequest(session.port, 'GET', `/session/${encoded}/url`, null, 15000);
  const currentUrl = String(unwrapWebDriverValue(currentUrlResponse) || '');
  const page = validatePinnedPage(currentUrl, expectedCaptureIdentity().host);
  const readContext = async () => {
    const response = await localJsonRequest(session.port, 'POST', `/session/${encoded}/execute/sync`, {
      script: 'return {userAgent:navigator.userAgent,pageUrl:location.href};', args: [],
    }, 15000);
    return normalizeBrowserContext(unwrapWebDriverValue(response), page.host);
  };
  const before = await readContext();
  const cookiesResponse = await localJsonRequest(session.port, 'GET', `/session/${encoded}/cookie`, null, 15000);
  const cookies = unwrapWebDriverValue(cookiesResponse);
  const after = await readContext();
  assertSameBrowserContext(before, after);
  const parts = selectSessionCookies(cookies, page.host);
  const cookieHeader = composeCookieHeader(parts);
  return { cookieHeader, parts, ...after, capturedAt: new Date().toISOString() };
}

function normalizeBrowserContext(value, expectedHost = '') {
  if (!value || typeof value !== 'object') throw new Error('未取得当前登录浏览器的信息。');
  return { ...validatePinnedPage(value.pageUrl, expectedHost), userAgent: normalizeUserAgent(value.userAgent) };
}

function assertSameBrowserContext(before, after) {
  if (before.origin !== after.origin || before.userAgent !== after.userAgent) {
    throw new Error('读取期间登录页面或浏览器信息发生变化，请保持原页面后手动重新读取。');
  }
}

async function acquireCookieFromSafariExtension(session) {
  while (true) {
    const captured = await session.waitForCapture(1500);
    if (captured) return captured;
    const action = appleScript([
      'on run argv',
      'set answer to display dialog "尚未收到 Safari 扩展发送的账号。\n\n请在普通 Safari 中完成登录，然后点击工具栏里的“GLaDOS Account Center Safari 扩展”，再点击“发送当前账号”。\n\n发送成功后回到这里重新检查。" with title "GLaDOS Account Center" buttons {"取消", "扩展设置", "重新检查"} default button "重新检查" cancel button "取消" with icon caution',
      'return button returned of answer',
      'end run',
    ]);
    if (!action || action === '取消') throw new Error('已取消读取 Safari 账号。');
    if (action === '扩展设置') openSafariExtensionSetup();
  }
}

async function acquireCookie(session) {
  if (session.kind === 'safari-extension') return acquireCookieFromSafariExtension(session);
  if (session.kind === 'cdp') return acquireCookieFromCDP(session);
  if (session.kind === 'webdriver') return acquireCookieFromWebDriver(session);
  throw new Error(`不支持的浏览器会话类型：${session.kind}`);
}

async function verifyCookie(host, cookieHeader, userAgent, parts, options = {}) {
  const page = validatePinnedPage(`https://${host}/console/checkin`, options.expectedHost || '');
  const actualUserAgent = normalizeUserAgent(userAgent);
  if (composeCookieHeader(parts) !== cookieHeader) throw new Error('登录 Cookie 与本次手动读取的会话不一致。');
  const fetchImpl = options.fetchImpl || fetch;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetchImpl(`${page.origin}/api/user/status`, {
      method: 'GET',
      redirect: 'error',
      headers: {
        accept: 'application/json, text/plain, */*',
        cookie: cookieHeader,
        origin: page.origin,
        referer: `${page.origin}/console/checkin`,
        'user-agent': actualUserAgent,
      },
      signal: controller.signal,
    });
    const body = await response.text();
    if (!response.ok) throw new Error(`GLaDOS 状态接口返回 HTTP ${response.status}。`);
    let data;
    try { data = JSON.parse(body); } catch { throw new Error('GLaDOS 状态接口返回了无法解析的数据。'); }
    // Do not save response Set-Cookie or infer check-in success from this read-only endpoint.
    return normalizeStatusIdentity(data, parts, options.expectedAccountKey || '');
  } finally {
    clearTimeout(timeout);
  }
}

function buildCapturePayload(acquired, verified, browser, capturedAt = acquired.capturedAt || new Date().toISOString()) {
  const page = validatePinnedPage(acquired.pageUrl || `https://${acquired.host}/console/checkin`, acquired.host);
  if (typeof capturedAt !== 'string' || !Number.isFinite(Date.parse(capturedAt))) throw new Error('读取时间无效。');
  if (composeCookieHeader(acquired.parts) !== acquired.cookieHeader) throw new Error('登录 Cookie 与手动读取结果不一致。');
  return {
    accountKey: verified.accountKey,
    secretName: verified.secretName,
    cookieHeader: acquired.cookieHeader,
    email: verified.accountEmail,
    daysLeft: verified.leftDays,
    host: page.host,
    browser,
    userAgent: normalizeUserAgent(acquired.userAgent),
    capturedAt: new Date(capturedAt).toISOString(),
  };
}

function findExecutable(name) {
  const candidates = [
    `/opt/homebrew/bin/${name}`,
    `/usr/local/bin/${name}`,
    `/usr/bin/${name}`,
  ];
  for (const candidate of candidates) if (fs.existsSync(candidate)) return candidate;
  const found = run('/bin/zsh', ['-lc', `command -v ${name}`], { timeout: 10000 });
  if (found.status === 0) {
    const value = String(found.stdout || '').trim().split('\n')[0];
    if (value && fs.existsSync(value)) return value;
  }
  return null;
}

function browserGitHubUrls(browser) {
  let script;
  if (browser.id === 'safari') {
    script = [
      'on run argv',
      'set outputText to ""',
      'tell application "Safari"',
      'repeat with w in windows',
      'repeat with t in tabs of w',
      'set u to URL of t',
      'if u starts with "https://github.com/" then set outputText to outputText & u & linefeed',
      'end repeat',
      'end repeat',
      'end tell',
      'return outputText',
      'end run',
    ];
  } else if (browser.kind === 'cdp') {
    script = [
      'on run argv',
      'set appName to item 1 of argv',
      'set outputText to ""',
      'using terms from application "Google Chrome"',
      'tell application appName',
      'repeat with w in windows',
      'repeat with t in tabs of w',
      'set u to URL of t',
      'if u starts with "https://github.com/" then set outputText to outputText & u & linefeed',
      'end repeat',
      'end repeat',
      'end tell',
      'end using terms from',
      'return outputText',
      'end run',
    ];
  } else {
    return [];
  }
  try {
    const output = appleScript(script, [browser.appName]);
    return String(output || '').split(/\r?\n/).map((value) => value.trim()).filter(Boolean);
  } catch {
    return [];
  }
}

function selectRepository(installed) {
  const mode = choose([
    `使用默认仓库：${DEFAULT_REPO}`,
    '从已打开的 GitHub 浏览器标签页识别',
    '手动输入 owner/repo',
  ], '选择 GitHub 仓库来源：');
  if (!mode) return null;
  if (mode.startsWith('使用默认仓库')) return DEFAULT_REPO;
  if (mode.startsWith('手动输入')) {
    const value = promptText('输入 GitHub 仓库，格式为 owner/repo：', DEFAULT_REPO);
    if (!value || !isSafeRepo(value)) throw new Error('GitHub 仓库格式无效。');
    return value;
  }
  const browserLabel = choose(installed.map((item) => item.label), '选择包含 GitHub 页面且已开启的浏览器：');
  if (!browserLabel) return null;
  const browser = installed.find((item) => item.label === browserLabel);
  const urls = browserGitHubUrls(browser);
  const repos = [...new Set(urls.map(parseGitHubRepo).filter(Boolean))];
  if (!repos.length) {
    alert('没有识别到有效的 GitHub 仓库标签页。将改为手动输入。', 'caution');
    const value = promptText('输入 GitHub 仓库，格式为 owner/repo：', DEFAULT_REPO);
    if (!value || !isSafeRepo(value)) throw new Error('GitHub 仓库格式无效。');
    return value;
  }
  return repos.length === 1 ? repos[0] : choose(repos, '选择目标 GitHub 仓库：');
}

function ghRun(gh, args, options = {}) {
  const result = run(gh, args, { input: options.input, timeout: options.timeout || 120000 });
  if (result.status !== 0) {
    throw new Error(redact(String(result.stderr || result.stdout || `gh ${args[0]} 执行失败。`).trim()));
  }
  return String(result.stdout || '').trim();
}

function ensureGitHubReadyForAccounts(gh, repo, branch) {
  ghRun(gh, ['auth', 'status'], { timeout: 30000 });
  const repoJson = JSON.parse(ghRun(gh, ['repo', 'view', repo, '--json', 'nameWithOwner,defaultBranchRef,viewerPermission']));
  const allowedPermissions = new Set(['ADMIN', 'MAINTAIN', 'WRITE']);
  if (!allowedPermissions.has(repoJson.viewerPermission)) throw new Error(`当前 GitHub 账户对 ${repo} 没有写入权限。`);
  ghRun(gh, ['api', `repos/${repo}/branches/${encodeURIComponent(branch)}`], { timeout: 30000 });
  ghRun(gh, ['api', `repos/${repo}/contents/checkin.py?ref=${encodeURIComponent(branch)}`], { timeout: 30000 });
  ghRun(gh, ['api', `repos/${repo}/contents/logging_config.py?ref=${encodeURIComponent(branch)}`], { timeout: 30000 });
  return repoJson;
}

function listRepositorySecretNames(gh, repo) {
  const raw = ghRun(gh, ['secret', 'list', '--repo', repo, '--json', 'name'], { timeout: 60000 });
  const items = JSON.parse(raw || '[]');
  return items.map((item) => String(item.name || '')).filter(isSafeSecretName).sort();
}

function readRepositoryFileMetadata(gh, repo, branch, filePath) {
  const endpoint = `repos/${repo}/contents/${filePath}?ref=${encodeURIComponent(branch)}`;
  const result = run(gh, ['api', endpoint], { timeout: 60000 });
  if (result.status === 0) return JSON.parse(String(result.stdout || '{}'));
  const detail = String(result.stderr || result.stdout || '');
  if (/HTTP 404|Not Found/i.test(detail)) return null;
  throw new Error(redact(detail.trim() || `无法读取 ${filePath}。`));
}

function upsertRepositoryTextFile(gh, repo, branch, filePath, content, message) {
  const current = readRepositoryFileMetadata(gh, repo, branch, filePath);
  const payload = {
    message,
    content: Buffer.from(content, 'utf8').toString('base64'),
    branch,
  };
  if (current?.sha) payload.sha = current.sha;
  const input = Buffer.from(JSON.stringify(payload), 'utf8');
  try {
    return JSON.parse(ghRun(gh, ['api', `repos/${repo}/contents/${filePath}`, '--method', 'PUT', '--input', '-'], {
      input,
      timeout: 120000,
    }) || '{}');
  } finally {
    input.fill(0);
  }
}

async function waitForWorkflowRegistration(gh, repo, branch) {
  const deadline = Date.now() + 60000;
  let lastError = '';
  while (Date.now() < deadline) {
    const result = run(gh, ['workflow', 'view', MANAGED_WORKFLOW_NAME, '--repo', repo, '--ref', branch, '--yaml'], { timeout: 30000 });
    if (result.status === 0) return;
    lastError = redact(String(result.stderr || result.stdout || '').trim());
    await new Promise((resolve) => setTimeout(resolve, 2500));
  }
  throw new Error(`多账号工作流已提交，但 GitHub 尚未注册该工作流。${lastError ? `\n${lastError}` : ''}`);
}

async function triggerManagedWorkflowAndWait(gh, settings, accountKey) {
  const { repo, branch, workflow } = settings;
  const triggerStarted = Date.now();
  ghRun(gh, ['workflow', 'run', workflow, '--repo', repo, '--ref', branch, '-f', `account=${accountKey}`], { timeout: 60000 });
  notify('新账号的 GitHub Actions 已触发，正在等待运行结果。');

  let selectedRun = null;
  const discoveryDeadline = Date.now() + 60000;
  while (Date.now() < discoveryDeadline && !selectedRun) {
    await new Promise((resolve) => setTimeout(resolve, 2500));
    const raw = ghRun(gh, [
      'run', 'list', '--repo', repo, '--workflow', workflow, '--event', 'workflow_dispatch', '--limit', '10',
      '--json', 'databaseId,status,conclusion,url,createdAt,headBranch',
    ], { timeout: 60000 });
    const runs = JSON.parse(raw || '[]');
    selectedRun = runs.find((item) => {
      const created = Date.parse(item.createdAt || 0);
      return item.headBranch === branch && created >= triggerStarted - 10000;
    }) || null;
  }
  if (!selectedRun) throw new Error('工作流已触发，但未能定位新生成的运行记录。请到 GitHub Actions 页面确认。');

  const completionDeadline = Date.now() + 8 * 60 * 1000;
  while (Date.now() < completionDeadline) {
    const raw = ghRun(gh, ['run', 'view', String(selectedRun.databaseId), '--repo', repo, '--json', 'status,conclusion,url'], { timeout: 60000 });
    const current = JSON.parse(raw);
    selectedRun = { ...selectedRun, ...current };
    if (current.status === 'completed') break;
    await new Promise((resolve) => setTimeout(resolve, 5000));
  }
  if (selectedRun.status !== 'completed') throw new Error(`Actions 等待超时。运行地址：${selectedRun.url}`);

  let logs = '';
  try { logs = ghRun(gh, ['run', 'view', String(selectedRun.databaseId), '--repo', repo, '--log'], { timeout: 120000 }); } catch { logs = ''; }
  const evidence = summarizeRunLog(logs);
  return { run: selectedRun, evidence };
}

async function addOrUpdateIndependentAccount(gh, settings, cookieHeader, secretName) {
  const { repo, branch, workflow } = settings;
  if (!isSafeRepo(repo) || !isSafeBranch(branch) || !isSafeWorkflow(workflow) || !isManagedAccountSecretName(secretName)) {
    throw new Error('GitHub 多账号参数未通过安全校验。');
  }

  const beforeNames = listRepositorySecretNames(gh, repo);
  const existed = beforeNames.includes(secretName);
  if (!existed && beforeNames.length >= REPOSITORY_SECRET_LIMIT) {
    throw new Error(`仓库已达到 ${REPOSITORY_SECRET_LIMIT} 个 Actions Secrets 的上限，无法继续新增账号。`);
  }

  const cookieBuffer = Buffer.from(`${cookieHeader}\n`, 'utf8');
  try {
    ghRun(gh, ['secret', 'set', secretName, '--repo', repo], { input: cookieBuffer, timeout: 60000 });
  } finally {
    cookieBuffer.fill(0);
  }

  const managedNames = [...new Set([...beforeNames.filter(isManagedAccountSecretName), secretName])].sort();
  const workflowContent = buildManagedWorkflow(managedNames);
  upsertRepositoryTextFile(
    gh,
    repo,
    branch,
    MANAGED_WORKFLOW_PATH,
    workflowContent,
    existed ? 'Refresh GLaDOS multi-account workflow' : 'Add independent GLaDOS account check-in',
  );
  await waitForWorkflowRegistration(gh, repo, branch);
  const accountKey = accountKeyFromSecretName(secretName);
  const result = await triggerManagedWorkflowAndWait(gh, settings, accountKey);
  return {
    ...result,
    mode: existed ? 'updated' : 'added',
    accountKey,
    managedAccountCount: managedNames.length,
    legacySecretPreserved: beforeNames.includes(LEGACY_SECRET),
  };
}

function openTerminalCommand(command) {
  appleScript([
    'on run argv',
    'tell application "Terminal"',
    'activate',
    'do script (item 1 of argv)',
    'end tell',
    'end run',
  ], [command]);
}

function openTerminalForAuth(gh) {
  openTerminalCommand(`${JSON.stringify(gh)} auth login --web --git-protocol https --scopes repo,workflow`);
}

function openTerminalForWorkflowScope(gh) {
  openTerminalCommand(`${JSON.stringify(gh)} auth refresh -h github.com -s workflow`);
}

function browserChoiceLabel(browser) {
  if (browser.id === 'safari') return `${browser.label}（普通窗口 + 正式 Native Messaging 扩展）`;
  if (browser.id === 'firefox') return `${browser.label}（隔离自动化窗口）`;
  return `${browser.label}（专用资料窗口）`;
}

async function main() {
  let cookieHeader = null;
  let cookieParts = null;
  let browserSession = null;
  try {
    ensureDir(SUPPORT_DIR);
    const installed = installedBrowsers();
    if (!installed.length) {
      throw new Error('没有找到受支持的浏览器。当前支持 Safari、Chrome、Edge、Brave、Arc、Firefox、Opera、Opera GX、Vivaldi 和 Chromium。');
    }

    const requestedBrowserId = String(process.env.GLADOS_BROWSER_ID || '').trim();
    let browser;
    if (requestedBrowserId) {
      browser = installed.find((item) => item.id === requestedBrowserId);
      if (!browser) throw new Error(`所选浏览器不可用：${requestedBrowserId}`);
    } else {
      alert('多账号版会为每个 GLaDOS 账号创建独立的 GitHub Secret 和独立签到任务。新增账号不会替换 GLADOS_COOKIES，也不会修改旧账号的 gladosCheck.yml。\n\n应用在你手动确认后读取当前 GLaDOS 登录域名的完整 Cookie 与实际浏览器 User-Agent，并通过账号状态接口核验身份。');
      const choiceLabels = installed.map(browserChoiceLabel);
      const browserLabel = choose(choiceLabels, '选择用于打开 GLaDOS 的浏览器：');
      if (!browserLabel) return;
      browser = installed[choiceLabels.indexOf(browserLabel)];
    }

    try {
      browserSession = await startBrowserSession(browser);
    } catch (error) {
      if (browser.id === 'safari' && /Safari|Native Messaging|Bridge|扩展|桥接/i.test(String(error.message))) {
        const setup = confirm(`${error.message}\n\n是否打开 Safari 扩展安装与设置入口？`, '打开扩展设置');
        if (setup) openSafariExtensionSetup();
        return;
      }
      throw error;
    }

    const persistenceNote = browser.loginPersistence === 'normal-profile'
      ? '这是普通 Safari 窗口。应用会让你选择已打开的 GLaDOS 页面；未登录时也可以正常登录。只有你点击 Safari 扩展里的“发送当前账号”后才会读取。'
      : browser.loginPersistence === 'profile'
        ? '此专用资料窗口会保留 GLaDOS 登录状态，后续通常无需重新登录。'
        : '这是隔离自动化窗口；Firefox 可能需要每次重新登录。';
    const windowDescription = browser.id === 'safari' ? '普通 GLaDOS 窗口' : '受控 GLaDOS 窗口';
    const ready = confirm(`已打开 ${browser.label} 的${windowDescription}。\n\n请确认当前是需要新增或更新的账号，保持会员签到页面打开并等待加载完成。\n\n${persistenceNote}\n\n完成后点击“读取账号”。`, '读取账号');
    if (!ready) return;

    let acquired;
    try {
      acquired = await acquireCookie(browserSession);
    } catch (error) {
      if (browser.id === 'safari' && /Safari|Native Messaging|Bridge|扩展|桥接/i.test(String(error.message))) {
        const setup = confirm(`${error.message}

是否打开 Safari 扩展安装文件夹和设置说明？`, '打开扩展设置');
        if (setup) openSafariExtensionSetup();
        return;
      }
      throw error;
    }
    cookieHeader = acquired.cookieHeader;
    cookieParts = acquired.parts;
    const expected = expectedCaptureIdentity();
    const verified = await verifyCookie(acquired.host, cookieHeader, acquired.userAgent, cookieParts, {
      expectedAccountKey: expected.accountKey, expectedHost: expected.host,
    });
    const accountSecret = verified.secretName;
    const accountKey = verified.accountKey;
    const capturePayload = buildCapturePayload(acquired, verified, browser.label);
    const remainingDisplay = verified.leftDays === null ? '接口未提供' : `${verified.leftDays} 天`;
    const accountDisplay = verified.accountEmail ? `\n账户：${verified.accountEmail}` : '';
    if (process.env.GLADOS_CAPTURE_ONLY === '1') {
      process.stdout.write(`GLADOS_CAPTURE_JSON=${JSON.stringify(capturePayload)}\n`);
      return;
    }

    alert(`GLaDOS 账号身份验证成功。${accountDisplay}\n浏览器：${browser.label}\n域名：${acquired.host}\n剩余天数：${remainingDisplay}\n账号标识：${accountKey}\nCookie：已完整获取（不会显示或复制）\n此状态查询未执行签到。`, 'note');

    const repo = selectRepository(installed);
    if (!repo) return;
    const settings = {
      repo,
      branch: DEFAULT_BRANCH,
      workflow: MANAGED_WORKFLOW_NAME,
    };

    const gh = findExecutable('gh');
    if (!gh) {
      throw new Error('未找到 GitHub CLI（gh）。请先运行交付包中的“安装依赖.command”。');
    }
    try {
      ensureGitHubReadyForAccounts(gh, settings.repo, settings.branch);
    } catch (error) {
      if (/auth|login|logged|认证|登录/i.test(String(error.message))) {
        const startAuth = confirm('GitHub CLI 尚未完成授权，或缺少 repo/workflow 权限。\n\n是否打开终端重新授权？\n完成授权后，请重新打开本应用。', '打开终端授权');
        if (startAuth) openTerminalForAuth(gh);
        return;
      }
      throw error;
    }

    const currentSecretNames = listRepositorySecretNames(gh, settings.repo);
    const accountExists = currentSecretNames.includes(accountSecret);
    const managedAccountCount = currentSecretNames.filter(isManagedAccountSecretName).length;
    const legacyPresent = currentSecretNames.includes(LEGACY_SECRET);
    if (!accountExists && currentSecretNames.length >= REPOSITORY_SECRET_LIMIT) {
      throw new Error(`仓库已达到 ${REPOSITORY_SECRET_LIMIT} 个 Actions Secrets 的上限，无法新增账号。`);
    }

    const summary = [
      accountExists ? '检测到这是已经添加过的账号。' : '检测到这是一个新账号。',
      '',
      `操作类型：${accountExists ? '只更新该账号的 Cookie' : '新增独立账号签到'}`,
      `GLaDOS：${verified.accountEmail || acquired.host}（剩余天数：${remainingDisplay}）`,
      `账号标识：${accountKey}`,
      `GitHub 仓库：${settings.repo}`,
      `分支：${settings.branch}`,
      `多账号工作流：${settings.workflow}`,
      `独立 Secret：${accountSecret}`,
      `当前独立账号数：${managedAccountCount}`,
      '',
      accountExists
        ? '将替换此账号自己的 Secret；其他账号 Secret 和任务完全不变。'
        : '将新增一个 Secret 和一个独立签到任务；其他账号 Secret 和任务完全不变。',
      `旧版 ${LEGACY_SECRET}：${legacyPresent ? '已存在，将原样保留' : '未检测到，也不会创建或修改'}`,
      `旧版 ${LEGACY_WORKFLOW}：不会修改或删除。`,
      '不会把 Cookie 写入日志、剪贴板或工作流文件。',
    ].join('\n');
    const confirmationLabel = accountExists ? '更新此账号并运行' : '新增账号并运行';
    if (!confirm(summary, confirmationLabel)) return;

    const manualSession = { schema: 'glados.manual-session', version: 1, ...capturePayload };
    delete manualSession.secretName;
    delete manualSession.daysLeft;
    const result = await addOrUpdateIndependentAccount(gh, settings, JSON.stringify(manualSession), accountSecret);
    cookieHeader = null;
    if (cookieParts) {
      cookieParts.session = '';
      cookieParts.signature = '';
      for (const cookie of cookieParts.cookies || []) cookie.value = '';
      cookieParts = null;
    }

    const conclusion = result.run.conclusion || 'unknown';
    const evidence = result.evidence;
    const lines = [
      `账号操作：${result.mode === 'added' ? '已新增独立账号' : '已更新此账号'}`,
      `独立账号总数：${result.managedAccountCount}`,
      `账号标识：${result.accountKey}`,
      `Actions 结论：${conclusion}`,
      `运行地址：${result.run.url}`,
      `读取到的 Cookie 数：${evidence.loadedCookies ?? '日志未明确显示'}`,
      `实际签到证据：${evidence.actualCheckinObserved ? (evidence.repeat ? '已签到/重复签到' : '签到成功') : '未在日志中确认'}`,
      `旧版 ${LEGACY_SECRET}：${result.legacySecretPreserved ? '保持不变' : '原本不存在'}`,
    ];
    if (conclusion !== 'success') {
      lines.unshift('新账号的 GitHub Actions 运行失败；其他账号配置未被修改。');
      alert(lines.join('\n'), 'stop');
    } else if (!evidence.actualCheckinObserved || evidence.explicitFailure) {
      lines.unshift('账号已独立保存，但没有确认到实际签到成功。请打开运行地址查看日志。');
      alert(lines.join('\n'), 'caution');
    } else {
      lines.unshift(result.mode === 'added' ? '新账号已新增，旧账号不受影响。' : '此账号已更新，其他账号不受影响。');
      alert(lines.join('\n'), 'note');
    }
  } catch (error) {
    const errorMessage = redact(error && error.message ? error.message : String(error));
    if (/workflow scope|Resource not accessible|refusing to allow an OAuth App to create or update workflow|OAuth App access restrictions/i.test(errorMessage)) {
      const fixScope = confirm(`GitHub 当前授权缺少 workflow 权限，无法创建多账号工作流。\n\n是否打开终端补充该权限？\n完成后请重新运行应用。\n\n${errorMessage}`, '补充权限');
      const gh = findExecutable('gh');
      if (fixScope && gh) openTerminalForWorkflowScope(gh);
      return;
    }
    alert(`操作未完成：\n\n${errorMessage}`, 'stop');
  } finally {
    cookieHeader = null;
    if (cookieParts) {
      cookieParts.session = '';
      cookieParts.signature = '';
      for (const cookie of cookieParts.cookies || []) cookie.value = '';
      cookieParts = null;
    }
    if (browserSession && typeof browserSession.close === 'function') {
      try { await browserSession.close(); } catch {}
    }
  }
}

if (require.main === module) {
  process.on('uncaughtException', (error) => alert(`程序异常：\n\n${redact(error.message)}`, 'stop'));
  process.on('unhandledRejection', (error) => alert(`程序异常：\n\n${redact(error && error.message ? error.message : String(error))}`, 'stop'));
  main();
}

module.exports = { assertSameBrowserContext, buildCapturePayload, expectedCaptureIdentity, normalizeBrowserContext, verifyCookie };
