# Responsible use

This software is a self-hosted administration panel for VPN and tunnel servers.
It is a tool. How you use it is your responsibility.

## Lawful use only

- Use it only for purposes that are legal in **both** the country where the
  server is located **and** the country from which it is used.
- You are solely responsible for complying with the laws of your jurisdiction and
  with your hosting provider's terms of service (acceptable-use policy, traffic
  limits, port rules, notification duties).
- Do not use it to facilitate unlawful access to networks or data you do not own
  or are not authorised to reach.

## What the authors are not responsible for

- The authors and contributors of this project are **not** responsible for how
  the software is deployed, configured, or used, nor for any misuse, data loss,
  service interruption or legal consequence arising from its use.
- The software is provided **"as is"**, without warranty of any kind, under the
  [MIT License](../LICENSE).

## Operating a public service

If you operate a public or commercial service with this panel:

- Publish your own terms of service and privacy policy. You — not this project —
  are the data controller for the accounts, traffic counters and logs you keep.
- Keep the host patched, restrict the panel to your own networks where possible,
  and use TLS in front of it.
- Respect end-user data minimisation: keep only what you need, retain it only as
  long as necessary, and protect backups (they are encrypted by this panel).

## Reporting abuse guide

Because this project ships no telemetry, the maintainers cannot and do not know
who runs it or what they do with it. Abusive instances must be reported to the
operator's hosting provider and the relevant authorities.

## Third-party tools

Tunnel cores and VPN daemons downloaded at install time are governed by their own
licenses and usage terms — see [`THIRD_PARTY_LICENSES.md`](../THIRD_PARTY_LICENSES.md).
