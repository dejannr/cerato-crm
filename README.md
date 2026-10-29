# Cerato CRM

A local outreach CRM: React, FastAPI, and PostgreSQL. Sending is currently disabled for simulation. The app has no authentication and is intended to bind to localhost only.

## Setup

Requires Python 3, Node.js and a running PostgreSQL server. Create a local database named `cerato_crm` (e.g. `createdb cerato_crm`).

```sh
cp .env.example .env
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
npm install --prefix frontend
```

Edit `.env` and configure your PostgreSQL URL and both mailbox addresses and passwords. Purelymail requires an app password if two-factor authentication is enabled. Keep `.env` private. Edit the email template in the app; `OUTREACH.md` contains the drafting instructions.

Start the API and interface together with `./start.sh`, then press Ctrl+C to stop them. Restart the script after changing `.env`. Alternatively, run each command in a separate terminal:

```sh
.venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8000
npm --prefix frontend run dev
```

Open http://127.0.0.1:5173 and click **Import spreadsheet**. Inbox synchronization is paused during simulation mode. Importing again skips existing company names.

## Workflow and limits

Ask Codex to use the instructions in `AGENTS.md` to research new leads and save drafts. Review each draft in the UI, then record a simulated send. Real sending is disabled. A successful SMTP response means the server accepted the message, not that it reached the inbox. Failed or interrupted sends may have uncertain outcomes and are never automatically retried; check the mailbox before proceeding. SMTP messages are stored in the CRM; whether they appear in Purelymail's Sent folder depends on provider behavior and is not guaranteed.

Reply matching uses message thread headers and requires the sender to match the original recipient. Replies without matching headers, replies from a different address, bounces, and opt-outs cannot be reliably identified automatically. Review the mailboxes manually for these cases, and mark opt-outs **do not contact** in the CRM. No automatic follow-ups are sent. Website URLs, email addresses and contact names from the spreadsheet have not been verified. Avoid contacting the same company group twice by reviewing duplicates.

Keep the local database and backups private; it contains contact information and email content. Ensure your outreach and provider use are permitted in the relevant jurisdictions.

## Codex tools

`python3 tools/outreach.py --help` lists the available commands. For example:

```sh
python3 tools/outreach.py leads --limit 5
python3 tools/outreach.py show 1
python3 tools/outreach.py research 1 --findings /path/to/findings.txt --sources /path/to/sources.txt
python3 tools/outreach.py draft 1 --sender stefan --to contact@example.com --subject 'Subject' --body-file /path/to/email.txt
python3 tools/outreach.py template
python3 tools/outreach.py review
```

These commands require the local API to be running. The helper does not send email. Edit the template in the app and verify the recipient before drafting. The interface supports editing and recording a simulation for each draft.

## Simulation mode

Real sending is currently disabled in the API. Codex can research leads and create real drafts, and each lead’s conversation can record a simulated send without connecting a mailbox. A simulated message remains in the lead's conversation history and the lead is marked `simulated`; it is not delivered. This simulation does not generate replies or delivery events. Re-enabling real sending would require an intentional implementation change and separate testing.

## Sender profiles

Choose Stefan or Andrija when opening the app; use **Switch** in the header to change profiles. The selection lasts for the browser tab's session. Each sender has a separately editable template. Andrija's initial template uses his name without assuming his job title; edit his signature as needed. A lead's owner is the sender of its first outbound draft or message, visible in the lead list and detail view. Deleting an only draft releases the assignment. Existing simulated messages keep their recorded sender. Browser profile selection is not authentication and is not visible to Codex; specify the sender when requesting drafts. The internal API and mailbox settings use `adrija` for Andrija.

## Production

Production uses the built React files served by Nginx, a loopback-only FastAPI service managed by systemd, and PostgreSQL. Configure `CRM_PASSWORD_HASH`, `CRM_SESSION_KEY`, `CRM_ORIGIN` and `CRM_SECURE_COOKIES=true` in `/etc/cerato-crm.env`. The password hash uses `pbkdf2_sha256$iterations$hex_salt$hex_hash`. Without both hash and session key, local development has no login and must remain bound to localhost. After changing production code, update the source, rebuild the frontend, install Python requirements into `/opt/cerato-crm/venv`, and restart `cerato-crm.service`; never overwrite the production database or environment file when updating. Run `nginx -t` before reloading Nginx. TLS requires `crm.ceratosystems.com` to resolve to the server before running Certbot.

To publish a later committed version, push it to GitHub and run `./deploy/update.sh` from a clean checkout. The script builds the frontend locally and transfers the committed source to the droplet over SSH, then restarts the service. It does not copy the local database or `.env`; production data and credentials remain on the server. Set `CRM_SSH_TARGET` to override the default SSH target. Configure SSH key access or authenticate when prompted.

## Codex lead research

Select a new lead and click **Create with Codex**. GPT-6.1 Terra researches public sources and, when it can substantiate the recipient and personalization, creates a draft with the selected sender's template. Otherwise it records research and asks for review. No email is sent. Only one research job runs at a time; a server restart interrupts it. Jobs use the Codex CLI signed in on the machine running the backend, with live search enabled and command tools disabled. Local jobs use your local Codex login; the droplet has a separate device-authorized login. Agent research may be wrong, so review sources and recipient details. CLI equivalents: `python3 tools/outreach.py generate LEAD_ID --sender stefan` and `python3 tools/outreach.py generation LEAD_ID`. The CLI requires a local, unauthenticated development API; production access uses the authenticated interface.
