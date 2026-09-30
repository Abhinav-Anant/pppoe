# Connecting a RADIUS server

The gateway works with any RFC 2865/2866 RADIUS server: Jaze, FreeRADIUS, radiusdesk, daloRADIUS,
Microsoft NPS or a billing platform. Nothing in the gateway is specific to one vendor.

Verified end to end against FreeRADIUS 3 on bng01 (`scripts/lab/radius-test.sh`: PASS). The test
covers:
- Access-Accept and Access-Reject;
- plan rate applied to the session;
- accounting Start, Interim-Update and Stop;
- CoA rate change;
- Disconnect-Request;
- rejection of a CoA or Disconnect with a wrong secret;
- all three plan-speed formats below.

## On the RADIUS server

1. **Add the gateway as a client (NAS).** Use the gateway's **NAS-IP-Address**, a shared secret
   (8–128 printable characters, no spaces or commas) and its **NAS-Identifier** (default: the node
   name).
2. **Return a plan speed in Access-Accept**, in one of these formats. accel-ppp 1.14.0 parses all of
   them natively:

| Format | Example reply | Notes |
|---|---|---|
| Filter-Id | `Filter-Id = "50000/25000"` | download/upload in kbit/s |
| MikroTik | `Mikrotik-Rate-Limit = "25M/50M"` | MikroTik order: upload/download; `M` = 1000 kbit |
| Cisco | `Cisco-AVPair = "lcp:interface-config#1=rate-limit output 50000000 …"` | Cisco rate-limit syntax |
| WISPr | `WISPr-Bandwidth-Max-Down = 50000000`, `…-Max-Up = 25000000` | bit/s |

   Use `M` rather than `G` for plans of 1 Gbit/s and above. accel-ppp 1.14.0 reads `1G` as
   10 Gbit/s; write `1000M`.
3. **Optional reply attributes:**
   - `Framed-IP-Address` (static IP) or `Framed-Pool` (the name of one of the gateway's pools);
   - `Session-Timeout` and `Idle-Timeout`;
   - `Acct-Interim-Interval`, which overrides the gateway default.
4. **Optional: CoA and Disconnect.**
   - The server sends to the gateway's CoA address, port 3799, with the same secret.
   - It identifies the session by `User-Name`, `Acct-Session-Id` or `Framed-IP-Address`.
   - A CoA that carries a new rate attribute changes the live session's speed without a reconnect.

## On the gateway

Use either the console (**RADIUS → Connect a RADIUS server**) or config.yaml plus the CLI:

```yaml
aaa: radius
radius:
  servers:
    - {address: 10.0.0.10}                       # primary
    - {address: 10.0.0.11, backup: true}         # used only when the primary fails
  nas_identifier: bng01
  nas_ip_address: 10.0.0.1                       # the address the RADIUS server sees
  coa_listen: 10.0.0.1
  status_server: true                            # false if the server ignores Status-Server
shaper:
  attr: Mikrotik-Rate-Limit                      # or Filter-Id (default); see docs/configuration.md
  vendor: Mikrotik
```

```bash
sudo bngctl config apply /path/to/config.yaml --allow-restart    # switching aaa restarts accel-ppp
sudo bngctl radius secret                                        # default secret, read without echo
sudo bngctl radius secret --server 10.0.0.11                     # a different secret for one server
sudo bngctl firewall apply                                       # opens 3799/udp to the servers only;
sudo bngctl firewall confirm                                     # confirm from a NEW ssh session
sudo bngctl radius test --user alice                             # sends a real Access-Request
```

**Secrets:**
- They are write-only. They live in `/etc/bng-platform/secrets/` (mode 0600) and are never shown,
  logged, audited or kept in configuration history.
- The console only shows whether one is set.
- Setting one re-applies the running configuration. If that fails, the old secret is restored.

**Per-server tuning** (config.yaml, per entry in `servers:`):
- `weight`: load share among primaries;
- `req_limit`: maximum requests in flight;
- `max_fail`: timeouts before failover;
- `fail_timeout`: seconds before a failed server is retried.

**Other options under `radius:`:**
- `bind`: the source address for requests;
- `strip_realm` / `default_realm`: realm handling for `user@realm`.

**Health.** The RADIUS health check sends Status-Server (RFC 5997) to each server with that server's
own secret. Servers that do not implement it set `status_server: false`. The check then reports SKIP
instead of FAIL, and `bngctl radius test --user` checks authentication for real.

## Lab server for testing and demos

```bash
sudo bash scripts/lab/radius-lab.sh up      # FreeRADIUS 3 in netns bngradius (10.255.0.2), NAS 10.255.0.1
sudo bash scripts/lab/radius-test.sh        # the end-to-end test above
```

The lab plans carry all three attribute formats, so any shaper choice works. They are in
`/etc/freeradius/bng-lab/mods-config/files/authorize`, and the shared password is in
`/etc/bng-platform/lab/password`.

## Found while doing this

- **The shaper kept an old vendor across reloads.** accel-ppp 1.14.0 only assigns the shaper
  `vendor` when the option is present. After a reload that removed `vendor=WISPr`, it kept looking
  up Filter-Id under WISPr and disabled all shaping. The renderer now always writes `vendor=`
  (`0` = none).
- **Validation errors echoed secrets back.** The API's 422 responses no longer include the rejected
  input, which could have been a password or a RADIUS secret.
