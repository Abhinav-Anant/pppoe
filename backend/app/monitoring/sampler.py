"""Rates from counter deltas, for the live dashboard.

Every rate is (counter now - counter before) / elapsed between two reads, so the
first read of anything yields None, not a guess. Only the latest sample is kept
(plus a 60 s churn window); history belongs in Prometheus/Zabbix.
"""
from __future__ import annotations

import time
from collections import deque
from pathlib import Path


def _rate(now: int | None, before: int | None, dt: float) -> float | None:
    if now is None or before is None or dt <= 0 or now < before:  # counter reset -> no rate
        return None
    return (now - before) / dt


def mbps(now, before, dt) -> float | None:
    r = _rate(now, before, dt)
    return round(r * 8 / 1e6, 3) if r is not None else None


class SessionRates:
    """Adds download_mbps/upload_mbps and their peaks to session rows in place;
    counts sessions that appeared/disappeared (churn) over the last minute."""

    def __init__(self):
        self.prev: dict[str, tuple[float, int | None, int | None]] = {}
        self.peak: dict[str, tuple[float, float]] = {}
        self.churn: deque[tuple[float, int, int]] = deque()

    def update(self, rows: list[dict], t: float | None = None) -> None:
        t = time.monotonic() if t is None else t
        cur = {}
        for r in rows:
            sid = r["sid"]
            ts, up, down = self.prev.get(sid, (t, None, None))
            r["upload_mbps"] = mbps(r["upload_bytes"], up, t - ts)
            r["download_mbps"] = mbps(r["download_bytes"], down, t - ts)
            pd, pu = self.peak.get(sid, (0.0, 0.0))
            pd, pu = max(pd, r["download_mbps"] or 0.0), max(pu, r["upload_mbps"] or 0.0)
            self.peak[sid] = (pd, pu)
            r["peak_download_mbps"], r["peak_upload_mbps"] = pd, pu
            cur[sid] = (t, r["upload_bytes"], r["download_bytes"])
        if self.prev:  # the first read is a baseline, not a burst of logins
            new, gone = len(cur.keys() - self.prev.keys()), len(self.prev.keys() - cur.keys())
            self.churn.append((t, new, gone))
        for sid in self.prev.keys() - cur.keys():
            self.peak.pop(sid, None)
        self.prev = cur
        while self.churn and t - self.churn[0][0] > 60:
            self.churn.popleft()

    def per_minute(self) -> dict:
        return {"logins_per_min": sum(c[1] for c in self.churn), "logouts_per_min": sum(c[2] for c in self.churn)}


def read_cpu(stat: str) -> dict[str, int]:
    """First line of /proc/stat -> jiffies per state."""
    names = ("user", "nice", "system", "idle", "iowait", "irq", "softirq", "steal")
    return dict(zip(names, map(int, stat.splitlines()[0].split()[1:9])))


class HostRates:
    def __init__(self, proc: Path = Path("/proc")):
        self.proc = proc
        self.prev: tuple[float, dict, dict] | None = None

    def update(self, nics: list[dict], t: float | None = None) -> dict:
        t = time.monotonic() if t is None else t
        try:
            cpu = read_cpu((self.proc / "stat").read_text())
        except (OSError, ValueError, IndexError):
            cpu = {}
        nic = {n["name"]: n for n in nics}
        out: dict = {"cpu_percent": None, "softirq_percent": None, "nics": {}}
        if self.prev:
            t0, cpu0, nic0 = self.prev
            dt = t - t0
            total = sum(cpu.values()) - sum(cpu0.values())
            if cpu and cpu0 and total > 0:
                out["cpu_percent"] = round(100 * (1 - (cpu["idle"] + cpu["iowait"] - cpu0["idle"] - cpu0["iowait"]) / total), 1)
                out["softirq_percent"] = round(100 * (cpu["softirq"] - cpu0["softirq"]) / total, 1)
            for name, n in nic.items():
                b = nic0.get(name)
                if not b:
                    continue
                out["nics"][name] = {
                    "rx_mbps": mbps(n["rx_bytes"], b["rx_bytes"], dt), "tx_mbps": mbps(n["tx_bytes"], b["tx_bytes"], dt),
                    "rx_pps": _round(_rate(n["rx_packets"], b["rx_packets"], dt)),
                    "tx_pps": _round(_rate(n["tx_packets"], b["tx_packets"], dt)),
                    "errors": n["rx_errors"] + n["tx_errors"], "drops": n["rx_dropped"] + n["tx_dropped"],
                    "state": n["state"], "speed_mbps": n["speed_mbps"],
                }
        self.prev = (t, cpu, nic)
        return out


def _round(x: float | None) -> float | None:
    return round(x, 1) if x is not None else None
