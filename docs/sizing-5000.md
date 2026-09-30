# Configuration for 5,000 subscribers

Based on the Phase 8 measurements on bng01 ([phase8-results.md](phase8-results.md)). The
`benchmark_5000_*.json` files are in [`benchmark-results/`](../benchmark-results/README.md).

## What the tests say about 5,000

| Question | Measured at 5,000 sessions | Verdict |
|---|---|---|
| Can the gateway hold 5,000 sessions? | 5,000 up, 0 failed, 0 dropped in the hold | Yes |
| Memory | 455 MB (93 kB per subscriber); accel-pppd 35 MB | 4 GB RAM is plenty |
| Idle CPU (LCP echo, accounting) | 0.4% average, busiest core 5% | Negligible |
| Full reconnect after a restart or outage | 158 logins/s: all 5,000 back in **32 s**, p99 6 s | Yes, at the default settings |
| Traffic, 1 Gbit/s each way | 0.97 / 0.98 Gbit/s, 0% loss | PASS |
| Traffic, 5 Gbit/s | 4.84 Gbit/s down with 0% loss; upload failed on generator CPU | Down only |
| Above ~9 Gbit/s | CPU saturated (BNG and generators share the VM) | Not demonstrated |

The session count is not the constraint at 5,000. **Throughput, IPv4 addresses and NAT are.**

## Config changes needed

The current bng01 config has three problems at 5,000 subscribers:
1. The subscriber pool is a /20, which gives **4,064 usable addresses** (accel uses .1–.254 of each /24), too few for 5,000.
2. All of NAT goes out of **one public IP**.
3. The lab and demo parts are still in the config.

`/etc/bng-platform/config.yaml` for production:

```yaml
node: bng01
aaa: radius
uplink: ens18
pppoe:
  ac_name: bng01
  accept_any_service: true
  interfaces:
  - name: ens18.4044          # access VLAN (bnglab0 removed: that is the lab/demo)
  padi_limit: 0
  pado_delay_ms: 0
  called_sid: mac
ppp:
  mtu: 1492
  mru: 1492
  ipv6: deny
  lcp_echo_interval: 20       # 5,000 / 20 s = 250 echo/s; measured cost ~0
  lcp_echo_failure: 3
ip_pools:
  gw_ip_address: 100.64.255.254
  default: subscribers
  pools:
  - name: subscribers         # existing /20: current subscribers keep their range
    network: 100.64.16.0/20
    next: subscribers2
  - name: subscribers2        # overflow /20: 8,128 addresses in total, ~60% headroom
    network: 100.64.32.0/20
dns:
- 8.8.8.8
- 1.1.1.1
shaper:
  attr: Filter-Id             # or the attribute Jaze sends; see radius.md
  down_limiter: tbf
  up_limiter: police
  max_rate_mbit: 1000
  require_rate: true
nat:
  pools:
  - name: cgnat
    subscribers:
    - 100.64.16.0/20
    - 100.64.32.0/20
    public_start: 203.0.113.1   # <- your public block (placeholder: a /26 here)
    public_end: 203.0.113.62
    port_min: 1024
    port_max: 65535
  mss_clamp: true
firewall:
  host_input: bng
  # console_port: 443         # only while presenting; see demo.md
radius:
  servers:
  - address: 192.0.2.10       # <- Jaze (placeholder)
  nas_identifier: bng01
  nas_ip_address: 43.229.72.90  # the address Jaze's NAS entry is made for
  coa_listen: 43.229.72.90      # CoA/Disconnect from Jaze arrive here (udp 3799)
  acct_interim_interval: 300  # 5,000 / 300 s = ~17 interim/s (60 s would be 83/s)
  acct_interim_jitter: 60     # spreads interims so a login burst doesn't re-synchronise them
```

### Why each value

- **Pools: two chained /20s (8,128 addresses).** Enough for 5,000 with room to grow. Keeping
  `100.64.16.0/20` first means today's subscribers don't change range. A pool change **restarts
  accel-ppp**, and every subscriber reconnects: measured at 32 s for 5,000. Do it in a maintenance
  window.
- **NAT: at least a /26 of public addresses (~78 subscribers per IP).**
  - One IP has 64,512 ports. Linux can reuse a port toward different destinations, but at 5,000
    subscribers one address is still a CGNAT bottleneck, and it gets blocklisted.
  - `persistent` pins each subscriber to one public IP.
  - **Not benchmarked:** port exhaustion was not tested. Watch NAT on the Monitoring page after
    go-live.
  - Log mappings for lawful intercept: see [nat-logging.md](nat-logging.md).
- **RADIUS interim 300 s.** 60 s is a lab value. Jaze's latency is **not measured** (it has no NAS
  entry yet), so do a test login and one CoA before go-live (`scripts/lab/radius-test.sh` against
  Jaze).
- **padi_limit 0.** Setup was clean at ~180 logins/s and saturated near 490/s. CPEs retry, so a
  storm shows up as delay, not lost subscribers. A limit was not tested, so none is set.
- **Remove `bnglab0`, the `lab` pool, and `console_port`.** Stop the demo first:
  `sudo systemctl disable --now bng-demo bng-radius-lab`.

## Host settings (already applied on bng01)

`bngctl tuning` is applied:
- `netdev_max_backlog` 16384 and `netdev_budget` 600.
- 4 MiB socket buffers and RPS across 12 CPUs.
- `nf_conntrack_max` 1,048,576 (~210 flows per subscriber). At its ceiling it uses ~320 MB. If
  Monitoring shows conntrack near the maximum at peak, raise it.

systemd-resolved is disabled (static DNS). At 20k+ subscribers it cost a full core; at 5,000 it
doesn't matter.

## Throughput: the real constraint

Size the uplink to peak demand, not to the plan speeds:

```
peak Gbit/s ≈ subscribers × peak-hour average per subscriber
5,000 × 2 Mbit/s = 10 Gbit/s     5,000 × 4 Mbit/s = 20 Gbit/s
```

Measure the per-subscriber peak on your current network; that number decides everything below.

| Peak need | This VM as it is | What is needed |
|---|---|---|
| ≤ 1 Gbit/s | Tested: PASS | Only the uplink. **ens18 measured ~0.5 Gbit/s down in Phase 3.** |
| 1–5 Gbit/s | Download tested clean to 4.84 Gbit/s | Proxmox multiqueue on the NICs (`queues=12`, then `ethtool -L ens18 combined 12`), and a 10G uplink |
| 5–20 Gbit/s | Not demonstrated | The BNG on its own host with passthrough/SR-IOV 10/25G NICs, then re-run `bngctl benchmark` with an external generator |

**Before go-live:**
1. Apply the config above: `bngctl config apply --allow-restart` (it restarts for the pool change).
2. Confirm the firewall: `bngctl firewall apply`, then `confirm` from a new SSH session.
3. Test a Jaze login, an accounting start, an interim and a stop, and one CoA.
4. Enable multiqueue in Proxmox if the peak is above 1 Gbit/s.
5. Put a second, separate NIC on the access side. Today PPPoE and the uplink share ens18 through a
   VLAN.
