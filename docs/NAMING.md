# Project Naming — candidates and availability

The current identifiers (`ovpn-panel`, `OpenVPN Web Panel`, `ovpn-panel.service`,
`/etc/ovpn-panel`, `/opt/ovpn-panel`, the `ovpn-panel` command) are **working
names only**. Before the first public push the owner must choose one unique name
and it will be applied everywhere (package, service units, paths, CLI, UI brand,
docs, installer).

The names below were chosen to be distinctive, pronouncable, easy to type and
unlikely to collide with existing VPN panels. **Availability shown here is
indicative and was not verified against a live registry from the build
environment** — the owner must confirm each of: GitHub org/repo, PyPI, npm,
Docker Hub and a general web search before committing.

## Candidates

| # | Name | Slug (`package / CLI / service`) | Rationale | Availability (to verify) |
| --- | --- | --- | --- | --- |
| 1 | **Tunnelwright** | `tunnelwright` | Evokes a craftsman who builds tunnels; unique, no known VPN product uses it | GitHub: no known repo — verify; PyPI: expected free; npm: expected free; web: no obvious clash |
| 2 | **Vantryx** | `vantryx` | Short, invented word; brandable, no dictionary meaning to collide with | Likely free everywhere; verify PyPI/npm |
| 3 | **Ovimbral** | `ovimbral` | Invented; hints at "OpenVPN" without using the trademark | Likely free; verify |
| 4 | **Linkmesh** | `linkmesh` | Descriptive of multi-node mesh/tunnels; check for existing networking libraries | Verify — "mesh" names are common |
| 5 | **Gategrove** | `gategrove` | Two real words joined; unique combination, friendly tone | Likely free; verify |

**Recommendation:** candidate **#1 (Tunnelwright)** balances descriptiveness and
originality; candidate **#2 (Vantryx)** is the safest bet for a fully unique,
trademark-friendly string.

## Naming rules (to apply after the choice)

1. **Do not use a third-party trademark in the product name.** "OpenVPN" is a
   registered trademark of OpenVPN Inc.; it may be referenced descriptively
   ("manages OpenVPN servers") but must not be the product name or a
   confusingly-similar name.
2. Apply the chosen slug consistently:
   - Python package + entry points (`pyproject.toml`),
   - systemd units (`<slug>.service`, `<slug>-helper.service`, `<slug>-agent@.service`),
   - paths (`/opt/<slug>`, `/etc/<slug>`, `/var/lib/<slug>`, `/run/<slug>`),
   - CLI command and menu (`/usr/local/bin/<slug>`),
   - UI brand strings and i18n defaults,
   - installer, docs (EN + FA), report and README.
3. Replace the placeholder tokens used in the docs/templates: `<name>`, `<slug>`,
   `<org>`, `<repo>`.
4. Re-run the originality/similarity check (name, author, domains, distinctive
   phrases, file names, installer wording) after the rename and record the
   result in `docs/RELEASE_REPORT.md`.

## What must NOT change

- The MIT [`LICENSE`](../LICENSE) and copyright line stay as-is except for the
  project name/author the owner wishes to record.
- No name may be borrowed from, or made to resemble, any commercial panel.
