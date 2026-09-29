# Phase 1 results — bng01

Date: 2026-09-29. Node: `bng01` (Proxmox KVM, 12 vCPU Ryzen 9 7900X, 15 GiB, Ubuntu 24.04.5, kernel 6.8.0-142).
accel-pppd: **1.14.0** built from the upstream tag (`/usr/local/sbin/accel-pppd -V`), no patches.

All tests ran on the isolated veth lab (`bnglab0` ↔ netns `bnglab`), because bng01 has a single, public NIC.
Nothing here measures production throughput or scale.

## End-to-end lab (`scripts/lab/lab-test.sh`)

| Step | Result |
|---|---|
| PPPoE discovery + session up (pppd 2.4.9 rp-pppoe client) | PASS |
| PAP auth via accel-ppp `chap-secrets` (lab only) | PASS |
| IPCP address from pool (`100.64.0.1`, gw `100.64.255.254`) | PASS |
| Session visible in `bngctl sessions` with matching IP | PASS |
| Gateway reachable from subscriber | PASS |
| Per-session download shaper (`tbf` on `ppp0`) | PASS |
| Per-session upload policer (ingress `police` on `ppp0`) | PASS |
| Download throughput, limit 20.48 Mbit/s | **19.79 Mbit/s** — PASS |
| Upload throughput, limit 20.48 Mbit/s | **19.53 Mbit/s** — PASS |
| Disconnect via `bngctl session disconnect` | PASS |

## Safety mechanisms

| Drill | Outcome |
|---|---|
| Firewall apply → confirm from a **new** SSH session | new SSH worked with `policy drop`; timer disarmed; rules persisted |
| Firewall apply, **not** confirmed | timer fired after 120 s, confirmed ruleset restored, pending file removed |
| Config with non-existent PPPoE interface (`bnglab9`) | diff shown; health gate failed (`PPPoE: not serving: bnglab9`); previous config restored and reloaded; audit `rolled_back`; no version created |
| Change needing restart (`thread_count` → `[core]`) | refused without `--allow-restart`; nothing written |
| `bngctl backup` → `bngctl restore` | archive created (0600); restore = `no changes` |
| Data-plane independence | with an active session, the whole management venv was moved away: 0% loss on subscriber traffic |
| Pool fix applied live | `[ip-pool]` change applied by `accel-cmd reload` (no restart, sessions kept) |

## Findings during Phase 1

1. **accel-ppp 1.14.0 hands out `.0`/`.255` from CIDR pools** (`ippool.c` `parse1`): the first lab session got `100.64.0.0`. Fixed in the renderer: pools are emitted as per-/24 `x.x.x.1-254` ranges. Side effect: `show ippool` totals reflect only the last range line of a pool (stats only).
2. An unparseable pool line makes accel-pppd `_exit()` (also on reload). The static validator rejects such lines before apply.
3. `show sessions` output uses CRLF and padded cells; state is `start` briefly before `active`. A captured fixture pins the parser to real 1.14.0 output.
4. Audit `source` shows `cli:local` for CLI actions under `sudo` (sudo drops `SSH_CLIENT`). The Phase 5 API will record the real client IP.

## PPPoE on VLAN 4044 (ens18)

- `ens18.4044` (802.1Q id 4044, L2 only) created persistently in `/etc/netplan/60-bng-vlan4044.yaml`
  (cloud-init file untouched), applied under a 120 s auto-revert timer, confirmed from a new SSH session.
- First attempt to add it via reload: **not served**. The health gate rolled back. Cause: 1.14.0 reads
  `[pppoe] interface=` only at start. Fixed: apply now adds new interfaces live with
  `accel-cmd pppoe interface add`.
- Config version 3: `pppoe interface show` lists `bnglab0` and `ens18.4044`, both `active`; accel-pppd
  not restarted (up since 13:58:47); lab test re-run PASS.
- **Not verified:** a real subscriber session on VLAN 4044 — needs a CPE/OLT sending tagged 4044 frames
  to this VM (the Proxmox bridge / upstream switch must carry VLAN 4044 to `ens18`).

## Open items (not verified — depend on external systems)

1. **Jaze RADIUS**: add bng01 as NAS in Jaze, set `aaa: radius` + `radius:` section, put the shared secret in `/etc/bng-platform/secrets/radius.secret` (root, 0600), then:
   - `sudo bngctl radius test` (Status-Server; Jaze may not support it)
   - `sudo bngctl radius test --user <test-subscriber>` (Access-Request)
   - bring up a lab session with a Jaze test subscriber; verify **Accounting Start / Interim / Stop** arrive in Jaze and CoA/Disconnect from Jaze works.
2. Confirm which attribute Jaze sends the bandwidth profile in (`shaper.attr`, default `Filter-Id`, format `down/up` kbit/s or Cisco `rate-limit` strings).
3. Proxmox: second vNIC on an isolated bridge for a real access network, and multiqueue on all vNICs.
