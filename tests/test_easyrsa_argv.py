"""Unit tests for the helper's easy-rsa command builder.

easy-rsa 3.1.x rejects the space-separated form ``--pki-dir <dir>`` with
"Missing value to option: --pki-dir"; the value must be attached with ``=``.
These tests pin the ``--opt=value`` form and the EASYRSA_PKI environment.
"""

from __future__ import annotations

from pathlib import Path

from helper import actions


def test_easyrsa_argv_uses_equals_form() -> None:
    argv = actions.build_easyrsa_argv("build-client-full", "alice", "nopass")
    assert argv[0] == actions.EASYRSA
    assert "--batch" in argv
    # The option value must be attached with "=", never a separate token.
    assert f"--pki-dir={actions.PKI_DIR}" in argv
    assert "--pki-dir" not in argv
    assert str(actions.PKI_DIR) not in argv
    # Command names remain positional arguments after the options.
    assert argv[-3:] == ["build-client-full", "alice", "nopass"]


def test_easyrsa_argv_custom_pki_dir(tmp_path: Path) -> None:
    argv = actions.build_easyrsa_argv("revoke", "bob", pki_dir=tmp_path)
    assert f"--pki-dir={tmp_path}" in argv
    assert "--pki-dir" not in argv


def test_easyrsa_env_sets_pki_and_batch() -> None:
    env = actions.build_easyrsa_env()
    assert env["EASYRSA_PKI"] == str(actions.PKI_DIR)
    assert env["EASYRSA_BATCH"] == "1"


def test_issue_certificate_command_uses_equals_form(monkeypatch) -> None:  # noqa: ANN001
    captured: dict = {}

    def fake_run(argv, **kwargs):  # noqa: ANN001
        captured["argv"] = argv
        captured["env"] = kwargs.get("env", {})
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(actions.subprocess, "run", fake_run)
    result = actions.issue_client_certificate("alice")
    assert result.ok
    assert f"--pki-dir={actions.PKI_DIR}" in captured["argv"]
    assert captured["env"].get("EASYRSA_PKI") == str(actions.PKI_DIR)


def test_revoke_certificate_does_not_hide_output(monkeypatch) -> None:  # noqa: ANN001
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):  # noqa: ANN001
        calls.append(argv)
        return type("P", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()

    monkeypatch.setattr(actions.subprocess, "run", fake_run)
    result = actions.revoke_client_certificate("alice")
    assert result.ok
    # revoke followed by gen-crl, both with the "=" form.
    assert len(calls) == 2
    assert all(f"--pki-dir={actions.PKI_DIR}" in c for c in calls)
