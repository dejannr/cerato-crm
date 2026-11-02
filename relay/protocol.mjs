// SMTP-over-TLS protocol helpers. No credentials or message bodies are logged.
export class SMTPRejected extends Error {}
export class SMTPProtocolError extends Error {}

export class SMTPConnection {
  constructor(socket) {
    this.socket = socket;
    this.reader = socket.readable.getReader();
    this.writer = socket.writable.getWriter();
    this.decoder = new TextDecoder();
    this.encoder = new TextEncoder();
    this.buffer = '';
  }
  async response() {
    let expectedCode;
    for (let lineCount = 0; lineCount < 100; lineCount++) {
      while (!this.buffer.includes('\n')) {
        const { value, done } = await this.reader.read();
        if (done) throw new SMTPProtocolError('SMTP connection closed');
        this.buffer += this.decoder.decode(value, { stream: true });
        if (this.buffer.length > 65536) throw new SMTPProtocolError('SMTP response too large');
      }
      const index = this.buffer.indexOf('\n');
      const line = this.buffer.slice(0, index).replace(/\r$/, '');
      this.buffer = this.buffer.slice(index + 1);
      const match = /^(\d{3})([ -])/.exec(line);
      if (!match || (expectedCode && expectedCode !== match[1])) throw new SMTPProtocolError('Invalid SMTP reply');
      expectedCode = match[1];
      if (match[2] === ' ') return Number(expectedCode);
    }
    throw new SMTPProtocolError('Too many SMTP reply lines');
  }
  async expect(allowed) {
    const code = await this.response();
    if (!allowed.includes(code)) {
      if (code >= 400 && code <= 599) throw new SMTPRejected('SMTP request rejected');
      throw new SMTPProtocolError('Unexpected SMTP reply');
    }
  }
  async command(line, allowed) {
    await this.writer.write(this.encoder.encode(line + '\r\n'));
    await this.expect(allowed);
  }
  async message(bytes) {
    // MIME data is generated with SMTP policy in the CRM and must use CRLF.
    let output = '';
    let atLineStart = true;
    for (const byte of bytes) {
      if (atLineStart && byte === 46) output += '.';
      output += String.fromCharCode(byte);
      atLineStart = byte === 10;
    }
    if (!output.endsWith('\r\n')) output += '\r\n';
    output += '.\r\n';
    const encoded = Uint8Array.from(output, character => character.charCodeAt(0));
    await this.writer.write(encoded);
  }
  async close() {
    try { this.reader.releaseLock(); } catch {}
    try { this.writer.releaseLock(); } catch {}
    try { await this.socket.close(); } catch {}
  }
}

export function basicAuth(username, password) {
  const bytes = new TextEncoder().encode('\0' + username + '\0' + password);
  return btoa(Array.from(bytes, byte => String.fromCharCode(byte)).join(''));
}
