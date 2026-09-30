# Management API (`bng-api`)

FastAPI service on each node, `127.0.0.1:8080`, run by `bng-api.service`. It is management
plane only: it calls the same code as `bngctl` (accel-cmd adapter, safe apply pipeline, health).
If it or PostgreSQL stops, PPPoE sessions and forwarding continue (verified, see
[phase5-results.md](phase5-results.md)).

Interactive OpenAPI docs: `http://localhost:8080/api/docs` (schema: `/api/openapi.json`).

## Access

It binds to localhost only. Reach it through SSH until the Phase 6 reverse proxy (TLS) exists:

```bash
ssh -L 8080:127.0.0.1:8080 bng01
```

First administrator (you type the password; it is stored as a scrypt hash):

```bash
sudo bngctl admin create <name> --role super_admin
sudo bngctl admin list | passwd <name> | disable <name> | enable <name> | delete <name>
```

## Authentication

| | |
|---|---|
| Login | `POST /api/auth/login` `{"username","password"}` with header `X-Requested-With: bng` |
| Session | cookie `bng_session`: HttpOnly, SameSite=Strict, Secure, path `/api`; 30 min idle, 12 h max. Only its SHA-256 is stored |
| CSRF | every POST/PUT/PATCH/DELETE needs `X-CSRF-Token` = `csrf_token` from login (or from `GET /api/auth/me`, which re-issues it) |
| Brute force | 5 failed logins per username or 20 per IP in 15 min → 429 |
| Rate limit | 20 req/s per client IP, burst 60 → 429 |
| Audit | logins (ok / failed / throttled), logout, password changes, user changes, disconnects, config apply/rollback → `audit_logs` table |

## Roles

| Permission | super_admin | network_admin | noc_operator | read_only |
|---|:-:|:-:|:-:|:-:|
| view_sessions | ✓ | ✓ | ✓ | ✓ |
| disconnect_sessions | ✓ | ✓ | ✓ | |
| view_logs | ✓ | ✓ | ✓ | |
| apply_config, rollback_config | ✓ | ✓ | | |
| change_network, change_radius, change_nat, change_qos | ✓ | ✓ | | |
| manage_users | ✓ | | | |

A config apply requires `apply_config` **plus** one permission per changed top-level
section: `shaper` → `change_qos`, `radius`/`aaa` → `change_radius`, `nat` → `change_nat`,
anything else → `change_network`. `POST /api/config/validate` reports what a candidate needs.
Status endpoints are readable by any logged-in role.

## Endpoints

| Method | Path | Permission |
|---|---|---|
| GET | `/api/system/status` — node, accel version + `show stat`, host load/memory | login |
| GET | `/api/system/tuning` — CPU/IRQ/NIC/softnet diagnostics and tuning recommendations (apply is CLI-only) | login |
| GET | `/api/benchmarks`, `/api/benchmarks/{name}` — results written by `bngctl benchmark run` | login |
| GET | `/api/health` — the `bngctl health` checks | login |
| GET | `/api/interfaces` — NICs with counters and queue counts | login |
| GET | `/api/metrics` — point-in-time counters (sessions, subscriber bytes, uplink, conntrack, NAT) | login |
| GET | `/api/sessions` — `search`, `state`, `duplicates`, `sort`, `desc`, `page`, `page_size` (max 1000) | view_sessions |
| GET | `/api/sessions/{sid}` | view_sessions |
| POST | `/api/sessions/{sid}/disconnect` `{"hard": false}` | disconnect_sessions |
| GET | `/api/pppoe/status`, `/api/ip-pools` (with used/usable) | login |
| GET | `/api/radius/status` — servers, Status-Server probe, accel radius stats; never the secret | login |
| GET | `/api/qos/status` — rate guard + active sessions per plan rate | login |
| GET / PUT | `/api/qos/config` — the `shaper` section | login / change_qos + apply_config |
| GET | `/api/nat/status`, `/api/nat/pools` | login |
| GET | `/api/config`, `/api/config/history` | login |
| GET | `/api/config/versions/{n}` (download YAML), `/api/config/versions/{n}/diff?against=m` | login |
| POST | `/api/config/validate` `{"config": {...}}` | login |
| POST | `/api/config/apply` `{"config": {...}, "allow_restart": false}` | apply_config + section permissions |
| POST | `/api/config/rollback` `{"version": null, "allow_restart": false}` | rollback_config |
| GET | `/api/audit` (DB: logins, API actions), `/api/audit/node` (node JSONL: CLI + API changes) | view_logs |
| GET / POST / PATCH | `/api/users`, `/api/users/{username}` | manage_users |
| POST | `/api/auth/login`, `/api/auth/logout`, `/api/auth/password`; GET `/api/auth/me` | — / login |

Session rows name direction explicitly: `upload_bytes` = received from the subscriber
(accel `rx` on `pppN`), `download_bytes` = sent to the subscriber. `duplicate` marks a
username or MAC seen in more than one live session. The list is read from accel-ppp at most
once per 2 s and paginated server-side.

Config applies go through the same pipeline as `bngctl config apply` (validate → backup →
reload → health gate → automatic rollback). They are serialised with the CLI by a file lock, and
recorded as versions with source `api:<client ip>`. A candidate with no real change returns
`no changes` without writing anything. An API apply stores `config.yaml` as normalised YAML,
so comments in a hand-edited file are not kept.

There is no endpoint that runs a shell command or passes operator text to `tc`/`nft`.
Firewall apply/confirm stays CLI-only (confirm-or-revert needs a second SSH session).

## Database

PostgreSQL 16 on the node, database `bng_platform`, role `bng_api` (password generated by
`install.sh`, stored only in `/etc/bng-platform/secrets/db.url`, 0600). Schema via Alembic:
`sudo bngctl db upgrade`. Tables so far: `admins`, `auth_sessions`, `audit_logs`. Roles and
permissions are fixed in code (`app/api/auth.py`). Node config versions stay on the node
(`/var/lib/bng-platform/versions`) so the data plane never needs the database.

## Test

```bash
sudo bash /opt/bng-platform/src/scripts/lab/api-smoke.sh   # throwaway admins, deleted afterwards
```
