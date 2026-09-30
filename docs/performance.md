# Performance: tuning and benchmarks

Nothing here claims a subscriber count or a throughput. The numbers in
[`benchmark-results/`](../benchmark-results/README.md) are what one machine measured under the
conditions written into each JSON file. Results are in [phase8-results.md](phase8-results.md).

## Tuning (`bngctl tuning`)

```bash
sudo bngctl tuning show          # diagnostics + recommendations (also: GUI → Benchmark)
sudo bngctl tuning apply         # apply the "apply" rows: all-or-nothing, each value read back
sudo bngctl tuning rollback      # restore the values from before the first apply
```

The module looks at what the machine has, not at a template:

| Area | What is read | What may be recommended |
|---|---|---|
| CPU | count, `scaling_governor` | `performance` governor (manual) |
| NIC queues | `ethtool -l`, `/sys/class/net/*/queues` | more combined channels, or Proxmox multiqueue (manual: resets the link) |
| Rings | `ethtool -g` | max ring size (manual: resets the link) |
| RPS | `rps_cpus` per RX queue | spread RX to all CPUs when there are fewer RX queues than CPUs (applied) |
| IRQs | `/proc/interrupts` | irqbalance/affinity when several NIC queues land on one CPU (manual) |
| softnet | `/proc/net/softnet_stat` drops, time_squeeze | `netdev_max_backlog` 4096 (16384 if drops were seen), `netdev_budget` 600 if squeezes were seen |
| Socket buffers | `rmem_max`, `wmem_max` | 4 MiB |
| conntrack | `nf_conntrack_max`, buckets, RAM | power of two using ≤ 1/32 of RAM (≈320 B/entry), buckets = max/4 |
| Offloads, XPS, file descriptors | reported | — (accel-ppp runs with `LimitNOFILE=1048576`) |

Rules the code enforces:
- A value is never lowered, and a setting that does not exist on this kernel or NIC is refused.
- Every write is read back. If one fails, the ones already written are restored.
- The first apply records the original values (`/var/lib/bng-platform/tuning/rollback.json`).
- sysctls persist in `/etc/sysctl.d/90-bng-tuning.conf`. RPS masks are re-applied at boot by
  `bng-tuning.service`.
- Lab veth and ppp interfaces are skipped; only real NICs are tuned.
- `uninstall.sh` rolls tuning back.

## Benchmarks (`bngctl benchmark`)

```bash
sudo bngctl benchmark run --sessions 10000 --traffic 1,5,10    # one JSON per traffic level
sudo bngctl benchmark run --sessions 30000 --rate 2000          # session setup only
sudo bngctl benchmark report                                    # regenerate README.md
sudo bash scripts/lab/bench-matrix.sh                           # the Phase 8 matrix
```

Results go to `/var/lib/bng-platform/benchmark-results/` as `benchmark_<sessions>_<G>g.json`, plus a
Markdown summary. The GUI's Benchmark page and `GET /api/benchmarks[/<name>]` read them. Running a
benchmark is CLI-only.

### What a run does

1. **Preflight.** It needs `aaa: lab` and the veth lab (`scripts/lab/lab-up.sh`). It refuses if any
   session exists outside the lab: a run restarts accel-ppp twice and loads every CPU.
2. **Bench pool.** A temporary pool `100.72.0.0/N` becomes the default pool through the normal
   safe-apply path, with a restart. A bench subscriber is added at the top of the lab chap-secrets,
   with the plan rate `shaper.max_rate_mbit`.
3. **Sessions.** `app/bench/loadgen.py` brings up N subscribers at `--rate` per second, from a
   userspace PPPoE client. It runs real PADI/PADO/PADR/PADS, LCP, PAP or CHAP-MD5, IPCP and
   LCP echo, one MAC per subscriber. The BNG builds a real kernel PPPoE session, ppp interface and
   shaper for each.
   - pppd needs about 1.25 MB per session, so 30k pppd clients would not fit in 15 GB.
   - It records setup latency percentiles, retransmits by stage, failures, memory and CPU.
   - It then holds all sessions for `--hold` seconds and counts any that drop.
4. **Traffic.**
   - K real pppd sessions, with K chosen so each carries at most 80% of its plan rate.
   - iperf3 runs from the lab namespace to a sink namespace (`bngsink`, 198.18.0.0/30) behind the
     BNG.
   - The path covers the per-session shaper (tbf down, police up), the forward filter, MSS clamp
     and a SNAT rule of the same shape as the CGNAT one. The SNAT rule lives in its own table,
     `ip bng_bench`, on egress `bngsink0`, so benchmark traffic never leaves the machine.
   - UDP runs at the target rate in each direction, then TCP runs at maximum.
5. **Cleanup** (always runs). It stops generators, removes the netns, veth, nft table and rule,
   restores chap-secrets and restores the original config.yaml, with a restart.

### Thresholds (PASS only when all hold)

- All N sessions established, none dropped during the hold.
- Per direction: delivered ≥ 95% of the target and UDP loss ≤ 0.5%.

A measurement that is missing (for example, UDP loss unmeasured) counts as FAIL.

### What is recorded

- **Sessions:** established, failed, dropped, setup rate and latency (p50/p95/p99/max),
  retransmits by stage, teardown rate.
- **Memory:** host memory used by the sessions and accel-pppd RSS.
- **Throughput:** Gbit/s delivered and PPS in each direction, UDP loss, and generator
  receive-buffer drops (so loss can be attributed).
- **CPU:** average and per core, busiest core per second, softirq per core, and accel-pppd vs
  generator CPU.
- **Kernel:** softnet drops and time_squeeze, NIC drops, peak conntrack, shaper qdisc drops.
- **TCP:** maximum TCP throughput.

RADIUS latency is `null` while `aaa: lab`: Jaze is not connected. CAKE was removed at the operator's
request (Phase 3), so shaper drops are tbf/police drops.

### Limits of this lab (read before quoting a number)

- **Shared CPUs.** The access side is a veth pair inside one VM. Subscribers, generators and the BNG
  share the same vCPUs, so every figure is a lower bound for the BNG alone on that CPU. veth has
  no NIC, IRQs or RSS.
- **VM results say nothing about 10/25/40G NICs.** bng01's only real NIC is single-queue virtio.
  Real throughput tests need passthrough/SR-IOV (see [installation.md](installation.md#proxmox-notes)).
- **CPU accounting.** The kernel has no `CONFIG_IRQ_TIME_ACCOUNTING`. Forwarding done in softirq
  from process context is charged to the sending process (iperf3/pppd), so `softirq_*`
  undercounts the BNG's forwarding cost.
- **TCP inflation.** On veth, TCP uses GSO/GRO super-packets, so TCP Gbit/s is far higher than UDP
  at 1400 bytes. The UDP PPS figures are the per-packet capacity.

### Findings made while building the framework

1. **accel-ppp 1.14.0 does not reload `[ip-pool]`.**
   - `ippool.c` registers no `EV_CONFIG_RELOAD` handler, so a new pool only exists after a restart.
   - The config manager treated pool changes as reload-safe. On a live node, adding a pool and
     making it the default failed every new login ("lcp-terminate").
   - `ip-pool` is now in `RESTART_SECTIONS`, so the change needs `--allow-restart`
     (`tests/test_manager.py::test_ip_pool_change_needs_restart`).
2. **PADS is sent before the session channel exists.**
   - In `pppoe.c:1245-1246` of 1.14.0, `pppoe_send_PADS()` runs, then `connect_channel` is queued.
   - A client that sends its LCP Conf-Request immediately after PADS (pppd does) can lose it if
     accel-ppp has not yet created the kernel PPPoE socket. The session then completes on the
     client's 3 s LCP restart timer.
   - Sessions still come up, but under a login burst (for example, after a BNG restart) most take
     about 3 s longer. The generator deliberately behaves like pppd, and each run reports
     `retransmits_by_state`.
3. **iperf3 3.16's UDP `-b` pacing spins a full core per sender, even at 100 Mbit/s.**
   - The first baseline measured the generator: 48 senders used every vCPU at 1 Gbit/s.
   - The runner now paces in the kernel (`-b 0 --fq-rate`, fq qdisc on the generator's egress).
   - Receiver socket-buffer drops are recorded apart from loss in the BNG path.
