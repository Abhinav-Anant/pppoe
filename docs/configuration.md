# Configuration

The node's desired state is `/etc/bng-platform/config.yaml`. `bngctl` renders it into
`/etc/bng-platform/accel-ppp/accel-ppp.conf`. Never edit the generated file by hand.
Unknown keys are rejected. Every string that reaches accel-ppp.conf is restricted to
`[A-Za-z0-9_.-]` so no value can inject extra directives.

## Keys

| Key | Type / default | accel-ppp.conf |
|---|---|---|
| `node` | token | — (identity) |
| `aaa` | `radius` \| `lab` | `[modules] radius` or `chap-secrets` |
| `uplink` | interface name | — (health check) |
| `thread_count` | 1–256, default: accel default (CPU count) | `[core] thread-count` — **restart** |
| `pppoe.ac_name` | token, `bng` | `[pppoe] ac-name` |
| `pppoe.service_name` | token, none | `[pppoe] service-name` |
| `pppoe.accept_any_service` | bool, `true` | `[pppoe] accept-any-service` |
| `pppoe.interfaces[].name` / `.padi_limit` | ifname / 0–100000 | `[pppoe] interface=name[,padi-limit=n]` |
| `pppoe.padi_limit` | 0–100000, `0` | `[pppoe] padi-limit` |
| `pppoe.pado_delay_ms` | 0–10000, `0` | `[pppoe] pado-delay` |
| `pppoe.called_sid` | `mac` \| `ifname` \| `ifname:mac` | `[pppoe] called-sid` |
| `ppp.mtu` / `ppp.mru` | 1280–1500, `1492` | `[ppp] mtu` / `mru` |
| `ppp.ipv6` | `deny` \| `allow`, `deny` | `[ppp] ipv6` |
| `ppp.lcp_echo_interval` / `lcp_echo_failure` | `20` / `3` | `[ppp] lcp-echo-*` |
| `ip_pools.gw_ip_address` | IPv4, outside all pools | `[ip-pool]`/`[radius]`/`[chap-secrets] gw-ip-address` |
| `ip_pools.default` | pool name | `[pppoe] ip-pool` |
| `ip_pools.pools[]` | `name`, `network` (CIDR), `next` | `[ip-pool]` host ranges (see below) |
| `dns` | ≤2 IPv4 | `[dns] dns1/dns2` |
| `radius.servers[]` | `address`, `auth_port` 1812, `acct_port` 1813, `backup` | secret include (below) |
| `radius.nas_identifier` / `nas_ip_address` | token / IPv4 | `[radius]` |
| `radius.timeout` / `max_try` / `acct_timeout` | 3 / 3 / 120 | `[radius]` |
| `radius.acct_interim_interval` / `_jitter` | 300 / 30 s | `[radius]` |
| `radius.coa_listen` / `coa_port` | IPv4 / 3799 | `dae-server` in secret include |
| `radius.dae_allowed` | CIDRs, default = RADIUS servers | `[radius] dae-allowed` + firewall |
| `radius.blast_protection` | bool, `true` | `[radius] blast-protection` |
| `shaper.attr` / `vendor` | `Filter-Id` / none | `[shaper] attr` / `vendor` |
| `shaper.down_limiter` / `up_limiter` | `tbf` / `police` | `[shaper]` |
| `shaper.max_rate_mbit` | Mbit, none | health check only: FAIL if an applied rate exceeds it |
| `shaper.require_rate` | bool, `false` | health check only: FAIL if an active session has no rate |
| `firewall.host_input` | `bng` \| `easywall`, `bng` | `easywall`: no bng input chain (see `docs/easywall.md`) |

### NAT (`nat:`)

| Key | Type / default | Effect |
|---|---|---|
| `nat.pools[].name` | token | rule comment `nat-pool <name>` |
| `nat.pools[].subscribers` | CIDRs, non-overlapping across pools | SNAT match + forward allow-list |
| `nat.pools[].public_start` / `public_end` | IPv4 range, outside subscriber space | SNAT address range |
| `nat.pools[].port_min` / `port_max` | `1024` / `65535` | SNAT port range (TCP/UDP) |
| `nat.mss_clamp` | bool, `true` | clamp TCP MSS to route MTU in forward |

Rendered into the same nftables file as the host firewall and applied with
`sudo bngctl firewall apply` + confirm from a new SSH session (120 s auto-revert).
`sudo bngctl nat status` shows forwarding, conntrack usage and per-pool counters.

`[modules]`, `[core]` and `[cli]` are read only at daemon start. A change there needs an
accel-ppp restart, which disconnects every subscriber, so apply refuses it unless
`--allow-restart` is given. Everything else is applied with `accel-cmd reload`; established
sessions stay up.

`[pppoe] interface=` lines are also read only at start (1.14.0 `pppoe_init` →
`load_interfaces`); reload ignores them. Apply therefore syncs them live with
`accel-cmd pppoe interface add|del`: a **new** interface is added without disturbing anyone;
**removing or changing** an interface disconnects the sessions on it and needs
`--allow-restart`.

**Pools.** accel-ppp 1.14.0 allocates every address of a CIDR pool, including `x.x.x.0` and
`x.x.x.255`. The renderer therefore writes per-/24 ranges (`100.64.0.1-254,name=lab`).

## Secrets

The RADIUS shared secret is never in `config.yaml`, `accel-ppp.conf`, config versions or the
audit log.

```bash
sudo install -m 0600 /dev/null /etc/bng-platform/secrets/radius.secret
sudoedit /etc/bng-platform/secrets/radius.secret     # one line, 8-128 chars, no space/comma
```

Apply renders `server=` and `dae-server=` lines into `/etc/bng-platform/secrets/radius.conf`
(0600), which accel-ppp pulls in with `$include` inside `[radius]`.

## Safe apply

```bash
sudo bngctl config validate [file]     # model + generated-config checks
sudo bngctl config diff [file]         # unified diff vs active accel-ppp.conf
sudo bngctl config apply [file] [--allow-restart]
sudo bngctl config history
sudo bngctl config rollback [version] [--allow-restart]
sudo bngctl backup | restore <archive>
```

Apply: validate → diff → back up the active files → write → start / reload / restart →
health gate (service active, CLI answers, every PPPoE interface up and served) → on failure
restore the previous files and re-activate them. Each applied config is saved as
`/var/lib/bng-platform/versions/NNNN/`.

The generated config is also checked against the list of sections and directives in the
1.14.0 source, because accel-pppd has no config-check mode and ignores unknown directives.

## Audit log

`/var/lib/bng-platform/audit.jsonl`, one JSON object per line: `timestamp`, `component`,
`admin`, `source`, `action` (`config_apply`, `session_disconnect`, `firewall_apply`,
`firewall_confirm`), `result` (`applied`, `rejected`, `rolled_back`, `ok`), plus `version`,
`restart`, `diff` (generated config only) or `detail`.

## Switching from lab to Jaze RADIUS

1. In Jaze, add this node as a NAS (its source IP = `nas_ip_address`) with a shared secret.
2. Write the secret to `/etc/bng-platform/secrets/radius.secret` (above).
3. In `config.yaml` set `aaa: radius` and fill the `radius:` section.
4. `sudo bngctl config diff`, then `sudo bngctl config apply --allow-restart` (the module
   list changes, which drops sessions).
5. `sudo bngctl firewall apply` + confirm from a new SSH session (opens CoA from the DAE sources).
6. `sudo bngctl radius test` and `sudo bngctl radius test --user <test user>`.

The `chap-secrets` module and `scripts/lab/` are for lab testing only.
