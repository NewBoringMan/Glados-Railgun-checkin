'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {
  buildBrowserCatalog,
  extractSessionId,
  unwrapWebDriverValue,
  webdriverCapabilities,
} = require('../Resources/browser_support');

test('browser catalog includes common macOS browsers', () => {
  const ids = new Set(buildBrowserCatalog('/Users/test').map((item) => item.id));
  for (const id of ['safari', 'chrome', 'edge', 'brave', 'arc', 'firefox', 'opera', 'vivaldi', 'chromium']) {
    assert.equal(ids.has(id), true, `missing ${id}`);
  }
});

test('Safari uses the normal-profile Native Messaging extension instead of WebDriver', () => {
  const safari = buildBrowserCatalog('/Users/test').find((item) => item.id === 'safari');
  assert.equal(safari.kind, 'safari-extension');
  assert.equal(safari.loginPersistence, 'normal-profile');
  assert.throws(() => webdriverCapabilities(safari, '/Applications/Safari.app/Contents/MacOS/Safari'), /不是 WebDriver/);
});

test('capture catalog recognizes the same existing MacData installation as the native picker', () => {
  const edge = buildBrowserCatalog('/Users/test').find((item) => item.id === 'edge');
  assert.deepEqual(edge.paths, [
    '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
    '/Users/test/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
    '/Volumes/MacData/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
  ]);
});

test('Firefox capabilities preserve the selected binary', () => {
  const firefox = buildBrowserCatalog('/Users/test').find((item) => item.id === 'firefox');
  const caps = webdriverCapabilities(firefox, '/Applications/Firefox.app/Contents/MacOS/firefox');
  assert.equal(caps.capabilities.alwaysMatch.browserName, 'firefox');
  assert.equal(caps.capabilities.alwaysMatch.webSocketUrl, true);
  assert.equal(caps.capabilities.alwaysMatch['moz:firefoxOptions'].binary, '/Applications/Firefox.app/Contents/MacOS/firefox');
});

test('WebDriver response helpers support W3C responses', () => {
  const response = { value: { sessionId: 'abc', answer: 42 } };
  assert.equal(extractSessionId(response), 'abc');
  assert.deepEqual(unwrapWebDriverValue(response), { sessionId: 'abc', answer: 42 });
});
