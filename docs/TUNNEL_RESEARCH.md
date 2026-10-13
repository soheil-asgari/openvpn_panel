# Tunnel Research (Phase 11 — Step 0)

Read-only discovery of which **open-source tunnel/transport tools** are used by the
reference project [`eylandoo/openvpn_webpanel_manager`](https://github.com/eylandoo/openvpn_webpanel_manager).

> The reference repository ships a **proprietary "All Rights Reserved" license**
> ([`LICENSE`](https://github.com/eylandoo/openvpn_webpanel_manager)):
> viewing/evaluation only, no copying, modification, or distribution without a
> commercial license. Its scripts were inspected **only to identify the upstream
> third-party tools they download/install**. No script code, text, structure, or
> config template from that repository was copied into this project.

## Scope of the inspection

Files reviewed read-only to extract **upstream tool identity only**
(repo URL, license, version, transports):

- `backhaul.sh`
- `DejTunnel.sh`
- `wg-tunnel-manager.sh`
- `mtu_finder.sh`

No content from those scripts is reproduced here beyond factual, publicly-known
package metadata (repository URL, SPDX license, version string, transport names).

## Findings — table

| tool | upstream repo | license | transports | UDP support | notes |
|------|---------------|---------|-----------|-------------|-------|
| **Backhaul** | https://github.com/Musixal/Backhaul | AGPL-3.0 | `tcp`, `tcpmux`, `udp` | Yes (`udp` transport; `tcpmux` can carry UDP via `accept_udp`) | Primary reverse-tunnel backend. Reference pins **v0.6.5** (`backhaul_linux_amd64.tar.gz`); a newer script resolves the **latest GitHub release** with a mirror fallback. Server/client split (Iran/entry vs Kharej/exit), TOML config, `web_port` for tunnel status API, token auth, per-port forwarding list. |
| **WireGuard** | https://git.zx2c4.com/wireguard-linux (tools: `wireguard-tools`) | GPL-2.0 | WireGuard (UDP transport) | Yes (UDP-only, kernel datapath) | Used by the `wg-tunnel-manager` flow: kernel module + `wireguard-tools`, `wg-quick@wg1`, keys via `wg genkey`/`genpsk`, peers stored as JSON with `chmod 600`. Provides point-to-point entry↔exit link. |
| **sing-box** | https://github.com/SagerNet/sing-box | GPL-3.0 (repo reports `NOASSERTION`; upstream is GPL-3.0-or-later) | VMess, VLESS, Shadowsocks, Trojan, Hysteria2 (with Reality/TLS) | Yes (Hysteria2/UDP-based protocols; UDP relay supported) | Optional proxy/anonymization core. Reference pins **v1.13.13** and installs `sing-box-1.13.13-linux-amd64`. Heavyweight; only relevant if sing-box-style transports are wanted. |
| **OpenVPN** | https://github.com/OpenVPN/openvpn | GPL-2.0 | TCP, UDP | Yes | The panel's primary VPN; `mtu_finder.sh` is a *helper*, not a tool install — it only derives `mssfix`/MTU for OpenVPN instances. |
| **Cisco AnyConnect (ocserv / OpenConnect)** | https://gitlab.com/openconnect/ocserv | GPL-2.0 | TCP (TLS), DTLS/UDP | Yes (DTLS) | Installed by the reference's `install_cisco.sh`; not a tunnel-forwarding backend, listed for completeness. |
| **L2TP/IPsec (Libreswan + xl2tpd)** | https://github.com/libreswan/libreswan , https://github.com/xelerance/xl2tpd | GPL-2.0 | UDP (ESP/IKE) | Yes | Installed by the reference's `install_l2tp.sh`; classic L2TP over IPsec, listed for completeness. |

### Not present in the reference repo

The inspected files reference **only the tools above**. No `rathole`, `wstunnel`,
`frp`, or `gost` was found in the reviewed scripts; those trace back to other
forks/derivatives of the same ecosystem and are therefore **not** in scope unless
you explicitly request them.

## License compatibility assessment

The panel (this repository) is licensed under its own repository license
(see [`LICENSE`](../LICENSE)). The relevant question is whether a backend may be
**downloaded and executed as a separate program** by the panel:

- **Backhaul — AGPL-3.0.** AGPL-3.0 is copyleft **for the covered program**. If the
  panel merely *downloads and runs the upstream binary* (no linking, no code
  copying, no derivative work), this is normally acceptable — the AGPL obligations
  attach to Backhaul itself and to any modified version we would distribute.
  **Do not** bundle a modified Backhaul into a proprietary artifact.
  → **Implementable** (Backhaul-first), with the caveat of keeping it an external
  binary and preserving upstream notices.
- **WireGuard — GPL-2.0.** Kernel/tooling, invoked as external commands
  (`wg`, `wg-quick`). External-process use is fine; do not statically link GPL code
  into a non-GPL panel.
  → **Implementable** if you confirm a WireGuard-based backend.
- **sing-box — GPL-3.0.** Same external-binary reasoning. Heavier operational
  surface; recommend deferring unless required.
- **ocserv / Libreswan** are the panel's own VPN servers, not tunnel backends —
  out of scope for the Tunnel Manager.

## Recommendation / proposed order

1. **Backhaul** first — smallest surface, TOML config, has `web_port` health
   endpoint, `tcp`/`tcpmux`/`udp` transports, matches the "entry/exit" model.
2. **WireGuard** second — only if you confirm; simplest, UDP-only, no multiplexing.
3. **sing-box** — optional/backlog.
4. **Auto mode** (Phase 11 task 3) benchmarks whatever backends are enabled and
   recommends one; transport choice is per-backend.

## Open questions — awaiting your confirmation

1. Which backends should I implement? (default proposal: **Backhaul** + optional
   **WireGuard**.)
2. For Backhaul: pin **v0.6.5** for reproducibility, or track **latest release**
   with mirror fallback?
3. Confirm the AGPL-3.0 external-binary approach (download + run, no bundling) is
   acceptable for your distribution model.

**Status: waiting for confirmation of backends before writing any code.**
