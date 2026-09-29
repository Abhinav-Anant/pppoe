# bng-platform

Linux BNG / PPPoE concentrator built on upstream **ACCEL-PPP 1.14.0**, nftables and tc,
with a management plane (`bngctl`, `bng-api`, web GUI) that is never in the
packet path. External AAA is Jaze RADIUS; no RADIUS server is deployed here.

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
| 7 | Multi-BNG | pending |
| 8 | Performance testing | pending |

No subscriber-count or throughput capability is claimed until Phase 8 benchmarks on
appropriate hardware demonstrate it.

## Docs

- [ARCHITECTURE_ASSESSMENT.md](ARCHITECTURE_ASSESSMENT.md) — environment and upstream findings
- [docs/installation.md](docs/installation.md)
- [docs/configuration.md](docs/configuration.md)
- [docs/api.md](docs/api.md) — management API, roles, endpoints
- [docs/gui.md](docs/gui.md) — web GUI: access, build, pages, live data
- [docs/phase1-results.md](docs/phase1-results.md)
- [docs/phase5-results.md](docs/phase5-results.md)

## Layout

```text
backend/app/     config model, accel-ppp renderer/validator, accel-cmd adapter,
                 safe apply manager, health, firewall, RADIUS test client, bngctl
frontend/        React GUI (npm run build -> dist, served by bng-api)
backend/tests/   pytest (cd backend && python -m pytest)
system/          systemd unit, example config
scripts/         install / uninstall / deploy / health-check, lab/
```
