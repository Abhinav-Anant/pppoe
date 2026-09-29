"""NIC inventory from sysfs; no interface names are hard-coded."""
from __future__ import annotations

from pathlib import Path

COUNTERS = ("rx_bytes", "tx_bytes", "rx_packets", "tx_packets", "rx_errors", "tx_errors", "rx_dropped", "tx_dropped")


def _read(p: Path) -> str:
    try:
        return p.read_text().strip()
    except OSError:  # e.g. speed on virtio returns EINVAL
        return ""


def _int(s: str) -> int:
    return int(s) if s.lstrip("-").isdigit() else 0


def list_interfaces(root: Path = Path("/sys/class/net")) -> list[dict]:
    nics = []
    for d in sorted(root.iterdir()):
        speed = _int(_read(d / "speed"))
        nics.append({
            "name": d.name,
            "mac": _read(d / "address"),
            "state": _read(d / "operstate"),
            "mtu": _int(_read(d / "mtu")),
            "speed_mbps": speed if speed > 0 else None,
            "duplex": _read(d / "duplex") or None,
            "rx_queues": len(list((d / "queues").glob("rx-*"))),
            "tx_queues": len(list((d / "queues").glob("tx-*"))),
            **{k: _int(_read(d / "statistics" / k)) for k in COUNTERS},
        })
    return nics
