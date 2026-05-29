# Security

Marvel Rivals Account Tracker is a **local-only** desktop app. It has no user accounts,
no backend server, and no network service beyond a loopback web UI.

## How your data is protected

- The app serves its UI on `127.0.0.1` (loopback) only — it is never reachable
  from your network or the internet, and it makes no outbound connections
  except the optional Marvel Rivals stats lookups you trigger yourself.
- The **password** field of each account is encrypted with a key derived from
  your master password (scrypt key derivation + AES-256-GCM). The master
  password is never written to disk; if you lose it, the encrypted passwords
  cannot be recovered.
- Other fields (in-game name, username, email, ranks, notes) are stored in
  **plain text** in the vault file — only the password field is encrypted.
- The vault is a single `vault.json` in your per-user data folder (see the
  README). Anyone with both read access to that folder *and* your master
  password can read the saved passwords — protect both accordingly.
- The decryption key is held only in memory and is cleared when the vault
  auto-locks, when you click **Lock**, or when the app quits.

## Reporting a vulnerability

If you find a security issue, please report it **privately** rather than
opening a public issue: go to the repository's **Security** tab and choose
*Report a vulnerability*. If that option isn't available, open a regular issue
and avoid posting exploit details.

Include steps to reproduce and the version (or commit) you tested.

## Supported versions

Only the latest release receives fixes. Always update to the newest version
from the [Releases page](https://github.com/itsnotyuiiii/marvel-account-keeper/releases).
