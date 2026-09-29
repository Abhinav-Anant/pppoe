# Phase 7 results — multi-BNG console and embedded easywall

Date: 2026-09-30. See [multi-bng.md](multi-bng.md) and [gui.md](gui.md).

**Only one BNG exists (bng01).** The console → node path was exercised by registering
bng01's own TLS listener (`bng-api-remote`, 127.0.0.1:8443) as a second node. The whole
remote path is real: certificate pinning, service token, acting-admin narrowing, HTTP and
WebSocket proxy, audit. Both "nodes" show the same accel-ppp data. A test with a second
physical or virtual BNG is still open.

## Unit tests

117 total, all pass. The Phase 7 tests cover:
- Bearer tokens: no CSRF needed; the acting role narrows the token and can't widen it; bad or
  revoked tokens are refused; `/api/auth/me` works for a token and password change is refused.
- The node registry: permissions, https-only URLs, fingerprint mismatch.
- The proxy: the acting admin's headers are forwarded; `auth/`, `nodes`, `fleet` and `ws/` paths
  are refused; CSRF is still enforced.
- Fleet views and cross-node search.
- The easywall rewrites (HTML/CSS/JS/redirect/cookie; regex literals like `/"/g` stay intact).
- The easywall proxy: login and `manage_firewall` required; our cookie is not forwarded;
  X-Frame-Options is replaced.

## Live: `scripts/lab/fleet-smoke.sh` — FLEET RESULT: PASS

| Step | Result |
|---|---|
| Service token created (network_admin); probe fingerprint = `bngctl tls fingerprint` | PASS |
| Register with a wrong fingerprint → 409; with a wrong token → 409; as read_only → 403 | PASS |
| Register with the confirmed fingerprint → 201; token and certificate files 0600 | PASS |
| `/api/fleet` shows both nodes reachable | PASS |
| Proxied GET system/status, health, sessions, qos/status, config, config/history | PASS |
| Proxy refuses `auth/*` | PASS |
| Subscriber search across nodes finds labuser on both | PASS |
| WebSocket `/api/nodes/<n>/ws/metrics` through the proxy | PASS |
| read_only disconnect via proxy → 403 (the node narrows the token) | PASS |
| super_admin disconnect via proxy → 200; session gone from accel-ppp | PASS |
| Node audit records `fleet-smoke-…@smoke-console-…` | PASS |
| Remove node (row and secret files gone) | PASS |

## Browser

- **All BNGs:** totals and the side-by-side comparison of both nodes.
- **Adding a node through the form:** the fingerprint was shown and matched
  `bngctl tls fingerprint`. After ticking the confirmation and pasting the token, the node was
  added with role network_admin.
- **Node selector:** switching to the remote node loaded its dashboard, health and top
  subscribers through the proxy and its WebSockets. Switching back worked.
- **Firewall page:** easywall's first-run wizard rendered inside the console. HTML, CSS, JS,
  fonts and icon were all 200 through `/easywall/`. The wizard was deliberately not completed:
  the account is the operator's to create.

Regression: `api-smoke.sh` PASS, `lab-test.sh` PASS, `bngctl health` all PASS (RADIUS SKIP,
aaa=lab).

## Changes on bng01

- `/etc/easywall/web.toml`: `bind_addr = "127.0.0.1:12227"` (was `0.0.0.0:12227`: all
  interfaces, first-run wizard open; it was shielded only by bng_filter's input policy) and
  `trusted_proxies = ["127.0.0.1/32"]`. A backup is in `web.toml.bng-backup`.
- `/etc/bng-platform/tls/api.{crt,key}` created; `bng-api-remote` listens on **127.0.0.1:8443**
  (`/etc/bng-platform/api-remote.env`). It stays loopback-only until a second node or central
  console needs it.
- Database schema 0002 (`api_tokens`, `bng_nodes`). No nodes or tokens are left registered.

## Found and fixed

1. `GET /api/auth/me` with a bearer token returned 500 (tried to rotate a cookie-session CSRF
   token). It now returns `csrf_token: null`; password change is refused for tokens.

## Open

- A second physical or virtual BNG, and a separate console node.
- The firewall rule letting a console reach a node's 8443: add it in easywall once easywall owns
  host input, or in bng_filter's input chain.
- The easywall console is embedded for the console's own node only.
