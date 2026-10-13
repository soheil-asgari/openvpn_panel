# Threat model

This document describes what the system protects, what it does not, and the
assumptions a deployer must be aware of.

## Assets

- **Panel accounts** — admin and reseller credentials, sessions, API keys.
- **VPN user records** — usernames, password hashes, quotas, expiry, ownership.
- **Secrets** — session key, hook token, API keys, tunnel tokens, SSH keys.
- **PKI material** — CA and server certificates, tls-crypt key, client bundles.
- **Traffic data** — per-user counters, session logs.
- **Backups** — encrypted archives of the database, config and PKI.

## Trust boundaries

| Boundary | From | To | Protection |
| --- | --- | --- | --- |
| Browser ↔ panel | untrusted client | panel (loopback behind a TLS proxy) | TLS at the proxy, CSRF token, session cookie (`Secure`, `HttpOnly`, `SameSite`), security headers, rate limiting |
| Panel ↔ helper | panel process | privileged helper (unix socket) | allow-listed actions only; no arbitrary command execution |
| Panel ↔ agent | panel | node agent | HMAC-SHA256 over `method\npath\ntimestamp\nbody`, bounded time window, replay-resistant |
| Panel ↔ OpenVPN | OpenVPN hooks | panel unix socket / `/hooks` | local unix socket for auth; shared token for HTTP hooks |
| Panel ↔ exit server | panel | SSH to provision tunnels | host-key pinning, key or password auth, passwords never stored/logged |

## In scope / defended

- **Authentication & authorization** — argon2id password hashing; server-side
  sessions; a central permission matrix; reseller/admin isolation enforced on
  **every** endpoint (tests iterate the OpenAPI surface to prove anonymous
  access is rejected and admin-only routes return `403` for resellers).
- **CSRF** — state-changing requests require a session-bound token.
- **Rate limiting** — login attempts limited per `ip:username`.
- **Injection** — no shell strings; privileged calls use argument lists and an
  allow-listed helper. The `server.conf` editor rejects `plugin`/`up`/`down`
  hooks and shell metacharacters.
- **Secret storage** — secrets live in `/etc/<slug>/` with modes `600`/`640`;
  API keys and passwords are stored only as hashes.
- **Backups** — encrypted and authenticated (PBKDF2-HMAC-SHA256 +
  encrypt-then-MAC) before leaving the host.
- **Static analysis** — `pip-audit`, `bandit` and `gitleaks` run in CI.

## Out of scope / not defended

- **A compromised host** — root on the server defeats every control here.
- **A malicious open relay** — a deployer who hands out accounts to third
  parties controls who can reach the tunnel; the project cannot police that.
- **Upstream ISP / government-level adversaries** doing traffic analysis.
- **Metadata leakage to the exit server** (see below).

## What a relay / tunnel server can and cannot see

- The **entry** server terminates the tunnel and forwards traffic. It can see
  source/destination IPs and ports of the traffic it forwards, and the volume —
  much like any router on the path.
- The **exit** server originates the traffic and can see the same plus, if the
  payload is plaintext (e.g. unencrypted HTTP), the payload itself.
- Neither server can see the **user's** local network, the user's files, or
  traffic that never traverses the tunnel.
- Traffic **inside** the tunnel is encrypted by the tunnel protocol; but the
  panel does not attempt to hide the fact that a tunnel exists, nor the client's
  real IP from the entry server.
- **Operational implication:** never rely on a tunnel to hide information from
  the server operator. Treat any relay/exit operator as able to observe the
  metadata (and any unencrypted payload) of traffic they carry.

## Assumptions

- The panel is reachable only over TLS (reverse proxy) or loopback.
- The operator rotates the session key and API keys if exposure is suspected.
- Third-party tunnel binaries are downloaded from official sources and are
  treated as untrusted code that runs with the privileges granted to them.
