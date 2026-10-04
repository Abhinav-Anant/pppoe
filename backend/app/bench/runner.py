"""Benchmark runner: N PPPoE sessions held while traffic runs through the BNG.

    bngctl benchmark run --sessions 10000 --traffic 1,5,10

Session scale: `loadgen` (userspace PPPoE clients) in the lab namespace.
Traffic: K real pppd sessions in the same namespace, iperf3 to a sink namespace
behind the BNG, through the per-session shaper, the forward filter and a SNAT rule
of the same shape as the CGNAT one (own table, own egress `bngsink0`, so benchmark
traffic never leaves the machine). Everything temporary is removed afterwards and
config.yaml is restored through the normal safe-apply path.

One JSON + the Markdown summary per (sessions, traffic) level. PASS only when the
thresholds below are met; the numbers are what this machine measured, nothing more.
"""
from __future__ import annotations

import ipaddress
import json
import math
import os
import platform
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

from app.accel.cmd import AccelCmd, parse_stat
from app.config.manager import ConfigManager, Paths

NS, ACCESS, PEER = "bnglab", "bnglab0", "bnglab1"
SINK_NS, SINK_HOST, SINK_PEER = "bngsink", "bngsink0", "bngsink1"
SINK_GW, SINK_IP = "198.18.0.1", "198.18.0.2"   # RFC 2544 benchmark range
BENCH_NET_BASE = ipaddress.ip_address("100.72.0.0")
CHAP = Path("/etc/bng-platform/lab/chap-secrets")
USER = "benchuser"
OUT = Path("/var/lib/bng-platform/benchmark-results")
THRESHOLDS = {"sessions_established_ratio": 1.0, "sessions_dropped_during_hold": 0,
              "delivered_ratio_min": 0.95, "udp_loss_percent_max": 0.5}
NOTES = [
    "Access side is a veth pair inside one VM; subscribers, traffic generators and the BNG share the same vCPUs.",
    "Virtual-NIC / veth results do not indicate what the software does on physical 10/25/40G NICs.",
    "Session scale uses a userspace PPPoE client (real PPPoE/LCP/auth/IPCP on the wire, no data); "
    "traffic uses real pppd sessions through the per-session shaper.",
    "AAA is the lab chap-secrets module (aaa: lab): no RADIUS round trip is included.",
    "This kernel has no CONFIG_IRQ_TIME_ACCOUNTING: forwarding work done in softirq from process context is "
    "charged to the sending process (iperf3/pppd), so generators_cpu_percent includes part of the BNG's "
    "forwarding cost and softirq_* undercounts it. accel_cpu_percent is accel-pppd alone.",
]


def sh(*cmd: str, check: bool = True, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, capture_output=True, text=True, **kw)


# --- metrics --------------------------------------------------------------------

def _cpu_lines() -> list[list[int]]:
    rows = []
    for l in Path("/proc/stat").read_text().splitlines()[1:]:
        if not l.startswith("cpu"):
            break
        rows.append(list(map(int, l.split()[1:9])))
    return rows


def _nic(name: str) -> dict[str, int]:
    base = Path("/sys/class/net") / name / "statistics"
    try:
        return {k: int((base / k).read_text()) for k in ("rx_packets", "tx_packets", "rx_bytes", "tx_bytes",
                                                          "rx_dropped", "tx_dropped")}
    except OSError:
        return {}


def _softnet() -> tuple[int, int]:
    rows = [[int(x, 16) for x in l.split()[:3]] for l in Path("/proc/net/softnet_stat").read_text().splitlines()]
    return sum(r[1] for r in rows), sum(r[2] for r in rows)


def _meminfo(key: str) -> int:
    return int(re.search(rf"{key}:\s+(\d+)", Path("/proc/meminfo").read_text()).group(1))


def _accel_rss_kb() -> int | None:
    pid = sh("pidof", "accel-pppd", check=False).stdout.split()
    try:
        return int(re.search(r"VmRSS:\s+(\d+)", Path(f"/proc/{pid[0]}/status").read_text()).group(1)) if pid else None
    except OSError:
        return None


def _conntrack() -> int:
    try:
        return int(Path("/proc/sys/net/netfilter/nf_conntrack_count").read_text())
    except OSError:
        return 0


HZ = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100


def _proc_jiffies() -> dict[str, int]:
    """utime+stime of accel-pppd vs the traffic/session generators (iperf3, pppd, loadgen)."""
    out = {"accel": 0, "generators": 0}
    for d in Path("/proc").glob("[0-9]*"):
        try:
            st = (d / "stat").read_text()
            comm, rest = st[st.index("(") + 1:st.rindex(")")], st[st.rindex(")") + 2:].split()
            j = int(rest[11]) + int(rest[12])
            if comm == "accel-pppd":
                out["accel"] += j
            elif comm in ("iperf3", "pppd") or (comm.startswith("python") and b"app.bench.loadgen" in (d / "cmdline").read_bytes()):
                out["generators"] += j
        except (OSError, ValueError, IndexError):
            continue
    return out


class Sampler(threading.Thread):
    """1 s samples of per-CPU busy/softirq, memory, conntrack, softnet and NIC counters."""

    def __init__(self, nics: list[str]):
        super().__init__(daemon=True)
        self.nics, self.samples, self.stop_evt = nics, [], threading.Event()

    def snap(self) -> dict:
        drops, squeeze = _softnet()
        return {"t": time.monotonic(), "cpu": _cpu_lines(), "mem_avail_kb": _meminfo("MemAvailable"),
                "conntrack": _conntrack(), "softnet_drops": drops, "time_squeeze": squeeze,
                "accel_rss_kb": _accel_rss_kb(), "nics": {n: _nic(n) for n in self.nics}, "procs": _proc_jiffies()}

    def run(self) -> None:
        while not self.stop_evt.is_set():
            self.samples.append(self.snap())
            self.stop_evt.wait(1.0)

    def window(self, t0: float, t1: float) -> dict:
        s = [x for x in self.samples if t0 <= x["t"] <= t1]
        if len(s) < 2:
            return {}
        a, b = s[0], s[-1]
        busy, soft = [], []
        for c0, c1 in zip(a["cpu"], b["cpu"]):
            tot = sum(c1) - sum(c0) or 1
            idle = (c1[3] + c1[4]) - (c0[3] + c0[4])
            busy.append(round(100 * (1 - idle / tot), 1))
            soft.append(round(100 * (c1[6] - c0[6]) / tot, 1))
        # peak per-core busy over 1 s intervals: shows a single saturated core the average hides
        peak = 0.0
        for x, y in zip(s, s[1:]):
            for c0, c1 in zip(x["cpu"], y["cpu"]):
                tot = sum(c1) - sum(c0) or 1
                peak = max(peak, 100 * (1 - ((c1[3] + c1[4]) - (c0[3] + c0[4])) / tot))
        dt = b["t"] - a["t"]
        nics = {}
        for n in self.nics:
            x, y = a["nics"].get(n), b["nics"].get(n)
            if x and y:
                nics[n] = {"rx_gbps": round((y["rx_bytes"] - x["rx_bytes"]) * 8 / dt / 1e9, 3),
                           "tx_gbps": round((y["tx_bytes"] - x["tx_bytes"]) * 8 / dt / 1e9, 3),
                           "rx_pps": round((y["rx_packets"] - x["rx_packets"]) / dt),
                           "tx_pps": round((y["tx_packets"] - x["tx_packets"]) / dt),
                           "drops": (y["rx_dropped"] + y["tx_dropped"]) - (x["rx_dropped"] + x["tx_dropped"])}
        return {"seconds": round(dt, 1), "cpu_avg_percent": round(sum(busy) / len(busy), 1),
                "cpu_per_core_percent": busy, "cpu_peak_core_percent_1s": round(peak, 1),
                "softirq_per_core_percent": soft, "softirq_avg_percent": round(sum(soft) / len(soft), 1),
                "mem_used_mb": round((s[0]["mem_avail_kb"] - min(x["mem_avail_kb"] for x in s)) / 1024, 1),
                "accel_rss_mb": round(max(x["accel_rss_kb"] or 0 for x in s) / 1024, 1),
                "conntrack_max": max(x["conntrack"] for x in s),
                "softnet_drops": b["softnet_drops"] - a["softnet_drops"],
                "time_squeeze": b["time_squeeze"] - a["time_squeeze"], "nics": nics,
                # share of the whole machine; see NOTES on softirq accounting
                **{f"{k}_cpu_percent": round(100 * (b["procs"][k] - a["procs"][k]) / (dt * HZ * len(busy)), 1)
                   for k in ("accel", "generators")}}


# --- environment ----------------------------------------------------------------

class Bench:
    def __init__(self, mgr: ConfigManager, paths: Paths, accel: AccelCmd, admin: str, rate: float = 300.0,
                 hold: int = 60, duration: int = 12, out: Path = OUT):
        self.mgr, self.paths, self.accel, self.admin = mgr, paths, accel, admin
        self.rate, self.hold, self.duration, self.out = rate, hold, duration, out
        self.cleanups: list = []
        self.tmp = Path(f"/run/bng-bench-{os.getpid()}")
        self.password = secrets.token_urlsafe(18)

    def log(self, msg: str) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

    def active(self) -> int:
        return int(parse_stat(self.accel.stat())["sessions"]["active"])

    def total(self) -> int:  # starting + active + finishing
        return sum(int(v) for v in parse_stat(self.accel.stat())["sessions"].values())

    # -- preconditions and setup --
    def preflight(self, sessions: int, traffic: list[float]) -> dict:
        cfg = yaml.safe_load(self.paths.config.read_text())
        if cfg.get("aaa") != "lab":
            raise RuntimeError("benchmark needs aaa: lab (the bench subscriber lives in the lab chap-secrets)")
        if ACCESS not in [i["name"] for i in cfg["pppoe"]["interfaces"]]:
            raise RuntimeError(f"{ACCESS} is not a PPPoE interface: run scripts/lab/lab-up.sh and add it")
        if NS not in sh("ip", "netns", "list").stdout.split():
            raise RuntimeError(f"network namespace {NS} missing: run scripts/lab/lab-up.sh")
        live = [s for s in self.accel.sessions() if s.get("inbound-if") != ACCESS]
        if live:  # the bench pool needs an accel-ppp restart (1.14.0 does not reload [ip-pool])
            raise RuntimeError(f"{len(live)} session(s) outside the lab: a benchmark restarts accel-ppp twice "
                               "and loads the whole box; run it only on a node without subscribers")
        if traffic and not (shutil.which("iperf3") and shutil.which("pppd")):
            raise RuntimeError("traffic tests need iperf3 and pppd")
        return cfg

    def bench_pool(self, cfg: dict, n: int) -> ipaddress.IPv4Network:
        prefix = 32 - math.ceil(math.log2(n + 256))
        net = ipaddress.ip_network(f"{BENCH_NET_BASE}/{prefix}")
        taken = [ipaddress.ip_network(p["network"]) for p in cfg["ip_pools"]["pools"]]
        if any(net.overlaps(t) for t in taken):
            raise RuntimeError(f"bench pool {net} overlaps an existing pool")
        return net

    def setup(self, cfg: dict, net: ipaddress.IPv4Network, rate_kbit: int | None) -> None:
        self.tmp.mkdir(mode=0o700, exist_ok=True)
        self.cleanups.append(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        (self.tmp / "pw").write_text(self.password)
        (self.tmp / "pppd-auth").write_text(f"user {USER}\npassword {self.password}\n")
        # bench subscriber first in chap-secrets: accel reads the file on every auth
        original = CHAP.read_text()
        rate = f" {rate_kbit}/{rate_kbit}" if rate_kbit else ""
        CHAP.write_text(f"{USER} * {self.password} *{rate}\n" + original)
        self.cleanups.append(lambda: CHAP.write_text(original))
        # temporary pool through the normal safe-apply path (reload, no restart)
        saved = self.tmp / "config.orig.yaml"
        saved.write_text(self.paths.config.read_text())
        cand = dict(cfg)
        cand["ip_pools"] = {**cfg["ip_pools"], "default": "bench",
                            "pools": cfg["ip_pools"]["pools"] + [{"name": "bench", "network": str(net)}]}
        if cand.get("nat"):  # the bench pool must sit inside a NAT subscribers network (config validation)
            first, *rest = cand["nat"]["pools"]
            cand["nat"] = {**cand["nat"], "pools": [{**first, "subscribers": [*first["subscribers"], str(net)]}, *rest]}
        (self.tmp / "config.bench.yaml").write_text(yaml.safe_dump(cand, sort_keys=False))
        base = self.active()
        self.log(self.mgr.apply(self.tmp / "config.bench.yaml", self.admin, "benchmark", allow_restart=True))
        time.sleep(5)  # accel-ppp answers the CLI before PPPoE discovery is ready; PADIs sent earlier are lost

        def undo():  # runs before the temp dir is removed (cleanups are LIFO)
            t0 = time.monotonic()
            while self.active() > base and time.monotonic() - t0 < 120:  # let bench sessions drain first
                time.sleep(1)
            self.log(self.mgr.apply(saved, self.admin, "benchmark", allow_restart=True))
        self.cleanups.append(undo)

    def sink(self, net: ipaddress.IPv4Network) -> None:
        sh("ip", "netns", "add", SINK_NS)
        self.cleanups.append(lambda: sh("ip", "netns", "del", SINK_NS, check=False))
        sh("ip", "link", "add", SINK_HOST, "type", "veth", "peer", "name", SINK_PEER, "netns", SINK_NS)
        self.cleanups.append(lambda: sh("ip", "link", "del", SINK_HOST, check=False))
        sh("ip", "addr", "add", f"{SINK_GW}/30", "dev", SINK_HOST)
        sh("ip", "link", "set", SINK_HOST, "up")
        for c in (("link", "set", "lo", "up"), ("link", "set", SINK_PEER, "up"),
                  ("addr", "add", f"{SINK_IP}/30", "dev", SINK_PEER), ("route", "add", "default", "via", SINK_GW)):
            sh("ip", "-n", SINK_NS, *c)
        nft = (f"table ip bng_bench {{\n chain postrouting {{\n  type nat hook postrouting priority srcnat; policy accept;\n"
               f'  oifname "{SINK_HOST}" ip saddr {net} meta l4proto {{ tcp, udp }} snat to {SINK_GW}:1024-65535 persistent\n'
               f'  oifname "{SINK_HOST}" ip saddr {net} snat to {SINK_GW}\n }}\n}}\n')
        sh("nft", "-f", "-", input=nft)
        self.cleanups.append(lambda: sh("nft", "delete", "table", "ip", "bng_bench", check=False))
        # appended (not inserted): the MSS clamp and ct-state rules still run first
        sh("nft", "add", "rule", "inet", "bng_filter", "forward", "iifname", "ppp*", "oifname", SINK_HOST,
           "ip", "saddr", str(net), "counter", "accept", "comment", '"bng-bench"')

        def drop_rule():
            out = sh("nft", "-a", "list", "chain", "inet", "bng_filter", "forward", check=False).stdout
            for h in re.findall(r'comment "bng-bench" # handle (\d+)', out):
                sh("nft", "delete", "rule", "inet", "bng_filter", "forward", "handle", h, check=False)
        self.cleanups.append(drop_rule)

    def cleanup(self) -> None:
        while self.cleanups:
            try:
                self.cleanups.pop()()
            except Exception as e:  # keep undoing the rest
                self.log(f"cleanup step failed: {e}")

    # -- sessions --
    def sessions(self, n: int, sampler: Sampler) -> tuple["Loadgen", dict]:
        base_active, mem0 = self.active(), _meminfo("MemAvailable")
        cmd = ["ip", "netns", "exec", NS, sys.executable, "-m", "app.bench.loadgen", "--iface", PEER,
               "--sessions", str(n), "--user", USER, "--password-file", str(self.tmp / "pw"), "--rate", str(self.rate),
               # in-flight cap: most sessions wait ~3 s on the LCP race (see NOTES), so a fixed window of
               # 500 held the rate near 167/s regardless of the BNG; allow 5 s worth of offered setups
               "--window", str(max(500, int(self.rate * 5)))]
        lg = Loadgen(cmd)
        self.cleanups.append(lg.stop)
        last: dict = {}
        t0 = time.monotonic()
        stall, best, logged = t0, -1, 0.0
        timeout = n / self.rate + 180
        while lg.proc.poll() is None:
            time.sleep(0.5)
            if not lg.lines:
                continue
            last = json.loads(lg.lines[-1])
            p = last["progress"]
            if p["up"] + p["failed"] > best:
                best, stall = p["up"] + p["failed"], time.monotonic()
            if time.monotonic() - logged >= 10:
                logged = time.monotonic()
                self.log(f"sessions: started {p['started']} up {p['up']} failed {p['failed']}")
            if last["done"] or time.monotonic() - t0 > timeout or time.monotonic() - stall > 60:
                break
        t_setup = time.monotonic()
        time.sleep(3)  # let accel finish shaper/ifup work before measuring
        accel_active = self.active() - base_active
        mem_used_kb = mem0 - _meminfo("MemAvailable")
        setup = sampler.window(t0, t_setup)
        self.log(f"holding {n} sessions for {self.hold}s")
        th = time.monotonic()
        time.sleep(self.hold)
        hold = sampler.window(th, time.monotonic())
        still = self.active() - base_active
        p = last.get("progress", {})
        up = p.get("up", 0)
        lg.lines.clear()
        if not p.get("up"):
            raise RuntimeError(f"no session came up: {p.get('reasons')}")
        return lg, {
            "target": n, "established": up, "failed": p.get("failed", 0), "failure_reasons": p.get("reasons", {}),
            "retransmits_by_state": p.get("retransmits", {}),
            "accel_active_after_setup": accel_active, "accel_active_after_hold": still,
            "dropped_during_hold": max(0, accel_active - still),
            "setup_seconds": p.get("t"), "setup_rate_per_s": round(up / p["t"], 1) if p.get("t") else None,
            "offered_rate_per_s": self.rate,
            "mem_used_mb": round(mem_used_kb / 1024, 1),
            "mem_per_session_kb": round(mem_used_kb / up, 1) if up else None,
            "cpu_setup": setup, "cpu_hold": hold, "hold_seconds": self.hold,
        }

    def finish_sessions(self, lg: "Loadgen", n: int) -> dict:
        base = self.total()
        t0 = time.monotonic()
        lat = lg.stop()
        while self.total() > base - n and time.monotonic() - t0 < 300:
            time.sleep(0.5)
        dt = time.monotonic() - t0
        pct = lambda q: round(lat[min(len(lat) - 1, int(q * len(lat)))] * 1000, 1) if lat else None
        return {"setup_latency_ms": {"p50": pct(0.5), "p95": pct(0.95), "p99": pct(0.99), "max": pct(1.0)},
                "teardown_seconds": round(dt, 1), "teardown_rate_per_s": round(min(n, base) / dt, 1) if dt else None}

    # -- traffic --
    def traffic_sessions(self, k: int, net: ipaddress.IPv4Network) -> list[tuple[str, str]]:
        plugin = next(iter(sorted(Path("/usr/lib/pppd").glob("*/rp-pppoe.so"))), None)
        if not plugin:
            raise RuntimeError("pppd rp-pppoe plugin not found")
        procs = []
        for i in range(k):
            procs.append(subprocess.Popen(
                ["ip", "netns", "exec", NS, "pppd", "plugin", str(plugin), f"nic-{PEER}", "file", str(self.tmp / "pppd-auth"),
                 "noauth", "nodefaultroute", "noipdefault", "nodetach", "maxfail", "1", "unit", str(900 + i),
                 "mtu", "1492", "mru", "1492", "debug"], stdout=open(self.tmp / f"pppd{i}.log", "w"),
                stderr=subprocess.STDOUT))
        self.cleanups.append(lambda: [_stop(p) for p in procs])
        out = []
        deadline = time.monotonic() + 60
        for i in range(k):
            ip = None
            while not ip and time.monotonic() < deadline:
                r = sh("ip", "-n", NS, "-j", "-4", "addr", "show", "dev", f"ppp{900 + i}", check=False)
                a = json.loads(r.stdout or "[]")
                ip = a[0]["addr_info"][0]["local"] if a and a[0].get("addr_info") else None
                if not ip:
                    time.sleep(0.5)
            if not ip or ipaddress.ip_address(ip) not in net:
                tail = (self.tmp / f"pppd{i}.log").read_text()[-1500:]
                raise RuntimeError(f"traffic session ppp{900 + i} did not come up in {net} (got {ip}); pppd:\n{tail}")
            sh("ip", "-n", NS, "rule", "add", "from", ip, "lookup", str(1000 + i))
            sh("ip", "-n", NS, "route", "add", f"{SINK_IP}/32", "dev", f"ppp{900 + i}", "table", str(1000 + i))
            # generator side only: fq paces iperf3 in the kernel (--fq-rate); iperf3's own -b pacing
            # spins a full core per sender, which starved the BNG in the first baseline
            sh("ip", "netns", "exec", NS, "tc", "qdisc", "replace", "dev", f"ppp{900 + i}", "root", "fq")
            out.append((f"ppp{900 + i}", ip))
        sh("ip", "netns", "exec", SINK_NS, "tc", "qdisc", "replace", "dev", SINK_PEER, "root", "fq")
        self.cleanups.append(lambda: [sh("ip", "-n", NS, "rule", "del", "lookup", str(1000 + i), check=False)
                                      for i in range(k)])
        servers = [subprocess.Popen(["ip", "netns", "exec", SINK_NS, "iperf3", "-s", "-p", str(5300 + i)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for i in range(k)]
        self.cleanups.append(lambda: [_stop(p) for p in servers])
        time.sleep(1)
        return out

    def iperf(self, flows: list[tuple[str, str]], per_mbit: float | None, reverse: bool) -> dict:
        procs = []
        for i, (_, ip) in enumerate(flows):
            cmd = ["ip", "netns", "exec", NS, "iperf3", "-c", SINK_IP, "-p", str(5300 + i), "-B", ip,
                   "-t", str(self.duration), "-O", "2", "-J"]
            if per_mbit:
                # no -w: iperf3 aborts if rmem_max caps it, and the generator must be identical with and
                # without tuning; receiver socket drops are recorded separately instead
                cmd += ["-u", "-b", "0", "--fq-rate", f"{per_mbit:.1f}M", "-l", "1400"]
            if reverse:
                cmd.append("-R")
            procs.append(subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True))
        sock0 = {ns: _udp_drops(ns) for ns in (NS, SINK_NS)}
        bps = pkts = lost = 0.0
        errors = 0
        for p in procs:
            try:
                out, _ = p.communicate(timeout=self.duration + 30)
                end = json.loads(out)["end"]
                s = end.get("sum_received") or end["sum"]  # receiver side
                bps += s["bits_per_second"]
                if per_mbit:
                    pkts += s.get("packets", end["sum"].get("packets", 0))
                    lost += s.get("lost_packets", end["sum"].get("lost_packets", 0))
            except (subprocess.TimeoutExpired, ValueError, KeyError):
                p.kill()
                errors += 1
        r = {"gbps": round(bps / 1e9, 3), "flows": len(flows), "failed_flows": errors}
        if per_mbit:
            # receiver socket-buffer drops happen in the generator, not in the BNG: reported separately
            rx_ns = NS if reverse else SINK_NS
            sock = _udp_drops(rx_ns) - sock0[rx_ns]
            r.update(pps=round((pkts - lost) / self.duration), loss_percent=round(100 * lost / pkts, 3) if pkts else None,
                     lost_packets=int(lost), receiver_socket_drops=sock)
        return r

    def shaper_drops(self, flows: list[tuple[str, str]], net: ipaddress.IPv4Network) -> int:
        ips = {ip for _, ip in flows}
        drops = 0
        for s in self.accel.sessions():
            if s.get("ip") in ips:
                q = sh("tc", "-s", "-j", "qdisc", "show", "dev", s["ifname"], check=False).stdout
                drops += sum(x.get("drops", 0) for x in json.loads(q or "[]"))
        return drops


def _udp_drops(ns: str) -> int:
    """UDP InErrors (includes RcvbufErrors) in a namespace; /proc/net/snmp is per network namespace."""
    rows = [l.split() for l in sh("ip", "netns", "exec", ns, "cat", "/proc/net/snmp", check=False).stdout.splitlines()
            if l.startswith("Udp:")]
    return dict(zip(rows[0][1:], map(int, rows[1][1:]))).get("InErrors", 0) if len(rows) >= 2 else 0


def _stop(p: subprocess.Popen) -> None:
    if p.poll() is None:
        p.terminate()
    try:
        p.wait(timeout=30)
    except subprocess.TimeoutExpired:
        p.kill()


class Loadgen:
    """loadgen child whose stdout is drained continuously: a full pipe would block it and
    its sessions would then die on LCP echo timeouts."""

    def __init__(self, cmd: list[str]):
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True)
        self.lines: list[str] = []
        self.reader = threading.Thread(target=lambda: self.lines.extend(self.proc.stdout), daemon=True)
        self.reader.start()
        self.latencies: list[float] | None = None

    def stop(self) -> list[float]:
        """SIGTERM (loadgen sends PADT for every session); returns setup latencies in seconds."""
        if self.latencies is None:
            _stop(self.proc)
            self.reader.join(10)
            final = [json.loads(l) for l in self.lines if '"final"' in l]
            self.latencies = final[-1]["latencies"] if final else []
        return self.latencies


def host_info() -> dict:
    cpu = re.search(r"model name\s*:\s*(.+)", Path("/proc/cpuinfo").read_text())
    virt = sh("systemd-detect-virt", check=False).stdout.strip() or "none"
    return {"hostname": platform.node(), "cpu_model": cpu.group(1) if cpu else None, "vcpus": os.cpu_count(),
            "mem_mb": _meminfo("MemTotal") // 1024, "kernel": platform.release(), "virtualization": virt}


def verdict(sessions: dict, traffic: dict | None) -> tuple[str, list[str]]:
    why = []
    if sessions["established"] < sessions["target"] * THRESHOLDS["sessions_established_ratio"]:
        why.append(f"{sessions['established']}/{sessions['target']} sessions established")
    if sessions["dropped_during_hold"] > THRESHOLDS["sessions_dropped_during_hold"]:
        why.append(f"{sessions['dropped_during_hold']} sessions dropped during hold")
    if traffic:
        tgt = traffic["target_gbps"]
        for d in ("down", "up"):
            r = traffic[d]
            if r["gbps"] < tgt * THRESHOLDS["delivered_ratio_min"]:
                why.append(f"{d}: {r['gbps']} of {tgt} Gbit/s delivered")
            if r.get("loss_percent") is None or r["loss_percent"] > THRESHOLDS["udp_loss_percent_max"]:
                why.append(f"{d}: UDP loss {r.get('loss_percent')}%")
    return ("PASS" if not why else "FAIL"), why


def run(b: Bench, n: int, traffic: list[float]) -> list[Path]:
    cfg = b.preflight(n, traffic)
    net = b.bench_pool(cfg, n + 64)
    rate_mbit = (cfg.get("shaper") or {}).get("max_rate_mbit")
    k = min(48, max(4, math.ceil(max(traffic) * 1000 / (0.8 * rate_mbit)))) if traffic and rate_mbit else 12
    sampler = Sampler([ACCESS, SINK_HOST, cfg["uplink"]])
    sampler.start()
    started = datetime.now(timezone.utc)
    accel_version = b.accel.version().strip()
    results = []
    try:
        b.setup(cfg, net, rate_mbit * 1000 if rate_mbit else None)
        if traffic:
            b.sink(net)
        lg, sess = b.sessions(n, sampler)
        flows = b.traffic_sessions(k, net) if traffic else []
        tcp = {}
        levels = []
        for g in traffic:
            per = g * 1000 / k
            lvl = {"target_gbps": g, "sessions_used": k, "per_session_mbit": round(per, 1),
                   "per_session_plan_mbit": rate_mbit}
            for d, rev in (("down", True), ("up", False)):
                b.log(f"traffic {g} Gbit/s {d} over {k} sessions")
                t0 = time.monotonic()
                lvl[d] = b.iperf(flows, per, rev)
                lvl[d]["host"] = sampler.window(t0, time.monotonic())
            lvl["shaper_drops"] = b.shaper_drops(flows, net)
            levels.append(lvl)
        if flows:
            for d, rev in (("down", True), ("up", False)):
                b.log(f"TCP maximum {d} over {k} sessions")
                t0 = time.monotonic()
                tcp[d] = b.iperf(flows, None, rev)
                tcp[d]["host"] = sampler.window(t0, time.monotonic())
        sess.update(b.finish_sessions(lg, n))
    finally:
        sampler.stop_evt.set()
        b.cleanup()
    base = {"date": started.isoformat(timespec="seconds"), "host": host_info(), "accel_ppp": accel_version,
            "config": {"aaa": cfg["aaa"], "shaper": cfg.get("shaper"), "bench_pool": str(net),
                       "nat": f"SNAT {net} -> {SINK_GW} on {SINK_HOST} (same rule shape as CGNAT)"},
            "radius_latency_ms": None, "sessions": sess, "tcp_max": tcp or None,
            "thresholds": THRESHOLDS, "notes": NOTES}
    b.out.mkdir(parents=True, exist_ok=True)
    for lvl in levels or [None]:
        res, why = verdict(sess, lvl)
        name = f"benchmark_{n}_{_g(lvl['target_gbps'])}g" if lvl else f"benchmark_{n}_sessions_{int(b.rate)}ps"
        doc = {"name": name, **base, "traffic": lvl, "result": res, "fail_reasons": why}
        p = b.out / f"{name}.json"
        p.write_text(json.dumps(doc, indent=1))
        results.append(p)
        b.log(f"{name}: {res} {'; '.join(why)}")
    write_summary(b.out)
    return results


def _g(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else str(x)


def write_summary(out: Path) -> Path:
    rows = []
    docs = [(p, json.loads(p.read_text())) for p in out.glob("benchmark_*.json")]
    for p, d in sorted(docs, key=lambda x: (x[1]["sessions"]["target"], (x[1].get("traffic") or {}).get("target_gbps", 0))):
        s, t = d["sessions"], d.get("traffic")
        host = (t or {}).get("down", {}).get("host") or s.get("cpu_hold") or {}
        rows.append("| " + " | ".join(str(x) for x in (
            f"[{d['name']}]({p.name})", f"{s['established']}/{s['target']}",
            f"{t['target_gbps']}" if t else "-",
            f"{t['down']['gbps']} / {t['up']['gbps']}" if t else "-",
            f"{t['down'].get('pps')} / {t['up'].get('pps')}" if t else "-",
            f"{host.get('cpu_avg_percent')} / {host.get('cpu_peak_core_percent_1s')}",
            f"{s.get('mem_used_mb')}",
            f"{t['down'].get('loss_percent')} / {t['up'].get('loss_percent')}" if t else "-",
            f"{s.get('setup_rate_per_s')}/s, p95 {s.get('setup_latency_ms', {}).get('p95')} ms",
            f"**{d['result']}**")) + " |")
    md = ("# Benchmark results\n\nGenerated by `bngctl benchmark`. Each row links to its JSON. Thresholds: all sessions "
          "established and none dropped during the hold; delivered >= 95% of the target in both directions with UDP "
          "loss <= 0.5%.\n\n"
          "| Run | Sessions up | Target Gbit/s | Delivered down / up Gbit/s | PPS down / up | CPU avg / peak core % | "
          "RAM MB (sessions) | UDP loss down / up % | Session setup | Result |\n"
          "|---|---|---|---|---|---|---|---|---|---|\n" + "\n".join(rows) + "\n\n" +
          "\n".join(f"- {n}" for n in NOTES) + "\n")
    f = out / "README.md"
    f.write_text(md)
    return f
