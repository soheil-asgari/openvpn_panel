"""Pydantic schemas for the REST API and forms."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.validators import validate_username


class VpnUserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=8, max_length=128)
    node_id: int | None = None
    expires_at: datetime | None = None
    expire_days: int | None = Field(default=None, ge=0)
    data_limit_bytes: int | None = Field(default=None, ge=0)
    max_connections: int | None = Field(default=None, ge=0)
    speed_limit_kbps: int | None = Field(default=None, ge=0)
    common_name: str | None = None

    @field_validator("username")
    @classmethod
    def _check_username(cls, value: str) -> str:
        return validate_username(value)


class VpnUserUpdate(BaseModel):
    password: str | None = Field(default=None, min_length=8, max_length=128)
    node_id: int | None = None
    expires_at: datetime | None = None
    expire_days: int | None = Field(default=None, ge=0)
    data_limit_bytes: int | None = Field(default=None, ge=0)
    max_connections: int | None = Field(default=None, ge=0)
    speed_limit_kbps: int | None = Field(default=None, ge=0)
    status: str | None = None


class VpnUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    status: str
    is_online: bool
    bytes_in: int
    bytes_out: int
    data_limit_bytes: int | None
    expires_at: datetime | None
    expire_days: int | None
    first_connected_at: datetime | None
    max_connections: int | None
    speed_limit_kbps: int | None
    node_id: int | None
    owner_id: int | None


class VpnUserBulkCreate(BaseModel):
    prefix: str = Field(min_length=1, max_length=28)
    count: int = Field(ge=1, le=1000)
    node_id: int | None = None
    expires_at: datetime | None = None
    expire_days: int | None = Field(default=None, ge=0)
    data_limit_bytes: int | None = Field(default=None, ge=0)
    max_connections: int | None = Field(default=None, ge=0)
    speed_limit_kbps: int | None = Field(default=None, ge=0)
    password_length: int = Field(default=20, ge=8, le=64)

    @field_validator("prefix")
    @classmethod
    def _check_prefix(cls, value: str) -> str:
        # Validate as a username with a trailing digit appended (the generated
        # suffixes start at ``1``) but keep the original prefix value.
        validate_username(value + "1")
        return value


class VpnUserBulkIds(BaseModel):
    ids: list[int] = Field(min_length=1)


class VpnUserBulkExtend(VpnUserBulkIds):
    days: int | None = Field(default=None, ge=0)
    expires_at: datetime | None = None


class VpnUserCredentials(BaseModel):
    """A created user together with its one-time plaintext password."""

    id: int
    username: str
    password: str


class BulkResult(BaseModel):
    affected: int


class TunnelCreate(BaseModel):
    """Wizard payload for creating + provisioning a tunnel."""

    name: str = Field(min_length=2, max_length=32)
    entry_host: str
    entry_ssh_port: int = Field(default=22, ge=1, le=65535)
    entry_username: str = Field(default="root", min_length=1, max_length=64)
    auth_method: str = Field(default="password", pattern="^(password|key)$")
    password: str | None = Field(default=None, max_length=256)
    private_key: str | None = Field(default=None, max_length=16 * 1024)
    host_key_fingerprint: str | None = Field(default=None, max_length=128)
    backend: str = Field(default="auto", pattern="^(auto|backhaul|wireguard)$")
    transport: str = Field(default="auto", pattern="^(auto|tcp|tcpmux|udp|wg)$")
    exit_host: str | None = None
    control_port: int | None = Field(default=None, ge=1, le=65535)
    public_ports: list[int] = Field(default_factory=list)
    node_id: int | None = None
    instance_name: str | None = Field(default=None, max_length=32)
    mtu: int | None = Field(default=None, ge=576, le=9000)
    auto_heal: bool = True
    disable_password_login: bool = False
    probe_only: bool = False


class TunnelUpdate(BaseModel):
    backend: str | None = Field(default=None, pattern="^(auto|backhaul|wireguard)$")
    transport: str | None = Field(default=None, pattern="^(auto|tcp|tcpmux|udp|wg)$")
    control_port: int | None = Field(default=None, ge=1, le=65535)
    public_ports: list[int] | None = None
    token: str | None = Field(default=None, max_length=128)
    mtu: int | None = Field(default=None, ge=576, le=9000)
    instance_name: str | None = Field(default=None, max_length=32)
    node_id: int | None = None
    auto_heal: bool | None = None
    is_active: bool | None = None


class TunnelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    backend: str
    transport: str
    status: str
    entry_host: str
    entry_ssh_port: int
    entry_username: str
    exit_host: str | None
    control_port: int
    public_ports: list[int] = Field(default_factory=list)
    mtu: int | None
    mssfix: int | None
    backend_version: str | None
    node_id: int | None
    instance_name: str | None
    is_active: bool
    auto_heal: bool
    consecutive_failures: int
    last_error: str | None
    last_seen_at: datetime | None

    @field_validator("public_ports", mode="before")
    @classmethod
    def _split_ports(cls, value: object) -> list[int]:
        if isinstance(value, str):
            return [int(p) for p in value.replace(",", " ").split() if p.isdigit()]
        return value  # type: ignore[return-value]


class TunnelDetail(TunnelOut):
    host_key_fingerprint: str | None = None
    latest_job: dict | None = None


class TunnelProvisionResult(BaseModel):
    tunnel_id: int
    job_id: int
    status: str
    step: str | None = None
    progress: int = 0
    log: str = ""
    error: str | None = None


class TunnelBenchmarkOut(BaseModel):
    target: str
    results: list[dict] = Field(default_factory=list)
    recommended: dict | None = None
    reason: str = ""


class MtuOut(BaseModel):
    ok: bool
    mtu: int | None = None
    mssfix: int | None = None
    target: str
    detail: str = ""


class TunnelFingerprintOut(BaseModel):
    host: str
    fingerprint: str


class NodeCreate(BaseModel):
    name: str = Field(min_length=2, max_length=32)
    host: str
    port: int = Field(default=1194, ge=1, le=65535)
    protocol: str = "udp"
    cipher: str = "AES-256-GCM"
    subnet: str = "10.8.0.0/24"
    agent_url: str | None = None


class NodeUpdate(BaseModel):
    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    protocol: str | None = None
    cipher: str | None = None
    subnet: str | None = None
    is_active: bool | None = None


class NodeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    host: str
    port: int
    protocol: str
    cipher: str
    status: str
    is_active: bool


class NodeCreated(NodeOut):
    agent_token: str | None = None


class NodeInstallCommand(BaseModel):
    node_id: int
    command: str
    install_script_url: str


class NodeConfigIn(BaseModel):
    content: str = Field(max_length=512 * 1024)


class NodeConfigOut(BaseModel):
    node_id: int
    path: str | None = None
    content: str


class NodeMetricOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    cpu_percent: float
    load1: float
    mem_total: int
    mem_used: int
    disk_total: int
    disk_used: int
    net_rx: int
    net_tx: int
    client_count: int
    uptime_seconds: int
    recorded_at: datetime


class NodeTaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    node_id: int | None
    kind: str
    status: str
    attempts: int
    max_attempts: int
    last_error: str | None
    run_after: datetime


class NodeHealthOut(BaseModel):
    node_id: int
    name: str
    status: str
    last_seen_at: datetime | None
    metric: NodeMetricOut | None = None
    pending_tasks: int
    reachable: bool


class ResellerCreate(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=8, max_length=128)
    email: str | None = None
    parent_id: int | None = None
    quota_users: int | None = Field(default=None, ge=0)
    max_data_bytes: int | None = Field(default=None, ge=0)
    credit: int | None = Field(default=None, ge=0)
    expires_at: datetime | None = None
    node_ids: list[int] = Field(default_factory=list)

    @field_validator("username")
    @classmethod
    def _check_username(cls, value: str) -> str:
        return validate_username(value)


class ResellerUpdate(BaseModel):
    password: str | None = Field(default=None, min_length=8, max_length=128)
    email: str | None = None
    quota_users: int | None = Field(default=None, ge=0)
    max_data_bytes: int | None = Field(default=None, ge=0)
    credit: int | None = Field(default=None, ge=0)
    expires_at: datetime | None = None
    is_active: bool | None = None
    node_ids: list[int] | None = None


class ResellerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    email: str | None
    role: str
    is_active: bool
    parent_id: int | None
    quota_users: int | None
    max_data_bytes: int | None
    credit: int | None
    expires_at: datetime | None


class ResellerUsage(BaseModel):
    reseller_id: int
    username: str
    user_count: int
    data_bytes: int
    quota_users: int | None
    max_data_bytes: int | None
    users_remaining: int | None
    data_remaining: int | None
    allowed_node_ids: list[int]
    restricted: bool
    expired: bool


class ReportSeries(BaseModel):
    preset: str
    labels: list[str]
    bytes_in: list[int]
    bytes_out: list[int]
    total_bytes_in: int
    total_bytes_out: int
    users: int


class ApiKeyOut(BaseModel):
    id: int
    name: str | None
    prefix: str
    is_active: bool
    scopes: list[str] = Field(default_factory=list)
    expires_at: datetime | None = None


class ApiKeyCreated(ApiKeyOut):
    key: str


class ApiKeyCreate(BaseModel):
    name: str | None = None
    scopes: list[str] = Field(default_factory=list)
    expires_at: datetime | None = None


class TopUserOut(BaseModel):
    username: str
    used_bytes: int
    is_online: bool


class StatsOut(BaseModel):
    total_users: int
    online_users: int
    total_nodes: int
    total_bytes: int
    cpu_percent: float = 0.0
    mem_total: int = 0
    mem_used: int = 0
    disk_total: int = 0
    disk_used: int = 0
    net_rx: int = 0
    net_tx: int = 0
    top_users: list[TopUserOut] = Field(default_factory=list)


class VpnUserRenew(BaseModel):
    days: int | None = Field(default=None, ge=1)
    expires_at: datetime | None = None
    reset_traffic: bool = False


class SubscriptionInfo(BaseModel):
    """Public subscription link and visibility overrides for a VPN user."""

    vpn_user_id: int
    username: str
    token: str
    url: str
    visible_node_ids: list[int]
    hidden_node_ids: list[int]


class SubscriptionToken(BaseModel):
    token: str
    url: str


class SubscriptionVisibility(BaseModel):
    node_ids: list[int] = Field(default_factory=list, description="Node IDs to show")


class Message(BaseModel):
    detail: str


# --------------------------------------------------------------------------- #
# Protocols (Phase 12-15)
# --------------------------------------------------------------------------- #
class ProtocolInstanceCreate(BaseModel):
    kind: str = Field(min_length=2, max_length=32)
    name: str = Field(min_length=2, max_length=32)
    node_id: int | None = None
    host: str | None = None
    port: int = Field(default=0, ge=0, le=65535)
    address: str | None = None
    subnet: str | None = None
    dns: str | None = None
    mtu: int | None = Field(default=None, ge=576, le=9000)
    is_default: bool = False
    settings: dict = Field(default_factory=dict)


class ProtocolInstanceUpdate(BaseModel):
    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    address: str | None = None
    subnet: str | None = None
    dns: str | None = None
    mtu: int | None = Field(default=None, ge=576, le=9000)
    is_active: bool | None = None
    is_default: bool | None = None
    settings: dict | None = None


class ProtocolInstanceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    name: str
    node_id: int | None
    host: str | None
    port: int
    address: str | None
    subnet: str | None
    dns: str | None
    mtu: int | None
    status: str
    is_active: bool
    is_default: bool
    version: str | None
    last_error: str | None


class ProtocolInstanceDetail(ProtocolInstanceOut):
    settings: dict = Field(default_factory=dict)


class ProtocolHealthOut(BaseModel):
    instance_id: int
    ok: bool
    detail: str = ""
    version: str | None = None
    status: str


class ProtocolInstallOut(BaseModel):
    instance_id: int
    ok: bool
    version: str | None = None
    error: str | None = None
    log: list[str] = Field(default_factory=list)


class ProtocolUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    vpn_user_id: int
    protocol_instance_id: int
    kind: str
    enabled: bool
    bytes_in: int
    bytes_out: int
    is_online: bool
    credentials: dict = Field(default_factory=dict)


class VersionInfo(BaseModel):
    """Panel version, and the result of the opt-in update check.

    ``update.enabled`` is off by default; when off, ``latest`` is ``None`` and no
    network request is made. No telemetry is ever sent.
    """

    version: str
    update_check_enabled: bool = False
    latest: str | None = None
    update_available: bool = False
    url: str | None = None
    error: str | None = None


class ProtocolUserToggle(BaseModel):
    enabled: bool = True


class ProtocolUserConfigOut(BaseModel):
    kind: str
    text: str = ""
    uri: str = ""
    qr_payload: str = ""
    filename: str = ""


class SubscriptionLinks(BaseModel):
    vpn_user_id: int
    links: list[str] = Field(default_factory=list)
    aggregated: str = ""
    configs: list[ProtocolUserConfigOut] = Field(default_factory=list)
