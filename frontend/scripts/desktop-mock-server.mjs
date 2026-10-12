/**
 * Mock of the #870 viewer socket for browser tests.
 *
 * Speaks the backend contract's viewer side: `GET /api/v1/ws/desktop/{view_id}
 * ?token=<jwt>` with subprotocol `binary`, binary frames only, RFB 3.8 with a
 * single security type None, then a fixed 64x48 framebuffer. The view id
 * prefix picks a scenario so tests can drive every viewer state:
 *
 *   live-*         handshake, framebuffer, stays open
 *   gone-*         live, then close 4410 (endpoint disconnected)
 *   expire-*       live, then close 4411 (view expired)
 *   unreachable-*  close 4415 before the greeting (endpoint never connected)
 *   drop-*         live, then close 1011 (no specific code)
 *
 * A missing token closes 4403. Every client message type received is kept
 * per view and served at `GET /__desktop_mock/log?view=<id>`, so a test can
 * assert that a view-only viewer sent no KeyEvent (4) or PointerEvent (5).
 */
import { WebSocketServer } from 'ws';

export const MOCK_WIDTH = 64;
export const MOCK_HEIGHT = 48;
const VIEWER_PREFIX = '/api/v1/ws/desktop/';

/** Client message types seen per view id. */
const received = new Map();

function serverInit() {
  const name = Buffer.from('preloop-mock');
  const buf = Buffer.alloc(24 + name.length);
  buf.writeUInt16BE(MOCK_WIDTH, 0);
  buf.writeUInt16BE(MOCK_HEIGHT, 2);
  // Pixel format: 32 bpp, depth 24, little endian, true colour, 8 bits each.
  buf.writeUInt8(32, 4);
  buf.writeUInt8(24, 5);
  buf.writeUInt8(0, 6);
  buf.writeUInt8(1, 7);
  buf.writeUInt16BE(255, 8);
  buf.writeUInt16BE(255, 10);
  buf.writeUInt16BE(255, 12);
  buf.writeUInt8(16, 14);
  buf.writeUInt8(8, 15);
  buf.writeUInt8(0, 16);
  buf.writeUInt32BE(name.length, 20);
  name.copy(buf, 24);
  return buf;
}

function rawFramebufferUpdate() {
  const pixels = MOCK_WIDTH * MOCK_HEIGHT * 4;
  const buf = Buffer.alloc(4 + 12 + pixels);
  buf.writeUInt8(0, 0); // FramebufferUpdate
  buf.writeUInt16BE(1, 2); // one rectangle
  buf.writeUInt16BE(0, 4);
  buf.writeUInt16BE(0, 6);
  buf.writeUInt16BE(MOCK_WIDTH, 8);
  buf.writeUInt16BE(MOCK_HEIGHT, 10);
  buf.writeInt32BE(0, 12); // Raw encoding
  for (let i = 0; i < MOCK_WIDTH * MOCK_HEIGHT; i++) {
    // Solid green, little-endian BGRX.
    buf.writeUInt8(0x00, 16 + i * 4);
    buf.writeUInt8(0xc0, 16 + i * 4 + 1);
    buf.writeUInt8(0x00, 16 + i * 4 + 2);
  }
  return buf;
}

/** Length of the client message at the start of `buf`, or 0 if incomplete. */
function clientMessageLength(buf) {
  if (buf.length < 1) return 0;
  switch (buf[0]) {
    case 0:
      return buf.length >= 20 ? 20 : 0;
    case 2: {
      if (buf.length < 4) return 0;
      const n = 4 + 4 * buf.readUInt16BE(2);
      return buf.length >= n ? n : 0;
    }
    case 3:
      return buf.length >= 10 ? 10 : 0;
    case 4:
      return buf.length >= 8 ? 8 : 0;
    case 5:
      return buf.length >= 6 ? 6 : 0;
    case 6: {
      if (buf.length < 8) return 0;
      const n = 8 + buf.readUInt32BE(4);
      return buf.length >= n ? n : 0;
    }
    case 150:
      return buf.length >= 10 ? 10 : 0;
    case 248: {
      if (buf.length < 9) return 0;
      const n = 9 + buf[8];
      return buf.length >= n ? n : 0;
    }
    default:
      return -1;
  }
}

function serveViewer(ws, viewId) {
  const types = [];
  received.set(viewId, types);
  let phase = 'version';
  let buffer = Buffer.alloc(0);
  let sentFrame = false;
  const timers = [];
  const later = (ms, fn) => timers.push(setTimeout(fn, ms));
  ws.on('close', () => timers.forEach(clearTimeout));

  if (viewId.startsWith('unreachable-')) {
    later(50, () => ws.close(4415, 'endpoint never connected'));
    return;
  }
  ws.send(Buffer.from('RFB 003.008\n'));

  ws.on('message', (data) => {
    // ws 7 hands text frames over as strings and binary frames as Buffers.
    if (typeof data === 'string') {
      ws.close(4400, 'text frame');
      return;
    }
    buffer = Buffer.concat([buffer, Buffer.from(data)]);
    for (;;) {
      if (phase === 'version') {
        if (buffer.length < 12) return;
        buffer = buffer.subarray(12);
        ws.send(Buffer.from([1, 1]));
        phase = 'security';
      } else if (phase === 'security') {
        if (buffer.length < 1) return;
        buffer = buffer.subarray(1);
        ws.send(Buffer.from([0, 0, 0, 0]));
        phase = 'clientinit';
      } else if (phase === 'clientinit') {
        if (buffer.length < 1) return;
        buffer = buffer.subarray(1);
        ws.send(serverInit());
        phase = 'normal';
      } else {
        const length = clientMessageLength(buffer);
        if (length < 0) {
          ws.close(4400, 'unknown message');
          return;
        }
        if (length === 0) return;
        const type = buffer[0];
        const incremental = buffer[1];
        buffer = buffer.subarray(length);
        types.push(type);
        if (type === 3 && (!incremental || !sentFrame)) {
          ws.send(rawFramebufferUpdate());
          if (!sentFrame) {
            sentFrame = true;
            if (viewId.startsWith('gone-'))
              later(300, () => ws.close(4410, 'agent disconnected'));
            if (viewId.startsWith('expire-'))
              later(300, () => ws.close(4411, 'expired'));
            if (viewId.startsWith('drop-'))
              later(300, () => ws.close(1011, 'internal'));
          }
        }
      }
    }
  });
}

/** Web Test Runner plugin wiring the mock onto the dev server. */
export function desktopMockServerPlugin() {
  const wss = new WebSocketServer({
    noServer: true,
    handleProtocols: (protocols) =>
      [...protocols].includes('binary') ? 'binary' : false,
  });
  return {
    name: 'desktop-mock-server',
    serverStart({ server }) {
      server.on('upgrade', (request, socket, head) => {
        const url = new URL(request.url, 'http://localhost');
        if (!url.pathname.startsWith(VIEWER_PREFIX)) return;
        const viewId = decodeURIComponent(
          url.pathname.slice(VIEWER_PREFIX.length)
        );
        const token = url.searchParams.get('token');
        wss.handleUpgrade(request, socket, head, (ws) => {
          if (!token) {
            ws.close(4403, 'forbidden');
            return;
          }
          serveViewer(ws, viewId);
        });
      });
    },
    serve(context) {
      if (context.path !== '/__desktop_mock/log') return undefined;
      const view = context.URL.searchParams.get('view') || '';
      return {
        body: JSON.stringify({ types: received.get(view) ?? null }),
        type: 'json',
      };
    },
  };
}
