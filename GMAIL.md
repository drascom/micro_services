# Gmail triage

`mail_list_messages`/`mail_read_message` read the inbox of every connected account (see README → Mail);
`mail_organize` sorts new INBOX mail into 🔴 urgent / 🟠 important / ⚪ later / 🗑 junk through the Gmail REST API
(`.pi/extensions/gmail`, `fetch`, no SDK). Not tied to macOS (Linux-ready); there is no Mail.app reader any more.

## One-time setup
1. Google Cloud Console → new project → **APIs & Services → Library → Gmail API → Enable**.
2. **OAuth consent screen**: External, add every Gmail address you will connect as a **test user**, scope `.../auth/gmail.modify`.
   (In "Testing" status Google expires refresh tokens after 7 days; publish the app — no verification needed for
   personal use with your own account — to avoid weekly reconnects.)
3. **Credentials → Create credentials → OAuth client ID → Desktop app**. Copy Client ID and Client secret.
4. Labels `Sonra Oku` and `Gereksiz` are created automatically when an account connects (and on Admin refresh while missing;
   `labels.create` under the same `gmail.modify` scope, only creation, never rename/delete). A warning shows only if creation failed.
5. Admin → Ayarlar → **Mail**: paste Client ID + secret → Kaydet (once); **Hesap ekle** → pick any Google account in the
   browser (loopback redirect to `127.0.0.1:<random port>`, PKCE); repeat per account. Then turn on **Mail düzenleme**.
6. Optional: urgent senders (`boss@work.com, @council.gov.uk`, `@*.gov.uk` = every subdomain, not gov.uk itself; `*` only as leading `*.` + 2 labels) and the importance text (`data/mail-importance.md`,
   starts from `mail-importance.example.md`).

Client ID/secret and one refresh token per account (`token-<email>`, list in `accounts`) are stored in the macOS Keychain
(`Ayhan Gmail`), never in `config.json` or logs. The old single `token` is migrated on first read (no reconnect); old
`gmail_account` in `config.json` is ignored. **Bağlantıyı kes** revokes one account; an expired account shows
"Yeniden bağlan" — **Hesap ekle** with the same account renews it. The browser must run on the same machine as the backend (loopback redirect).

## Behaviour
- Rules first: urgent sender list → urgent only if Gmail's `Authentication-Results` (mx.google.com) shows `dmarc=pass`
  or aligned `dkim=pass`; Promotions → junk; Social/Forums → later. The rest: one compact model call per 25 mails
  (sender, subject, snippet, category; no body) → `{urgent, important, later, junk, unsure}`; unsure/invalid → later;
  unauthenticated urgent → important.
- Actions (code, not the model): urgent = STARRED + IMPORTANT, notification and `📧 Cevapla: …` reminder (never run as a
  task); important = STARRED; later/junk = label + remove INBOX/UNREAD.
- `propose` changes nothing; an act sent as `action` (e.g. `trash`) is read as propose + act, and `apply` without a valid
  token uses the plan pending for that turn (small local models); `apply`/`purge_junk` need a token valid only in the user's next message (2 min).
  `purge_junk` moves `Gereksiz` mail to Trash (never permanent delete). `undo` restores the last apply (also Admin → Durum).
- Multiple accounts: no `account` → every connected account in one plan (per-account counts, one token); `account` =
  address or a unique part ("work"). One account failing is listed under `errors`, the others go on. Mail düzenleme,
  urgent senders, importance text and the daily cap are shared.
- Log `data/mail-actions.jsonl` (account, ids, levels, labels only); processed ids per account in `data/mail-triage-state.json`;
  daily cap `gmail_daily_limit` (500, all accounts together). Assistant tasks may only propose.
