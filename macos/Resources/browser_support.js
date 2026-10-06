'use strict';

const os = require('os');
const path = require('path');

function buildBrowserCatalog(homeDir = os.homedir()) {
  const userApplications = path.join(homeDir, 'Applications');
  return [
    {
      id: 'safari',
      kind: 'safari-extension',
      label: 'Safari',
      appName: 'Safari',
      paths: [
        '/Applications/Safari.app/Contents/MacOS/Safari',
        '/System/Applications/Safari.app/Contents/MacOS/Safari',
      ],
      loginPersistence: 'normal-profile',
    },
    {
      id: 'chrome',
      kind: 'cdp',
      label: 'Google Chrome',
      appName: 'Google Chrome',
      preferredPort: 19222,
      paths: [
        '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
        path.join(userApplications, 'Google Chrome.app/Contents/MacOS/Google Chrome'),
      ],
      loginPersistence: 'profile',
    },
    {
      id: 'edge',
      kind: 'cdp',
      label: 'Microsoft Edge',
      appName: 'Microsoft Edge',
      preferredPort: 19224,
      paths: [
        '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
        path.join(userApplications, 'Microsoft Edge.app/Contents/MacOS/Microsoft Edge'),
      ],
      loginPersistence: 'profile',
    },
    {
      id: 'brave',
      kind: 'cdp',
      label: 'Brave Browser',
      appName: 'Brave Browser',
      preferredPort: 19223,
      paths: [
        '/Applications/Brave Browser.app/Contents/MacOS/Brave Browser',
        path.join(userApplications, 'Brave Browser.app/Contents/MacOS/Brave Browser'),
      ],
      loginPersistence: 'profile',
    },
    {
      id: 'arc',
      kind: 'cdp',
      label: 'Arc',
      appName: 'Arc',
      preferredPort: 19227,
      paths: [
        '/Applications/Arc.app/Contents/MacOS/Arc',
        path.join(userApplications, 'Arc.app/Contents/MacOS/Arc'),
      ],
      loginPersistence: 'profile',
    },
    {
      id: 'firefox',
      kind: 'webdriver',
      driver: 'geckodriver',
      driverPaths: [],
      driverArgs: (port) => ['--port', String(port), '--log', 'error'],
      label: 'Mozilla Firefox',
      appName: 'Firefox',
      preferredPort: 19226,
      paths: [
        '/Applications/Firefox.app/Contents/MacOS/firefox',
        path.join(userApplications, 'Firefox.app/Contents/MacOS/firefox'),
      ],
      loginPersistence: 'session',
    },
    {
      id: 'opera',
      kind: 'cdp',
      label: 'Opera',
      appName: 'Opera',
      preferredPort: 19228,
      paths: [
        '/Applications/Opera.app/Contents/MacOS/Opera',
        path.join(userApplications, 'Opera.app/Contents/MacOS/Opera'),
      ],
      loginPersistence: 'profile',
    },
    {
      id: 'opera-gx',
      kind: 'cdp',
      label: 'Opera GX',
      appName: 'Opera GX',
      preferredPort: 19232,
      paths: [
        '/Applications/Opera GX.app/Contents/MacOS/Opera GX',
        path.join(userApplications, 'Opera GX.app/Contents/MacOS/Opera GX'),
      ],
      loginPersistence: 'profile',
    },
    {
      id: 'vivaldi',
      kind: 'cdp',
      label: 'Vivaldi',
      appName: 'Vivaldi',
      preferredPort: 19229,
      paths: [
        '/Applications/Vivaldi.app/Contents/MacOS/Vivaldi',
        path.join(userApplications, 'Vivaldi.app/Contents/MacOS/Vivaldi'),
      ],
      loginPersistence: 'profile',
    },
    {
      id: 'chromium',
      kind: 'cdp',
      label: 'Chromium',
      appName: 'Chromium',
      preferredPort: 19230,
      paths: [
        '/Applications/Chromium.app/Contents/MacOS/Chromium',
        path.join(userApplications, 'Chromium.app/Contents/MacOS/Chromium'),
      ],
      loginPersistence: 'profile',
    },
  ];
}

function webdriverCapabilities(browser, binaryPath) {
  if (!browser || browser.kind !== 'webdriver') {
    throw new Error('浏览器不是 WebDriver 类型。');
  }
  if (browser.id === 'firefox') {
    const firefoxOptions = {};
    if (binaryPath) firefoxOptions.binary = binaryPath;
    return {
      capabilities: {
        alwaysMatch: {
          browserName: 'firefox',
          acceptInsecureCerts: false,
          'moz:firefoxOptions': firefoxOptions,
        },
      },
    };
  }
  throw new Error(`不支持的 WebDriver 浏览器：${browser.id}`);
}

function extractSessionId(response) {
  if (!response || typeof response !== 'object') return null;
  if (typeof response.sessionId === 'string' && response.sessionId) return response.sessionId;
  if (response.value && typeof response.value.sessionId === 'string' && response.value.sessionId) {
    return response.value.sessionId;
  }
  return null;
}

function unwrapWebDriverValue(response) {
  if (response && typeof response === 'object' && Object.prototype.hasOwnProperty.call(response, 'value')) {
    return response.value;
  }
  return response;
}

module.exports = {
  buildBrowserCatalog,
  extractSessionId,
  unwrapWebDriverValue,
  webdriverCapabilities,
};
