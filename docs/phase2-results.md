# Phase 2 results — CGNAT on bng01

Date: 2026-09-29. nftables 1.0.9, kernel 6.8.0-142. Lab pool `100.64.0.0/24` →
`43.229.72.90:1024-65535` (bng01 has one public IPv4). Functional test only — no capacity claim.

## Ruleset (confirmed, persisted in `/etc/bng-platform/nftables/filter.nft`)

- `inet bng_filter` input: policy drop, SSH + ICMP + established only.
- `inet bng_filter` forward: policy drop; MSS clamp (`tcp flags syn / syn,rst … maxseg size set rt mtu`);
  established/related accept; invalid drop; `iifname "ppp*" oifname "ens18" ip saddr 100.64.0.0/24` accept
  (spoofed subscriber sources are dropped).
- `ip bng_nat` postrouting: per pool one TCP/UDP rule with the port range and one rule for other
  protocols, both `persistent` (one public IP per subscriber). No masquerade.
- `net.ipv4.ip_forward = 1` via `/etc/sysctl.d/90-bng-platform.conf`.

## Lab test (`scripts/lab/lab-test.sh`, all PASS)

| Step | Result |
|---|---|
| PPPoE session, IP `100.64.0.4`, shaper present | PASS |
| Download / upload at 20.48 Mbit/s limit | 19.79 / 19.66 Mbit/s — PASS |
| Internet ping (ICMP) via NAT | PASS |
| DNS (UDP to 8.8.8.8) via NAT | PASS |
| HTTPS via CGNAT — external service sees **43.229.72.90** | PASS |
| conntrack SNAT entry for the subscriber | PASS |
| MSS clamp rule present | PASS |
| Disconnect via bngctl | PASS |

`bngctl nat status`: `NAT PASS`, conntrack `26/262144`, pool counter increasing.

## Safety

| Drill | Outcome |
|---|---|
| `nft -c` before load | caught an invalid rule (port range without transport match) before anything changed; fixed |
| Apply + confirm from new SSH session | OK |
| Apply, delete `bng_nat` (simulated breakage), no confirm | revert restored **both** tables and cleared pending |
| Revert timing | fired up to ~60 s late: systemd default `AccuracySec=1min`. Fixed: `AccuracySec=1s` (verified `AccuracyUSec=1s`) |

## Not done / open

- NAT logging: designed in `docs/nat-logging.md`, not deployed.
- conntrack sizing (`nf_conntrack_max` 262144 default) and timeouts: tuning module, later phase.
- IPv6 forwarding: subscribers have `ppp ipv6=deny`; the inet forward chain already drops IPv6 by policy.
- Real subscriber traffic on VLAN 4044: needs tagged frames from the access network.
