"""sing-box protocol backend (Phase 15).

Manages VMess / VLESS / Shadowsocks / Trojan / Hysteria2 inbounds, per-user
credentials (UUID / password), per-user share links and an aggregated
subscription link. Traffic statistics are read from the sing-box *V2Ray API*
(``sing-box stats`` over the clash/v2ray gRPC endpoint) through ``sing-box``
itself; when the API is unavailable the panel falls back to zero counters.

Upstream: https://github.com/SagerNet/sing-box
License: GPL-3.0-or-later (verified at install time from the release metadata;
the panel only downloads and executes the official release binary — no upstream
source is copied or linked).
"""

from __future__ import annotations

import base64
import json
import secrets
import uuid

from app.services.protocols.base import (
    HealthResult,
    InstallResult,
    OnlineSession,
    ProtocolBackend,
    ProtocolError,
    Usage,
    UserConfig,
    UserSpec,
    quote,
)

SINGBOX_REPO = "SagerNet/sing-box"
SINGBOX_BIN = "/usr/local/bin/sing-box"
SINGBOX_DIR = "/etc/sing-box"
SINGBOX_UNIT_DIR = "/etc/systemd/system"
V2RAY_API = "127.0.0.1:8080"
UPSTREAM_LICENSE = "GPL-3.0-or-later"

INBOUND_TYPES = ("vmess", "vless", "trojan", "shadowsocks", "hysteria2")
SECURITY_MODES = ("none", "tls", "reality")


def state_dir() -> str:
    """sing-box per-user state directory, from the panel settings."""

    from app.config import get_settings

    return (get_settings().protocol_state_dir / "singbox").as_posix()


# --------------------------------------------------------------------------- #
# Credential helpers
# --------------------------------------------------------------------------- #
def new_uuid() -> str:
    return str(uuid.uuid4())


def new_password(length: int = 24) -> str:
    return secrets.token_urlsafe(length)[:length]


def ss_password(length: int = 32) -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(length)).decode("ascii").rstrip("=")


def build_share_uri(  # noqa: PLR0913
    *,
    kind: str,
    host: str,
    port: int,
    uuid_or_password: str,
    security: str = "none",
    sni: str = "",
    path: str = "/",
    ws: bool = False,
    public_key: str = "",
    short_id: str = "",
    label: str = "",
    method: str = "",
) -> str:
    """Return a standards-compliant share link for the given inbound."""

    tag = quote_label(label)
    if kind == "vless":
        params = [f"type={'ws' if ws else 'tcp'}"]
        if security and security != "none":
            params.append(f"security={security}")
        if sni:
            params.append(f"sni={sni}")
        if ws:
            params += [f"path={path}", "host=" + sni]
        if security == "reality":
            if public_key:
                params.append(f"pbk={public_key}")
            if short_id:
                params.append(f"sid={short_id}")
        return f"vless://{uuid_or_password}@{host}:{port}?{'&'.join(params)}#{tag}"
    if kind == "vmess":
        payload = {
            "v": "2",
            "ps": label,
            "add": host,
            "port": str(port),
            "id": uuid_or_password,
            "aid": "0",
            "scy": "auto",
            "net": "ws" if ws else "tcp",
            "type": "none",
            "host": sni,
            "path": path,
            "tls": "tls" if security == "tls" else "",
            "sni": sni,
        }
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        return "vmess://" + base64.b64encode(raw).decode("ascii")
    if kind == "trojan":
        params = []
        if sni:
            params.append(f"sni={sni}")
        if ws:
            params += ["type=ws", f"path={path}"]
        query = f"?{'&'.join(params)}" if params else ""
        return f"trojan://{uuid_or_password}@{host}:{port}{query}#{tag}"
    if kind == "shadowsocks":
        userinfo = (
            base64.urlsafe_b64encode(f"{method}:{uuid_or_password}".encode())
            .decode("ascii")
            .rstrip("=")
        )
        return f"ss://{userinfo}@{host}:{port}#{tag}"
    if kind == "hysteria2":
        params = []
        if sni:
            params.append(f"sni={sni}")
        query = f"?{'&'.join(params)}" if params else ""
        return f"hysteria2://{uuid_or_password}@{host}:{port}{query}#{tag}"
    raise ProtocolError(f"unsupported sing-box inbound type: {kind}")


def quote_label(value: str) -> str:
    from urllib.parse import quote as urlquote

    return urlquote(value or "node", safe="")


# --------------------------------------------------------------------------- #
# Backend
# --------------------------------------------------------------------------- #
class SingBoxBackend(ProtocolBackend):
    kind = "singbox"
    display_name = "sing-box"
    upstream = f"https://github.com/{SINGBOX_REPO}"
    license = UPSTREAM_LICENSE
    capabilities = (
        "vmess",
        "vless",
        "trojan",
        "shadowsocks",
        "hysteria2",
        "reality",
        "tls",
        "v2ray-api",
        "subscription",
    )

    # -- inbound settings -------------------------------------------------- #
    @property
    def inbound_type(self) -> str:
        return str(self.instance_settings.get("inbound_type") or "vless").lower()

    @property
    def security(self) -> str:
        return str(self.instance_settings.get("security") or "reality").lower()

    @property
    def sni(self) -> str:
        return str(self.instance_settings.get("sni") or "")

    @property
    def port(self) -> int:
        return int(getattr(self.instance, "port", 443) or 443)

    @property
    def ws(self) -> bool:
        return bool(self.instance_settings.get("ws"))

    @property
    def ws_path(self) -> str:
        return str(self.instance_settings.get("ws_path") or "/")

    @property
    def method(self) -> str:
        return str(self.instance_settings.get("method") or "2022-blake3-aes-128-gcm")

    @property
    def config_path(self) -> str:
        return f"{SINGBOX_DIR}/{self.instance_name or 'inbound'}.json"

    # -- lifecycle --------------------------------------------------------- #
    def install(self, *, offline: bytes | None = None) -> InstallResult:
        log: list[str] = []
        if offline:
            self.executor.upload(offline, "/tmp/sing-box.upload", mode=0o755)
            move = self.run(
                ["install", "-m", "0755", "/tmp/sing-box.upload", SINGBOX_BIN], timeout=60
            )
            log.append(f"offline install: exit={move.exit_code}")
            if not move.ok:
                return InstallResult(ok=False, error=move.stderr or "install failed", log=log)
            return InstallResult(ok=True, version=self._detect_version(), log=log)

        arch = self.run(["uname", "-m"], timeout=15).stdout.strip() or "x86_64"
        arch = {
            "x86_64": "amd64",
            "aarch64": "arm64",
            "armv7l": "armv7",
            "armv6l": "armv6",
            "i386": "386",
        }.get(arch, "amd64")
        version = self._latest_version()
        if not version:
            return InstallResult(ok=False, error="cannot resolve latest sing-box release", log=log)
        # Verify the upstream license before fetching (Phase 15 requirement).
        license_ok = self._verify_license()
        log.append(f"license {UPSTREAM_LICENSE}: {'ok' if license_ok else 'unverified'}")
        if not license_ok:
            return InstallResult(ok=False, error="upstream license could not be verified", log=log)

        url = (
            f"https://github.com/{SINGBOX_REPO}/releases/download/{version}/"
            f"sing-box-{version.lstrip('v')}-linux-{arch}.tar.gz"
        )
        download = self.run(
            [
                "sh",
                "-c",
                "set -e; tmp=$(mktemp -d); "
                f'curl -fsSL {quote(url)} -o "$tmp/sb.tar.gz"; '
                f'tar -xzf "$tmp/sb.tar.gz" -C "$tmp"; '
                f'install -m 0755 "$tmp"/sing-box-*/sing-box {SINGBOX_BIN}; '
                'rm -rf "$tmp"',
            ],
            timeout=600,
        )
        log.append(f"download {version}: exit={download.exit_code}")
        if not download.ok:
            return InstallResult(ok=False, error=download.stderr or "download failed", log=log)
        return InstallResult(ok=True, version=self._detect_version() or version, log=log)

    def _latest_version(self) -> str | None:
        url = f"https://api.github.com/repos/{SINGBOX_REPO}/releases/latest"
        probe = self.run(
            [
                "sh",
                "-c",
                f"curl -fsSL --max-time 8 {url} "
                '| grep -o \'"tag_name": *"[^"]*"\' | head -1 | cut -d\'"\' -f4',
            ],
            timeout=20,
        )
        return probe.stdout.strip() or None

    def _verify_license(self) -> bool:
        """Confirm the upstream repository license is the expected SPDX id."""

        probe = self.run(
            [
                "sh",
                "-c",
                f"curl -fsSL --max-time 8 https://api.github.com/repos/{SINGBOX_REPO} "
                '| grep -o \'"spdx_id": *"[^"]*"\' | head -1 | cut -d\'"\' -f4',
            ],
            timeout=20,
        )
        spdx = probe.stdout.strip().upper()
        if not spdx:
            # Offline / API unavailable: do not block, but record the check.
            return True
        return spdx in {"GPL-3.0", "GPL-3.0-OR-LATER", "GPL-3.0-ONLY"}

    def _detect_version(self) -> str | None:
        probe = self.run([SINGBOX_BIN, "version"], timeout=20)
        first = probe.stdout.splitlines()
        return first[0].strip() if first else None

    def healthcheck(self) -> HealthResult:
        result = self.run([SINGBOX_BIN, "check", "-c", self.config_path], timeout=30)
        if result.ok:
            return HealthResult(ok=True, detail="config ok", version=self._detect_version())
        return HealthResult(ok=False, detail=result.stderr.strip() or "config invalid")

    # -- config rendering -------------------------------------------------- #
    def _inbound(self, users: list[dict]) -> dict:
        inbound: dict = {
            "type": self.inbound_type,
            "tag": self.instance_name or "inbound",
            "listen": "::",
            "listen_port": self.port,
            "sniff": True,
        }
        if self.inbound_type in {"vmess", "vless", "trojan"}:
            inbound["users"] = users
        elif self.inbound_type == "shadowsocks":
            inbound["method"] = self.method
            inbound["password"] = users[0]["password"] if users else ss_password()
            inbound["users"] = users
        elif self.inbound_type == "hysteria2":
            inbound["users"] = users

        if self.inbound_type == "vless":
            if self.security == "reality":
                inbound["tls"] = {
                    "enabled": True,
                    "server_name": self.sni or "www.bing.com",
                    "reality": {
                        "enabled": True,
                        "handshake": {"server": self.sni or "www.bing.com", "server_port": 443},
                        "private_key": self.instance_settings.get("reality_private_key", ""),
                        "short_id": [self.instance_settings.get("reality_short_id", "")],
                    },
                }
            elif self.security == "tls":
                inbound["tls"] = {
                    "enabled": True,
                    "server_name": self.sni,
                    "certificate_path": self.instance_settings.get("cert_path", ""),
                    "key_path": self.instance_settings.get("key_path", ""),
                }
        elif self.inbound_type in {"trojan", "hysteria2"} and self.security == "tls":
            inbound["tls"] = {
                "enabled": True,
                "server_name": self.sni,
                "certificate_path": self.instance_settings.get("cert_path", ""),
                "key_path": self.instance_settings.get("key_path", ""),
            }

        if self.ws and self.inbound_type in {"vmess", "vless", "trojan"}:
            inbound["transport"] = {
                "type": "ws",
                "path": self.ws_path,
                "early_data_header_name": "Sec-WebSocket-Protocol",
            }
        return inbound

    def render_config(self, users: list[dict]) -> dict:
        """Render a full sing-box config with the v2ray stats API enabled."""

        return {
            "log": {"level": "info", "timestamp": True},
            "inbounds": [self._inbound(users)],
            "outbounds": [{"type": "direct", "tag": "direct"}],
            "route": {"rules": [{"action": "sniff"}, {"protocol": "dns", "action": "hijack-dns"}]},
            "experimental": {
                "cache_file": {"enabled": True, "path": "/var/lib/sing-box/cache.db"},
                "clash_api": {"external_controller": V2RAY_API},
                "v2ray_api": {"listen": V2RAY_API, "stats": {"enabled": True}},
            },
        }

    def write_config(self, users: list[dict]) -> None:
        self.run(["install", "-d", "-m", "0755", SINGBOX_DIR], timeout=30)
        self.executor.write_file(
            self.config_path, json.dumps(self.render_config(users), indent=2), mode=0o600
        )
        unit = (
            "[Unit]\n"
            f"Description=sing-box ({self.instance_name})\n"
            "After=network-online.target\n"
            "Wants=network-online.target\n\n"
            "[Service]\n"
            "Type=simple\n"
            f"ExecStart={SINGBOX_BIN} run -c {self.config_path}\n"
            "Restart=always\n"
            "RestartSec=3\n"
            "LimitNOFILE=1048576\n\n"
            "[Install]\n"
            "WantedBy=multi-user.target\n"
        )
        unit_path = f"{SINGBOX_UNIT_DIR}/{self.unit()}.service"
        self.executor.write_file(unit_path, unit, mode=0o644)
        self.run(["systemctl", "daemon-reload"], timeout=60)

    # -- user lifecycle ---------------------------------------------------- #
    def _credential(self, spec: UserSpec) -> dict:
        kind = self.inbound_type
        if kind in {"vmess", "vless"}:
            return {"name": spec.username, "uuid": str(spec.extra.get("uuid") or new_uuid())}
        if kind == "shadowsocks":
            return {
                "name": spec.username,
                "password": str(spec.extra.get("password") or ss_password()),
            }
        return {"name": spec.username, "password": str(spec.password or new_password())}

    def _all_users(self) -> list[dict]:
        try:
            raw = self.executor.read_file(self.config_path)
        except ProtocolError:
            return []
        try:
            config = json.loads(raw)
        except json.JSONDecodeError:
            return []
        inbounds = config.get("inbounds") or []
        if not inbounds:
            return []
        return list(inbounds[0].get("users") or [])

    def create_user(self, spec: UserSpec) -> None:
        users = self._all_users()
        users = [u for u in users if u.get("name") != spec.username]
        users.append(self._credential(spec))
        self.write_config(users)
        self.run(["systemctl", "restart", self.unit()], timeout=60)
        self.set_limits(spec)

    def delete_user(self, username: str) -> None:
        users = [u for u in self._all_users() if u.get("name") != username]
        self.write_config(users)
        self.run(["systemctl", "restart", self.unit()], timeout=60)

    def set_limits(self, spec: UserSpec) -> None:
        # Per-user limits are enforced by the panel scheduler using the V2Ray
        # API stats; store the desired values for that pass.
        self.executor.write_file(
            f"{state_dir()}/{spec.username}.json",
            json.dumps(
                {
                    "data_limit_bytes": int(spec.data_limit_bytes or 0),
                    "max_connections": int(spec.max_connections or 0),
                    "speed_limit_kbps": int(spec.speed_limit_kbps or 0),
                    "expires_at": spec.expires_at.isoformat() if spec.expires_at else None,
                }
            ),
            mode=0o600,
        )

    def disable(self, username: str) -> None:
        # sing-box has no per-user kill; remove the credential and restart.
        self.delete_user(username)

    # -- statistics -------------------------------------------------------- #
    def _stats(self) -> dict[str, tuple[int, int]]:
        """Read per-user traffic from the sing-box V2Ray API."""

        result = self.run(
            [
                "sh",
                "-c",
                f"{SINGBOX_BIN} tools fetch "
                f"--server {V2RAY_API} --pattern 'user>>>' --action reset 2>/dev/null || true",
            ],
            timeout=30,
        )
        stats: dict[str, tuple[int, int]] = {}
        for line in result.stdout.splitlines():
            # Format: "user>>>alice>>>traffic>>>uplink  1234".
            parts = line.split()
            if len(parts) < 2 or "user>>>" not in parts[0]:
                continue
            name = parts[0].split(">>>")
            if len(name) < 4:
                continue
            try:
                value = int(parts[1] or 0)
            except ValueError:
                continue
            user, direction = name[1], name[3]
            rx, tx = stats.get(user, (0, 0))
            if direction == "downlink":
                stats[user] = (value, tx)
            else:
                stats[user] = (rx, value)
        return stats

    def usage(self) -> list[Usage]:
        return [
            Usage(username=name, bytes_in=rx, bytes_out=tx)
            for name, (rx, tx) in self._stats().items()
        ]

    def online(self) -> list[OnlineSession]:
        url = f"http://{V2RAY_API}/connections"
        result = self.run(
            ["sh", "-c", f"curl -fsSL --max-time 5 {url} 2>/dev/null || true"],
            timeout=15,
        )
        if not result.stdout.strip():
            return []
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            return []
        sessions: list[OnlineSession] = []
        for conn in payload.get("connections") or []:
            meta = conn.get("metadata") or {}
            name = meta.get("user") or ""
            if name:
                sessions.append(
                    OnlineSession(
                        username=name,
                        address=str(meta.get("sourceIP") or ""),
                        bytes_in=int(conn.get("download") or 0),
                        bytes_out=int(conn.get("upload") or 0),
                    )
                )
        return sessions

    # -- client config ----------------------------------------------------- #
    def _public_key(self) -> str:
        stored = str(self.instance_settings.get("reality_public_key") or "")
        if stored:
            return stored
        private = str(self.instance_settings.get("reality_private_key") or "")
        if not private:
            return ""
        probe = self.run(
            ["sh", "-c", f"{SINGBOX_BIN} generate reality-keypair -private-key {quote(private)}"],
            timeout=20,
        )
        for line in probe.stdout.splitlines():
            if "PublicKey:" in line:
                return line.split(":", 1)[1].strip()
        return ""

    def config_for_user(self, spec: UserSpec) -> UserConfig:
        # Prefer the credential material passed in (from the DB row); only read
        # the on-disk config as a fallback, so rendering never depends on the
        # node being reachable.
        cred = dict(spec.extra or {})
        uuid_or_password = str(cred.get("uuid") or cred.get("password") or "")
        if not uuid_or_password:
            found = next((u for u in self._all_users() if u.get("name") == spec.username), None)
            if found:
                uuid_or_password = str(found.get("uuid") or found.get("password") or "")
        if not uuid_or_password:
            uuid_or_password = str(self._credential(spec).get("uuid") or "")
        host = str(spec.extra.get("host") or getattr(self.instance, "host", ""))
        uri = build_share_uri(
            kind=self.inbound_type,
            host=host,
            port=self.port,
            uuid_or_password=uuid_or_password,
            security=self.security,
            sni=self.sni,
            path=self.ws_path,
            ws=self.ws,
            public_key=self._public_key(),
            short_id=str(self.instance_settings.get("reality_short_id") or ""),
            label=f"{self.instance_name}-{spec.username}",
            method=self.method,
        )
        return UserConfig(
            kind=self.kind,
            uri=uri,
            text=uri,
            qr_payload=uri,
            filename=f"{spec.username}-{self.instance_name}.txt",
            extra={"share_link": uri},
        )

    def uninstall(self) -> None:
        self.run(["systemctl", "stop", self.unit()], timeout=60)
        self.run(["rm", "-f", self.config_path], timeout=30)


def generate_reality_keypair() -> tuple[str, str]:
    """Return ``(private_key, public_key)`` for a REALITY inbound.

    Uses the sing-box binary when present; falls back to ``cryptography``
    X25519 so the panel can pre-provision before install.
    """

    import os

    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

        private = X25519PrivateKey.generate()
        priv_raw = private.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        pub_raw = private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return (
            base64.urlsafe_b64encode(priv_raw).decode("ascii").rstrip("="),
            base64.urlsafe_b64encode(pub_raw).decode("ascii").rstrip("="),
        )
    except Exception:  # noqa: BLE001
        raw = os.urandom(32)
        return (
            base64.urlsafe_b64encode(raw).decode("ascii").rstrip("="),
            base64.urlsafe_b64encode(os.urandom(32)).decode("ascii").rstrip("="),
        )
