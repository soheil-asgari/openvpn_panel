"""Tests for OpenVPN status parsing and hook helpers."""

from __future__ import annotations

from app.services.ovpn import parse_auth_line, parse_status


def test_parse_status_with_clients() -> None:
    lines = [
        "TITLE,OpenVPN 2.6",
        "CLIENT_LIST\tclient1\t1.2.3.4:1194\t10.8.0.2\t\t1024\t2048\tMon Oct 12 08:00:00 2026",
        "END",
    ]
    status = parse_status(lines)
    assert status.online_count == 1
    client = status.clients[0]
    assert client.common_name == "client1"
    assert client.bytes_received == 1024
    assert client.bytes_sent == 2048


def test_parse_status_empty() -> None:
    assert parse_status(["TITLE,OpenVPN", "END"]).online_count == 0


def test_parse_status_tolerates_garbage() -> None:
    lines = ["CLIENT_LIST\ttoo\tshort", "END"]
    assert parse_status(lines).online_count == 0


def test_parse_auth_line() -> None:
    assert parse_auth_line("alice:secret") == ("alice", "secret")


def test_parse_auth_line_with_colon_in_password() -> None:
    assert parse_auth_line("alice:sec:ret") == ("alice", "sec:ret")


def test_parse_auth_line_invalid() -> None:
    assert parse_auth_line("") is None
    assert parse_auth_line("no-colon") is None
    assert parse_auth_line(":only-password") is None
