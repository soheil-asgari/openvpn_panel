"""Connectivity/latency/loss/throughput benchmarking for tunnel "Auto" mode.

The wizard's *Auto* option probes each candidate ``backend``/``transport`` and
recommends the best one. Results are advisory only; the admin may always
override the recommendation in the UI.

Probing is deliberately dependency-light:

* latency/loss via repeated ICMP echo (``ping``) to the entry host;
* throughput via a short HTTP download (``curl``) of a fixed-size object,
  capped by ``max_seconds`` so a slow link cannot stall the request.

When an :class:`~app.services.tunnel_ssh.SSHTransport` is supplied the ICMP probe
runs on the remote entry host (so it reflects the tunnel's own path).
"""

from __future__ import annotations

import platform
import re
import subprocess
from dataclasses import dataclass, field

from app.core.validators import validate_hostname
from app.services.tunnel_ssh import SSHTransport, quote

DEFAULT_PROBE_COUNT = 5
THROUGHPUT_URL = "https://speed.cloudflare.com/__down?bytes=2000000"  # 2 MB


@dataclass(slots=True)
class ProbeResult:
    transport: str
    backend: str
    latency_ms: float = 0.0
    packet_loss: float = 0.0
    throughput_kbps: float = 0.0
    reachable: bool = False
    score: float = 0.0
    detail: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "backend": self.backend,
            "transport": self.transport,
            "latency_ms": round(self.latency_ms, 2),
            "packet_loss": round(self.packet_loss, 2),
            "throughput_kbps": round(self.throughput_kbps, 1),
            "reachable": self.reachable,
            "score": round(self.score, 3),
            "detail": self.detail,
        }


@dataclass(slots=True)
class BenchmarkReport:
    target: str
    results: list[ProbeResult] = field(default_factory=list)
    recommended: ProbeResult | None = None
    reason: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "target": self.target,
            "results": [r.as_dict() for r in self.results],
            "recommended": self.recommended.as_dict() if self.recommended else None,
            "reason": self.reason,
        }


# --------------------------------------------------------------------------- #
# Low-level probes
# --------------------------------------------------------------------------- #
def _ping_argv(target: str, count: int) -> list[str]:
    system = platform.system().lower()
    if system == "windows":
        return ["ping", "-n", str(count), target]
    return ["ping", "-c", str(count), "-W", "2", target]


_LOSS_RE = re.compile(r"(\d+(?:\.\d+)?)%\s*(?:packet\s*)?loss", re.IGNORECASE)
_TIME_RE = re.compile(r"time[=<]\s*([\d.]+)\s*ms", re.IGNORECASE)


def _parse_ping(text: str, count: int) -> tuple[float, float]:
    """Return ``(avg_latency_ms, loss_percent)`` parsed from ping output."""

    loss_match = _LOSS_RE.search(text)
    loss = float(loss_match.group(1)) if loss_match else 100.0
    times = [float(m.group(1)) for m in _TIME_RE.finditer(text)]
    latency = sum(times) / len(times) if times else 0.0
    if not times and loss == 0.0:
        loss = 100.0
    return latency, loss


def probe_icmp(
    target: str, *, ssh: SSHTransport | None = None, count: int = DEFAULT_PROBE_COUNT
) -> tuple[float, float, bool]:
    """Return ``(latency_ms, loss_percent, reachable)``."""

    validate_hostname(target)
    if ssh is not None:
        command = f"ping -c {int(count)} -W 2 {quote(target)} 2>&1 || true"
        try:
            out = ssh.run(command, timeout=count * 3 + 5).stdout
        except Exception:  # noqa: BLE001
            return 0.0, 100.0, False
    else:
        try:
            proc = subprocess.run(  # noqa: S603 - fixed argv, shell disabled
                _ping_argv(target, count),
                capture_output=True,
                text=True,
                timeout=count * 3 + 5,
                check=False,
            )
            out = proc.stdout + proc.stderr
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return 0.0, 100.0, False
    latency, loss = _parse_ping(out, count)
    return latency, loss, loss < 100.0


def probe_throughput(
    url: str = THROUGHPUT_URL,
    *,
    ssh: SSHTransport | None = None,
    max_seconds: int = 8,
) -> float:
    """Return download throughput in kbit/s (0.0 when it cannot be measured)."""

    if ssh is not None:
        command = (
            f"curl -fsSL --max-time {int(max_seconds)} -o /dev/null "
            f"-w '%{{speed_download}}' {quote(url)} 2>/dev/null || true"
        )
        try:
            raw = ssh.run(command, timeout=max_seconds + 5).stdout.strip()
        except Exception:  # noqa: BLE001
            return 0.0
    else:
        try:
            proc = subprocess.run(  # noqa: S603
                [
                    "curl",
                    "-fsSL",
                    "--max-time",
                    str(int(max_seconds)),
                    "-o",
                    "/dev/null",
                    "-w",
                    "%{speed_download}",
                    url,
                ],
                capture_output=True,
                text=True,
                timeout=max_seconds + 5,
                check=False,
            )
            raw = proc.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return 0.0
    try:
        # curl reports bytes/second.
        return float(raw) * 8 / 1000.0
    except ValueError:
        return 0.0


# --------------------------------------------------------------------------- #
# Scoring / benchmarking
# --------------------------------------------------------------------------- #
def score(*, latency_ms: float, packet_loss: float, throughput_kbps: float) -> float:
    """A monotonic score where higher is better (used only to rank results)."""

    if packet_loss >= 100:
        return 0.0
    latency_term = 1.0 / (1.0 + max(0.0, latency_ms))
    quality = max(0.0, 1.0 - packet_loss / 100.0)
    throughput_term = min(1.0, throughput_kbps / 100_000.0)  # 100 Mbit/s caps at 1.0
    return round(latency_term * quality * 0.4 + quality * 0.3 + throughput_term * 0.3, 4)


def benchmark_candidates(
    target: str,
    candidates: list[tuple[str, str]],
    *,
    ssh: SSHTransport | None = None,
    count: int = DEFAULT_PROBE_COUNT,
    measure_throughput: bool = True,
) -> BenchmarkReport:
    """Benchmark each ``(backend, transport)`` candidate and pick the best."""

    report = BenchmarkReport(target=target)
    latency, loss, reachable = probe_icmp(target, ssh=ssh, count=count)
    throughput = probe_throughput(ssh=ssh) if (reachable and measure_throughput) else 0.0

    for backend, transport in candidates:
        result = ProbeResult(
            backend=backend,
            transport=transport,
            latency_ms=latency,
            packet_loss=loss,
            throughput_kbps=throughput,
            reachable=reachable,
            detail="icmp+http probe" if reachable else "unreachable",
        )
        result.score = score(latency_ms=latency, packet_loss=loss, throughput_kbps=throughput)
        report.results.append(result)

    if not reachable:
        report.reason = "entry host did not answer ICMP; defaulting to the first candidate"
        report.recommended = report.results[0] if report.results else None
        return report
    # Prefer the lowest loss, then the highest score.
    report.recommended = max(report.results, key=lambda r: r.score)
    report.reason = (
        f"best score {report.recommended.score} "
        f"(latency {report.recommended.latency_ms:.1f} ms, loss "
        f"{report.recommended.packet_loss:.1f}%)"
    )
    return report
