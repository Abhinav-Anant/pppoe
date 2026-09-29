# Phase 3 results — aggregate CAKE on bng01

> **Removed on 2026-09-29 at the operator's request.** CAKE code, `bng-qos.service` and the
> `qos` config section were deleted (branch `phase-4`); this page records what was measured.
> The code remains in git history on branch `phase-3`.

Date: 2026-09-29. Kernel 6.8.0-142, iproute2 6.1, uplink `ens18` (virtio, 1 queue).
Tool: `scripts/bench/loaded-latency.py` (parallel HTTP load against public servers + 5 pings/s).
Public servers throttle, so throughput numbers are lower bounds. VM result — no capacity claim.

## Direction handling (as deployed)

| Direction | Where CAKE sits | Isolation |
|---|---|---|
| Customer → Internet (upload) | `ens18` egress root qdisc | `dual-srchost nat` |
| Internet → Customer (download) | `ens18` ingress → `mirred` redirect → IFB `bngifb0` egress, `ingress` keyword | `dual-dsthost nat` |

`nat` makes CAKE look up conntrack, so isolation uses the subscriber's private address although
everything leaves as 43.229.72.90. Per-subscriber limits remain accel-ppp's shaper on each `pppN`;
no per-subscriber tc classes exist. The redirect is installed only after the IFB queue exists.

## Capabilities (detected on loopback, removed again)

`cake` yes, **`cake_mq` no** (not in kernel 6.8, unknown to iproute2 6.1). No ECN switch exists in
CAKE (it always ECN-marks ECN-capable flows), so none is exposed.

## Uplink measurement (no CAKE)

| | Throughput (multi-stream) | Ping 1.1.1.1 avg / p95 / max |
|---|---|---|
| idle | — | 30.1 / 30.3 / 30.4 ms |
| download | 479–545 Mbit/s | 29.9–30.0 / 30.2 / 34.1 ms |
| upload | 680–783 Mbit/s | 30.8–30.9 / 31.3–31.4 / 31.5 ms |

**No bufferbloat at the achievable rates**: the bottleneck was the remote test servers, not the
uplink. The true uplink capacity is unknown from this VM and is above these figures.

## CAKE demonstration (test rate 100/100 Mbit/s so CAKE is the bottleneck)

| Test | Result |
|---|---|
| Shaping, upload | 85 Mbit/s; ping p95 30.0 ms, max 30.2 ms |
| Shaping, download (ingress via IFB) | 79–80 Mbit/s; ping p95 30.0 ms; 7,317 AQM drops on IFB |
| Queue management | CAKE queue delay avg 128 µs / peak 248 µs on the download queue; RTT unchanged under load |
| Fairness: host 1 flow vs subscriber 2 flows, `dual-dsthost nat` | host 33 vs 44 Mbit/s (43 %), 36 vs 39 (48 %) |
| Control: same with isolation `flows` | host 24 vs 49 (33 %), 29 vs 46 (39 %) — per-flow split |
| Confirm-or-revert | applied, SSH survived the ingress redirect, confirmed from a new session |
| Boot persistence | `bng-qos.service` runs the confirmed `/etc/bng-platform/tc/cake.sh` (no Python in the path) |

Per-host isolation through NAT works: with per-flow isolation the host with fewer flows got about a
third; with `dual-dsthost nat` it moved toward half. Occasional single ping spikes (up to ~190 ms)
were seen during ingress shaping; p95 stayed at 30 ms.

## Deployed state

CAKE is **configured but disabled** (`qos.cake.enabled: false`, upload 650 / download 480 Mbit/s =
~90–95 % of the measured floors). Enabling it at those values would cap subscribers below what the
link delivered, with no measured latency benefit. Enable it once the contracted uplink rate is
known: set `bandwidth_mbit` to ~95 % of contract, `enabled: true`, `bngctl config apply`,
`bngctl qos apply`, confirm from a new SSH session.

## Bugs found and fixed during Phase 3

1. Measurement tool dropped timed-out uploads (reported 0 Mbit/s while CAKE counted 353 MB) — fixed.
2. Clear-only script (CAKE disabled) exited 1 because `ip link del bngifb0` failed → `bng-qos.service`
   failed. Clear lines now `|| true`; regression test added.
3. Health reported "no qos.cake configured" for a disabled config — now says "disabled".

## Open

- cake_mq: revisit with the HWE kernel (7.0 available) + newer iproute2, and a multi-queue NIC.
- Multiqueue on the Proxmox vNIC (currently 1 queue: all RX on one CPU).
- Contracted uplink rate → enable CAKE.
