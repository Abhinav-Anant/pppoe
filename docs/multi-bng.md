# Multi-BNG

Any node's BNG console can manage other BNG nodes. Each node keeps running on its own:
its bng-api, its database, its config versions, its safe-apply pipeline. The console
only calls the node's API, so a console outage never affects another node's subscribers.

```text
 browser ──ssh tunnel──> console (bng01: bng-api + GUI)
                            │  HTTPS, pinned certificate, service token
                            ├──> bng02: bng-api-remote (TLS :8443) ──> its accel-ppp / nft
                            └──> bng03: bng-api-remote (TLS :8443) ──> ...
```

## Trust model

| | |
|---|---|
| Transport | HTTPS to the node's `bng-api-remote` listener. Self-signed certificate created by `install.sh` (`/etc/bng-platform/tls/api.crt`) |
| Node identity | The console pins the certificate at registration (TOFU): it trusts exactly that certificate. You confirm the fingerprint shown in the GUI against `sudo bngctl tls fingerprint` on the node |
| Console identity | A service token made on the node: `sudo bngctl token create <console-name> --role network_admin`. It is shown once; the node stores only its SHA-256. Stored on the console in `/etc/bng-platform/secrets/nodes/<node>.token` (0600), never in the database |
| Who is acting | The console sends `X-BNG-Admin` / `X-BNG-Role`. The node grants the token's role **narrowed** to that role, never widened. It audits the change as `<admin>@<token-name>` (e.g. `alice@console01`) in its own audit and config history |
| Revocation | `sudo bngctl token revoke <console-name>` on the node; *Remove* on the console's All BNGs page |

The console still checks its own login and CSRF before it forwards anything. It does not proxy
login, node management or fleet endpoints. The node checks permissions again.

## Set up a node for a console

On the new node, after `install.sh`:

```bash
# 1. listen for the console on a management address (not the subscriber side)
echo -e "BNG_API_REMOTE_HOST=10.0.0.2\nBNG_API_REMOTE_PORT=8443" | sudo tee /etc/bng-platform/api-remote.env
sudo bash /opt/bng-platform/src/scripts/install.sh       # enables bng-api-remote.service
# 2. let only the console reach it (bng_filter input, or easywall once it owns host input)
#    e.g. easywall: allow TCP 8443 from the console's address
# 3. a token for the console, and the fingerprint to compare
sudo bngctl token create console01 --role network_admin
sudo bngctl tls fingerprint
```

On the console: **All BNGs → Add a BNG**. Enter the name and `https://10.0.0.2:8443`, then
*Get certificate*. Compare the fingerprint, tick "it matches", paste the token and click *Add node*.
The console checks that the certificate matches and the token works before it saves anything.

## Using it

- The node selector at the top of the sidebar switches every page to that BNG: dashboard,
  sessions, QoS, config (edit, apply, rollback), audit and so on. Live data comes through the
  console's WebSocket proxy.
- **All BNGs** (`/bng`):
  - Totals, and a side-by-side comparison of every node: sessions, churn, subscriber and uplink
    throughput, pps, CPU/softirq, RAM, conntrack, NIC errors, and duplicate or unshaped sessions.
  - Unreachable nodes are shown as unreachable, never hidden.
  - A subscriber search across every node.
- The easywall console (Firewall page) is embedded for the console's own node only.

## API

| | |
|---|---|
| `GET /api/nodes` | this node + registered nodes |
| `POST /api/nodes/probe` `{url}` | certificate fingerprint (manage_nodes) |
| `POST /api/nodes` `{name, url, token, fingerprint}` | register (manage_nodes) |
| `DELETE /api/nodes/{name}` | remove (manage_nodes) |
| `GET /api/fleet` | every node's live snapshot |
| `GET /api/fleet/sessions?search=` | subscriber search on every node |
| `/api/nodes/{name}/<path>` | the node's `/api/<path>` (GET/POST/PUT/PATCH) |
| `/api/nodes/{name}/ws/{metrics,system,sessions}` | the node's WebSockets |

New permissions: `manage_nodes`, `manage_firewall` (super_admin and network_admin).
