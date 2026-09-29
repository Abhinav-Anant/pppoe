# Phase 6 results — web GUI on bng01

Date: 2026-09-29. Built on the workstation (Vite 8, React 19, TypeScript 7, Tailwind 4,
Recharts 3); `tsc` type-check clean. Bundle 684 kB (204 kB gzip). Served by bng-api from
`/opt/bng-platform/web`; opened through `ssh -L 8080:127.0.0.1:8080`.

## Checked in a browser (lab subscriber downloading through CGNAT)

| Check | Result |
|---|---|
| Login (temporary super_admin, deleted afterwards), redirect to dashboard | PASS |
| Dashboard live over WebSocket: subscriber download 6.8 Mbit/s vs uplink rx 6.65 Mbit/s; all health checks green (RADIUS SKIP, aaa=lab) | PASS |
| Sessions: live row updates every 2 s; download reached 20.5 Mbit/s = the 20480 kbit plan; plan, MAC, interface shown | PASS |
| Session detail: current and peak rates, packet counts, live traffic graph | PASS |
| Monitoring graphs and per-NIC rates | PASS |
| Configuration: invalid edit (`thread_count: 99999`) → error shown; comment-only edit → valid, empty accel diff | PASS |
| History (v1–v12), QoS, NAT, RADIUS, PPPoE & pools, Interfaces, Audit, Users, System | PASS |
| WebSockets reconnect by themselves after `bng-api` restarts | PASS |

Regression after the final deploy: `api-smoke.sh` **API RESULT: PASS**, `lab-test.sh`
**LAB RESULT: PASS**. Backend: 108 unit tests pass (WebSocket auth/Origin, incremental session
stream, rate/churn maths, VLAN parsing, YAML comments, SPA fallback + CSP).

## Found and fixed

1. `uvicorn` without extras has no WebSocket support ("Unsupported upgrade request"). Added
   `websockets` to the `api` extra.
2. NAT pool "bytes" were misleading: the SNAT rule only sees each flow's first packet.
   They are now shown as translated flows.
3. A comment-only YAML edit could not be saved. YAML that differs from the file is now
   applied as a new version; unchanged JSON still returns `no changes`.
4. Validation errors named the field "body"; the GUI now shows the config path.

## Open

See [gui.md](gui.md#not-in-this-phase): connection history, configurable alert thresholds,
TLS/remote access, multi-BNG views.
