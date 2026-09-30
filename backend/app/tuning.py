"""Linux tuning: diagnose, recommend, apply only validated settings, keep a rollback.

Recommendations come from what this machine has (CPUs, RAM, NIC queues, observed
softnet drops/squeezes), not from a 100G template. Only runtime-safe settings are
applied (sysctls that are never lowered, RPS masks); anything that resets a link or
needs the hypervisor (NIC channels, ring sizes, multiqueue) is shown as a manual step.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

SYSCTL_FILE = Path("/etc/sysctl.d/90-bng-tuning.conf")
STATE = Path("/var/lib/bng-platform/tuning")
SKIP_NIC = re.compile(r"^(lo|ppp\d+|veth.*|bnglab\d|bngsink\d|docker\d*|br-.*|virbr\d*)$")
CONNTRACK_BYTES = 320  # approx. kernel memory per conntrack entry


@dataclass
class Rec:
    key: str        # sysctl name, "rps:<nic>/<queue>", or a label for manual items
    current: str
    recommended: str
    reason: str
    apply: bool     # False = shown only (manual / needs a maintenance window)


def _read(p: Path) -> str | None:
    try:
        return p.read_text().strip()
    except OSError:
        return None


def _run(*cmd: str) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def sysctl_path(key: str, root: Path) -> Path:
    return root / "proc/sys" / key.replace(".", "/")


def _ethtool_pairs(text: str, section: str) -> dict[str, str]:
    """'Pre-set maximums:' / 'Current hardware settings:' blocks -> {field: value}."""
    out, cur = {}, None
    for line in text.splitlines():
        if line.rstrip().endswith(":") and not line.startswith(("\t", " ")):
            cur = line.strip().rstrip(":")
            continue
        if cur and cur.lower().startswith(section) and ":" in line:
            k, v = line.split(":", 1)
            out[k.strip().lower()] = v.strip()
    return out


def parse_softnet(text: str) -> list[dict]:
    rows = []
    for cpu, line in enumerate(text.splitlines()):
        f = [int(x, 16) for x in line.split()]
        rows.append({"cpu": cpu, "processed": f[0], "dropped": f[1], "time_squeeze": f[2]})
    return rows


def parse_softirqs(text: str, names=("NET_RX", "NET_TX")) -> dict[str, list[int]]:
    return {p[0].rstrip(":"): list(map(int, p[1:])) for p in (l.split() for l in text.splitlines()[1:])
            if p and p[0].rstrip(":") in names}


def parse_interrupts(text: str, nics: list[str]) -> list[dict]:
    """IRQ lines that belong to a NIC (virtio input/output queues, mlx5_comp, ens18-TxRx-0...)."""
    lines = text.splitlines()
    ncpu = len(lines[0].split())
    out = []
    for l in lines[1:]:
        p = l.split()
        if not p or not p[0].rstrip(":").isdigit():
            continue
        name = " ".join(p[1 + ncpu:])
        if any(n in name for n in nics) or re.search(r"virtio\d+-(input|output)", name):
            out.append({"irq": int(p[0].rstrip(":")), "name": p[-1], "per_cpu": list(map(int, p[1:1 + ncpu]))})
    return out


def diagnostics(root: Path = Path("/")) -> dict:
    proc, sysfs = root / "proc", root / "sys"
    cpus = len(parse_softnet(_read(proc / "net/softnet_stat") or ""))
    mem_kb = int(re.search(r"MemTotal:\s+(\d+)", _read(proc / "meminfo") or "MemTotal: 0").group(1))
    nics = []
    for d in sorted((sysfs / "class/net").glob("*")):
        if SKIP_NIC.match(d.name) or not (d / "device").exists():  # vlan/bridge/tunnel: the parent has the queues
            continue
        q = d / "queues"
        rx = sorted(q.glob("rx-*"))
        nics.append({
            "name": d.name,
            "driver": Path(_read_link(d / "device/driver")).name,
            "rx_queues": len(rx), "tx_queues": len(list(q.glob("tx-*"))),
            "rps_cpus": {r.name: _read(r / "rps_cpus") for r in rx},
            "channels": {"max": _ethtool_pairs(out := _run("ethtool", "-l", d.name), "pre-set"),
                         "current": _ethtool_pairs(out, "current")},
            "rings": {"max": _ethtool_pairs(out := _run("ethtool", "-g", d.name), "pre-set"),
                      "current": _ethtool_pairs(out, "current")},
            "offload": {k: v for k, v in (l.strip().split(": ", 1) for l in _run("ethtool", "-k", d.name).splitlines()
                                          if ": " in l) if k in ("generic-receive-offload", "generic-segmentation-offload",
                                                                 "tcp-segmentation-offload", "rx-checksumming")},
        })
    gov = _read(sysfs / "devices/system/cpu/cpu0/cpufreq/scaling_governor")
    sysctls = {k: _read(sysctl_path(k, root)) for k in (
        "net.core.netdev_max_backlog", "net.core.netdev_budget", "net.core.netdev_budget_usecs",
        "net.core.rmem_max", "net.core.wmem_max", "net.netfilter.nf_conntrack_max",
        "net.netfilter.nf_conntrack_buckets", "net.netfilter.nf_conntrack_count", "fs.file-max",
        "net.ipv4.ip_forward")}
    return {
        "cpus": cpus, "mem_mb": mem_kb // 1024, "governor": gov, "nics": nics, "sysctl": sysctls,
        "irqbalance": _run("systemctl", "is-active", "irqbalance").strip() or None,
        "softnet": parse_softnet(_read(proc / "net/softnet_stat") or ""),
        "softirqs": parse_softirqs(_read(proc / "softirqs") or ""),
        "irqs": parse_interrupts(_read(proc / "interrupts") or "CPU0\n", [n["name"] for n in nics]),
    }


def _read_link(p: Path) -> str:
    try:
        return str(p.readlink())
    except OSError:
        return ""


def _pow2_floor(n: int) -> int:
    return 1 << max(n.bit_length() - 1, 0)


def recommend(d: dict) -> list[Rec]:
    recs: list[Rec] = []
    s, cpus = d["sysctl"], max(d["cpus"], 1)
    drops = sum(r["dropped"] for r in d["softnet"])
    squeeze = sum(r["time_squeeze"] for r in d["softnet"])

    def raise_to(key: str, want: int, reason: str) -> None:
        cur = s.get(key)
        if cur is not None and cur.isdigit() and int(cur) < want:  # never lower a value
            recs.append(Rec(key, cur, str(want), reason, True))

    raise_to("net.core.netdev_max_backlog", 4096 if drops == 0 else 16384,
             f"per-CPU input backlog; softnet drops so far: {drops}")
    if squeeze:
        raise_to("net.core.netdev_budget", 600, f"softirq ran out of budget {squeeze} times (time_squeeze)")
    raise_to("net.core.rmem_max", 4 << 20, "lets PPPoE discovery / packet sockets absorb PADI bursts")
    raise_to("net.core.wmem_max", 4 << 20, "same, transmit side")
    ct = min(_pow2_floor(d["mem_mb"] * 1024 * 1024 // 32 // CONNTRACK_BYTES), 4 << 20)  # <= 1/32 of RAM
    raise_to("net.netfilter.nf_conntrack_max", ct, f"CGNAT flows; {ct} entries use <= 1/32 of {d['mem_mb']} MB RAM")
    raise_to("net.netfilter.nf_conntrack_buckets", ct // 4, "hash buckets = conntrack_max / 4")

    full = format((1 << cpus) - 1, "x")
    for n in d["nics"]:
        if n["rx_queues"] < cpus:
            for q, mask in n["rps_cpus"].items():
                if mask is not None and int(mask.replace(",", ""), 16) == 0:
                    recs.append(Rec(f"rps:{n['name']}/{q}", mask, full,
                                    f"{n['rx_queues']} RX queue(s) for {cpus} CPUs: spread receive processing (RPS)", True))
        cmax, ccur = n["channels"]["max"].get("combined"), n["channels"]["current"].get("combined")
        if cmax and ccur and cmax.isdigit() and ccur.isdigit():
            if int(cmax) > int(ccur):
                recs.append(Rec(f"channels:{n['name']}", ccur, cmax,
                                f"ethtool -L {n['name']} combined {cmax} (resets the link: maintenance window)", False))
            elif int(cmax) == 1 and n["driver"] == "virtio_net":
                recs.append(Rec(f"multiqueue:{n['name']}", "1", str(min(cpus, 16)),
                                "virtio NIC has one queue: enable multiqueue in Proxmox (queues=N), then ethtool -L", False))
        for k in ("rx", "tx"):
            rmax, rcur = n["rings"]["max"].get(k), n["rings"]["current"].get(k)
            if rmax and rcur and rmax.isdigit() and rcur.isdigit() and int(rmax) > int(rcur):
                recs.append(Rec(f"ring-{k}:{n['name']}", rcur, rmax,
                                f"ethtool -G {n['name']} {k} {rmax} (resets the link: maintenance window)", False))
    if d["governor"] and d["governor"] != "performance":
        recs.append(Rec("cpu-governor", d["governor"], "performance", "set via cpupower; frequency scaling adds latency", False))
    busiest = {max(range(len(i["per_cpu"])), key=i["per_cpu"].__getitem__) for i in d["irqs"] if i["per_cpu"]}
    if d["irqbalance"] != "active" and len(d["irqs"]) > 2 and len(busiest) == 1:  # several queues, one CPU
        recs.append(Rec("irqbalance", d["irqbalance"] or "absent", "active or manual smp_affinity",
                        "NIC IRQs are not being distributed", False))
    return recs


def _path(key: str, root: Path) -> Path:
    if key.startswith("rps:"):
        nic, q = key[4:].split("/")
        return root / "sys/class/net" / nic / "queues" / q / "rps_cpus"
    return sysctl_path(key, root)


def _norm(key: str, v: str) -> str:
    return str(int(v.replace(",", ""), 16)) if key.startswith("rps:") else v.strip()  # rps_cpus reads back as 00000fff


def _write(key: str, value: str, root: Path) -> None:
    p = _path(key, root)
    if not p.is_file():
        raise FileNotFoundError(f"{key}: not present on this kernel/NIC")
    p.write_text(value + "\n")
    got = _read(p)
    if got is None or _norm(key, got) != _norm(key, value):
        raise RuntimeError(f"{key}: wrote {value}, kernel reports {got}")


def apply(recs: list[Rec], root: Path = Path("/"), state: Path = STATE, sysctl_file: Path = SYSCTL_FILE) -> list[Rec]:
    """Apply the applicable recs; all-or-nothing. The first apply records the original values for rollback."""
    todo = [r for r in recs if r.apply]
    state.mkdir(parents=True, exist_ok=True)
    rb_file, applied_file = state / "rollback.json", state / "applied.json"
    rollback = json.loads(rb_file.read_text()) if rb_file.exists() else {}
    done: list[Rec] = []
    try:
        for r in todo:
            rollback.setdefault(r.key, r.current)
            _write(r.key, r.recommended, root)
            done.append(r)
    except (OSError, RuntimeError):
        for r in reversed(done):
            _write(r.key, r.current, root)
        raise
    applied = json.loads(applied_file.read_text()) if applied_file.exists() else {}
    applied.update({r.key: r.recommended for r in done})
    rb_file.write_text(json.dumps(rollback, indent=1))
    applied_file.write_text(json.dumps(applied, indent=1))
    sysctl_file.parent.mkdir(parents=True, exist_ok=True)
    sysctl_file.write_text("# written by bngctl tuning apply; undo with bngctl tuning rollback\n" +
                           "".join(f"{k} = {v}\n" for k, v in sorted(applied.items()) if not k.startswith("rps:")))
    return done


def reapply(root: Path = Path("/"), state: Path = STATE) -> int:
    """At boot: RPS masks are not persistent (sysctls are, via sysctl.d)."""
    f = state / "applied.json"
    applied = json.loads(f.read_text()) if f.exists() else {}
    n = 0
    for k, v in applied.items():
        try:
            _write(k, v, root)
            n += 1
        except (OSError, RuntimeError):
            pass  # a NIC renamed/removed since: skip, the rest still applies
    return n


def rollback(root: Path = Path("/"), state: Path = STATE, sysctl_file: Path = SYSCTL_FILE) -> dict:
    rb_file = state / "rollback.json"
    if not rb_file.exists():
        return {}
    old = json.loads(rb_file.read_text())
    for k, v in old.items():
        try:
            _write(k, v, root)
        except (OSError, RuntimeError):
            pass
    sysctl_file.unlink(missing_ok=True)
    rb_file.unlink()
    (state / "applied.json").unlink(missing_ok=True)
    return old


def report(root: Path = Path("/")) -> dict:
    d = diagnostics(root)
    f = STATE / "applied.json"
    return {"diagnostics": d, "recommendations": [asdict(r) for r in recommend(d)],
            "applied": json.loads(f.read_text()) if f.exists() else {}}
