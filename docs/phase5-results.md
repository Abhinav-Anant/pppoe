# Phase 5 results — management API on bng01

Date: 2026-09-29. bng-api (FastAPI, uvicorn, one worker) on 127.0.0.1:8080, PostgreSQL 16
(Ubuntu package), schema 0001 via Alembic. See [api.md](api.md).

## Unit tests

102 passed (13 new API tests, SQLite through the real Alembic migration): scrypt round-trip,
login header requirement, audit of failed/ok logins, brute-force throttle (429), 401 without
login, CSRF refusal, RBAC refusals, session paging/search/sort/direction/duplicates, disconnect
audit (DB + node JSONL), per-section config permissions, injection-shaped config rejected
(422), apply → version → diff → rollback, no-change apply, user management (weak password,
invalid role, self-demotion refused, disabled user cannot log in), CSRF re-issue, logout,
database unreachable → 503, no execution-style route exists.

## Live (`scripts/lab/api-smoke.sh`): API RESULT: PASS

| Step | Result |
|---|---|
| Login super_admin and read_only (random passwords, never printed) | PASS |
| Unauthenticated → 401 | PASS |
| 16 GET endpoints → 200 | PASS |
| `/api/health` has no FAIL (RADIUS SKIP: aaa=lab) | PASS |
| Lab PPPoE session listed via `/api/sessions` (rate 20480 kbit) + detail | PASS |
| read_only disconnect → 403; missing CSRF → 403; super_admin disconnect → 200, session gone from accel | PASS |
| `/api/config/validate` | PASS |
| `PUT /api/qos/config` change + restore → versions 11, 12, source `api:127.0.0.1`, health-gated reload | PASS |
| Unchanged config → `no changes` | PASS |
| Invalid config (`node: "x; reboot"`) → 422 | PASS |
| DB audit contains login, session_disconnect, config_apply, admin_create/delete | PASS |

## Separation drill (spec §30)

Lab subscriber online, pinging 1.1.1.1 through CGNAT every 200 ms, then:
PostgreSQL stopped → login returned **503** (API stayed up) → bng-api stopped → `bngctl
health`: ACCEL-PPP/PPPoE/NAT PASS, API FAIL, Database FAIL → both restarted.

**150/150 pings, 0 % loss**, RTT avg 30.2 ms / max 31.1 ms. After the restart: API PASS,
Database PASS. `lab-test.sh` (Phases 1–4): **PASS**.

## Found and fixed

1. `bngctl config history`: long admin names ran into the source column.
2. An API apply of an unchanged config created a version (v10) that only re-formatted the
   YAML. Now it returns `no changes`.
3. `bngctl health` right after install: API FAIL from the start-up race. The installer now
   waits for `/api/livez`.

## Open

- Runs as root, with systemd hardening (ProtectSystem=strict, and writes only to
  /etc/bng-platform and /var/lib/bng-platform). Split it into an unprivileged API plus a
  privileged worker before exposing it beyond localhost.
- WebSocket `/ws/sessions|metrics|system` is deferred to Phase 6, where the GUI consumes it.
- Session history, `bng_nodes` and the other spec tables arrive with the phases that use
  them (history worker, Phase 7 multi-BNG).
- TLS and the reverse proxy: Phase 6.
