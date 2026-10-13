"""Tests for the privileged helper's allow-listed actions."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

from helper import actions


def test_unknown_action_rejected() -> None:
    result = actions.dispatch({"action": "rm_rf"})
    assert result.ok is False
    assert result.error == "unknown action"


def test_service_action_rejects_unknown_service() -> None:
    result = actions.service_action("sshd", "restart")
    assert result.ok is False


def test_service_action_rejects_unknown_verb() -> None:
    result = actions.service_action("ovpn-panel", "destroy")
    assert result.ok is False


def test_service_action_uses_argv(monkeypatch) -> None:  # noqa: ANN001
    captured: dict = {}

    def fake_run(argv, **kwargs):  # noqa: ANN001
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(actions, "_run", fake_run)
    result = actions.service_action("ovpn-panel", "restart")
    assert result.ok
    assert captured["argv"] == ["/usr/bin/systemctl", "restart", "ovpn-panel"]
    assert "shell" not in captured["kwargs"]


def test_shell_is_never_enabled_in_module() -> None:
    import inspect

    source = inspect.getsource(actions)
    assert "shell=" not in source


def test_agent_service_action_validates_node_id() -> None:
    assert actions.agent_service_action(-1, "restart").ok is False
    assert actions.agent_service_action("x", "restart").ok is False  # type: ignore[arg-type]


def test_issue_certificate_rejects_bad_cn() -> None:
    assert actions.issue_client_certificate("bad cn").ok is False


def test_revoke_certificate_rejects_bad_cn() -> None:
    assert actions.revoke_client_certificate("../etc/passwd").ok is False


def test_write_auth_file_rejects_bad_hash() -> None:
    result = actions.write_auth_file({"client1": "line1\nline2"})
    assert result.ok is False


def test_write_auth_file_validates_username(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(actions, "AUTH_FILE", tmp_path / "users.db")
    result = actions.write_auth_file({"bad name": "$argon2id$..."})
    assert result.ok is False


def test_write_auth_file_writes_hmac_lines(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    target = tmp_path / "users.db"
    monkeypatch.setattr(actions, "AUTH_FILE", target)
    result = actions.write_auth_file({"client1": "$argon2id$v=19$hash"})
    assert result.ok
    assert target.read_text(encoding="utf-8") == "client1:$argon2id$v=19$hash\n"


def test_run_does_not_use_shell(monkeypatch) -> None:  # noqa: ANN001
    seen: dict = {}

    def fake_run(argv, **kwargs):  # noqa: ANN001
        seen.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    actions._run(["/bin/true"])
    assert seen.get("shell") is None


def test_instance_action_rejects_bad_name() -> None:
    assert actions.openvpn_instance_action("../evil", "restart").ok is False
    assert actions.openvpn_instance_action("server", "destroy").ok is False


def test_instance_action_uses_argv(monkeypatch) -> None:  # noqa: ANN001
    captured: dict = {}

    def fake_run(argv, **kwargs):  # noqa: ANN001
        captured["argv"] = argv
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(actions, "_run", fake_run)
    result = actions.openvpn_instance_action("server", "restart")
    assert result.ok
    assert captured["argv"] == ["/usr/bin/systemctl", "restart", "openvpn-server@server"]


def test_read_write_config_file_roundtrip(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(actions, "ALLOWED_CONFIG_DIRS", (tmp_path,))
    target = tmp_path / "server.conf"
    write = actions.write_config_file(str(target), "port 1194\n")
    assert write.ok
    read = actions.read_config_file(str(target))
    assert read.ok
    assert read.data["content"] == "port 1194\n"


def test_config_file_rejects_outside_root(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(actions, "ALLOWED_CONFIG_DIRS", (tmp_path / "allowed",))
    (tmp_path / "allowed").mkdir()
    assert actions.read_config_file(str(tmp_path / "outside.conf")).ok is False
    assert actions.write_config_file("/etc/passwd", "x").ok is False


def test_config_file_rejects_relative_path() -> None:
    assert actions.read_config_file("relative.conf").ok is False


def test_reload_firewall_uses_argv(monkeypatch) -> None:  # noqa: ANN001
    captured: dict = {}

    def fake_run(argv, **kwargs):  # noqa: ANN001
        captured["argv"] = argv
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(actions, "_run", fake_run)
    assert actions.reload_firewall().ok
    assert captured["argv"] == ["/usr/bin/systemctl", "restart", "ovpn-panel-nat.service"]


def test_dispatch_routes_new_actions(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(actions, "ALLOWED_CONFIG_DIRS", (tmp_path,))
    target = tmp_path / "instances" / "server.conf"
    result = actions.dispatch(
        {"action": "write_config_file", "path": str(target), "content": "port 1194\n"}
    )
    assert result.ok
    result = actions.dispatch({"action": "instance", "name": "server", "verb": "status"})
    # systemctl is not available in the test environment; must not raise.
    assert isinstance(result.ok, bool)
