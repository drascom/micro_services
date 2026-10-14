---
name: vault
description: Passwords, logins, tokens and 2FA codes in the user's Bitwarden vault: find, copy, save (şifre, parola, token, 2FA kodu).
---
# Vault

- Give: `vault_search`, then `vault_copy` with its `id` and field. Say only "<item> <field> panoya kopyalandı (30 sn)."
- Save: `vault_save` with name and source: `clipboard` (user copied it), `prompt` (secure input window) or `generate` (new password, copied). Token: kind `token`.
- `exists`: ask whether to replace the password (old one kept in history) and stop; call again with `confirm_token` only after the user agrees.
- Never say, guess, spell or write a secret. If the user speaks a secret, do not save it: say briefly it is now in the transcript, offer clipboard or prompt.
- Several matches: ask which one. No deleting. `locked`: unlock Bitwarden in Admin.
