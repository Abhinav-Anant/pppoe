# Demo stage

bng01 is staged so the console can be shown to customers with a live, believable network behind it.
Everything below is simulated on the lab and should be presented as a demo, not as field data.

## What is running

| Part | What it is |
|---|---|
| `bng-radius-lab.service` | FreeRADIUS 3 in namespace `bngradius`. The gateway authenticates against it exactly as it would against Jaze. Plans are 50, 100, 200 and 500 Mbit/s. |
| `bng-demo.service` | ~1,200 simulated subscribers (userspace PPPoE clients, one username and MAC each) across the four plans. |
| (the same service) | 20 real subscribers (pppd, each with its own MAC) generating varying traffic within their plan to a simulated internet (namespace `bnginet`). Mostly downloads, 5–35% of the plan at a time. |
| (the same service) | Churn: active subscribers log out and back in every 1–5 minutes. |
| Gateway | accel-ppp with `aaa: radius`, per-plan shaping, the forward filter and SNAT: the real data path. |

**Load:** ~0.3–0.5 Gbit/s of traffic at ~1–3% CPU on bng01's 12 vCPUs.

**The internet side is simulated.** Demo traffic uses `bnginet0`, not the real uplink `ens18`. The
dashboard therefore shows subscriber traffic on the path, and the uplink counters (near zero)
separately in its footer. The NAT tile counts only the real uplink's NAT, so it stays at 0 flows.

## Presenting

1. Open an SSH tunnel from your laptop and browse to `http://localhost:8080`:

   ```bash
   ssh -L 8080:127.0.0.1:8080 bng01
   ```

2. Sign in with an administrator account. For a customer session, create a read-only demo login:

   ```bash
   sudo bngctl admin create demo --role read_only
   ```

   Delete it afterwards with `sudo bngctl admin delete demo`.
3. **Day** theme is the default and projects best. **Night** is in the sidebar footer.
4. **Brand name.** Set it in `/etc/bng-platform/branding.json`, then reload the page:

   ```json
   {"name": "Your ISP", "tagline": "Broadband network gateway"}
   ```

**Suggested tour:**
1. **Network overview:** the live path (subscribers → gateway → internet), plan mix, top subscribers.
2. **Subscribers:** search `home100`, open one session and watch its live rate against its plan.
3. **RADIUS:** the server connection, plan speed formats and write-only secrets.
4. **Plans & shaping**, **NAT**, **Monitoring** (15-minute live graphs).
5. **Configuration:** validate, apply, history and rollback, plus the **Audit log**.
6. **Performance:** the Phase 8 benchmark matrix and tuning.

**Live actions** that are safe during the demo:
- disconnect an active subscriber (`home…` or `biz…`) from its session page: it logs back in within seconds. The
  simulated `sub…` subscribers stay offline until the demo restarts.
- `radclient` CoA from the lab RADIUS (see [radius.md](radius.md)) to change a live rate.

## Start, stop, reset

```bash
sudo systemctl stop bng-demo           # removes every simulated subscriber, namespace, rule and process
sudo systemctl start bng-demo
sudo systemctl enable bng-demo         # keep the demo running across reboots (currently: enabled)
```

Stopping the demo leaves the gateway, RADIUS lab and configuration as they are.

To return to the plain lab:
1. `sudo systemctl disable --now bng-demo bng-radius-lab`.
2. Apply a config with `aaa: lab` (or roll back in Configuration).
3. Run `sudo bngctl firewall apply` and `sudo bngctl firewall confirm`.

The Phase 8 benchmark needs `aaa: lab` and no demo running.
