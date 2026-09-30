# bng-platform

Linux BNG / PPPoE concentrator built on upstream **ACCEL-PPP 1.14.0**, nftables and tc,
with a management plane (`bngctl`, `bng-api`, web GUI) that is never in the
packet path. AAA is any external RADIUS server (Jaze in production; verified with FreeRADIUS); the lab
and demo ship a FreeRADIUS instance in its own namespace.

```text
             CONTROL PLANE            bngctl, bng-api (FastAPI + PostgreSQL), React GUI
                  |
     +------------+-------------+
   ACCEL          nft           tc
     +------------+-------------+
                  |
              DATA PLANE          PPPoE / per-session shaping / CGNAT  -> Internet
```

If the management plane is stopped or removed, established PPPoE sessions keep forwarding
(verified: `docs/phase1-results.md`; API + database stopped: `docs/phase5-results.md`).

## Status

| Phase | Scope | State |
|---|---|---|
| 1 | ACCEL-PPP, PPPoE, RADIUS config, IP pools, per-session shaping, safe config apply, health, CLI | done in lab; Jaze verification open |
| 2 | nftables CGNAT, forward filter, MSS clamp, conntrack | done in lab (`docs/phase2-results.md`); NAT logging designed |
| 3 | CAKE | demonstrated (`docs/phase3-results.md`), then **removed** at the operator's request |
| 4 | Priority = plan rate from RADIUS (per-session shaper), rate guard; easywall host firewall | done in lab (`docs/rate-limits.md`, `docs/easywall.md`); Jaze attribute + easywall hand-over open |
| 5 | FastAPI management API, RBAC, audit, PostgreSQL | done in lab (`docs/api.md`, `docs/phase5-results.md`) |
| 6 | Web GUI (dashboard, sessions, QoS, NAT, RADIUS, config, monitoring), WebSockets | done in lab (`docs/gui.md`, `docs/phase6-results.md`) |
| 7 | Multi-BNG (pinned-TLS node registry, service tokens, proxy, fleet compare/search); easywall console embedded | done in lab (`docs/multi-bng.md`, `docs/phase7-results.md`); second physical node not yet available |
| 8 | Tuning module, benchmark framework, 1k–30k sessions × 1–40 Gbit/s matrix | done on bng01 (`docs/performance.md`, `docs/phase8-results.md`, `benchmark-results/`) |

Measured on bng01 (12-vCPU KVM guest, veth lab, generators on the same vCPUs): 30,000 PPPoE sessions held
(~80 kB each); 1 Gbit/s passes at every session count; 5 Gbit/s down passes on loss but the run fails on
upload; the shared VM saturates near 0.8 Mpps / 9–11 Gbit/s. **30k subscribers × 40 Gbit/s is not
demonstrated**; that needs the BNG on its own hardware with an external traffic generator.

## Docs

- [ARCHITECTURE_ASSESSMENT.md](ARCHITECTURE_ASSESSMENT.md) — environment and upstream findings
- [docs/installation.md](docs/installation.md)
- [docs/configuration.md](docs/configuration.md)
- [docs/api.md](docs/api.md) — management API, roles, endpoints
- [docs/gui.md](docs/gui.md) — web GUI: access, build, pages, live data
- [docs/multi-bng.md](docs/multi-bng.md) — managing several BNGs from one console
- [docs/radius.md](docs/radius.md) — connecting any RADIUS server (plan formats, CoA, secrets)
- [docs/demo.md](docs/demo.md) — the staged demo on bng01 and how to present it
- [docs/performance.md](docs/performance.md) — tuning module and benchmark framework; [benchmark-results/](benchmark-results/README.md)
- [docs/sizing-5000.md](docs/sizing-5000.md) — production config for 5,000 subscribers, from the benchmark results
- [docs/phase1-results.md](docs/phase1-results.md)
- [docs/phase5-results.md](docs/phase5-results.md), [phase6](docs/phase6-results.md), [phase7](docs/phase7-results.md), [phase8](docs/phase8-results.md)

## Layout

```text
backend/app/     config model, accel-ppp renderer/validator, accel-cmd adapter,
                 safe apply manager, health, firewall, RADIUS test client, bngctl
frontend/        React GUI (npm run build -> dist, served by bng-api)
backend/tests/   pytest (cd backend && python -m pytest)
system/          systemd unit, example config
scripts/         install / uninstall / deploy / health-check, lab/
```
