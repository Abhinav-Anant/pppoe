# Subscriber rates from RADIUS (Gold / Silver / Bronze)

Priority is the subscriber's **plan rate**, dictated by Jaze in the Access-Accept (and changed
live by CoA). accel-ppp's `shaper` enforces it on the session's own `pppN` interface:
download with `tbf` on `pppN` egress, upload with an ingress `police` on `pppN`.
There is no shared queue and no per-subscriber class elsewhere; under uplink congestion
subscribers are not prioritised against each other (operator decision, 2026-09-29).

Example plans: Gold `1000M/1000M`, Silver `200M/200M`, Bronze `100M/50M`.

## Attribute and formats (accel-ppp 1.14.0 `shaper.c`)

`config.yaml` → `shaper.attr` (+ `shaper.vendor`) names the attribute accel-ppp reads.

| Jaze sends | `shaper:` config | Value format | Direction |
|---|---|---|---|
| `Filter-Id` (default) or any string attribute | `attr: Filter-Id` | `down/up`, e.g. `200M/50M`; also Cisco `rate-limit output …` / `input …` strings | first = download |
| `Mikrotik-Rate-Limit` | `vendor: Mikrotik`, `attr: Mikrotik-Rate-Limit` | `rx/tx` as MikroTik defines it, e.g. `50M/200M` | accel swaps it: rx = upload, tx = download |
| `Cisco-AVPair` | `vendor: Cisco`, `attr: Cisco-AVPair` | `rate-limit output <bps> …` (down), `rate-limit input <bps> …` (up) | explicit |
| an integer attribute | `attr: <name>` | kbit/s | same value both ways |

Units: plain numbers are **kbit/s**; suffix `K` ×1, `M` ×1000.

### Do not use the `G` suffix

accel-ppp 1.14.0 multiplies `G` by 10,000,000 instead of 1,000,000 (still so upstream on
2026-09-29). Measured on bng01:

| Sent | Applied | tc |
|---|---|---|
| `1G/1G` | `10000000/10000000` kbit | **10 Gbit** — effectively unlimited |
| `1000M/1000M` | `1000000/1000000` kbit | 1 Gbit |
| `100M/20M` | `100000/20000` kbit | 100 Mbit down |

Jaze profiles must express 1 Gbit and above in `M` (`1000M`, `2500M`).

## Guard: `Rate limits` health check

```yaml
shaper:
  attr: Filter-Id
  max_rate_mbit: 1000     # highest plan; anything above is reported
  require_rate: true      # a session without a RADIUS rate is reported
```

`bngctl health` reads every session's applied rate (`show sessions … rate-limit`) and FAILs
with the usernames if a rate exceeds `max_rate_mbit` (catches a `G` value) or, with
`require_rate`, if a session has none (it would be unshaped). The check only reports; it
does not disconnect.

## CoA

accel-ppp 1.14.0 handles RADIUS CoA for the shaper (`ev_radius_coa`): Jaze can change a live
session's rate without a reconnect. CoA must be allowed through the host input firewall from the
Jaze addresses (`radius.dae_allowed`, UDP 3799) — in easywall once it owns host input.

## Open (needs Jaze)

- Which attribute Jaze sends the plan in, and its exact string → set `shaper.attr`/`vendor`.
- Verify Access-Accept rate and a CoA rate change on a real Jaze test subscriber.
