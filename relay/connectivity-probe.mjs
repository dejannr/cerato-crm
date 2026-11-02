// Temporary SMTP connectivity probe. It never authenticates or sends mail.
// Set a long random PROBE_TOKEN as a Cloudflare Worker secret before deploying.
import { connect } from 'cloudflare:sockets';

async function authorized(header, secret) {
  if (!secret || !header?.startsWith('Bearer ')) return false;
  const encoder = new TextEncoder();
  const [given, expected] = await Promise.all([
    crypto.subtle.digest('SHA-256', encoder.encode(header.slice(7))),
    crypto.subtle.digest('SHA-256', encoder.encode(secret)),
  ]);
  const a = new Uint8Array(given), b = new Uint8Array(expected);
  let difference = 0;
  for (let i = 0; i < a.length; i++) difference |= a[i] ^ b[i];
  return difference === 0;
}

export default {
  async fetch(request, env) {
    if (request.method !== 'POST' || new URL(request.url).pathname !== '/probe') {
      return new Response('Not found', { status: 404 });
    }
    if (!(await authorized(request.headers.get('Authorization'), env.PROBE_TOKEN))) {
      return new Response('Unauthorized', { status: 401 });
    }
    let socket;
    let timer;
    try {
      socket = connect({ hostname: 'smtp.purelymail.com', port: 465 }, { secureTransport: 'on' });
      const reader = socket.readable.getReader();
      const result = await Promise.race([
        reader.read(),
        new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('Connection timed out')), 10000); }),
      ]);
      const greeting = new TextDecoder().decode(result.value || new Uint8Array());
      if (!greeting.startsWith('220')) throw new Error('SMTP greeting not received');
      return Response.json({ connected: true, smtp_greeting_received: true }, { headers: { 'Cache-Control': 'no-store' } });
    } catch {
      return Response.json({ connected: false }, { status: 502, headers: { 'Cache-Control': 'no-store' } });
    } finally {
      clearTimeout(timer);
      if (socket) { try { await socket.close(); } catch {} }
    }
  },
};
