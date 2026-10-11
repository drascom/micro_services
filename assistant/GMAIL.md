# Gmail triage

`mail_organize` sorts new INBOX mail into 🔴 urgent / 🟠 important / ⚪ later / 🗑 junk through the Gmail REST API
(`.pi/extensions/gmail`, `fetch`, no SDK). Not tied to macOS (Linux-ready); Apple Mail read tools are unchanged.

## One-time setup
1. Google Cloud Console → new project → **APIs & Services → Library → Gmail API → Enable**.
2. **OAuth consent screen**: External, add your Gmail address as a **test user**, scope `.../auth/gmail.modify`.
   (In "Testing" status Google expires refresh tokens after 7 days; publish the app — no verification needed for
   personal use with your own account — to avoid weekly reconnects.)
3. **Credentials → Create credentials → OAuth client ID → Desktop app**. Copy Client ID and Client secret.
4. In Gmail create two labels: `Sonra Oku` and `Gereksiz` (the app never creates labels; missing → that action is skipped).
5. Admin → Ayarlar → **Gmail**: paste Client ID + secret → Kaydet; enter the account address; **Bağlan** → approve in the
   browser (loopback redirect to `127.0.0.1:<random port>`, PKCE). Then turn on **Mail düzenleme**.
6. Optional: urgent senders (`boss@work.com, @council.gov.uk`, `@*.gov.uk` = every subdomain, not gov.uk itself; `*` only as leading `*.` + 2 labels) and the importance text (`data/mail-importance.md`,
   starts from `mail-importance.example.md`).

Client ID/secret and the refresh token are stored in the macOS Keychain (`Ayhan Gmail`), never in `config.json` or logs.
**Bağlantıyı kes** revokes the token. The browser must run on the same machine as the backend (loopback redirect).

## Behaviour
- Rules first: urgent sender list → urgent only if Gmail's `Authentication-Results` (mx.google.com) shows `dmarc=pass`
  or aligned `dkim=pass`; Promotions → junk; Social/Forums → later. The rest: one compact model call per 25 mails
  (sender, subject, snippet, category; no body) → `{urgent, important, later, junk, unsure}`; unsure/invalid → later;
  unauthenticated urgent → important.
- Actions (code, not the model): urgent = STARRED + IMPORTANT, notification and `📧 Cevapla: …` reminder (never run as a
  task); important = STARRED; later/junk = label + remove INBOX/UNREAD.
- `propose` changes nothing; `apply`/`purge_junk` need a token valid only in the user's next message (2 min).
  `purge_junk` moves `Gereksiz` mail to Trash (never permanent delete). `undo` restores the last apply (also Admin → Durum).
- Log `data/mail-actions.jsonl` (ids, levels, labels only); daily cap `gmail_daily_limit` (500). Assistant tasks may only propose.
