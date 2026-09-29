# Web GUI (BNG Console)

React + TypeScript (Vite, Tailwind, Recharts) in `frontend/`. The built files are served by
`bng-api` itself from `/opt/bng-platform/web`, on the same origin as the API, so there is no
separate web server or CORS. The GUI is a client of the management API only; closing it or
stopping `bng-api` does not affect subscribers.

## Open it

```bash
ssh -L 8080:127.0.0.1:8080 bng01      # then http://localhost:8080
```

Log in with an account from `sudo bngctl admin create <name> --role super_admin`.

## Build and deploy

The node has no Node.js, so the GUI is built on the workstation and shipped with the deploy:

```bash
cd frontend && npm ci && npm run build   # type-checks, writes frontend/dist
scripts/deploy.sh                        # refuses to run without frontend/dist
ssh bng01 sudo bash /opt/bng-platform/src/scripts/install.sh   # installs /opt/bng-platform/web
```

Dev server with hot reload, against the node through the tunnel: `cd frontend && npm run dev`,
then open `http://localhost:5173`.

## Pages

| Route | Content |
|---|---|
| `/dashboard` | accel-ppp state, active sessions, logins/logouts per minute, subscriber and uplink Mbit/s and pps, CPU/softirq/load, RAM, conntrack, NIC errors/drops, duplicates, unshaped sessions; health checks; top 10/50/100 subscribers by current or peak rate; active sessions per plan rate |
| `/monitoring` | live graphs (throughput, sessions, pps, CPU/softirq, conntrack) and per-NIC rates |
| `/sessions` | server-side search, filter, sort and pagination, with live counters for the visible rows; CSV export of the whole filter; disconnect |
| `/sessions/:sid` | subscriber detail, current and peak rates, plan, live traffic graph, soft/hard disconnect |
| `/qos` | rate guard, sessions per plan, shaper settings (editable with `change_qos` + `apply_config`) |
| `/nat` | NAT/conntrack status and pools, with translated-flow counters |
| `/radius` | Status-Server probe, NAS settings and servers, accel radius stats (never the secret) |
| `/pppoe` | PPPoE interfaces and counters, IP pool use |
| `/interfaces` | NICs with live rates, queues, errors and drops |
| `/configuration` | edit `config.yaml` (comments are kept) → validate (generated accel-ppp.conf diff, permissions needed) → apply with health gate and automatic rollback |
| `/configuration/history` | versions, diff against the previous one, download, rollback |
| `/audit` | management audit (DB) and the node's audit (CLI + API) |
| `/users` | administrators: create, role, disable, password reset (`manage_users`) |
| `/system` | health checks (every 15 s), node and accel-ppp details, change your own password |

Menu entries and buttons follow the role's permissions; the API enforces them regardless.

## Live data

| Socket | Content |
|---|---|
| `/api/ws/metrics` | dashboard snapshot every 2 s. One snapshot is shared by all viewers (a single `show sessions` / `show stat` per 2 s) |
| `/api/ws/system` | health checks every 15 s |
| `/api/ws/sessions` | the client sends `{"sids": [...]}` for the rows it shows (max 1000); every 2 s it receives only rows whose counters or state changed, and `gone` for ended sessions |

Rates are counter deltas between two 2-second samples. A value appears after the second
sample; a counter reset gives no value rather than a spike. Peaks count from when bng-api
first saw the session. The graphs keep 15 minutes in the browser; long-term history belongs
in Prometheus/Zabbix (not built yet).

Sockets authenticate with the session cookie and reject any `Origin` other than the page's
own host. Sessions are re-checked every 30 messages, so a logout or expiry ends the stream.
Pages get `Content-Security-Policy: default-src 'self'` (inline styles allowed for the charts),
`frame-ancestors 'none'`.

## Not in this phase

- Connection history per subscriber (needs the session-history worker and table).
- Configurable alert thresholds (spec §43). Failing health checks show as a banner on
  every page.
- Other RADIUS reply attributes per session: accel-ppp 1.14.0 does not expose them.
- TLS / access beyond localhost (reverse proxy), `/bng` multi-node views (Phase 7),
  `/benchmark` (Phase 8).
