# easywall integration

[easywall](https://github.com/jp1337/easywall) (GPL-3.0, Go) manages a host firewall in its own
nftables table `inet easywall`, through a web UI, with a 120 s confirm-or-revert on every apply.
In bng-platform it is the **host input firewall**; bng-platform keeps the **data plane**
(subscriber forward filter, CGNAT, CAKE).

| Hook | Table / chain | Owner | Policy |
|---|---|---|---|
| input | `inet easywall` input | easywall (UI) | drop, once configured |
| input | `inet bng_filter` input | bng-platform — **removed** when `firewall.host_input: easywall` | drop |
| forward | `inet easywall` forward | easywall, `routing.mode = "open"` → no rules | accept |
| forward | `inet bng_filter` forward | bng-platform | drop; subscribers → uplink, established |
| postrouting | `ip bng_nat` | bng-platform | SNAT pools |

## Why routing.mode must be "open"

Several tables may have base chains at the same hook. A packet must survive **every** one:
an accept in one table does not skip another, and a drop anywhere is final. easywall's default
`routing.mode = "closed"` puts a drop-policy forward chain at the forward hook, which would drop
every subscriber packet `bng_filter` accepted. With `"open"` (and Docker coexistence off)
easywall's forward chain is empty with policy accept — verified in its
`buildForwardChain` (v2.25.0) — so routed traffic is decided by `bng_filter` alone.

`bngctl health` → `easywall` **FAIL** if `/etc/easywall/easywall.toml` says anything but
`"open"`.

## Install

```bash
sudo bash /opt/bng-platform/src/scripts/easywall-install.sh
```

- Downloads `easywall_amd64.deb` **v2.25.0**, verifies it against a pinned SHA-256
  (`f4b1ea13…f0c1`, GitHub's release-asset digest; the release's `checksums.txt` lists only the
  tarballs). Upgrading means changing the version and the hash together.
- Seeds `/etc/easywall/easywall.toml` from the package's own template with
  `routing.mode = "open"` **before** the package's postinst starts the core (postinst keeps an
  existing file), and refuses to continue if the mode is not `"open"`.
- A never-configured easywall does not enforce anything (`everConfigured`), so installing it
  changes no rules.

## Hand-over of host input to easywall

1. Tunnel to the UI (bng-platform's input chain still blocks 12227 from outside):
   `ssh -L 12227:localhost:12227 bng01`, open `https://localhost:12227/firstrun`.
   Setup token: `sudo journalctl -u easywall-web -g 'setup token' | tail -1`.
2. Create the admin account (this is yours to do — credentials are never handled by automation).
3. In easywall open **TCP 22**, and **UDP 3799 from the Jaze servers** once RADIUS CoA is used.
   Open 12227 only if you want the UI reachable without the tunnel (then restrict its source).
4. Apply, and confirm from a **new** SSH session within 120 s.
5. Set `firewall: {host_input: easywall}` in `/etc/bng-platform/config.yaml`,
   `sudo bngctl config apply`, `sudo bngctl firewall apply`, confirm from a new SSH session.
   bng_filter's input chain is gone; easywall alone filters host input.

Until step 5, both input chains are active and a port must be open in both.

## What easywall must not be used for here

- Forwarding / port-forward rules or Docker coexistence — they would put rules into the forward
  chain that sees subscriber traffic.
- `routing.mode` other than `"open"`.
