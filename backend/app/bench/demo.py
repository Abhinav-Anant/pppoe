"""Demo stage: a lab gateway that looks and behaves like one in service.

    python -m app.bench.demo            (run by bng-demo.service; SIGTERM stops and cleans up)

- ~1,200 simulated subscribers (loadgen) on RADIUS plans, one username and MAC each.
- ~20 real pppd subscribers, each with its own MAC (macvlan "CPE"), generating varying TCP
  traffic within their plan to a simulated internet namespace behind the gateway. Traffic goes
  through per-plan shaping, the forward filter and a SNAT rule (own table, own egress), exactly the
  data path a subscriber uses.
- Gentle churn: now and then an active subscriber logs out and back in.

Everything it adds (namespace, veth, macvlans, nft table and forward rule, processes) is removed
on stop. Needs aaa: radius with the lab RADIUS (scripts/lab/radius-lab.sh) and the veth lab.
Simulated, and says so: present it as a demo, not as field data.
"""
from __future__ import annotations

import json
import random
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from app.bench.runner import Loadgen, _stop, sh

NS, PEER = "bnglab", "bnglab1"
INET_NS, INET_HOST, INET_PEER = "bnginet", "bnginet0", "bnginet1"
INET_GW, INET_IP = "198.18.1.1", "198.18.1.2"
LAB = Path("/etc/bng-platform/lab")
BACKGROUND = [("sub50-{i}", 600), ("sub100-{i}", 400), ("sub200-{i}", 150), ("sub500-{i}", 50)]
ACTIVE = [(f"home50-{i}", 50) for i in range(1, 9)] + [(f"home100-{i}", 100) for i in range(1, 7)] + \
         [(f"biz200-{i}", 200) for i in range(1, 5)] + [(f"biz500-{i}", 500) for i in range(1, 3)]
LOAD = (0.05, 0.35)   # share of the plan each active subscriber uses at a time


class Demo:
    def __init__(self):
        self.cleanups: list = []
        self.stop = threading.Event()
        self.pw = (LAB / "password").read_text().strip()
        self.plugin = str(next(iter(sorted(Path("/usr/lib/pppd").glob("*/rp-pppoe.so")))))

    def log(self, msg: str) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

    def cleanup(self) -> None:
        while self.cleanups:
            try:
                self.cleanups.pop()()
            except Exception as e:  # keep undoing the rest
                self.log(f"cleanup: {e}")

    def subscriber_nets(self) -> list[str]:
        import yaml
        cfg = yaml.safe_load(Path("/etc/bng-platform/config.yaml").read_text())
        if cfg.get("aaa") != "radius":
            raise SystemExit("demo needs aaa: radius (scripts/lab/radius-lab.sh)")
        return [p["network"] for p in cfg["ip_pools"]["pools"]]

    def internet(self, nets: list[str]) -> None:
        sh("ip", "netns", "add", INET_NS, check=False)
        self.cleanups.append(lambda: sh("ip", "netns", "del", INET_NS, check=False))
        sh("ip", "link", "add", INET_HOST, "type", "veth", "peer", "name", INET_PEER, "netns", INET_NS, check=False)
        self.cleanups.append(lambda: sh("ip", "link", "del", INET_HOST, check=False))
        sh("ip", "addr", "replace", f"{INET_GW}/30", "dev", INET_HOST)
        sh("ip", "link", "set", INET_HOST, "up")
        for c in (("link", "set", "lo", "up"), ("link", "set", INET_PEER, "up"),
                  ("addr", "replace", f"{INET_IP}/30", "dev", INET_PEER), ("route", "replace", "default", "via", INET_GW)):
            sh("ip", "-n", INET_NS, *c)
        # downloads are paced here, one htb class per active subscriber (matched on its iperf3 server
        # port); iperf3's own -b pacing spins a core per stream and --fq-rate is ignored with -R
        self.tc("qdisc", "replace", "dev", INET_PEER, "root", "handle", "1:", "htb", "default", "999")
        self.tc("class", "replace", "dev", INET_PEER, "parent", "1:", "classid", "1:999", "htb", "rate", "10gbit")
        src = ", ".join(nets)
        sh("nft", "-f", "-", input=(
            f"table ip bng_demo {{\n chain postrouting {{\n  type nat hook postrouting priority srcnat; policy accept;\n"
            f'  oifname "{INET_HOST}" ip saddr {{ {src} }} meta l4proto {{ tcp, udp }} snat to {INET_GW}:1024-65535 persistent\n'
            f'  oifname "{INET_HOST}" ip saddr {{ {src} }} snat to {INET_GW}\n }}\n}}\n'))
        self.cleanups.append(lambda: sh("nft", "delete", "table", "ip", "bng_demo", check=False))
        sh("nft", "add", "rule", "inet", "bng_filter", "forward", "iifname", "ppp*", "oifname", INET_HOST,
           "ip", "saddr", f"{{ {src} }}", "counter", "accept", "comment", '"bng-demo"')

        def drop_rule():
            out = sh("nft", "-a", "list", "chain", "inet", "bng_filter", "forward", check=False).stdout
            for h in re.findall(r'comment "bng-demo" # handle (\d+)', out):
                sh("nft", "delete", "rule", "inet", "bng_filter", "forward", "handle", h, check=False)
        self.cleanups.append(drop_rule)

    def tc(self, *args: str) -> None:
        sh("ip", "netns", "exec", INET_NS, "tc", *args, check=False)

    def pace(self, i: int, mbit: float) -> None:
        self.tc("class", "replace", "dev", INET_PEER, "parent", "1:", "classid", f"1:{100 + i}", "htb",
                "rate", f"{mbit:.1f}mbit", "ceil", f"{mbit:.1f}mbit")

    def background(self) -> None:
        pwf = Path("/run/bng-demo.pw")
        pwf.write_text(self.pw)
        pwf.chmod(0o600)
        self.cleanups.append(lambda: pwf.unlink(missing_ok=True))
        offset = 1_000_000
        for user, n in BACKGROUND:
            lg = Loadgen(["ip", "netns", "exec", NS, sys.executable, "-m", "app.bench.loadgen", "--iface", PEER,
                          "--sessions", str(n), "--user", user, "--password-file", str(pwf), "--rate", "40",
                          "--offset", str(offset)])
            self.cleanups.append(lg.stop)
            offset += 100_000
        self.log(f"{sum(n for _, n in BACKGROUND)} simulated subscribers logging in (40/s per plan)")

    def active(self, i: int, user: str, plan: int) -> None:
        """One real subscriber: its own MAC, pppd, policy route, iperf3 in a loop; relogs on churn."""
        dev, unit, port = f"cpe{i}", 800 + i, 5400 + i
        mac = f"02:c0:00:00:00:{i:02x}"
        sh("ip", "-n", NS, "link", "add", "link", PEER, dev, "address", mac, "type", "macvlan", "mode", "bridge", check=False)
        sh("ip", "-n", NS, "link", "set", dev, "up")
        self.cleanups.append(lambda: sh("ip", "-n", NS, "link", "del", dev, check=False))
        self.pace(i, plan * LOAD[0])
        self.tc("filter", "replace", "dev", INET_PEER, "parent", "1:", "protocol", "ip", "prio", "1", "handle", f"800::{i:x}",
                "u32", "match", "ip", "sport", str(port), "0xffff", "flowid", f"1:{100 + i}")
        srv = subprocess.Popen(["ip", "netns", "exec", INET_NS, "iperf3", "-s", "-p", str(port)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.cleanups.append(lambda: _stop(srv))
        auth = Path(f"/run/bng-demo-{i}.auth")
        auth.write_text(f"user {user}\npassword {self.pw}\n")
        auth.chmod(0o600)
        self.cleanups.append(lambda: auth.unlink(missing_ok=True))
        while not self.stop.is_set():
            ppp = subprocess.Popen(["ip", "netns", "exec", NS, "pppd", "plugin", self.plugin, f"nic-{dev}", "file", str(auth),
                                    "noauth", "nodefaultroute", "noipdefault", "nodetach", "maxfail", "1", "unit", str(unit),
                                    "mtu", "1492", "mru", "1492"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            ip = self._wait_ip(f"ppp{unit}")
            if not ip:
                _stop(ppp)
                self.stop.wait(5)
                continue
            sh("ip", "-n", NS, "rule", "add", "from", ip, "lookup", str(2000 + i), check=False)
            sh("ip", "-n", NS, "route", "replace", f"{INET_IP}/32", "dev", f"ppp{unit}", "table", str(2000 + i), check=False)
            sh("ip", "netns", "exec", NS, "tc", "qdisc", "replace", "dev", f"ppp{unit}", "root", "fq", check=False)
            session_end = time.monotonic() + random.uniform(60, 300)   # churn: relog after 1-5 min
            while not self.stop.is_set() and ppp.poll() is None and time.monotonic() < session_end:
                rate = plan * random.uniform(*LOAD)
                cmd = ["ip", "netns", "exec", NS, "iperf3", "-c", INET_IP, "-p", str(port), "-B", ip,
                       "-t", str(random.randint(20, 60))]
                if random.random() < 0.8:   # mostly downloads, like real subscribers
                    self.pace(i, rate)
                    cmd.append("-R")
                else:
                    cmd += ["--fq-rate", f"{rate:.1f}M"]
                cl = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                while cl.poll() is None and not self.stop.is_set():
                    self.stop.wait(1)
                _stop(cl)
                self.stop.wait(random.uniform(0, 8))
            sh("ip", "-n", NS, "rule", "del", "from", ip, "lookup", str(2000 + i), check=False)
            _stop(ppp)
            self.stop.wait(random.uniform(2, 6))

    def _wait_ip(self, dev: str) -> str | None:
        for _ in range(40):
            r = sh("ip", "-n", NS, "-j", "-4", "addr", "show", "dev", dev, check=False)
            a = json.loads(r.stdout or "[]")
            if a and a[0].get("addr_info"):
                return a[0]["addr_info"][0]["local"]
            if self.stop.wait(0.5):
                return None
        return None

    def run(self) -> None:
        signal.signal(signal.SIGTERM, lambda *_: self.stop.set())
        signal.signal(signal.SIGINT, lambda *_: self.stop.set())
        try:
            self.internet(self.subscriber_nets())
            self.background()
            threads = [threading.Thread(target=self.active, args=(i, u, p), daemon=True) for i, (u, p) in enumerate(ACTIVE, 1)]
            for t in threads:
                t.start()
                self.stop.wait(1.5)
            self.log(f"{len(ACTIVE)} active subscribers generating traffic")
            self.stop.wait()
            for t in threads:
                t.join(30)
        finally:
            self.log("stopping: removing everything the demo added")
            self.cleanup()


if __name__ == "__main__":
    Demo().run()
