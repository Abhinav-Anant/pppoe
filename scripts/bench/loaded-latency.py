#!/usr/bin/env python3
"""Throughput + ping latency while the uplink is loaded (bufferbloat / CAKE check).

  loaded-latency.py idle|down|up [--streams N] [--seconds S] [--ping HOST] [--netns NS]

down: parallel HTTP range requests on proof.ovh.net/files/10Gb.dat (OVH allows ~3 concurrent),
      or --server cf: 25 MB GETs from speed.cloudflare.com/__down
up:   parallel 5 MB POSTs to speed.cloudflare.com/__up
Prints Mbit/s actually transferred and ping min/avg/p95/max/loss measured during the load.
Public test servers throttle: treat throughput as a lower bound.
"""
import argparse
import os
import re
import statistics
import subprocess
import threading
import time

p = argparse.ArgumentParser()
p.add_argument("mode", choices=["idle", "down", "up"])
p.add_argument("--streams", type=int, default=3)
p.add_argument("--seconds", type=int, default=15)
p.add_argument("--ping", default="1.1.1.1")
p.add_argument("--netns", default=None, help="run load+ping inside this network namespace")
p.add_argument("--server", choices=["ovh", "cf"], default="ovh", help="download source")
a = p.parse_args()
pre = ["ip", "netns", "exec", a.netns] if a.netns else []
UP_FILE = "/tmp/lat.up5"
if a.mode == "up" and not os.path.exists(UP_FILE):
    with open(UP_FILE, "wb") as f:
        f.write(os.urandom(5_000_000))

total = 0
lock = threading.Lock()
deadline = time.monotonic() + a.seconds


def worker(i: int) -> None:
    global total
    while (left := deadline - time.monotonic()) > 1:
        if a.mode == "down" and a.server == "cf":
            cmd = ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code} %{size_download}", "--max-time",
                   str(int(left)), "https://speed.cloudflare.com/__down?bytes=25000000"]
        elif a.mode == "down":
            start = (i * 7 + int(time.monotonic())) % 9 * 1_000_000_000
            cmd = ["curl", "-s", "-o", "/dev/null", "-r", f"{start}-{start + 999_999_999}",
                   "-w", "%{http_code} %{size_download}", "--max-time", str(int(left)),
                   "https://proof.ovh.net/files/10Gb.dat"]
        else:
            cmd = ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code} %{size_upload}", "--max-time",
                   str(int(left)), "--data-binary", f"@{UP_FILE}", "https://speed.cloudflare.com/__up"]
        out = subprocess.run(pre + cmd, capture_output=True, text=True).stdout.split()
        if len(out) == 2 and out[0] in ("200", "206", "000"):   # 000 = timed out; count bytes moved
            with lock:
                total += int(float(out[1]))
        elif len(out) == 2 and out[0] != "000":
            time.sleep(0.5)  # throttled; don't hammer


t0 = time.monotonic()
threads = [threading.Thread(target=worker, args=(i,)) for i in range(a.streams)] if a.mode != "idle" else []
for t in threads:
    t.start()
time.sleep(1 if threads else 0)  # let the queue build before sampling
count = max(5, int((a.seconds - 2) / 0.2))
ping = subprocess.run(pre + ["ping", "-n", "-i", "0.2", "-c", str(count), "-W", "2", a.ping],
                      capture_output=True, text=True).stdout
for t in threads:
    t.join()
el = time.monotonic() - t0
rtts = [float(x) for x in re.findall(r"time=([\d.]+)", ping)]
loss = re.search(r"([\d.]+)% packet loss", ping)
q = statistics.quantiles(rtts, n=20)[-1] if len(rtts) >= 20 else max(rtts, default=0)
thr = f"{total * 8 / el / 1e6:6.0f} Mbit/s" if threads else "      idle"
print(f"{a.mode:<5} {thr}  ping {a.ping}: min {min(rtts, default=0):.1f} avg {statistics.mean(rtts) if rtts else 0:.1f} "
      f"p95 {q:.1f} max {max(rtts, default=0):.1f} ms  loss {loss.group(1) if loss else '?'}%")
