# Purelymail relay

This is a prepared implementation; it has not been deployed. The currently deployed Worker is only `connectivity-probe.mjs` and cannot send email.

The CRM backend optionally sends manual email over HTTPS to the private Worker. It passes the already configured mailbox username and decrypted password, recipient, and SMTP-formatted message over TLS. The Worker authenticates the backend using the `RELAY_TOKEN` secret, connects only to `smtp.purelymail.com:465`, restricts usernames to the two Cerato mailboxes, and sends through Purelymail. It does not store the passwords or log the message. HTTP credentials are never exposed to the browser. The `/verify` endpoint authenticates the mailbox but does not submit an email. IMAP fetching and Sent-folder copies remain on the CRM backend.

When production deployment is approved, configure a new high-entropy `RELAY_TOKEN` secret in Cloudflare, deploy `worker.mjs` and its imported modules, and set these values privately in the CRM's server environment:

```
MAIL_TRANSPORT=relay
RELAY_URL=https://purelymail-relay.business-dejan-radovanovic.workers.dev/send
RELAY_TOKEN=<same-secret>
```

Keep the token and mailbox passwords out of Git, command-line output and logs. The CRM's default remains direct SMTP. Once deployed, verify both configured mailboxes with the `/verify` endpoint before asking the user to explicitly choose and authorize any real test email. A dropped HTTP response or an interrupted SMTP submission is treated as uncertain and is not retried automatically. A successful SMTP response indicates acceptance, not delivery; saving to the Purelymail Sent folder is a separate IMAP operation and may fail independently.

`node --test tests/relay.test.mjs` tests SMTP framing and safety with mock sockets. Python tests mock the HTTPS relay and IMAP. These tests do not verify Cloudflare limits, real SMTP authentication, or actual delivery.
