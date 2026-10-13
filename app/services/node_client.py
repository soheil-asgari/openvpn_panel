"""HTTP client for node agents.

All requests are authenticated with an HMAC-SHA256 signature over
``method\\npath\\ntimestamp\\nbody`` using the node's shared secret. Bearer-token
auth is kept as a fallback for nodes hand-provisioned before HMAC existed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

from app.core import hmac_auth
from app.models.node import Node


class NodeClientError(RuntimeError):
    """Raised when a node agent is unreachable or returns an error."""


@dataclass(slots=True)
class AgentResult:
    ok: bool
    status_code: int = 0
    data: dict = field(default_factory=dict)
    error: str | None = None


def _path_of(url: str) -> str:
    parsed = urlparse(url)
    return parsed.path or "/"


class NodeAgentClient:
    """Synchronous client for a single node agent."""

    def __init__(self, node: Node, timeout: float = 5.0) -> None:
        if not node.agent_url:
            raise NodeClientError("node has no agent_url")
        self.node = node
        self.base_url = node.agent_url.rstrip("/")
        self.timeout = timeout

    def _headers(self, method: str, path: str, body: bytes) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        secret = self.node.agent_secret
        if secret:
            headers.update(hmac_auth.build_headers(secret, method=method, path=path, body=body))
        return headers

    def request(self, method: str, path: str, payload: dict | None = None) -> AgentResult:
        url = self.base_url + path
        body = json.dumps(payload).encode("utf-8") if payload is not None else b""
        headers = self._headers(method, _path_of(url), body)
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.request(method, url, content=body, headers=headers)
        except httpx.HTTPError as exc:
            raise NodeClientError(f"agent unreachable: {exc}") from exc
        data: dict = {}
        try:
            if response.content:
                data = response.json()
        except ValueError:
            data = {}
        if response.status_code >= 400:
            return AgentResult(
                ok=False,
                status_code=response.status_code,
                data=data,
                error=str(data.get("detail") or f"HTTP {response.status_code}"),
            )
        return AgentResult(ok=True, status_code=response.status_code, data=data)

    # Convenience wrappers ------------------------------------------------- #
    def health(self) -> AgentResult:
        return self.request("GET", "/health")

    def metrics(self) -> AgentResult:
        return self.request("GET", "/metrics")

    def status(self) -> AgentResult:
        return self.request("GET", "/status")

    def create_client(self, common_name: str) -> AgentResult:
        return self.request("POST", "/clients", {"common_name": common_name})

    def delete_client(self, common_name: str) -> AgentResult:
        return self.request("DELETE", f"/clients/{common_name}")

    def kill(self, common_name: str) -> AgentResult:
        return self.request("POST", "/kill", {"common_name": common_name})

    def reload(self) -> AgentResult:
        return self.request("POST", "/reload")

    def sync_user(self, user: dict) -> AgentResult:
        return self.request("POST", "/users", user)

    def delete_user(self, username: str) -> AgentResult:
        return self.request("DELETE", f"/users/{username}")

    def write_config(self, content: str, name: str | None = None) -> AgentResult:
        return self.request("POST", "/config", {"content": content, "name": name})

    # Protocol management -------------------------------------------------- #
    def exec_command(self, argv: list[str], timeout: float = 60.0) -> AgentResult:
        return self.request("POST", "/protocol/exec", {"argv": list(argv), "timeout": int(timeout)})

    def write_file(self, path: str, content: str, mode: int = 0o600) -> AgentResult:
        return self.request(
            "POST", "/protocol/file", {"path": path, "content": content, "mode": int(mode)}
        )

    def read_file(self, path: str) -> AgentResult:
        return self.request("GET", f"/protocol/file?path={path}")

    def upload_file(self, path: str, data: bytes, mode: int = 0o600) -> AgentResult:
        import base64

        return self.request(
            "POST",
            "/protocol/file",
            {
                "path": path,
                "content_b64": base64.b64encode(data).decode("ascii"),
                "mode": int(mode),
            },
        )

    def unit_action(self, unit: str, verb: str) -> AgentResult:
        return self.request("POST", "/protocol/unit", {"unit": unit, "verb": verb})
