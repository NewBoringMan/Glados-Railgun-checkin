'use strict';

const crypto = require('crypto');
const { composeCookieHeader, normalizeUserAgent, selectSessionCookies, validatePinnedPage } = require('./core');

const TOKEN_PATTERN = /^[a-f0-9]{64}$/i;
const MIN_PORT = 1024;
const MAX_PORT = 65535;

function isValidBridgeToken(token) {
  return typeof token === 'string' && TOKEN_PATTERN.test(token);
}

function isValidBridgePort(port) {
  if (!['string', 'number'].includes(typeof port)) return false;
  const value = Number(port);
  return Number.isInteger(value) && value >= MIN_PORT && value <= MAX_PORT;
}

function isAllowedSafariHost(host) {
  return typeof host === 'string' && /^[a-z0-9.-]+$/i.test(host) && !host.startsWith('.') && !host.endsWith('.') && !host.includes('..') &&
    ['glados.cloud', 'railgun.info'].includes(host.toLowerCase());
}

function normalizeSafariNativeCapture(payload, expectedToken, expectedPort, expectedHost) {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) throw new Error('Safari 扩展没有返回有效数据。');
  if (!isValidBridgeToken(expectedToken) || !isValidBridgeToken(payload.token) ||
      !crypto.timingSafeEqual(Buffer.from(payload.token), Buffer.from(expectedToken))) {
    throw new Error('Safari 扩展握手令牌无效。');
  }
  if (!isValidBridgePort(expectedPort) || Number(payload.port) !== Number(expectedPort)) {
    throw new Error('Safari 扩展返回的本机端口与当前会话不一致。');
  }
  if (payload.type !== 'CAPTURE_ACCOUNT') throw new Error('Safari 扩展消息类型无效。');

  const host = typeof payload.host === 'string' ? payload.host.toLowerCase() : '';
  if (!isAllowedSafariHost(host) || !isAllowedSafariHost(expectedHost)) throw new Error('Safari 扩展返回了非白名单域名。');
  if (host !== expectedHost.toLowerCase()) throw new Error('Safari 扩展返回的域名与本次手动读取的原域名不一致。');
  const page = validatePinnedPage(payload.pageUrl, host);
  const userAgent = normalizeUserAgent(payload.userAgent);
  if (!Array.isArray(payload.cookies) || payload.cookies.length > 256) throw new Error('Safari 扩展没有返回完整 Cookie 列表。');
  const parts = selectSessionCookies(payload.cookies, host);
  const capturedAt = new Date().toISOString();
  return {
    host,
    pageUrl: page.pageUrl,
    parts,
    cookieHeader: composeCookieHeader(parts),
    userAgent,
    capturedAt,
  };
}

module.exports = {
  isValidBridgePort,
  isValidBridgeToken,
  isAllowedSafariHost,
  normalizeSafariNativeCapture,
};
