# Security Policy

## Reporting a vulnerability

Please report suspected vulnerabilities privately by opening a
[security advisory](https://docs.github.com/en/code-security/security-advisories)
on the repository rather than a public issue. Include a description, a
reproduction and the affected version.

## Supported versions

Only the latest release receives security fixes.

## Design guarantees

- Passwords are hashed with **argon2id**; the raw value is never stored.
- Sessions are stored server-side and referenced by a signed cookie, so they
  survive restarts and can be revoked centrally.
- State-changing requests require a CSRF token bound to the session.
- Login attempts are rate limited per `ip:username`.
- No shell command is ever assembled as a string; privileged operations live
  behind an allow-listed helper and use `subprocess` argument lists. The
  `server.conf` editor additionally rejects `plugin`/`up`/`down`-style hooks and
  shell metacharacters.
- Authorization is enforced server-side on **every** endpoint: admins and
  sub-admins are scoped by role (a central permission matrix) and by ownership;
  tests iterate the OpenAPI surface to assert anonymous access is rejected and
  admin-only routes return `403` for sub-admins.
- API keys are stored only as argon2 hashes, are individually revocable, can be
  scoped and can expire. Panel→agent calls are signed with HMAC-SHA256 over
  `method\npath\ntimestamp\nbody` with a bounded time window (replay-resistant).
- Backups are encrypted and authenticated (PBKDF2-HMAC-SHA256 + encrypt-then-MAC)
  before leaving the host.
- Secrets are generated at install time and stored in `/etc/ovpn-panel/`
  with mode 600.
- There is **no** license key, activation, phone-home or telemetry code in this
  project, by design.

## Dependency and static analysis

The repository runs [`pip-audit`](https://pypi.org/project/pip-audit/) and
[`bandit`](https://pypi.org/project/bandit/) in CI
([`.github/workflows/security.yml`](.github/workflows/security.yml)) on every
push, on pull requests and weekly. Please report a vulnerable transitive
dependency through the same private advisory process as a code issue.
