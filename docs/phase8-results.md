# Phase 8 results — performance

Date: 2026-09-30. Method, thresholds and caveats: [performance.md](performance.md). The raw data
is in [`benchmark-results/`](../benchmark-results/README.md): one JSON per run, with the untuned
baseline in `baseline-untuned/`.

**What this machine is:** bng01 is a Proxmox KVM guest.
- 12 vCPUs (AMD Ryzen 9 7900X, 1 thread per core), 15.6 GB RAM, kernel 6.8.0-142, accel-ppp 1.14.0.
- The access side is a veth pair in the same VM, so subscribers, traffic generators and the BNG
  all share these 12 vCPUs.
- Its only real NIC is single-queue virtio.

**Nothing below is a claim about 10/25/40G hardware.** The spec's 30,000 subscribers × 40 Gbit/s
target is **not demonstrated**. The 30,000-subscriber count is demonstrated on this VM. Throughput
is demonstrated only up to 5 Gbit/s down (UDP) and ~25 Gbit/s TCP with offloads (see TCP below).

## Matrix: sessions × traffic (tuned)

The UDP rows use 1400-byte payloads spread over 48 pppd sessions, each shaped to its 1000 Mbit/s
plan. Each direction is measured for 10 s after a 2 s warm-up.

| Sessions | 1 Gbit/s | 5 Gbit/s | 10 Gbit/s | 20 Gbit/s | 30 Gbit/s | 40 Gbit/s |
|---|---|---|---|---|---|---|
| 1,000 | **PASS** | FAIL | FAIL | FAIL | FAIL | FAIL |
| 5,000 | **PASS** | FAIL | FAIL | FAIL | FAIL | FAIL |
| 10,000 | **PASS** | FAIL | FAIL | FAIL | FAIL | FAIL |
| 20,000 | **PASS** | FAIL | FAIL | FAIL | FAIL | FAIL |
| 30,000 | **PASS** | FAIL | FAIL | FAIL | FAIL | FAIL |

What was delivered. The numbers are practically identical at every session count; this is 30,000:

| Target | Down Gbit/s | Down loss | Down PPS | Up Gbit/s | Up loss | CPU avg (down/up) | Why it failed |
|---|---|---|---|---|---|---|---|
| 1 | 0.97 | 0.00% | 86.5k | 0.98 | 0.00% | 18% / 38% | — (PASS) |
| 5 | 4.84 | 0.00% | 432k | 4.65 | 3.46% | 42% / 99% | up: loss, almost all in the sink's iperf3 receive buffers |
| 10 | 9.18 | 0.37% | 820k | 7.41 | 16.3% | 96% / 100% | < 95% delivered; CPU saturated |
| 20 | 8.79 | 6.2% | 785k | 10.6 | 8.5% | 100% / 100% | CPU saturated |
| 30 | 8.26 | 12.2% | 737k | 10.1 | 12.7% | 100% / 100% | CPU saturated |
| 40 | 8.13 | 13.1% | 726k | 9.57 | 17.5% | 100% / 100% | CPU saturated |

Reading it:
- **Capacity.** The machine as a whole (BNG plus generators) tops out at about 0.8 Mpps /
  9–11 Gbit/s of 1400-byte packets.
- **Where the loss happens.** In every failing run, the generator's UDP receive-buffer drops account
  for (and exceed) the lost packets. There were no softnet drops after tuning, no NIC drops and
  **no shaper drops**. The BNG forwarded what was offered; the receiving iperf3 processes, competing
  for the same saturated CPUs, could not keep up.
- **The limit is this box.** At 20–40 Gbit/s the generators alone use ~98% of all 12 vCPUs. That
  limits the test rig, not the BNG code, and it can only be separated with the BNG on its own
  hardware and an external traffic generator.
- **Session count doesn't matter.** 1k to 30k sessions changed neither throughput nor loss.
- **The shaper held.** Every session carried its share, with 0 tbf/police drops.

**TCP maximum** (48 sessions, GSO/GRO on veth): about 25 Gbit/s down and 25 Gbit/s up at every
session count. These are super-packets, so it says nothing about per-packet capacity. It does show
that the per-session shaper does not throttle bulk TCP below its plan rate.

## Session scale

| Sessions | Up | Failed | Dropped in 60 s hold | Memory used | per session | accel-pppd RSS | Idle hold CPU (avg / busiest core) |
|---|---|---|---|---|---|---|---|
| 1,000 | 1,000 | 0 | 0 | 37 MB | 38 kB | 14 MB | 0.2% / 2% |
| 5,000 | 5,000 | 0 | 0 | 455 MB | 93 kB | 35 MB | 0.4% / 5% |
| 10,000 | 10,000 | 0 | 0 | 827 MB | 85 kB | 60 MB | 0.7% / 6% |
| 20,000 | 20,000 | 0 | 0 | 1,506 MB | 77 kB | 113 MB | 8.1% / **100%** |
| 30,000 | 30,000 | 0 | 0 | 2,316 MB | 79 kB | 163 MB | 10.8% / **100%** |

- **Memory.** Each subscriber costs about 80–95 kB of kernel and accel-ppp memory: ppp netdev,
  PPPoE socket, tbf + ingress police. accel-pppd itself is about 5.4 kB per session. 30,000 sessions
  fit in 2.3 GB.
- **accel-pppd CPU while holding 30,000 sessions** (LCP echo every 20 s) is 0.4%.
- **The busy core at 20,000+ is systemd-resolved**, at ~91% of a core, with systemd-networkd at
  ~13%. Both process every rtnetlink link event and track 20–30k ppp links; accel-ppp isn't
  involved. See the operator decisions below.

## Session setup and teardown

`--rate` is the offered login rate, and every run holds 30,000 sessions. accel-ppp's own ceiling
comes from the 500–2000/s runs.

| Offered | Achieved | Failed | Setup p50 / p95 / p99 | Retransmits (by stage) | Result |
|---|---|---|---|---|---|
| 300/s, ≤ 500 in flight (matrix runs) | 160–184/s | 0 at 1k–30k | 3.1 s / 3.3 s / 6.1 s (30k) | LCP 23.8k, IPCP 1.3k (30k) | PASS |
| 500/s | 383/s | 429 (1.4%) | 3.1 s / 7.8 s / 11.1 s | PADI 2.8k, PADR 3.3k, LCP 26.1k, IPCP 1.5k | FAIL |
| 1000/s | 440/s | 1,777 (5.9%) | 6.2 s / 15.4 s / 18.0 s | PADI 18.0k, PADR 11.1k, LCP 26.1k, auth 0.4k, IPCP 1.3k | FAIL |
| 2000/s | 486/s | 4,174 (13.9%) | 12.2 s / 23.1 s / 27.2 s | PADI 37.6k, PADR 9.9k, LCP 26.1k, auth 6.5k, IPCP 10.2k | FAIL |

**Setup capacity on this VM.** About 180 logins/s sustained with no failures. Above that, PADI and
PADR go unanswered: by ~380/s, 1.4% of clients give up after 5 tries, and it saturates near 490/s.
The generator gives up after 5 tries; real CPEs keep retrying, so in the field this shows up as
delay, not permanent failure. During setup, accel-pppd used about 17% of the machine, i.e. about 2 of
12 cores. The rest went to the kernel creating netdevs and qdiscs, to systemd-resolved/networkd
reacting to every new link, and to the generator. Which of these is the ceiling was not isolated.

- **Even at low rates most sessions take ~3 s.** accel-ppp sends PADS before it connects the
  session's PPPoE channel (`pppoe.c:1245-1246` in 1.14.0). A client that sends LCP Conf-Request
  immediately, as pppd does, loses it and waits for its 3 s LCP restart timer. At every rate,
  75–80% of sessions retransmitted in LCP; none failed because of it.
- **Teardown runs at 100–265 sessions/s.** Each logout deletes a netdev and its qdiscs.
- **A graceful accel-ppp stop with 10,000 live sessions takes 117 s.** That exceeded systemd's
  90 s default: accel-pppd was SIGKILLed, and the manager's 60 s timeout mistook it for a failed
  restart. Both are fixed (`TimeoutStopSec=600`, manager 660 s). Start takes about 1 s.

## Tuning: before and after

`bngctl tuning apply` on bng01 set these, all read back:
- `netdev_max_backlog` 1000 → 16384. The higher step was chosen because the untuned baseline had
  produced softnet drops.
- `netdev_budget` 300 → 600, because the baseline produced time_squeeze counts.
- `rmem_max` / `wmem_max` 212992 → 4194304
- `nf_conntrack_max` 262144 → 1048576
- RPS `ens18/rx-0` 000 → fff

It also listed as manual (not applied): virtio multiqueue for ens18 (it has 1 queue for
12 vCPUs), which needs a Proxmox change.

| 1,000 sessions | Untuned | Tuned |
|---|---|---|
| 1 Gbit/s | PASS, 0% loss | PASS, 0% loss |
| 5 Gbit/s down | 4.83 Gbit/s, 0.22% loss, **12,266 softnet drops** | 4.84 Gbit/s, 0.00% loss, **0 softnet drops** |
| 10 Gbit/s down | 9.29 Gbit/s, 0.28% | 9.17 Gbit/s, 0.41% |
| 20–40 Gbit/s | CPU saturated | CPU saturated (no change) |

The measurable effect in this lab: the larger backlog removed the softnet drops at 5 Gbit/s. The
RPS and multiqueue recommendations concern the real NIC. The lab traffic runs on veth, so their
effect is not measured here.

## Found and fixed in Phase 8

1. **`[ip-pool]` changes were applied with a reload, but accel-ppp 1.14.0 never reloads pools.**
   A pool added through the GUI or CLI silently never existed; made the default, it failed every
   login. Pool changes now require `--allow-restart`, with a regression test.
2. **accel-ppp stop timeout** (above): systemd SIGKILL, and a false rollback by the manager.
3. **Benchmark tooling defects**, found by checking the numbers rather than trusting them:
   - The first "1000 sessions up" was actually all failures (the pool issue).
   - iperf3's UDP `-b` pacing spun a core per sender, so the first baseline measured iperf3.
   - Two packet sockets let LCP overtake PADS.
   - A fixed in-flight window capped the setup rate at about 167/s.

## Operator decisions (not applied)

- **systemd-resolved on the BNG.** It costs ~1 core at 20k+ subscribers, and more during login
  churn. The usual remedy is a static `/etc/resolv.conf` with resolved disabled. That changes how
  this host resolves DNS, so it's your call.
- **Proxmox multiqueue for ens18** (`queues=12`), then `ethtool -L ens18 combined 12`.
- **Real throughput numbers** need the BNG on its own host with passthrough/SR-IOV NICs and an
  external generator (TRex/pktgen or a second machine). Every Gbit/s figure here is a lower bound
  set by a shared 12-vCPU VM.
- **RADIUS latency** is not measured: `aaa: lab`, and Jaze has no NAS entry yet.

## State left on bng01

- **Tuning is applied** (the 6 settings above); undo with `sudo bngctl tuning rollback`.
  `bng-tuning.service` is enabled, which re-applies RPS at boot.
- **Configuration** is restored to the pre-benchmark config. It went through several restart
  versions, v29–v54.
- **Clean-up checked:** no bench pool or bench user, no `bngsink` namespace, no `bng_bench` table.
- **Results** are in `/var/lib/bng-platform/benchmark-results/`, shown on the GUI's Benchmark page.
