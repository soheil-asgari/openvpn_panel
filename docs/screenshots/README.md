# Screenshots

Capture screenshots from a **seeded demo instance with fake data only** — no real
IPs, domains, usernames or secrets. Generate the data with:

```bash
OVPN_ALLOW_DEMO_SEED=1 OVPN_PANEL_CONFIG_DIR=/path/to/demo python scripts/seed_demo.py
```

Suggested set (dark + light, desktop + mobile):

| File | Page |
| --- | --- |
| `dashboard.png` | Dashboard |
| `users.png` | Users list and editor |
| `online.png` | Online sessions |
| `tunnels.png` | Tunnel wizard and detail |
| `subscription.png` | Public subscription page |
| `login.png` | Login |

Reference them from `README.md` / `README.fa.md`:

```markdown
![Dashboard](docs/screenshots/dashboard.png)
```

Do not commit images that contain real data.
