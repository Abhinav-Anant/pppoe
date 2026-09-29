# bng-platform

Linux BNG / PPPoE concentrator built on upstream **ACCEL-PPP 1.14.0**, nftables and tc,
with a management plane (`bngctl` now; API + web GUI in later phases) that is never in the
packet path. External AAA is Jaze RADIUS; no RADIUS server is deployed here.

```text
             CONTROL PLANE            bngctl  (later: FastAPI + React)
                  |
     +------------+-------------+
   ACCEL          nft           tc
     +------------+-------------+
                  |
              DATA PLANE          PPPoE / NAT / CAKE / QoS  -> Internet
```

If the management plane is stopped or removed, established PPPoE sessions keep forwarding
(verified: `docs/phase1-results.md`).

## Status

| Phase | Scope | State |
|---|---|---|
| 1 | ACCEL-PPP, PPPoE, RADIUS config, IP pools, per-session shaping, safe config apply, health, CLI | done in lab; Jaze verification open |
| 2 | nftables CGNAT, forward filter, MSS clamp, conntrack | done in lab (`docs/phase2-results.md`); NAT logging designed |
| 3 | CAKE / cake_mq, direction handling, IFB | pending |
| 4 | Gold/Silver/Bronze tiers via nft marks | pending |
| 5 | FastAPI management API | pending |
| 6 | Web GUI | pending |
| 7 | Multi-BNG | pending |
| 8 | Performance testing | pending |

No subscriber-count or throughput capability is claimed until Phase 8 benchmarks on
appropriate hardware demonstrate it.

## Docs

- [ARCHITECTURE_ASSESSMENT.md](ARCHITECTURE_ASSESSMENT.md) — environment and upstream findings
- [docs/installation.md](docs/installation.md)
- [docs/configuration.md](docs/configuration.md)
- [docs/phase1-results.md](docs/phase1-results.md)

## Layout

```text
backend/app/     config model, accel-ppp renderer/validator, accel-cmd adapter,
                 safe apply manager, health, firewall, RADIUS test client, bngctl
backend/tests/   pytest (cd backend && python -m pytest)
system/          systemd unit, example config
scripts/         install / uninstall / deploy / health-check, lab/
```
