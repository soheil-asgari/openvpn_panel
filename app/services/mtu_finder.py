"""Path-MTU discovery using DF-bit pings.

Wraps the platform ``ping`` command's *don't fragment* mode to binary-search the
largest packet that traverses the path without fragmentation, then derives the
recommended OpenVPN ``tun-mtu`` and ``mssfix`` values.

Runs the ping against the **entry host** through the panel's SSH transport when
one is supplied (so the probe reflects the tunnel's own path), or locally with
``subprocess`` otherwise. Requires no shell string assembly.
"""

from __future__ import annotations

import platform
import subprocess
from dataclasses import dataclass

from app.core.validators import validate_hostname
from app.services.tunnel_ssh import SSHTransport, quote

# IPv4 + ICMP headers subtracted from the MTU to get the ping payload size.
IP_ICMP_OVERHEAD = 28
# TCP header (20) + IP header (20) subtracted to get a conservative mssfix.
MSS_OVERHEAD = 40

DEFAULT_TARGET = "1.1.1.1"


@dataclass(slots=True)
class MtuResult:
    ok: bool
    mtu: int | None = None
    mssfix: int | None = None
    target: str = DEFAULT_TARGET
    detail: str = ""


def _ping_argv(target: str, payload: int, timeout: int) -> list[str]:
    system = platform.system().lower()
    if system == "windows":
        return ["ping", "-n", "1", "-f", "-l", str(payload), "-w", str(timeout * 1000), target]
    if system == "darwin":
        return ["ping", "-c", "1", "-D", "-s", str(payload), target]
    # Linux/BSD: -M do sets the DF bit.
    return ["ping", "-c", "1", "-M", "do", "-s", str(payload), "-W", str(timeout), target]


def _ping_remote_command(target: str, payload: int, timeout: int) -> str:
    """A POSIX-shell command that pings with DF using the remote's ping."""

    # Prefer the Linux form; fall back to busybox/BSD style on failure.
    return (
        f"ping -c 1 -M do -s {int(payload)} -W {int(timeout)} {quote(target)} >/dev/null 2>&1 "
        f"|| ping -c 1 -D -s {int(payload)} {quote(target)} >/dev/null 2>&1"
    )


def _works_local(target: str, payload: int, timeout: int) -> bool:
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, shell disabled
            _ping_argv(target, payload, timeout),
            capture_output=True,
            timeout=timeout + 3,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def _works_remote(ssh: SSHTransport, target: str, payload: int, timeout: int) -> bool:
    try:
        result = ssh.run(_ping_remote_command(target, payload, timeout), timeout=timeout + 3)
    except Exception:  # noqa: BLE001 - treat any ssh error as "no answer"
        return False
    return result.ok


def find_mtu(
    target: str = DEFAULT_TARGET,
    *,
    ssh: SSHTransport | None = None,
    high: int = 1500,
    low: int = 1200,
    timeout: int = 2,
) -> MtuResult:
    """Binary-search the largest non-fragmenting MTU between ``low`` and ``high``."""

    try:
        validate_hostname(target)
    except Exception as exc:  # noqa: BLE001
        return MtuResult(ok=False, target=target, detail=str(exc))

    def works(mtu: int) -> bool:
        payload = mtu - IP_ICMP_OVERHEAD
        if payload <= 0:
            return False
        if ssh is not None:
            return _works_remote(ssh, target, payload, timeout)
        return _works_local(target, payload, timeout)

    if not works(low):
        return MtuResult(
            ok=False,
            target=target,
            detail=f"no reply even at the minimum MTU ({low}); ICMP may be blocked",
        )
    if works(high):
        best = high
    else:
        lo, hi, best = low, high, low
        while lo <= hi:
            mid = (lo + hi) // 2
            if works(mid):
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1
    mssfix = best - MSS_OVERHEAD
    return MtuResult(ok=True, mtu=best, mssfix=mssfix, target=target, detail="ok")


def recommend_openvpn(mtu: int) -> dict[str, int]:
    """Derive OpenVPN knobs from a discovered path MTU."""

    return {"tun_mtu": mtu, "mssfix": max(0, mtu - MSS_OVERHEAD)}
