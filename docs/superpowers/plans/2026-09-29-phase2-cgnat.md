# Phase 2 — nftables CGNAT Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Steps use `- [ ]`.

**Goal:** Subscribers (PPPoE, 100.64.0.0/10 space) reach the Internet through configurable nftables SNAT pools, with a stateful forward filter, TCP MSS clamping, conntrack visibility and a documented NAT-logging export path.

**Architecture:** The node model gains an optional `nat` section (pools of subscriber networks → public address range + port range). `app/networking/firewall.py` renders one nftables file containing the existing `inet bng_filter` table (input + new forward chain) and a new `ip bng_nat` table, applied with the Phase 1 confirm-or-revert mechanism. Forwarding is enabled by a sysctl drop-in from the installer. `bngctl nat status` reads nft JSON counters and conntrack usage. NAT logging is designed (doc), not built.

**Tech Stack:** as Phase 1 + nftables `snat … persistent`, conntrack-tools (debug / future logging).

**Spec:** user brief §17 (NAT/CGNAT), §18 (NAT logging), §25 (safety), §27 (no arbitrary nft input), Phase 2.

## Global Constraints

- No MASQUERADE; explicit SNAT pools (`snat to A[-B]:pmin-pmax persistent`).
- `persistent` so one subscriber keeps one public IP (address pairing, needed for CGNAT apps/logging).
- Only rendered, validated rules reach `nft`; operator input is typed model fields, never nft text.
- Every ruleset change: `nft -c` check → load under 120 s auto-revert → confirm from a new SSH session.
- NAT logs never go to PostgreSQL; data plane never depends on a log sink.
- bng01 has one public IPv4 (43.229.72.90): the lab pool uses it. No capacity claim from this VM.

---

### Task 1: NAT model

**Files:** Modify `backend/app/config/model.py`, `system/config.example.yaml`; Test `backend/tests/test_model.py`

**Produces:** `NatPool(name, subscribers: list[IPv4Network], public_start, public_end=None, port_min=1024, port_max=65535)` with `.snat_target() -> str` (`"A:1024-65535"` or `"A-B:1024-65535"`); `Nat(pools, mss_clamp=True)`; `BngConfig.nat: Nat | None`; `BngConfig.subscriber_networks() -> list[IPv4Network]` (NAT subscriber nets if NAT, else IP-pool nets).

- [ ] Tests (append to `test_model.py`):

```python
NAT = {"pools": [{"name": "lab", "subscribers": ["100.64.0.0/24"], "public_start": "192.0.2.1"}]}


def test_nat_defaults_and_target(base_cfg):
    base_cfg["nat"] = NAT
    cfg = BngConfig.model_validate(base_cfg)
    assert cfg.nat.mss_clamp and cfg.nat.pools[0].snat_target() == "192.0.2.1:1024-65535"
    assert [str(n) for n in cfg.subscriber_networks()] == ["100.64.0.0/24"]


def test_nat_range_target(base_cfg):
    base_cfg["nat"] = {"pools": [dict(NAT["pools"][0], public_end="192.0.2.8", port_min=2000, port_max=3000)]}
    assert BngConfig.model_validate(base_cfg).nat.pools[0].snat_target() == "192.0.2.1-192.0.2.8:2000-3000"


@pytest.mark.parametrize("patch, msg", [
    ({"public_end": "192.0.1.1"}, "public_end"),
    ({"port_min": 5000, "port_max": 4000}, "port"),
    ({"public_start": "100.64.0.9"}, "inside subscriber pool"),
])
def test_nat_pool_rejected(base_cfg, patch, msg):
    base_cfg["nat"] = {"pools": [dict(NAT["pools"][0], **patch)]}
    with pytest.raises(ValidationError, match=msg):
        BngConfig.model_validate(base_cfg)


def test_nat_overlapping_subscribers_rejected(base_cfg):
    p = NAT["pools"][0]
    base_cfg["nat"] = {"pools": [p, dict(p, name="b", subscribers=["100.64.0.128/25"])]}
    with pytest.raises(ValidationError, match="overlap"):
        BngConfig.model_validate(base_cfg)
```

- [ ] Run → FAIL (`nat` extra field forbidden).
- [ ] Implement in `model.py` (before `BngConfig`):

```python
class NatPool(Strict):
    name: str = Field(pattern=TOKEN)
    subscribers: list[IPv4Network] = Field(min_length=1)
    public_start: IPv4Address
    public_end: IPv4Address | None = None
    port_min: int = Field(default=1024, ge=1, le=65535)
    port_max: int = Field(default=65535, ge=1, le=65535)

    @model_validator(mode="after")
    def _ranges(self) -> NatPool:
        if self.public_end is not None and self.public_end < self.public_start:
            raise ValueError(f"nat pool {self.name!r}: public_end is below public_start")
        if self.port_min > self.port_max:
            raise ValueError(f"nat pool {self.name!r}: port_min is above port_max")
        return self

    def snat_target(self) -> str:
        end = self.public_end if self.public_end not in (None, self.public_start) else None
        ips = f"{self.public_start}-{end}" if end else str(self.public_start)
        return f"{ips}:{self.port_min}-{self.port_max}"


class Nat(Strict):
    pools: list[NatPool] = Field(min_length=1)
    mss_clamp: bool = True

    @model_validator(mode="after")
    def _consistent(self) -> Nat:
        names = [p.name for p in self.pools]
        if len(set(names)) != len(names):
            raise ValueError("nat pool names must be unique")
        nets = [(p.name, n) for p in self.pools for n in p.subscribers]
        for (pa, a), (pb, b) in combinations(nets, 2):
            if a.overlaps(b):
                raise ValueError(f"nat subscriber networks {a} ({pa}) and {b} ({pb}) overlap")
        return self
```

In `BngConfig`: field `nat: Nat | None = None`; in `_aaa` validator add

```python
        if self.nat:
            pool_nets = [p.network for p in self.ip_pools.pools]
            for p in self.nat.pools:
                for addr in filter(None, (p.public_start, p.public_end)):
                    if any(addr in n for n in pool_nets + p.subscribers):
                        raise ValueError(f"nat pool {p.name!r}: public address {addr} is inside subscriber pool")
```

and method

```python
    def subscriber_networks(self) -> list[IPv4Network]:
        if self.nat:
            return [n for p in self.nat.pools for n in p.subscribers]
        return [p.network for p in self.ip_pools.pools]
```

Example config: append

```yaml
nat:
  pools:
    - name: lab
      subscribers: [100.64.0.0/24]
      public_start: 192.0.2.1   # install.sh sets this to the uplink's IPv4
```

- [ ] Run → PASS. Commit `feat: CGNAT pool model`.

### Task 2: nftables forward filter + SNAT render, NAT status

**Files:** Modify `backend/app/networking/firewall.py`, `backend/app/monitoring/health.py`, `backend/app/cli.py`; Test `backend/tests/test_nat.py`

**Produces:** `firewall.render(cfg)` now also emits `chain forward` and `table ip bng_nat`; `firewall.parse_nat_counters(json_text) -> list[dict(pool, packets, bytes)]`; `health.check_nat(cfg)`; `bngctl nat status`.

- [ ] Tests `backend/tests/test_nat.py`:

```python
import json

from app.config.model import BngConfig
from app.monitoring import health
from app.networking import firewall

NAT = {"pools": [{"name": "lab", "subscribers": ["100.64.0.0/24"], "public_start": "192.0.2.1"}]}


def test_render_forward_and_snat(base_cfg):
    base_cfg["nat"] = NAT
    text = firewall.render(BngConfig.model_validate(base_cfg))
    fwd = text.split("chain forward")[1].split("}")[0]
    assert "policy drop" in fwd
    assert "tcp flags & (syn | rst) == syn tcp option maxseg size set rt mtu" in fwd
    assert 'iifname "ppp*" oifname "eth0" ip saddr { 100.64.0.0/24 } counter accept' in fwd
    assert "table ip bng_nat\ndelete table ip bng_nat\n" in text
    assert ('oifname "eth0" ip saddr { 100.64.0.0/24 } counter snat to 192.0.2.1:1024-65535 persistent '
            'comment "nat-pool lab"') in text
    assert "masquerade" not in text


def test_render_without_nat_still_removes_stale_nat_table(base_cfg):
    text = firewall.render(BngConfig.model_validate(base_cfg))
    assert "table ip bng_nat\ndelete table ip bng_nat\n" in text
    assert "snat" not in text
    assert "ip saddr { 100.64.0.0/24 }" in text          # forward still limited to subscriber nets


def test_parse_nat_counters():
    doc = {"nftables": [
        {"metainfo": {"json_schema_version": 1}},
        {"table": {"family": "ip", "name": "bng_nat", "handle": 1}},
        {"rule": {"family": "ip", "table": "bng_nat", "chain": "postrouting", "handle": 3,
                  "comment": "nat-pool lab",
                  "expr": [{"match": {}}, {"counter": {"packets": 5, "bytes": 420}}, {"snat": {}}]}},
    ]}
    assert firewall.parse_nat_counters(json.dumps(doc)) == [{"pool": "lab", "packets": 5, "bytes": 420}]


def test_nat_health_skip_without_nat(base_cfg):
    assert health.check_nat(BngConfig.model_validate(base_cfg)).status == "SKIP"
```

- [ ] Run → FAIL.
- [ ] Implement in `firewall.py`: replace `render` body so that it returns

```python
def render(cfg: BngConfig) -> str:
    up = cfg.uplink
    subs = ", ".join(str(n) for n in cfg.subscriber_networks())
    dae = ""
    if cfg.aaa == "radius":
        srcs = ", ".join(str(n) for n in cfg.radius.dae_sources())
        dae = f"        udp dport {cfg.radius.coa_port} ip saddr {{ {srcs} }} accept\n"
    mss = ("        tcp flags & (syn | rst) == syn tcp option maxseg size set rt mtu\n"
           if not cfg.nat or cfg.nat.mss_clamp else "")
    text = (
        "# generated by bng-platform - do not edit\n"
        "table inet bng_filter\n"
        "delete table inet bng_filter\n"
        "table ip bng_nat\n"
        "delete table ip bng_nat\n"
        "table inet bng_filter {\n"
        "    chain input {\n"
        "        type filter hook input priority filter; policy drop;\n"
        '        iif "lo" accept\n'
        "        ct state established,related accept\n"
        "        ct state invalid drop\n"
        "        meta l4proto { icmp, ipv6-icmp } accept\n"
        "        tcp dport 22 accept\n"
        f"{dae}"
        "    }\n"
        "    chain forward {\n"
        "        type filter hook forward priority filter; policy drop;\n"
        f"{mss}"
        "        ct state established,related accept\n"
        "        ct state invalid drop\n"
        f'        iifname "ppp*" oifname "{up}" ip saddr {{ {subs} }} counter accept\n'
        "    }\n"
        "}\n"
    )
    if cfg.nat:
        rules = "".join(
            f'        oifname "{up}" ip saddr {{ {", ".join(map(str, p.subscribers))} }} counter '
            f'snat to {p.snat_target()} persistent comment "nat-pool {p.name}"\n'
            for p in cfg.nat.pools)
        text += ("table ip bng_nat {\n"
                 "    chain postrouting {\n"
                 "        type nat hook postrouting priority srcnat; policy accept;\n"
                 f"{rules}"
                 "    }\n"
                 "}\n")
    return text


def parse_nat_counters(json_text: str) -> list[dict]:
    out = []
    for obj in json.loads(json_text).get("nftables", []):
        rule = obj.get("rule")
        if not rule or not rule.get("comment", "").startswith("nat-pool "):
            continue
        c = next((e["counter"] for e in rule["expr"] if "counter" in e), {})
        out.append({"pool": rule["comment"].removeprefix("nat-pool "),
                    "packets": c.get("packets", 0), "bytes": c.get("bytes", 0)})
    return out


def nat_counters() -> list[dict]:
    p = subprocess.run(["nft", "-j", "list", "table", "ip", "bng_nat"], capture_output=True, text=True, timeout=10)
    return parse_nat_counters(p.stdout) if p.returncode == 0 else []
```

(add `import json`), and change `REVERT_SCRIPT` to also drop the NAT table:
`"nft delete table inet bng_filter; nft delete table ip bng_nat; if [ -f {RULES} ]; then nft -f {RULES}; fi; rm -f {PENDING}"`.

Update Phase 1 test `test_firewall_render` in `test_health.py`: `lab.splitlines()[1:3]` still `["table inet bng_filter", "delete table inet bng_filter"]` (unchanged).

`health.py`: add

```python
def check_nat(cfg: BngConfig) -> Check:
    if not cfg.nat:
        return Check("NAT", "SKIP", "no nat section")
    try:
        fwd = Path("/proc/sys/net/ipv4/ip_forward").read_text().strip() == "1"
    except OSError:
        fwd = False
    loaded = _run("nft", "list", "table", "ip", "bng_nat").returncode == 0
    return _ok("NAT", fwd and loaded, "" if fwd and loaded else
               f"ip_forward={'1' if fwd else '0'}, bng_nat {'loaded' if loaded else 'missing'}")
```

and in `run_all` replace `Check("NAT", "SKIP", "Phase 2")` with `check_nat(cfg)`.

`cli.py`: parser `nat = sub.add_parser("nat").add_subparsers(dest="action", required=True); nat.add_parser("status")`; dispatch:

```python
    if a.cmd == "nat":
        cfg = load(paths.config)
        print(health.format_report([health.check_nat(cfg), health.check_conntrack()]))
        for p in (cfg.nat.pools if cfg.nat else []):
            print(f"pool {p.name:<12} {', '.join(map(str, p.subscribers))} -> {p.snat_target()}")
        for c in firewall.nat_counters():
            print(f"counter {c['pool']:<10} packets={c['packets']} bytes={c['bytes']}")
        return 0
```

- [ ] Run all tests → PASS. Commit `feat: stateful forward filter, MSS clamp and SNAT pools`.

### Task 3: installer + lab NAT test

**Files:** Modify `scripts/install.sh`, `scripts/lab/lab-up.sh`, `scripts/lab/lab-test.sh`

- [ ] `install.sh`: add `conntrack` to apt packages; after "Configuration" block add

```bash
log "Forwarding"
printf 'net.ipv4.ip_forward = 1\n' > /etc/sysctl.d/90-bng-platform.conf
sysctl -q -p /etc/sysctl.d/90-bng-platform.conf
```

and in the new-config branch also set the NAT public address:

```bash
  PUBLIC=$(ip -4 -o addr show dev "$UPLINK" scope global | awk '{split($4,a,"/"); print a[1]; exit}')
  sed -i "s/public_start: 192.0.2.1 .*/public_start: ${PUBLIC}   # uplink IPv4 at install time/" "$ETC/config.yaml"
```

`uninstall.sh`: `rm -f /etc/sysctl.d/90-bng-platform.conf; sysctl -q -w net.ipv4.ip_forward=0; nft delete table ip bng_nat 2>/dev/null || true`.

- [ ] `lab-up.sh`: give the namespace its own resolver (host uses 127.0.0.53, unreachable from the netns):

```bash
install -d /etc/netns/"$NS"
echo "nameserver 8.8.8.8" > /etc/netns/"$NS"/resolv.conf
```

- [ ] `lab-test.sh`: before the disconnect step add

```bash
if nft list table ip bng_nat >/dev/null 2>&1; then
  EXPECT=$(/opt/bng-platform/venv/bin/python -c \
    'from app.config.model import load; print(load("/etc/bng-platform/config.yaml").nat.pools[0].public_start)')
  ip -n "$NS" route replace default dev ppp0
  ip netns exec "$NS" ping -c 3 -W 2 1.1.1.1 >/dev/null && step "Internet ping via NAT" PASS || step "Internet ping via NAT" FAIL
  ip netns exec "$NS" getent hosts api.ipify.org >/dev/null && step "DNS (UDP) via NAT" PASS || step "DNS (UDP) via NAT" FAIL
  SEEN=$(ip netns exec "$NS" curl -s -4 --max-time 10 https://api.ipify.org)
  step "HTTPS via CGNAT (seen as ${SEEN:-none})" "$([ "$SEEN" = "$EXPECT" ] && echo PASS || echo FAIL)"
  conntrack -L --src "$CLIENT_IP" --src-nat 2>/dev/null | grep -q "dst=$EXPECT" \
    && step "conntrack SNAT entry" PASS || step "conntrack SNAT entry" FAIL
  nft list chain inet bng_filter forward | grep -q "maxseg size set rt mtu" \
    && step "MSS clamp rule" PASS || step "MSS clamp rule" FAIL
fi
```

- [ ] `bash -n` all scripts. Commit `feat: forwarding sysctl and lab NAT checks`.

### Task 4: Deploy, apply, verify, document

**Files:** Create `docs/nat-logging.md`, `docs/phase2-results.md`, `backend/tests/fixtures/nft_bng_nat.json`; Modify `backend/tests/test_nat.py`, `docs/configuration.md`, `README.md`

- [ ] Deploy + install (`scripts/deploy.sh`, `install.sh`), add to `/etc/bng-platform/config.yaml` on bng01:

```yaml
nat:
  pools:
    - name: lab
      subscribers: [100.64.0.0/24]
      public_start: 43.229.72.90
```

`sudo bngctl config diff` (expect: no accel-ppp.conf change), `sudo bngctl config apply`, `sudo bngctl firewall apply`, confirm from a new SSH session, `sudo bngctl health` → `NAT PASS`.
- [ ] `sudo bash scripts/lab/lab-test.sh` → all PASS incl. NAT steps, `HTTPS via CGNAT (seen as 43.229.72.90)`.
- [ ] Capture real `nft -j list table ip bng_nat` into the fixture; add `test_parse_real_nft_json` asserting one `lab` counter with `packets > 0`.
- [ ] Revert drill: temporarily render a pool whose SNAT would break nothing but is unconfirmed → confirm the timer restores the confirmed rules (same as Phase 1 drill, now with both tables).
- [ ] `docs/nat-logging.md`: design (below). `docs/phase2-results.md`: measured results. `docs/configuration.md`: `nat` keys. README status row.
- [ ] Commit `docs: Phase 2 results and NAT logging design`.

**NAT logging design (content for `docs/nat-logging.md`):** conntrack NEW/DESTROY events carry original src/sport and translated src/sport, proto, timestamps. A separate, optional `bng-natlog` service subscribes via netlink (`conntrack -E -e NEW,DESTROY -o timestamp,extended` or ulogd2 NFCT) and ships records to an external sink (IPFIX collector / syslog-TLS / ClickHouse). Requirements: `nf_conntrack_timestamp=1`; bounded in-memory queue with drop counter (never block the data plane); never PostgreSQL. Scaling alternative to per-flow logs: port-block allocation (each subscriber gets a fixed port block on a fixed public IP; log only block assignment per session) — nft `snat` has no native PBA, so this is a future design choice (deterministic mapping via per-subscriber-range rules or a dedicated CGNAT engine), to be decided with measured flow rates.
