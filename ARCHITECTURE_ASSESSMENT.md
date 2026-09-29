# Architecture Assessment — bng-platform

Inspection date: 2026-09-29. Target: `bng01` (43.229.72.90), inspected over SSH with read-only
commands plus one reversible CAKE/IFB/clsact test on `lo` (state verified restored afterwards).

## 1. OS / kernel

| Item | Value |
|---|---|
| OS | Ubuntu 24.04.5 LTS |
| Kernel | 6.8.0-142-generic (GA kernel), PREEMPT_DYNAMIC |
| HWE kernel available | `linux-generic-hwe-24.04` → 7.0.0-34 (not installed) |
| Virtualisation | KVM (Proxmox), machine type i440FX |
| Disk | 100 GB, single root partition, 4 GB swap |
| Tools | ethtool 6.7, iproute2 6.1.0 (libbpf 1.3.0), nftables 1.0.9 |
| Python | 3.12.3 |
| Build toolchain | **not installed** (no build-essential, cmake, libssl-dev, libpcre2-dev, lua, snmp dev) |
| PostgreSQL / Redis / Node | not installed |

## 2. CPU

| Item | Value |
|---|---|
| Model | AMD Ryzen 9 7900X (host passthrough CPU type — model name and AVX2/AES visible) |
| vCPU | 12 (1 socket × 12 cores × 1 thread), 1 NUMA node |
| Flags of interest | `aes`, `avx2`, `pdpe1gb`, `hypervisor` |
| cpufreq governor | not exposed (normal in a VM — host governor applies) |

RAM: 15 GiB usable, ~15 GiB free.

## 3. NICs

| Interface | Driver | MAC | Addr | Link | Speed | MTU |
|---|---|---|---|---|---|---|
| `ens18` | virtio_net | bc:24:11:51:87:d2 | 43.229.72.90/24, gw 43.229.72.1 | up | unknown (virtio) | 1500 (max 1500) |

**There is only one NIC.** Management (SSH), and would-be uplink, share `ens18` on a public /24.
There is no subscriber-facing (PPPoE access) interface.

Offloads: rx/tx checksum, SG, TSO, GSO, GRO on; LRO and VLAN offload fixed off. Ring TX 256.

## 4. Queues

| Interface | Combined channels (max/current) | rx/tx queues | IRQs |
|---|---|---|---|
| `ens18` | 1 / 1 | rx-0, tx-0 | `virtio2-input.0`, `virtio2-output.0` |

Proxmox **multiqueue is not enabled** on this vNIC. All packet RX for this NIC lands on one
queue/one IRQ → one CPU. Softnet stats are near zero (idle box). RPS/XPS unset.

## 5. CAKE support

| Test | Result |
|---|---|
| `sch_cake` module | available (`CONFIG_NET_SCH_CAKE=m`) |
| `tc qdisc add dev lo root cake` | **works**; removed again, `lo` back to `noqueue` |
| tc cake options (iproute2 6.1) | `bandwidth/unlimited/autorate-ingress`, `rtt` presets, `besteffort/diffserv3/4/8`, `flowblind/srchost/dsthost/hosts/flows/dual-srchost/dual-dsthost/triple-isolate`, `nat/nonat`, `wash/nowash`, `split-gso/no-split-gso`, `ack-filter/ack-filter-aggressive/no-ack-filter`, `memlimit`, **`fwmark MASK`**, `ptm/atm/noatm`, `overhead N/conservative/raw`, `mpu N`, `ingress/egress` |
| IFB (`ip link add … type ifb`) | works |
| `clsact` qdisc | works |
| `act_mirred`, `cls_u32`, `cls_fw`, `sch_htb` | available |

`fwmark MASK` is supported → CAKE tin selection by nftables mark is viable for Gold/Silver/Bronze
(Phase 4), without HTB and without per-subscriber classes.

## 6. cake_mq support

**Not available on this system.**

- The 6.8 `sch_cake.ko` contains no `cake_mq` symbol.
- `tc qdisc add dev lo root cake_mq` → `Specified qdisc kind is unknown` (iproute2 6.1 does not know it).
- cake_mq is a newer mainline addition. It *may* exist in the available HWE 7.0 kernel, but that
  is **unverified**, and it would also require a newer iproute2 than Ubuntu 24.04 ships.
- On this VM cake_mq would bring nothing anyway: the vNIC has one TX queue.

The GUI/API must therefore detect capability at runtime and offer only `cake` here.

## 7. ACCEL-PPP version

| Source | Version |
|---|---|
| Latest release tag | **1.14.0** (2026-01-24) |
| `master` | 1.14.0-204-g50c54c4 (2026-09-28) |

Relevant findings from the source (not assumed):

- Build: CMake ≥ 3.10, OpenSSL-only, libpcre2. Options: `RADIUS`, `SHAPER`, `LUA`, `NETSNMP`, `BACKUP`,
  plus out-of-tree kernel modules `BUILD_VLAN_MON_DRIVER`, `BUILD_IPOE_DRIVER` (neither currently present).
- `[pppoe]` directives present in source: `interface`, `ac-name`, `service-name`, `accept-any-service`,
  `accept-blank-service`, `pado-delay`, `padi-limit`, `session-timeout`, `called-sid`, `tr101`,
  `ifname-in-sid`, `sid-uppercase`, `cookie-timeout`, `vlan-mon`, `vlan-name`, `vlan-timeout`,
  `ip-pool`, `ipv6-pool`, `verbose`. MTU/MRU are `[ppp]` directives, not `[pppoe]`.
- `[shaper]` directives: `attr`, `attr-down`, `attr-up`, `vendor`, `rate-multiplier`, `down-limiter`,
  `up-limiter`, `leaf-qdisc`, `ifb`, `fwmark`, `burst-factor`, `up/down-burst-factor`, `latency`,
  `mpu`, `mtu`, `quantum`, `moderate-quantum`, `cburst`, `rate-limit`, `verbose`.
  The shaper attaches its qdisc to **each session's own `pppN` interface** — per-subscriber rate
  enforcement without any shared per-subscriber tc class, which satisfies Rule 2.
- `accel-cmd show sessions` accepts a column list, `order` and `match`. Columns: `ifname username ip ip6
  ip6-dp type state uptime uptime-raw calling-sid called-sid sid comp rx-bytes tx-bytes
  rx-bytes-raw tx-bytes-raw rx-pkts tx-pkts inbound-if service-name rate-limit vrf netns`.
  `-raw` columns read live counters — this is what live Mbps must be computed from.
- `terminate sid <id> soft|hard` for disconnect.
- **`master` only (not in 1.14.0):** a `metrics` module — HTTP `/metrics`, Prometheus or JSON, with
  `address`, `allowed_ips`, `read_timeout`, `max_clients`, and `sessions=1` for per-session JSON.
  Its per-session traffic counters are only as fresh as the RADIUS interim interval, so live
  bandwidth still needs `show sessions …-raw`. `master` also carries RADIUS hardening
  (reply authentication against the on-wire request, integer decoding fixes).

### accel-web-manager

Last release v0.4.0 (2022-09). Single-file Flask-style Python backend that shells out to
`accel-cmd show sessions <cols>` / `show stat` / `terminate sid` and splits the `|`-table;
React 18 + CRA (`react-scripts` 5, deprecated). No auth beyond static role flags, no DB,
no config management.

Verdict: reuse the **ideas** (column set, duplicate detection by User-Name/IP/Calling-Sid,
soft/hard drop, per-BRAS view, `pppoe interface show` stats, degraded mode when one BRAS is down)
and its mock-`accel-cmd` test approach. Do not reuse the code — the spec's FastAPI/Vite/RBAC/
config-management architecture replaces all of it.

## 8. Existing network configuration

- netplan `50-cloud-init.yaml`: `ens18` static 43.229.72.90/24, default via 43.229.72.1, DNS 8.8.8.8.
- No IPv6 global address or route (link-local only).
- `net.ipv4.ip_forward=0`, `net.ipv6.conf.all.forwarding=0`.
- nftables ruleset **empty**; ufw enabled-at-boot but **inactive**; iptables empty.
  → the host is on the public Internet with **no firewall**. Only `sshd` (22, key-only since
  2026-09-29) and local `systemd-resolved` listen.
- conntrack not loaded; `netdev_max_backlog=1000`, `rmem/wmem_max=212992` (defaults).

## 9. Compatibility problems / risks

| # | Problem | Impact | Action |
|---|---|---|---|
| 1 | **Single NIC, public-facing** | Cannot run PPPoE on `ens18`: PADO would answer on the provider's shared LAN. | Phase 1 functional test uses an isolated **veth + network namespace** client on the VM (as accel-ppp's own test suite does). Real test needs a 2nd vNIC on an isolated Proxmox bridge. |
| 2 | vNIC has 1 queue | All RX on one CPU; throughput tests meaningless. | Proxmox: `queues=8..12` on each virtio NIC, then `ethtool -L`. |
| 3 | No firewall on a public host | Opening the API/metrics port would expose it. | Phase 1 installs an nftables `inet filter` input policy (SSH + established only), applied with an auto-rollback timer so SSH can't be lost. |
| 4 | cake_mq absent (kernel 6.8, iproute2 6.1) | Only single-queue `cake`. | Detect at runtime; revisit on HWE kernel + newer iproute2 during Phase 3. |
| 5 | `CONFIG_PPPOE_HASH_BITS=4` (Ubuntu default) | Kernel PPPoE session lookup table has 16 buckets. At 30k sessions that is ~1,900 entries per chain on the upstream receive path. Likely a CPU limit at scale — **to be measured**, not assumed. | Benchmark in Phase 8; custom kernel with `PPPOE_HASH_BITS_8` is the known mitigation. |
| 6 | ACCEL-PPP 1.14.0 vs `master` | `metrics` module and RADIUS hardening only on `master`. | See recommendation 2 below. |
| 7 | i440FX machine type | PCIe passthrough/SR-IOV for real throughput tests needs q35. | Switch when moving to passthrough. |
| 8 | Jaze not yet reachable / no NAS entry | Access-Accept, accounting cannot be verified. | Phase 1 ships config + `bngctl radius test`; Jaze verification is a tracked open item. |
| 9 | Management on the same interface as future uplink | Management traffic shares the data plane NIC. | Acceptable on this test VM; production design keeps a dedicated management NIC. |

## 10. Recommended implementation plan

1. **Repo skeleton**: the monorepo from the spec at the project root; `upstream/` stays git-ignored reference.
2. **ACCEL-PPP version**: build **1.14.0** (latest release) by default, pinned by tag in `install.sh`.
   Keep the version as one variable. If the per-session metrics JSON or the post-1.14 RADIUS hardening is
   needed before 1.15 ships, pin a specific reviewed `master` commit and record why (spec rule: no
   unreleased code without a documented reason). **Decision needed from you.**
3. **Phase 1 (on bng01)**:
   - `install.sh`: detect OS/CPU/RAM/NICs/kernel/modules, install build deps, build ACCEL-PPP from the
     pinned tag, install `accel-ppp.service`; never touch netplan without confirmation.
   - Config generator: Python, directive allow-list taken from the 1.14.0 man page/source above;
     renders `accel-ppp.conf` from `config.yaml`; RADIUS secret read from a root-only file,
     never logged.
   - Safe apply: candidate → `accel-pppd` config parse check → diff → backup to
     `/var/lib/bng-platform/backups/<ts>` → apply → health check → automatic rollback on failure.
   - Lab access network: `bng-lab` netns + veth pair (`bngacc0` ↔ client), `pppd` + `rp-pppoe`
     plugin in the netns as the subscriber. Lets PPPoE discovery, LCP, auth, IPCP and shaping be
     verified without touching `ens18`.
   - Until Jaze has a NAS entry: a local `chap-secrets` profile **for lab only**, switched off
     in production config (not a RADIUS server; the spec's no-FreeRADIUS rule is kept).
   - `bngctl health` + `health-check.sh` (ACCEL-PPP, RADIUS reachability, PPPoE iface, route, nft,
     conntrack, NIC link, DNS); `bngctl radius test`.
   - Host firewall with auto-rollback.
4. Phase 2 onward as in the spec. NAT uses nftables `snat` to configurable pools with port ranges;
   QoS tiers use nftables marks in the `0x0000ff00` mask + CAKE `fwmark` → tins, with CAKE on the
   uplink egress and on an IFB fed from uplink ingress (direction detail below).

   Direction on a single uplink: Internet→Customer leaves via the `pppN` interfaces (per-session
   shaper) — aggregate CAKE for this direction goes on the **uplink ingress via IFB** or on the
   access-side egress; Customer→Internet aggregate CAKE goes on **uplink egress**. Final placement
   is decided in Phase 3 from measurements, and each policy records its direction.

### What I need from you

1. ACCEL-PPP: **1.14.0 release** (default) or a pinned `master` commit?
2. Proxmox: can you add a **second vNIC on an isolated bridge** (no physical port) and enable
   **multiqueue** (e.g. `queues=8`) on both NICs? Until then Phase 1 runs on the veth lab.
3. Jaze: when ready, the NAS IP Jaze will see (43.229.72.90?) and the auth/acct ports.
