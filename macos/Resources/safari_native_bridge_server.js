'use strict';

const net = require('net');
const { isAllowedSafariHost, isValidBridgeToken, normalizeSafariNativeCapture } = require('./safari_native_protocol');

const MAX_CAPTURE_BYTES = 64 * 1024;

function validateBridgeConfig(config) {
  if (!config || !isValidBridgeToken(config.token) || !isAllowedSafariHost(config.host)) throw new Error('INVALID_BRIDGE_CONFIG');
  return { token: config.token, host: config.host.toLowerCase() };
}

// A pure one-use receiver lets tests exercise the protocol without opening a socket.
function createCaptureReceiver(config, getPort) {
  const { token, host } = validateBridgeConfig(config);
  let finished = false;
  return (line) => {
    if (finished) return { response: { ok: false, reason: 'already_captured' } };
    if (typeof line !== 'string' || Buffer.byteLength(line, 'utf8') > MAX_CAPTURE_BYTES) {
      return { response: { ok: false, reason: 'payload_too_large' } };
    }
    try {
      const capture = normalizeSafariNativeCapture(JSON.parse(line), token, getPort(), host);
      finished = true;
      return { response: { ok: true }, capture };
    } catch {
      return { response: { ok: false, reason: 'invalid_capture' } };
    }
  };
}

function startBridge(config) {
  let listeningPort = null;
  const receive = createCaptureReceiver(config, () => listeningPort);
  const deadline = setTimeout(() => shutdown(3), 30 * 60 * 1000);
  const server = net.createServer((socket) => {
    socket.setEncoding('utf8');
    socket.setTimeout(8000, () => socket.destroy());
    let buffer = '';
    let settled = false;
    socket.on('data', (chunk) => {
      if (settled) return;
      buffer += String(chunk || '');
      const newline = buffer.indexOf('\n');
      if (newline < 0 && Buffer.byteLength(buffer, 'utf8') <= MAX_CAPTURE_BYTES) return;
      settled = true;
      const result = receive(newline < 0 ? buffer : buffer.slice(0, newline));
      buffer = '';
      try { socket.end(`${JSON.stringify(result.response)}\n`); } catch { socket.destroy(); }
      if (result.capture) {
        // This stdout is a private pipe to the app, never a log or a public URL.
        process.stdout.write(`CAPTURE ${JSON.stringify(result.capture)}\n`);
        setTimeout(() => shutdown(0), 150).unref();
      }
    });
    socket.on('error', () => {});
  });

  function shutdown(code = 0) {
    clearTimeout(deadline);
    try { server.close(() => process.exit(code)); } catch { process.exit(code); }
    setTimeout(() => process.exit(code), 1000).unref();
  }

  server.on('error', () => {
    process.stderr.write('SERVER_ERROR\n');
    shutdown(4);
  });
  server.listen(0, '127.0.0.1', () => {
    listeningPort = server.address().port;
    process.stdout.write(`READY ${listeningPort}\n`);
  });
  process.on('SIGTERM', () => shutdown(0));
  process.on('SIGINT', () => shutdown(0));
}

function readBridgeConfig(stream) {
  return new Promise((resolve, reject) => {
    let buffer = '';
    const timer = setTimeout(() => finish(new Error('INVALID_BRIDGE_CONFIG')), 10000);
    const finish = (error, value) => {
      clearTimeout(timer);
      stream.off('data', onData);
      stream.off('end', onEnd);
      buffer = '';
      if (error) reject(error); else resolve(value);
    };
    const onEnd = () => finish(new Error('INVALID_BRIDGE_CONFIG'));
    const onData = (chunk) => {
      buffer += String(chunk || '');
      if (Buffer.byteLength(buffer, 'utf8') > 4096) return finish(new Error('INVALID_BRIDGE_CONFIG'));
      const newline = buffer.indexOf('\n');
      if (newline < 0) return;
      try { finish(null, validateBridgeConfig(JSON.parse(buffer.slice(0, newline)))); }
      catch { finish(new Error('INVALID_BRIDGE_CONFIG')); }
    };
    stream.setEncoding('utf8');
    stream.on('data', onData);
    stream.on('end', onEnd);
  });
}

if (require.main === module) {
  // Receive the short-lived token through a private stdin pipe, never command-line arguments.
  readBridgeConfig(process.stdin).then(startBridge).catch(() => {
    process.stderr.write('INVALID_BRIDGE_CONFIG\n');
    process.exitCode = 2;
  });
}

module.exports = { createCaptureReceiver, readBridgeConfig, validateBridgeConfig };
