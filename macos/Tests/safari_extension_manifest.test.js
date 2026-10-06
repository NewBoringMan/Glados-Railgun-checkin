'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');

const resources = path.join(__dirname, '..', '..', 'app_integration', 'SafariExtensionSource', 'Resources');

test('Safari manifest uses native messaging and strict host allowlist', () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(resources, 'manifest.json'), 'utf8'));
  assert.equal(manifest.permissions.includes('nativeMessaging'), true);
  assert.equal(manifest.permissions.includes('cookies'), true);
  assert.equal(manifest.host_permissions.some((item) => item.includes('127.0.0.1')), false);
  assert.equal(manifest.host_permissions.some((item) => item === '<all_urls>'), false);
  assert.deepEqual(new Set(manifest.host_permissions), new Set([
    'https://glados.cloud/*',
    'https://*.glados.cloud/*',
    'https://railgun.info/*',
    'https://*.railgun.info/*',
  ]));
});

test('content script never auto-captures cookies', () => {
  const content = fs.readFileSync(path.join(resources, 'content.js'), 'utf8');
  const background = fs.readFileSync(path.join(resources, 'background.js'), 'utf8');
  assert.match(content, /REGISTER_PENDING/);
  assert.doesNotMatch(content, /cookies\.getAll/);
  assert.match(background, /MANUAL_CAPTURE/);
  assert.match(background, /sendNativeMessage/);
});
