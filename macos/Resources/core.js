'use strict';

const crypto = require('crypto');

const ALLOWED_HOSTS = ['glados.cloud', 'glados.network', 'glados.rocks', 'glados.one', 'glados.space', 'glados.vip', 'glados-facility.com', 'railgun.info'];
// The single Cookie header in manual-session v1 is reused by status.py/checkin.py.
const MANUAL_SESSION_PATHS = Object.freeze(['/api/user/status', '/api/user/session', '/api/user/points', '/api/user/checkin', '/api/user/exchange']);
const MAX_COOKIE_HEADER_BYTES = 32 * 1024;
const ACCOUNT_SECRET_PREFIX = 'GLADOS_ACCOUNT_';
const MANAGED_WORKFLOW_NAME = 'gladosAccounts.yml';

function normalizeHost(value) {
  return String(value || '').trim().toLowerCase().replace(/^\.+/, '').replace(/\.+$/, '');
}

function isAllowedHost(host) {
  if (typeof host !== 'string' || !/^[a-z0-9.-]+$/i.test(host) || host.startsWith('.') || host.endsWith('.') || host.includes('..')) return false;
  const normalized = normalizeHost(host);
  return ALLOWED_HOSTS.includes(normalized);
}

function cookieMatchesHost(cookie, host) {
  const domain = normalizeHost(cookie.domain);
  const target = normalizeHost(host);
  const hostOnly = cookie.hostOnly === true || (cookie.hostOnly === undefined && !String(cookie.domain || '').startsWith('.'));
  return domain === target || (!hostOnly && target.endsWith(`.${domain}`));
}

function normalizeUserAgent(value) {
  if (typeof value !== 'string' || !value.trim() || Buffer.byteLength(value, 'utf8') > 2048 || /[^\x20-\x7e]/.test(value)) {
    throw new Error('未取得有效的实际浏览器 User-Agent，请手动重新读取登录信息。');
  }
  return value.trim();
}

function validatePinnedPage(pageText, expectedHost = '') {
  let page;
  try { page = new URL(String(pageText || '')); } catch { throw new Error('登录页面地址无效。'); }
  if (page.protocol !== 'https:' || page.username || page.password || page.port || !isAllowedHost(page.hostname)) {
    throw new Error('登录页面不属于允许的 HTTPS GLaDOS 域名。');
  }
  if (expectedHost && (!isAllowedHost(expectedHost) || page.hostname !== expectedHost.toLowerCase())) {
    throw new Error('登录页面与原账号绑定的域名不一致，已停止读取。');
  }
  return { host: page.hostname, origin: page.origin, pageUrl: `${page.origin}${page.pathname}` };
}

function cookiePathMatches(cookiePath, requestPath) {
  const source = typeof cookiePath === 'string' && cookiePath.startsWith('/') ? cookiePath : '/';
  return requestPath === source || (requestPath.startsWith(source) && (source.endsWith('/') || requestPath[source.length] === '/'));
}

function validateCookiePair(name, value) {
  // The deployed signed-cookie names contain ':'. Preserve them verbatim.
  if (typeof name !== 'string' || !/^[!#$%&'*+\-.^_`|~0-9A-Za-z:]+$/.test(name)) throw new Error('Cookie 名称无效。');
  if (typeof value !== 'string' || /[^\x21-\x7e]|[;,]/.test(value) || value.length > 16 * 1024) throw new Error('Cookie 值格式或长度无效。');
}

function selectSessionCookies(cookies, host, options = {}) {
  if (!isAllowedHost(host)) {
    throw new Error('所选页面不属于允许的 GLaDOS 域名。');
  }
  const nowSeconds = (options.nowMs ?? Date.now()) / 1000;
  const requestPath = options.requestPath || '/api/user/status';
  const eligible = (Array.isArray(cookies) ? cookies : []).filter((cookie) => {
    if (!cookie || !isAllowedHost(normalizeHost(cookie.domain)) || !cookieMatchesHost(cookie, host) || !cookiePathMatches(cookie.path, requestPath)) return false;
    // Never combine partitioned credentials from a different browser context.
    if (cookie.partitionKey || cookie.partitioned) return false;
    const expires = cookie.expirationDate ?? cookie.expires ?? cookie.expiry;
    if (cookie.session !== true && expires !== undefined && Number(expires) !== -1 && Number(expires) <= nowSeconds) return false;
    return true;
  });
  const seen = new Map();
  for (const cookie of eligible) {
    validateCookiePair(cookie.name, cookie.value);
    if (seen.has(cookie.name)) throw new Error('同一域名返回了重名 Cookie，无法安全选择账号会话。');
    seen.set(cookie.name, { name: cookie.name, value: cookie.value, domain: cookie.domain, path: cookie.path || '/', hostOnly: cookie.hostOnly });
  }
  const completeGld = Boolean(seen.get('gld:sess')?.value && seen.get('gld:sess.sig')?.value);
  for (const prefix of ['gld', 'koa']) {
    const session = seen.get(`${prefix}:sess`);
    const signature = seen.get(`${prefix}:sess.sig`);
    if ((session || signature) && (!session?.value || !signature?.value) && !(prefix === 'koa' && completeGld)) {
      throw new Error(`缺少必要 Cookie：${!session?.value ? `${prefix}:sess` : `${prefix}:sess.sig`}。请手动重新读取完整登录信息。`);
    }
  }
  if (!seen.has('gld:sess') && !seen.has('koa:sess')) throw new Error('缺少完整的 gld:sess / gld:sess.sig 登录会话，请先手动登录。');
  const parts = {
    sessionKind: seen.has('gld:sess') ? 'gld' : 'koa',
    session: seen.get('koa:sess')?.value || '',
    signature: seen.get('koa:sess.sig')?.value || '',
    cookies: [...seen.values()],
  };
  composeCookieHeader(parts);
  return parts;
}

function composeCookieHeader(parts) {
  const cookies = parts?.cookies;
  if (!Array.isArray(cookies) || !cookies.length) throw new Error('Cookie 数据不完整。');
  const seen = new Set();
  for (const cookie of cookies) {
    validateCookiePair(cookie.name, cookie.value);
    if (seen.has(cookie.name)) throw new Error('Cookie 名称重复。');
    seen.add(cookie.name);
  }
  const header = cookies.map((cookie) => `${cookie.name}=${cookie.value}`).join('; ');
  if (Buffer.byteLength(header, 'utf8') > MAX_COOKIE_HEADER_BYTES) throw new Error('Cookie 总长度异常，已停止读取。');
  return header;
}

function cookieScopeError() {
  const error = new Error('各接口适用的登录 Cookie 不一致，当前保存格式无法安全复用，已停止且未保存。请保留原网页登录。');
  error.code = 'GLADOS_COOKIE_SCOPE_MISMATCH';
  return error;
}

function selectReusableSessionCookies(cookies, host, options = {}) {
  // Input must cover every path above (CDP URLs or the extension's explicit reads).
  // Never concatenate the union into a header: independently select each path.
  const nowMs = options.nowMs ?? Date.now();
  const first = selectSessionCookies(cookies, host, { nowMs, requestPath: MANUAL_SESSION_PATHS[0] });
  const canonical = (parts) => JSON.stringify(parts.cookies.map(({ name, value }) => [name, value]).sort((a, b) => a[0].localeCompare(b[0])));
  const expected = canonical(first);
  for (const requestPath of MANUAL_SESSION_PATHS.slice(1)) {
    let next;
    try { next = selectSessionCookies(cookies, host, { nowMs, requestPath }); }
    catch { throw cookieScopeError(); }
    if (canonical(next) !== expected) throw cookieScopeError();
  }
  return first;
}

function parseGitHubRepo(urlText) {
  let url;
  try {
    url = new URL(String(urlText || '').trim());
  } catch {
    return null;
  }
  if (url.protocol !== 'https:' || url.hostname.toLowerCase() !== 'github.com') return null;
  const segments = url.pathname.split('/').filter(Boolean);
  if (segments.length < 2) return null;
  const owner = segments[0];
  const repo = segments[1].replace(/\.git$/i, '');
  if (!/^[A-Za-z0-9_.-]+$/.test(owner) || !/^[A-Za-z0-9_.-]+$/.test(repo)) return null;
  return `${owner}/${repo}`;
}

function isSafeRepo(repo) {
  return /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(String(repo || ''));
}

function isSafeBranch(branch) {
  return /^[A-Za-z0-9._\/-]+$/.test(String(branch || '')) && !String(branch).includes('..');
}

function isSafeWorkflow(workflow) {
  return /^[A-Za-z0-9._-]+\.ya?ml$/i.test(String(workflow || ''));
}

function isSafeSecretName(name) {
  return /^[A-Z][A-Z0-9_]*$/.test(String(name || ''));
}

function decodeBase64Json(value) {
  const source = String(value || '').trim();
  if (!source) throw new Error('koa:sess 为空，无法识别账号。');
  const normalized = source.replace(/-/g, '+').replace(/_/g, '/');
  const padded = normalized + '='.repeat((4 - (normalized.length % 4 || 4)) % 4);
  let text;
  try {
    text = Buffer.from(padded, 'base64').toString('utf8');
  } catch {
    throw new Error('koa:sess 不是有效的 Base64 会话数据。');
  }
  try {
    return JSON.parse(text);
  } catch {
    throw new Error('koa:sess 中没有可解析的账号信息。');
  }
}

function findStableUserId(value, depth = 0) {
  if (!value || typeof value !== 'object' || depth > 5) return null;
  const preferredKeys = ['userId', 'user_id', 'uid'];
  for (const key of preferredKeys) {
    const candidate = value[key];
    if ((typeof candidate === 'string' || typeof candidate === 'number') && String(candidate).trim()) {
      return String(candidate).trim();
    }
  }
  for (const child of Object.values(value)) {
    const found = findStableUserId(child, depth + 1);
    if (found) return found;
  }
  return null;
}

function extractGladosUserId(sessionValue) {
  const payload = decodeBase64Json(sessionValue);
  const userId = findStableUserId(payload);
  if (!userId) throw new Error('无法从 GLaDOS 会话中识别稳定账号 ID，已停止写入以避免覆盖其他账号。');
  return userId;
}

function accountSecretNameFromSession(sessionValue) {
  const userId = extractGladosUserId(sessionValue);
  return accountSecretNameFromUserId(userId);
}

function normalizedUserId(value) {
  if (typeof value === 'number' && (!Number.isSafeInteger(value) || value <= 0)) throw new Error('状态接口账号 ID 无效。');
  if (!['string', 'number'].includes(typeof value) || !/^[A-Za-z0-9_-]{1,128}$/.test(String(value).trim())) throw new Error('状态接口账号 ID 无效。');
  return String(value).trim();
}

function accountSecretNameFromUserId(value) {
  const userId = normalizedUserId(value);
  const digest = crypto.createHash('sha256').update(`glados-user:${userId}`, 'utf8').digest('hex').slice(0, 16).toUpperCase();
  return `${ACCOUNT_SECRET_PREFIX}${digest}`;
}

function statusScopes(payload) {
  const object = (value) => value && typeof value === 'object' && !Array.isArray(value);
  if (!object(payload)) throw new Error('状态接口没有返回账号对象。');
  return [payload, payload.data, payload.user, payload.data?.user].filter(object);
}

function normalizeStatusIdentity(payload, parts = null, expectedAccountKey = '', sessionPayload = null) {
  if (sessionPayload !== null && sessionPayload?.code !== 0) throw new Error('会话接口拒绝认证，请手动重新登录并读取。');
  const payloads = sessionPayload === null ? [payload] : [payload, sessionPayload];
  const scopes = payloads.flatMap(statusScopes);
  const userScopes = new Set(payloads.flatMap((item) => [item.user, item.data?.user]));
  for (const scope of payloads.flatMap((item) => [item, item.data]).filter((value) => value && typeof value === 'object' && !Array.isArray(value))) {
    if ((scope.code !== undefined && scope.code !== 0) || scope.ok === false || scope.success === false) {
      const device = scope.reason === 'device-mismatch' || /automated check-in detected/i.test(String(scope.message || ''));
      throw new Error(device ? '状态接口拒绝当前登录设备，请手动重新登录并读取。' : '状态接口拒绝会话认证，请手动重新登录并读取。');
    }
  }
  const ids = [];
  const emails = [];
  const days = [];
  for (const scope of scopes) {
    for (const field of ['userId', 'user_id', 'uid']) {
      if (scope[field] !== undefined && scope[field] !== null) ids.push(normalizedUserId(scope[field]));
    }
    // A generic root `id` could identify a response or a plan; only `user.id` is unambiguous.
    if (userScopes.has(scope) && scope.id !== undefined) ids.push(normalizedUserId(scope.id));
    for (const field of ['email', 'userEmail', 'user_email']) {
      const value = scope[field];
      if (value === undefined || value === null || value === '') continue;
      if (typeof value !== 'string' || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value.trim()) || value.length > 320) throw new Error('状态接口邮箱格式无效。');
      emails.push(value.trim());
    }
    for (const field of ['leftDays', 'daysLeft', 'left_days']) {
      const value = scope[field];
      if (value === undefined || value === null || value === '') continue;
      if (!['string', 'number'].includes(typeof value) || !Number.isFinite(Number(value))) throw new Error('状态接口剩余天数格式无效。');
      days.push(Math.floor(Number(value)));
    }
  }
  if (new Set(ids).size > 1 || new Set(emails.map((email) => email.toLowerCase())).size > 1 || new Set(days).size > 1) {
    throw new Error('状态接口返回了相互冲突的账号信息，已停止保存。');
  }
  const userId = ids[0];
  // Legacy metadata is an optional consistency check, never an identity source or a gld expiry gate.
  if (userId && parts?.session) {
    let legacy = null;
    try { legacy = decodeBase64Json(parts.session); } catch { /* Opaque legacy cookies cannot be decoded. */ }
    if (legacy && typeof legacy === 'object' && !Array.isArray(legacy)) {
      const legacyIds = [];
      const visit = (scope, depth = 0) => {
        if (!scope || typeof scope !== 'object' || depth > 5) return;
        for (const field of ['userId', 'user_id', 'uid']) {
          if (scope[field] !== undefined && scope[field] !== null) legacyIds.push(normalizedUserId(scope[field]));
        }
        for (const child of Object.values(scope)) visit(child, depth + 1);
      };
      visit(legacy);
      if (legacyIds.some((id) => id !== userId)) throw new Error('新旧会话的账号身份不一致，已停止保存。');
    }
  }
  const secretName = userId ? accountSecretNameFromUserId(userId) : '';
  const accountKey = secretName ? accountKeyFromSecretName(secretName) : '';
  if (expectedAccountKey && (!/^[A-F0-9]{16}$/.test(expectedAccountKey) || (accountKey && expectedAccountKey !== accountKey))) {
    throw new Error('当前登录账号与要更新的账号不一致，已停止保存。');
  }
  if (!ids.length || !emails.length) {
    const error = new Error('只读身份接口未提供可核验的账号 ID 与邮箱，已停止保存；积分可读不能替代身份验证。');
    error.code = 'INCOMPLETE_API_IDENTITY';
    throw error;
  }
  return { userId, accountKey, secretName, accountEmail: emails[0], leftDays: days.length ? days[0] : null };
}

function isManagedAccountSecretName(name) {
  return new RegExp(`^${ACCOUNT_SECRET_PREFIX}[A-F0-9]{16}$`).test(String(name || ''));
}

function accountKeyFromSecretName(name) {
  if (!isManagedAccountSecretName(name)) throw new Error('账号 Secret 名称无效。');
  return String(name).slice(ACCOUNT_SECRET_PREFIX.length);
}

function buildManagedWorkflow(secretNames) {
  const accounts = [...new Set((secretNames || []).map(String))]
    .filter(isManagedAccountSecretName)
    .sort();
  if (!accounts.length) throw new Error('没有可写入工作流的新增账号。');

  const lines = [
    '# Managed by GLaDOS Account Center V2.',
    '# Account cookies remain in independent GitHub Actions secrets.',
    'name: GLaDOS Multi-Account Check-in',
    '',
    'on:',
    '  workflow_dispatch:',
    '    inputs:',
    '      account:',
    '        description: Account key to run, or all',
    '        required: false',
    '        default: all',
    '        type: string',
    '  schedule:',
    "    - cron: '0 5,17 * * *'",
    "      timezone: 'Asia/Taipei'",
    '',
    'permissions:',
    '  contents: read',
    '',
    'concurrency:',
    '  group: glados-multi-account-${{ github.ref }}',
    '  cancel-in-progress: false',
    '',
    'jobs:',
  ];

  for (const secretName of accounts) {
    const key = accountKeyFromSecretName(secretName);
    const jobId = `account_${key.toLowerCase()}`;
    lines.push(
      `  ${jobId}:`,
      `    name: GLaDOS account ${key}`,
      `    if: \${{ github.event_name == 'schedule' || inputs.account == 'all' || inputs.account == '${key}' }}`,
      '    runs-on: ubuntu-latest',
      '    timeout-minutes: 5',
      '    steps:',
      '      - name: Checkout code',
      '        uses: actions/checkout@v6',
      '',
      '      - name: Set up Python',
      '        uses: actions/setup-python@v6',
      '        with:',
      "          python-version: '3.13'",
      '',
      '      - name: Install dependencies',
      '        run: pip install -r requirements.txt',
      '',
      '      - name: Run check-in',
      '        env:',
      `          GLADOS_COOKIES: \${{ secrets.${secretName} }}`,
      `          GLADOS_ACCOUNT_KEY: '${key}'`,
      "          GLADOS_AUTO_EXCHANGE: 'false'",
      "          GLADOS_EXCHANGE_CATALOG: '.github/glados/exchange_plans.json'",
      '          PUSHDEER_SENDKEY: ${{ secrets.PUSHDEER_SENDKEY }}',
      "          GLADOS_VERBOSE: 'true'",
      '        run: python checkin.py',
      '',
    );
  }

  return `${lines.join('\n').trimEnd()}\n`;
}

function redact(text) {
  let output = String(text ?? '');
  output = output.replace(/(?:gld|koa):sess(?:\.sig)?=[^;\s]+;?/gi, '[REDACTED_COOKIE]');
  output = output.replace(/((?:glados-assistant|token)=)[a-f0-9]{64}/gi, '$1[REDACTED_TOKEN]');
  output = output.replace(/("(?:token|cookieHeader|session|signature)"\s*:\s*")[^"\r\n]*(")/gi, '$1[REDACTED]$2');
  output = output.replace(/(gh[oprsu]_[A-Za-z0-9_]{20,})/g, '[REDACTED_TOKEN]');
  output = output.replace(/github_pat_[A-Za-z0-9_]+/g, '[REDACTED_TOKEN]');
  return output;
}

function summarizeRunLog(logText) {
  const text = String(logText || '');
  const loadedMatch = text.match(/共加载了\s*(\d+)\s*个\s*Cookie/);
  const loadedCookies = loadedMatch ? Number(loadedMatch[1]) : null;
  const success = /签到成功/.test(text);
  const repeat = /重复签到/.test(text);
  const explicitFailure = /签到失败|未找到有效的 Cookie|未找到 cookies|No successful or repeated check-in evidence/i.test(text);
  return {
    loadedCookies,
    actualCheckinObserved: success || repeat,
    success,
    repeat,
    explicitFailure,
  };
}

module.exports = {
  ACCOUNT_SECRET_PREFIX,
  ALLOWED_HOSTS,
  MANAGED_WORKFLOW_NAME,
  MANUAL_SESSION_PATHS,
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
  selectReusableSessionCookies,
  summarizeRunLog,
  validatePinnedPage,
};
