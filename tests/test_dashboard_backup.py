"""Tests for the dashboard, server-config editor, TLS, backups and notifications."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core import config_validate, crypto
from app.models.node import Node
from app.models.node_metric import NodeMetric


# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #
def test_dashboard_aggregates(db) -> None:  # noqa: ANN001
    from app.services import dashboard as dashboard_service
    from app.services import nodes as nodes_service
    from app.services import vpn_users as vpn_users_service

    node, _ = nodes_service.create_node(db, name="de1", host="10.0.0.1")
    node.status = "online" if False else node.status
    db.flush()
    first = vpn_users_service.create_vpn_user(db, username="client1", password="client-pass")
    second = vpn_users_service.create_vpn_user(db, username="client2", password="client-pass")
    first.bytes_in, first.bytes_out = 100, 50
    second.bytes_in = 25
    second.is_online = True
    db.add(
        NodeMetric(
            node_id=node.id,
            cpu_percent=20.0,
            mem_total=1000,
            mem_used=500,
            disk_total=2000,
            disk_used=1000,
            net_rx=10,
            net_tx=20,
            client_count=1,
            recorded_at=datetime.now(timezone.utc),
        )
    )
    db.flush()

    data = dashboard_service.build(db)
    assert data.total_users == 2
    assert data.online_users == 1
    assert data.total_nodes == 1
    assert data.total_bytes == 175
    assert data.metrics.available
    assert data.metrics.mem_percent == 50.0
    assert data.metrics.disk_percent == 50.0
    assert data.top_users[0].username == "client1"
    assert len(data.traffic_labels) == 14


def test_dashboard_scoped_to_owner(db) -> None:  # noqa: ANN001
    from app.models.user import Role
    from app.services import dashboard as dashboard_service
    from app.services import users as users_service
    from app.services import vpn_users as vpn_users_service

    reseller = users_service.create_user(
        db, username="reseller1", password="reseller-pass", role=Role.RESELLER
    )
    vpn_users_service.create_vpn_user(
        db, username="client1", password="client-pass", owner=reseller
    )
    vpn_users_service.create_vpn_user(db, username="client2", password="client-pass")
    db.flush()
    scoped = dashboard_service.build(db, owner_id=reseller.id)
    assert scoped.total_users == 1


def test_dashboard_metrics_without_samples(db) -> None:  # noqa: ANN001
    from app.services import dashboard as dashboard_service

    data = dashboard_service.build(db)
    assert data.metrics.available is False
    assert data.metrics.mem_percent == 0.0


# --------------------------------------------------------------------------- #
# Config validation
# --------------------------------------------------------------------------- #
def test_validate_server_conf_ok() -> None:
    result = config_validate.validate_server_conf(
        "port 1194\nproto udp\ndev tun\nserver 10.8.0.0 255.255.255.0\ncipher AES-256-GCM\n"
    )
    assert result.ok
    assert "proto" in result.directives


def test_validate_server_conf_rejects_shell_metacharacters() -> None:
    result = config_validate.validate_server_conf(
        'port 1194\nproto udp\npush "dhcp-option DNS 1.1.1.1; rm -rf /"\n'
    )
    assert not result.ok


def test_validate_server_conf_rejects_forbidden_directive() -> None:
    result = config_validate.validate_server_conf("port 1194\nproto udp\nup /etc/openvpn/evil.sh\n")
    assert not result.ok


def test_validate_server_conf_rejects_unterminated_block() -> None:
    result = config_validate.validate_server_conf(
        "port 1194\nproto udp\n<ca>\n-----BEGIN CERTIFICATE-----\n"
    )
    assert not result.ok


def test_validate_server_conf_rejects_empty() -> None:
    assert not config_validate.validate_server_conf("").ok


def test_validate_server_conf_unknown_directive_is_warning() -> None:
    result = config_validate.validate_server_conf("port 1194\nproto udp\nmade-up-directive 5\n")
    assert result.ok
    assert any("unknown" in w for w in result.warnings)


def test_validate_inline_block_ok() -> None:
    result = config_validate.validate_server_conf("port 1194\nproto udp\n<ca>\nCERTDATA\n</ca>\n")
    assert result.ok


# --------------------------------------------------------------------------- #
# Server config apply / rollback
# --------------------------------------------------------------------------- #
def test_server_config_apply_rejects_invalid() -> None:
    from app.services import server_config

    result = server_config.apply("up /evil\n")
    assert not result.ok
    assert "not allowed" in result.message


def test_server_config_read_without_helper() -> None:
    from app.services import server_config

    # The helper is unavailable in tests; read returns an empty string.
    assert server_config.read_config("/etc/openvpn/instances/server.conf") == ""


def test_server_config_apply_write_failure(monkeypatch) -> None:  # noqa: ANN001
    from app.services import helper_client, server_config

    def _boom(_path, _content):  # noqa: ANN001
        raise helper_client.HelperError("unavailable")

    monkeypatch.setattr(server_config, "read_config", lambda path=None: "")
    monkeypatch.setattr(helper_client, "write_config_file", _boom)
    result = server_config.apply("port 1194\nproto udp\n")
    assert not result.ok


def test_server_config_apply_rollback(monkeypatch) -> None:  # noqa: ANN001
    from app.services import helper_client, server_config

    writes: list[str] = []

    def _write(path, content):  # noqa: ANN001
        writes.append(content)
        return helper_client.HelperResult(ok=True, data={})

    def _instance(name, verb):  # noqa: ANN001
        return helper_client.HelperResult(ok=False, error="restart failed")

    monkeypatch.setattr(server_config, "read_config", lambda path=None: "port 1194\n")
    monkeypatch.setattr(helper_client, "write_config_file", _write)
    monkeypatch.setattr(helper_client, "instance_action", _instance)

    result = server_config.apply("port 443\nproto udp\n")
    assert not result.ok
    assert result.rolled_back
    # The previous config was written back (2 writes: new + rollback).
    assert len(writes) == 2
    assert writes[-1] == "port 1194\n"


# --------------------------------------------------------------------------- #
# Crypto
# --------------------------------------------------------------------------- #
def test_crypto_roundtrip() -> None:
    payload = b"secret backup bytes \x00\x01\x02"
    container = crypto.encrypt(payload, "correct horse battery staple")
    assert crypto.is_container(container)
    assert crypto.decrypt(container, "correct horse battery staple") == payload


def test_crypto_wrong_password() -> None:
    container = crypto.encrypt(b"data", "right-password")
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt(container, "wrong-password")


def test_crypto_rejects_foreign_data() -> None:
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt(b"not-a-container", "x")


def test_crypto_nonce_is_random() -> None:
    a = crypto.encrypt(b"same", "pw")
    b = crypto.encrypt(b"same", "pw")
    assert a != b  # fresh salt+nonce each time


# --------------------------------------------------------------------------- #
# Backup service
# --------------------------------------------------------------------------- #
def test_backup_roundtrip(db) -> None:  # noqa: ANN001
    from app.services import backup

    result = backup.create_backup("backup-password", include_pki=False)
    assert result.filename.endswith(".ovpnbak")
    assert result.size > 0
    manifest = backup.read_manifest(result.payload, "backup-password")
    assert manifest["format"] == 1


def test_backup_requires_password() -> None:
    from app.services import backup

    with pytest.raises(backup.BackupError):
        backup.create_backup("")


def test_backup_restore_wrong_password() -> None:
    from app.services import backup

    result = backup.create_backup("backup-password", include_pki=False)
    with pytest.raises(crypto.CryptoError):
        backup.restore_backup(result.payload, "wrong")


def test_backup_restore_extracts_database(db) -> None:  # noqa: ANN001
    from app.services import backup

    result = backup.create_backup("backup-password", include_pki=False)
    restored = backup.restore_backup(result.payload, "backup-password")
    assert restored["manifest"]["format"] == 1


# --------------------------------------------------------------------------- #
# TLS
# --------------------------------------------------------------------------- #
def test_ssl_configure_letsencrypt(db) -> None:  # noqa: ANN001
    from app.services import ssl_certs

    status = ssl_certs.configure_letsencrypt(
        db, domain="panel.example.com", email="admin@example.com"
    )
    assert status.mode == "letsencrypt"
    assert status.enabled
    assert status.domain == "panel.example.com"
    assert status.cert_path.endswith("fullchain.pem")


def test_ssl_rejects_bad_domain(db) -> None:  # noqa: ANN001
    from app.services import ssl_certs

    with pytest.raises(ssl_certs.SslError):
        ssl_certs.configure_letsencrypt(db, domain="not a domain", email="a@b.c")


def test_ssl_configure_custom(db) -> None:  # noqa: ANN001
    from app.services import ssl_certs

    cert = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n"
    key = "-----BEGIN PRIVATE KEY-----\nMIIE\n-----END PRIVATE KEY-----\n"
    status = ssl_certs.configure_custom(db, cert_pem=cert, key_pem=key)
    assert status.mode == "custom"
    assert status.has_custom_cert


def test_ssl_rejects_invalid_pem(db) -> None:  # noqa: ANN001
    from app.services import ssl_certs

    with pytest.raises(ssl_certs.SslError):
        ssl_certs.configure_custom(db, cert_pem="nope", key_pem="nope")


def test_ssl_letsencrypt_commands() -> None:
    from app.services import ssl_certs

    commands = ssl_certs.letsencrypt_commands("panel.example.com")
    assert "certbot" in commands["obtain"]
    assert "renew" in commands["renew"]
    assert "timer" in commands


# --------------------------------------------------------------------------- #
# Notifications
# --------------------------------------------------------------------------- #
def test_notifications_no_channels(db) -> None:  # noqa: ANN001
    from app.services import notifications

    results = notifications.send(db, "hello")
    assert results == {"telegram": False, "webhook": False}


def test_notifications_quota_alert(db) -> None:  # noqa: ANN001
    from app.services import notifications
    from app.services import vpn_users as vpn_users_service

    user = vpn_users_service.create_vpn_user(
        db, username="client1", password="client-pass", data_limit_bytes=100
    )
    user.bytes_in = 95
    db.flush()
    assert notifications.scan_user_alerts(db) >= 1
    # Second scan does not re-alert (deduplicated).
    assert notifications.scan_user_alerts(db) == 0


def test_notifications_expiry_alert(db) -> None:  # noqa: ANN001
    from app.services import notifications
    from app.services import vpn_users as vpn_users_service

    vpn_users_service.create_vpn_user(
        db,
        username="client1",
        password="client-pass",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=12),
    )
    db.flush()
    assert notifications.scan_user_alerts(db) >= 1


def test_notifications_export(db) -> None:  # noqa: ANN001
    from app.services import notifications

    assert notifications.to_json_event("hi", event="test")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def test_node_model_import_for_typing() -> None:
    assert Node.__tablename__ == "nodes"
