// Private HTTPS -> Purelymail relay. See relay/README.md before deployment.
import { SMTPConnection, SMTPRejected, basicAuth } from './protocol.mjs';

const MAILBOXES = new Set(['stefan@ceratosystems.com', 'andrija@ceratosystems.com']);
const ADDRESS = /^[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+$/;

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

function response(status, result) {
  return Response.json({ status: result }, { status, headers: { 'Cache-Control': 'no-store' } });
}

export function createHandler(connectSocket) {
  return {
    async fetch(request, env) {
      const path = new URL(request.url).pathname;
      if (request.method !== 'POST' || !['/send', '/verify'].includes(path)) return response(404, 'not_found');
      if (!(await authorized(request.headers.get('Authorization'), env.RELAY_TOKEN))) return response(401, 'unauthorized');
      const length = Number(request.headers.get('Content-Length'));
      if (length && length > 300000) return response(413, 'invalid_request');
      let data;
      try { data = await request.json(); } catch { return response(400, 'invalid_request'); }
      const { username, password, recipient, raw_email: raw } = data || {};
      if (typeof username !== 'string' || !MAILBOXES.has(username.toLowerCase()) || typeof password !== 'string' || !password || password.length > 1024 || /[\x00\r\n]/.test(password)) return response(400, 'invalid_request');
      let bytes;
      if (path === '/send') {
        if (typeof recipient !== 'string' || recipient.length > 254 || !ADDRESS.test(recipient) || typeof raw !== 'string' || raw.length > 200000 || !/^[A-Za-z0-9+/]*={0,2}$/.test(raw)) return response(400, 'invalid_request');
        try { bytes = Uint8Array.from(atob(raw), char => char.charCodeAt(0)); } catch { return response(400, 'invalid_request'); }
        if (!bytes.length) return response(400, 'invalid_request');
      }
      let smtp, timer, submitting = false;
      try {
        const socket = connectSocket({ hostname: 'smtp.purelymail.com', port: 465 }, { secureTransport: 'on' });
        smtp = new SMTPConnection(socket);
        const transaction = async () => {
          await smtp.expect([220]);
          await smtp.command('EHLO ceratosystems.com', [250]);
          await smtp.command('AUTH PLAIN ' + basicAuth(username, password), [235]);
          if (path === '/verify') return response(200, 'authenticated');
          await smtp.command('MAIL FROM:<' + username + '>', [250]);
          await smtp.command('RCPT TO:<' + recipient + '>', [250, 251]);
          await smtp.command('DATA', [354]);
          submitting = true;
          await smtp.message(bytes);
          await smtp.expect([250]);
          return response(200, 'sent');
        };
        return await Promise.race([
          transaction(),
          new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('Timed out')), 25000); }),
        ]);
      } catch (error) {
        if (error instanceof SMTPRejected) return response(502, 'rejected');
        return response(502, submitting ? 'uncertain' : 'failed');
      } finally {
        clearTimeout(timer);
        if (smtp) await smtp.close();
      }
    },
  };
}

