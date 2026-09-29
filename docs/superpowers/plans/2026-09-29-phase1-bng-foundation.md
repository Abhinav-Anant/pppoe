# Phase 1 — BNG Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ACCEL-PPP 1.14.0 terminating real PPPoE sessions on bng01, driven by a validated config generator with backup / health-check / automatic rollback, plus `bngctl` (sessions, disconnect, config, health, RADIUS test, backup/restore, firewall).

**Architecture:** One Python package (`backend/app`) holds the pure logic: a Pydantic model of the node's desired state (`/etc/bng-platform/config.yaml`), a renderer to `accel-ppp.conf` whose output is checked against a directive allow-list taken from the 1.14.0 source, a single adapter that talks to `accel-cmd`, and a config manager that does validate → diff → backup → apply (reload or restart) → health → rollback. `bngctl` is a thin argparse front end. The data plane (accel-pppd, kernel PPPoE, tc) never depends on any of it at runtime. Functional testing uses an isolated veth + network-namespace "subscriber" because bng01 has only one NIC and it is public (see ARCHITECTURE_ASSESSMENT.md §9).

**Tech Stack:** Python ≥3.11 (VM: 3.12), pydantic 2, PyYAML, pytest; ACCEL-PPP 1.14.0 built from source; nftables 1.0.9; systemd; pppd 2.4.9 (lab subscriber only); iperf3 (lab only).

**Spec:** the user's "Production-Grade Linux BNG / PPPoE Concentrator" brief (conversation) + `ARCHITECTURE_ASSESSMENT.md` (facts this plan relies on).

## Global Constraints

- ACCEL-PPP version: **1.14.0** (git tag), no source patches.
- Never use an accel-ppp directive that is not in the 1.14.0 source/man page (Rule 7). Allow-list lives in `backend/app/accel/validate.py`.
- Never touch netplan / `ens18` configuration. PPPoE runs only on the lab veth `bnglab0` in Phase 1.
- No FreeRADIUS. The lab uses accel-ppp's own `chap-secrets` module, **lab mode only**.
- RADIUS secret never appears in `config.yaml`, `accel-ppp.conf`, versions, audit log, or log output. It lives in `/etc/bng-platform/secrets/radius.secret` (0600) and is rendered into `/etc/bng-platform/secrets/radius.conf` (0600), pulled in by `$include`.
- No generic command execution; subprocess calls use fixed argv lists, never `shell=True` with operator text.
- No per-subscriber tc classes created by us. Per-session limits are accel-ppp's `shaper` on each `pppN`.
- A config change that needs an accel-ppp restart (drops all sessions) is refused without `--allow-restart`.
- Health output never says PASS for something not built yet: it says SKIP.
- Repo root: `C:\Projects\PPPoE Server` (monorepo). `upstream/` is reference only, git-ignored.
- Tests run locally (`cd backend && python -m pytest -q`); system steps run on bng01 via `ssh bng01` (passwordless sudo).

## File map

| File | Responsibility |
|---|---|
| `backend/pyproject.toml` | package `app`, deps, `bngctl` entry point, pytest config |
| `backend/app/config/model.py` | `BngConfig` desired-state model + `load()` |
| `backend/app/accel/render.py` | `BngConfig` → `Rendered(main, secrets)` |
| `backend/app/accel/validate.py` | static check of accel-ppp.conf text against 1.14.0 allow-list |
| `backend/app/accel/cmd.py` | `AccelCmd` (only accel-cmd user), `parse_sessions`, `AccelService` |
| `backend/app/radius/probe.py` | stdlib RADIUS test client (Status-Server, Access-Request) |
| `backend/app/config/manager.py` | safe apply/rollback/versions/audit/backup/restore |
| `backend/app/monitoring/health.py` | health checks, report formatting |
| `backend/app/networking/interfaces.py` | NIC discovery from sysfs |
| `backend/app/networking/firewall.py` | host input firewall render + confirm-or-revert |
| `backend/app/cli.py` | `bngctl` |
| `system/config.example.yaml` | initial lab-mode node config |
| `system/systemd/accel-ppp.service` | accel-pppd unit |
| `scripts/install.sh`, `uninstall.sh`, `health-check.sh`, `deploy.sh` | install/remove/check/copy-to-node |
| `scripts/lab/lab-up.sh`, `lab-down.sh`, `lab-test.sh` | veth/netns subscriber lab + E2E test |
| `docs/installation.md`, `docs/configuration.md`, `docs/phase1-results.md`, `README.md` | docs |

---

### Task 1: Repo skeleton and config model

**Files:**
- Create: `.gitignore`, `.gitattributes`, `backend/pyproject.toml`, `backend/app/__init__.py`, `backend/app/config/__init__.py`, `backend/app/config/model.py`, `system/config.example.yaml`
- Test: `backend/tests/conftest.py`, `backend/tests/test_model.py`

**Interfaces:**
- Produces: `app.config.model.BngConfig`, `load(path) -> BngConfig`, `TOKEN`, `IFNAME`; sub-models `PPPoE`, `PPPoEInterface`, `PPP`, `IpPools`, `IpPool`, `Radius`, `RadiusServer`, `Shaper`; `Radius.dae_sources() -> list[IPv4Network]`. Test helpers `base_cfg` fixture (dict), `radius_cfg` fixture (dict), `write_cfg(path, dict) -> Path`.

- [ ] **Step 1: git init and skeleton files**

```bash
cd "/c/Projects/PPPoE Server" && git init -q && mkdir -p backend/app/config backend/app/accel backend/app/radius backend/app/monitoring backend/app/networking backend/tests system/systemd scripts/lab docs
```

`.gitignore`:
```
upstream/
__pycache__/
*.egg-info/
.pytest_cache/
.venv/
build/
```

`.gitattributes`:
```
* text=auto eol=lf
```

`backend/pyproject.toml`:
```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "bng-platform"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["pydantic>=2.5,<3", "PyYAML>=6"]

[project.optional-dependencies]
test = ["pytest>=8"]

[project.scripts]
bngctl = "app.cli:main"

[tool.setuptools.packages.find]
include = ["app*"]

[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
```

Empty files: `backend/app/__init__.py`, `backend/app/config/__init__.py`, `backend/app/accel/__init__.py`, `backend/app/radius/__init__.py`, `backend/app/monitoring/__init__.py`, `backend/app/networking/__init__.py`.

Run: `cd backend && python -m pip install -q -e ".[test]"`

- [ ] **Step 2: Write the failing tests**

`backend/tests/conftest.py`:
```python
import copy
from pathlib import Path

import pytest
import yaml

BASE = {
    "node": "t1",
    "aaa": "lab",
    "uplink": "eth0",
    "pppoe": {"interfaces": [{"name": "veth0"}]},
    "ip_pools": {
        "gw_ip_address": "100.64.255.254",
        "default": "p",
        "pools": [{"name": "p", "network": "100.64.0.0/24"}],
    },
}
RADIUS = {
    "nas_identifier": "t1",
    "nas_ip_address": "192.0.2.1",
    "coa_listen": "192.0.2.1",
    "servers": [{"address": "192.0.2.10"}],
}


@pytest.fixture
def base_cfg() -> dict:
    return copy.deepcopy(BASE)


@pytest.fixture
def radius_cfg() -> dict:
    d = copy.deepcopy(BASE)
    d["aaa"] = "radius"
    d["radius"] = copy.deepcopy(RADIUS)
    return d


def write_cfg(path: Path, data: dict) -> Path:
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path
```

`backend/tests/test_model.py`:
```python
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config.model import BngConfig, load

EXAMPLE = Path(__file__).resolve().parents[2] / "system" / "config.example.yaml"


def test_example_config_loads():
    cfg = load(EXAMPLE)
    assert cfg.aaa == "lab"
    assert cfg.pppoe.interfaces[0].name == "bnglab0"


def test_defaults(base_cfg):
    cfg = BngConfig.model_validate(base_cfg)
    assert cfg.ppp.mtu == 1492 and cfg.shaper.down_limiter == "tbf"


@pytest.mark.parametrize("bad", ["bng\n[radius]", "a,b", "x=y", "has space"])
def test_injection_rejected(base_cfg, bad):
    base_cfg["pppoe"]["ac_name"] = bad
    with pytest.raises(ValidationError):
        BngConfig.model_validate(base_cfg)


def test_unknown_key_rejected(base_cfg):
    base_cfg["pppoe"]["bogus"] = 1
    with pytest.raises(ValidationError):
        BngConfig.model_validate(base_cfg)


def test_overlapping_pools_rejected(base_cfg):
    base_cfg["ip_pools"]["pools"].append({"name": "q", "network": "100.64.0.128/25"})
    with pytest.raises(ValidationError, match="overlap"):
        BngConfig.model_validate(base_cfg)


def test_gateway_inside_pool_rejected(base_cfg):
    base_cfg["ip_pools"]["gw_ip_address"] = "100.64.0.1"
    with pytest.raises(ValidationError, match="inside pool"):
        BngConfig.model_validate(base_cfg)


def test_unknown_next_pool_rejected(base_cfg):
    base_cfg["ip_pools"]["pools"][0]["next"] = "nope"
    with pytest.raises(ValidationError, match="next pool"):
        BngConfig.model_validate(base_cfg)


def test_radius_mode_needs_radius_section(base_cfg):
    base_cfg["aaa"] = "radius"
    with pytest.raises(ValidationError, match="requires a radius section"):
        BngConfig.model_validate(base_cfg)


def test_dae_sources_default_to_servers(radius_cfg):
    cfg = BngConfig.model_validate(radius_cfg)
    assert [str(n) for n in cfg.radius.dae_sources()] == ["192.0.2.10/32"]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_model.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.config.model'`

- [ ] **Step 4: Implement the model and example config**

`backend/app/config/model.py`:
```python
"""Desired state of one BNG node (/etc/bng-platform/config.yaml).

Every string that ends up in accel-ppp.conf is constrained to TOKEN/IFNAME,
so no value can smuggle ',', '=', whitespace or a newline into the generated
file. Secrets are deliberately absent from this model.
"""
from __future__ import annotations

from itertools import combinations
from ipaddress import IPv4Address, IPv4Network
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

TOKEN = r"^[A-Za-z0-9_.\-]{1,64}$"
IFNAME = r"^[A-Za-z0-9_.\-]{1,15}$"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PPPoEInterface(Strict):
    name: str = Field(pattern=IFNAME)
    padi_limit: int | None = Field(default=None, ge=0, le=100_000)


class PPPoE(Strict):
    ac_name: str = Field(default="bng", pattern=TOKEN)
    service_name: str | None = Field(default=None, pattern=TOKEN)
    accept_any_service: bool = True
    interfaces: list[PPPoEInterface] = Field(min_length=1)
    padi_limit: int = Field(default=0, ge=0, le=100_000)
    pado_delay_ms: int = Field(default=0, ge=0, le=10_000)
    called_sid: Literal["mac", "ifname", "ifname:mac"] = "mac"


class PPP(Strict):
    mtu: int = Field(default=1492, ge=1280, le=1500)
    mru: int = Field(default=1492, ge=1280, le=1500)
    ipv6: Literal["deny", "allow"] = "deny"
    lcp_echo_interval: int = Field(default=20, ge=0, le=300)
    lcp_echo_failure: int = Field(default=3, ge=1, le=20)


class IpPool(Strict):
    name: str = Field(pattern=TOKEN)
    network: IPv4Network
    next: str | None = Field(default=None, pattern=TOKEN)


class IpPools(Strict):
    gw_ip_address: IPv4Address
    default: str = Field(pattern=TOKEN)
    pools: list[IpPool] = Field(min_length=1)

    @model_validator(mode="after")
    def _consistent(self) -> IpPools:
        names = [p.name for p in self.pools]
        if len(set(names)) != len(names):
            raise ValueError("ip pool names must be unique")
        if self.default not in names:
            raise ValueError(f"default pool {self.default!r} is not defined")
        for p in self.pools:
            if p.next and p.next not in names:
                raise ValueError(f"pool {p.name!r}: next pool {p.next!r} is not defined")
            if self.gw_ip_address in p.network:
                raise ValueError(f"gw_ip_address is inside pool {p.name!r}")
        for a, b in combinations(self.pools, 2):
            if a.network.overlaps(b.network):
                raise ValueError(f"pools {a.name!r} and {b.name!r} overlap")
        return self


class RadiusServer(Strict):
    address: IPv4Address
    auth_port: int = Field(default=1812, ge=1, le=65535)
    acct_port: int = Field(default=1813, ge=1, le=65535)
    backup: bool = False


class Radius(Strict):
    servers: list[RadiusServer] = Field(min_length=1, max_length=4)
    nas_identifier: str = Field(pattern=TOKEN)
    nas_ip_address: IPv4Address
    timeout: int = Field(default=3, ge=1, le=60)
    max_try: int = Field(default=3, ge=1, le=10)
    acct_timeout: int = Field(default=120, ge=0, le=3600)
    acct_interim_interval: int = Field(default=300, ge=0, le=86_400)
    acct_interim_jitter: int = Field(default=30, ge=0, le=3600)
    coa_listen: IPv4Address
    coa_port: int = Field(default=3799, ge=1, le=65535)
    dae_allowed: list[IPv4Network] = []
    blast_protection: bool = True

    def dae_sources(self) -> list[IPv4Network]:
        """Who may send CoA/Disconnect: explicit list, else the RADIUS servers."""
        return self.dae_allowed or [IPv4Network(s.address) for s in self.servers]


class Shaper(Strict):
    attr: str = Field(default="Filter-Id", pattern=TOKEN)
    vendor: str | None = Field(default=None, pattern=TOKEN)
    down_limiter: Literal["tbf", "htb", "clsact"] = "tbf"
    up_limiter: Literal["police", "htb"] = "police"


class BngConfig(Strict):
    node: str = Field(pattern=TOKEN)
    aaa: Literal["radius", "lab"]
    uplink: str = Field(pattern=IFNAME)
    thread_count: int | None = Field(default=None, ge=1, le=256)
    pppoe: PPPoE
    ppp: PPP = PPP()
    ip_pools: IpPools
    dns: list[IPv4Address] = Field(default_factory=list, max_length=2)
    radius: Radius | None = None
    shaper: Shaper | None = Shaper()

    @model_validator(mode="after")
    def _aaa(self) -> BngConfig:
        if self.aaa == "radius" and self.radius is None:
            raise ValueError("aaa=radius requires a radius section")
        return self


def load(path: Path | str) -> BngConfig:
    with open(path, encoding="utf-8") as f:
        return BngConfig.model_validate(yaml.safe_load(f))
```

`system/config.example.yaml`:
```yaml
# bng-platform node configuration (desired state). Apply with: sudo bngctl config apply
# Secrets never go here: the RADIUS shared secret lives in
# /etc/bng-platform/secrets/radius.secret (root, mode 0600).
node: bng01
aaa: lab                 # lab = accel-ppp chap-secrets on the veth lab; radius = Jaze
uplink: ens18            # install.sh replaces this with the default-route interface
pppoe:
  ac_name: bng01
  interfaces:
    - name: bnglab0      # lab veth created by scripts/lab/lab-up.sh
ppp:
  mtu: 1492
  mru: 1492
ip_pools:
  gw_ip_address: 100.64.255.254
  default: lab
  pools:
    - name: lab
      network: 100.64.0.0/24
dns: [8.8.8.8, 1.1.1.1]
shaper:
  attr: Filter-Id        # attribute Jaze sends the rate in; confirm with Jaze
# radius:                # required when aaa: radius
#   nas_identifier: bng01
#   nas_ip_address: 43.229.72.90
#   coa_listen: 43.229.72.90
#   servers:
#     - address: 192.0.2.10
#     - address: 192.0.2.11
#       backup: true
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_model.py -q`
Expected: 12 passed

- [ ] **Step 6: Commit**

```bash
git add .gitignore .gitattributes backend system ARCHITECTURE_ASSESSMENT.md docs/superpowers
git commit -m "feat: repo skeleton and node config model"
```

---

### Task 2: accel-ppp renderer and static validator

**Files:**
- Create: `backend/app/accel/render.py`, `backend/app/accel/validate.py`
- Test: `backend/tests/test_render.py`

**Interfaces:**
- Consumes: `BngConfig` (Task 1).
- Produces: `render(cfg: BngConfig, radius_secret: str | None) -> Rendered`; `Rendered(main: str, secrets: str)` (frozen dataclass, `secrets == ""` in lab mode); constants `SECRETS_INCLUDE`, `LAB_CHAP_SECRETS`, `CLI_TCP = "127.0.0.1:2001"`; `validate_text(text: str) -> list[str]` (empty = valid).

Facts from 1.14.0 source used here: accel-pppd has no config-check flag (`main.c`: `-c -p -d -V -h`); unknown directives are silently ignored; `triton/conf_file.c` uses a 1024-byte line buffer, rejects re-opening a section while inside another one, and resolves `$include` into the *current* section with a shared static `cur_sect` — so the include must sit inside `[radius]` and the included file must have no section header.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_render.py`:
```python
import pytest

from app.accel.render import SECRETS_INCLUDE, render
from app.accel.validate import validate_text
from app.config.model import BngConfig

SECRET = "S3cr3t-XYZ!"


def test_lab_render_is_valid(base_cfg):
    r = render(BngConfig.model_validate(base_cfg), None)
    assert validate_text(r.main) == []
    assert r.secrets == ""
    assert "chap-secrets" in r.main.split("[core]")[0]      # module loaded
    assert "interface=veth0" in r.main
    assert "100.64.0.0/24,name=p" in r.main
    assert "tcp=127.0.0.1:2001" in r.main


def test_radius_secret_only_in_include(radius_cfg):
    r = render(BngConfig.model_validate(radius_cfg), SECRET)
    assert validate_text(r.main) == []
    assert SECRET not in r.main
    assert f"server=192.0.2.10,{SECRET},auth-port=1812,acct-port=1813" in r.secrets
    assert f"dae-server=192.0.2.1:3799,{SECRET}" in r.secrets
    assert "[" not in r.secrets                                # no section header
    radius = r.main.split("[radius]")[1].split("\n[")[0]
    assert radius.strip().endswith(f"$include {SECRETS_INCLUDE}")
    assert "dae-allowed=192.0.2.10/32" in radius


def test_radius_mode_requires_secret(radius_cfg):
    with pytest.raises(ValueError, match="secret"):
        render(BngConfig.model_validate(radius_cfg), None)


@pytest.mark.parametrize("secret", ["short", "has space", "a,b-cdefgh", "line\nbreak"])
def test_bad_secret_rejected(radius_cfg, secret):
    with pytest.raises(ValueError, match="secret"):
        render(BngConfig.model_validate(radius_cfg), secret)


def test_backup_server_flag(radius_cfg):
    radius_cfg["radius"]["servers"].append({"address": "192.0.2.11", "backup": True})
    r = render(BngConfig.model_validate(radius_cfg), SECRET)
    assert r.secrets.splitlines()[1].endswith(",backup")


GOOD = "[modules]\npppoe\nippool\n[pppoe]\ninterface=eth1\n[ip-pool]\ngw-ip-address=10.0.0.1\n10.1.0.0/24,name=a\n[cli]\ntcp=127.0.0.1:2001\n"


def test_validator_accepts_minimal():
    assert validate_text(GOOD) == []


@pytest.mark.parametrize("bad, msg", [
    (GOOD + "[pppoe]\nac-name=x\n", "duplicate section"),
    (GOOD.replace("interface=eth1", "interfase=eth1"), "unknown directive 'interfase'"),
    (GOOD + "[bogus]\n", "unknown section"),
    (GOOD.replace("ippool", "ipool"), "unknown module"),
    (GOOD + "[radius]\nserver=1.2.3.4,secret\n", "include file"),
    (GOOD + "$include /etc/passwd\n", "$include"),
    (GOOD.replace("10.1.0.0/24,name=a", "10.1.0.0/33,name=a"), "bad pool"),
    (GOOD.replace("10.1.0.0/24,name=a", "10.1.0.0/24,colour=a"), "bad pool"),
    ("[pppoe]\ninterface=eth1\n", "missing section [modules]"),
    (GOOD + "[ppp]\nmtu=" + "1" * 1100 + "\n", "1023"),
])
def test_validator_rejects(bad, msg):
    errors = validate_text(bad)
    assert any(msg in e for e in errors), errors
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_render.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.accel.render'`

- [ ] **Step 3: Implement the renderer**

`backend/app/accel/render.py`:
```python
"""Render BngConfig into accel-ppp 1.14.0 configuration.

The main file never contains a secret. RADIUS server lines (which embed the
shared secret) go to a root-only file pulled in by `$include` as the last line
of [radius]; per 1.14.0 conf_file.c the included file must have no header.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.config.model import BngConfig

SECRETS_INCLUDE = "/etc/bng-platform/secrets/radius.conf"
LAB_CHAP_SECRETS = "/etc/bng-platform/lab/chap-secrets"
DICTIONARY = "/usr/local/share/accel-ppp/radius/dictionary"
CLI_TCP = "127.0.0.1:2001"
LOG_DIR = "/var/log/accel-ppp"

# printable ASCII without space and comma (',' separates server= fields)
_SECRET_RE = re.compile(r"^[\x21-\x2b\x2d-\x7e]{8,128}$")


@dataclass(frozen=True)
class Rendered:
    main: str
    secrets: str


def _b(v: bool) -> str:
    return "1" if v else "0"


def render(cfg: BngConfig, radius_secret: str | None) -> Rendered:
    out: list[str] = ["# generated by bng-platform from /etc/bng-platform/config.yaml - do not edit"]

    def section(name: str, *lines: str | None) -> None:
        out.append(f"[{name}]")
        out.extend(line for line in lines if line is not None)
        out.append("")

    radius = cfg.aaa == "radius"
    section("modules", "log_file", "pppoe", "auth_pap", "auth_chap_md5", "auth_mschap_v2",
            "radius" if radius else "chap-secrets", "ippool", "shaper" if cfg.shaper else None)
    section("core", f"log-error={LOG_DIR}/core.log",
            f"thread-count={cfg.thread_count}" if cfg.thread_count else None)
    section("common", "check-ip=1")
    p = cfg.ppp
    section("ppp", "verbose=1", "min-mtu=1280", f"mtu={p.mtu}", f"mru={p.mru}", "ipv4=require",
            f"ipv6={p.ipv6}", f"lcp-echo-interval={p.lcp_echo_interval}",
            f"lcp-echo-failure={p.lcp_echo_failure}", "unit-cache=1000")
    e = cfg.pppoe
    section("pppoe", "verbose=1", f"ac-name={e.ac_name}",
            f"service-name={e.service_name}" if e.service_name else None,
            f"accept-any-service={_b(e.accept_any_service)}", f"pado-delay={e.pado_delay_ms}",
            f"padi-limit={e.padi_limit}", f"called-sid={e.called_sid}",
            f"ip-pool={cfg.ip_pools.default}",
            *(f"interface={i.name}" + (f",padi-limit={i.padi_limit}" if i.padi_limit is not None else "")
              for i in e.interfaces))
    if cfg.dns:
        section("dns", *(f"dns{n}={a}" for n, a in enumerate(cfg.dns, 1)))

    secrets = ""
    if radius:
        r = cfg.radius
        if not radius_secret or not _SECRET_RE.match(radius_secret):
            raise ValueError("RADIUS secret missing or invalid "
                             "(8-128 printable ASCII characters, no space or comma)")
        section("radius", f"dictionary={DICTIONARY}", f"nas-identifier={r.nas_identifier}",
                f"nas-ip-address={r.nas_ip_address}", f"gw-ip-address={cfg.ip_pools.gw_ip_address}",
                f"timeout={r.timeout}", f"max-try={r.max_try}", f"acct-timeout={r.acct_timeout}",
                f"acct-interim-interval={r.acct_interim_interval}",
                f"acct-interim-jitter={r.acct_interim_jitter}",
                "dae-allowed=" + ",".join(str(n) for n in r.dae_sources()),
                f"blast-protection={_b(r.blast_protection)}", "verbose=1",
                f"$include {SECRETS_INCLUDE}")
        secrets = "".join(
            f"server={s.address},{radius_secret},auth-port={s.auth_port},acct-port={s.acct_port}"
            + (",backup" if s.backup else "") + "\n"
            for s in r.servers
        ) + f"dae-server={r.coa_listen}:{r.coa_port},{radius_secret}\n"
    else:
        section("chap-secrets", f"gw-ip-address={cfg.ip_pools.gw_ip_address}",
                f"chap-secrets={LAB_CHAP_SECRETS}")

    ip = cfg.ip_pools
    section("ip-pool", f"gw-ip-address={ip.gw_ip_address}", "attr=Framed-Pool",
            *(f"{pl.network},name={pl.name}" + (f",next={pl.next}" if pl.next else "") for pl in ip.pools))
    if cfg.shaper:
        s = cfg.shaper
        section("shaper", f"attr={s.attr}", f"vendor={s.vendor}" if s.vendor else None,
                f"down-limiter={s.down_limiter}", f"up-limiter={s.up_limiter}", "verbose=1")
    section("log", f"log-file={LOG_DIR}/accel-ppp.log", f"log-emerg={LOG_DIR}/emerg.log", "level=3")
    section("cli", f"tcp={CLI_TCP}", "verbose=1")
    return Rendered("\n".join(out), secrets)
```

- [ ] **Step 4: Implement the validator**

`backend/app/accel/validate.py`:
```python
"""Static validation of accel-ppp 1.14.0 configuration text.

accel-pppd 1.14.0 has no config-check mode and silently ignores unknown
directives, so a typo changes behaviour without an error. Every section and
directive here was extracted from the 1.14.0 source (conf_get_opt calls plus
the options its modules read by iterating a section). Update this table only
from the source of the version being deployed.
"""
from __future__ import annotations

import ipaddress
import re

from app.accel.render import SECRETS_INCLUDE

MODULES = {
    "log_file", "log_syslog", "log_tcp", "pppoe", "auth_pap", "auth_chap_md5", "auth_mschap_v1",
    "auth_mschap_v2", "radius", "chap-secrets", "ippool", "shaper", "connlimit", "pppd_compat",
    "ipv6_nd", "ipv6_dhcp", "ipv6pool", "net-snmp", "logwtmp", "sigchld",
}
DIRECTIVES: dict[str, set[str]] = {
    "core": {"log-error", "log-debug", "thread-count"},
    "common": {"check-ip", "max-sessions", "max-starting", "netns-run-dir", "nl-rcv-buffer",
               "nl-snd-buffer", "seq-file", "session-timeout", "sid-case", "sid-source",
               "single-session", "single-session-ignore-case"},
    "ppp": {"accomp", "ccp", "ccp-max-configure", "check-ip", "ipv4", "ipv6", "ipv6-accept-peer-intf-id",
            "ipv6-intf-id", "ipv6-peer-intf-id", "lcp-echo-failure", "lcp-echo-interval",
            "lcp-echo-timeout", "max-configure", "max-failure", "max-mtu", "max-terminate", "min-mtu",
            "mppe", "mru", "mtu", "pcomp", "timeout", "unit-cache", "unit-preallocate", "verbose"},
    "pppoe": {"ac-name", "accept-any-service", "accept-blank-service", "called-sid", "cookie-timeout",
              "ifname", "ifname-in-sid", "interface", "ip-pool", "ipv6-pool", "ipv6-pool-delegate",
              "mac-filter", "mppe", "padi-limit", "pado-delay", "service-name", "session-timeout",
              "sid-uppercase", "tr101", "verbose", "vlan-mon", "vlan-name", "vlan-timeout"},
    "dns": {"dns1", "dns2"},
    "radius": {"acct-delay-start", "acct-delay-time", "acct-interim-interval", "acct-interim-jitter",
               "acct-on", "acct-timeout", "attr-tunnel-type", "bind", "blast-protection", "dae-allowed",
               "default-realm", "dictionary", "fail-time", "fail-timeout", "gw-ip-address",
               "interim-verbose", "max-fail", "max-try", "nas-identifier", "nas-ip-address",
               "nas-port-id-in-req", "req-limit", "require-nas-identification", "sid-in-auth",
               "strip-realm", "timeout", "verbose"},
    "chap-secrets": {"chap-secrets", "encrypted", "gw-ip-address", "username-hash"},
    "ip-pool": {"attr", "gw", "gw-ip-address", "shuffle", "tunnel", "vendor"},
    "shaper": {"attr", "attr-down", "attr-up", "burst-factor", "cburst", "down-burst-factor",
               "down-limiter", "fwmark", "ifb", "latency", "leaf-qdisc", "moderate-quantum", "mpu",
               "mtu", "quantum", "r2q", "rate-limit", "rate-multiplier", "up-burst-factor",
               "up-limiter", "vendor", "verbose"},
    "log": {"color", "copy", "level", "log-debug", "log-emerg", "log-fail-file", "log-file",
            "per-session", "per-session-dir", "per-user-dir", "syslog"},
    "cli": {"history-file", "password", "prompt", "sessions-columns", "tcp", "telnet", "verbose"},
}
SECRET_BEARING = {"server", "dae-server", "auth-server", "acct-server", "dm_coa_secret"}
REQUIRED = ("modules", "pppoe", "ip-pool", "cli")
_POOL_OPT = re.compile(r"^(name|next)=[A-Za-z0-9_.\-]{1,64}$")


def _pool_line_ok(line: str) -> bool:
    first, *opts = line.split(",")
    try:
        if "/" in first:
            ipaddress.IPv4Network(first, strict=True)
        else:
            start, _, last = first.partition("-")
            ipaddress.IPv4Address(start)
            if not last.isdigit() or not 0 <= int(last) <= 255:
                return False
    except ValueError:
        return False
    return all(_POOL_OPT.match(o) for o in opts)


def validate_text(text: str) -> list[str]:
    errors: list[str] = []
    section: str | None = None
    seen: set[str] = set()
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if len(raw) >= 1023:
            errors.append(f"line {n}: longer than accel-ppp's 1023-byte line buffer")
            continue
        if line.startswith("$include"):
            if section != "radius" or line != f"$include {SECRETS_INCLUDE}":
                errors.append(f"line {n}: only '$include {SECRETS_INCLUDE}' inside [radius] is allowed")
            continue
        if line.startswith("["):
            if not line.endswith("]"):
                errors.append(f"line {n}: malformed section header")
                continue
            section = line[1:-1]
            if section != "modules" and section not in DIRECTIVES:
                errors.append(f"line {n}: unknown section [{section}]")
            if section in seen:
                errors.append(f"line {n}: duplicate section [{section}] (1.14.0 rejects re-opening)")
            seen.add(section)
            continue
        if section is None:
            errors.append(f"line {n}: directive outside any section")
        elif section == "modules":
            if line not in MODULES:
                errors.append(f"line {n}: unknown module {line!r}")
        elif section == "ip-pool" and line[0].isdigit():
            if not _pool_line_ok(line):
                errors.append(f"line {n}: bad pool line {line!r}")
        else:
            name, sep, _ = line.partition("=")
            name = name.strip()
            if not sep:
                errors.append(f"line {n}: expected name=value")
            elif section == "radius" and name in SECRET_BEARING:
                errors.append(f"line {n}: {name} carries the RADIUS secret and belongs in the include file")
            elif name not in DIRECTIVES.get(section, set()):
                errors.append(f"line {n}: unknown directive {name!r} in [{section}]")
    errors += [f"missing section [{s}]" for s in REQUIRED if s not in seen]
    return errors
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest -q`
Expected: 31 passed (model 12 + render 19)

- [ ] **Step 6: Commit**

```bash
git add backend/app/accel backend/tests/test_render.py
git commit -m "feat: accel-ppp 1.14.0 config renderer and static validator"
```

---

### Task 3: RADIUS test client (stdlib)

**Files:**
- Create: `backend/app/radius/probe.py`
- Test: `backend/tests/test_probe.py`

**Interfaces:**
- Produces: `status_server(host: str, port: int, secret: bytes, nas_identifier: str, timeout: float = 3.0, tries: int = 2) -> Reply | None`; `access_request(host, port, secret: bytes, user: str, password: str, nas_ip: str, nas_identifier: str, timeout=3.0, tries=2) -> Reply | None`; `Reply(code: int, rtt_ms: float, attrs: list[tuple[int, bytes]])` with `.name`; helpers `pap_hide`, `build`, `valid_response`, `parse_attrs`.

This is a test client (RFC 2865 / RFC 5997 Status-Server), not a RADIUS server. Message-Authenticator is sent first (BlastRADIUS guidance).

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_probe.py`:
```python
import hashlib
import hmac
import socket
import struct
import threading

from app.radius import probe

SECRET = b"xyzzy5461"


def test_pap_hide_rfc2865_vector():
    auth = bytes.fromhex("0f403f9473978057bd83d5cb98f4227a")
    assert probe.pap_hide(b"arctangent", SECRET, auth) == bytes.fromhex("0dbe708d93d413ce3196e43f782a0aee")


def test_message_authenticator_first_and_correct():
    auth = bytes(16)
    pkt = probe.build(probe.STATUS_SERVER, 7, SECRET, [(probe.NAS_IDENTIFIER, b"t1")], auth)
    assert pkt[20] == probe.MESSAGE_AUTHENTICATOR and pkt[21] == 18
    zeroed = pkt[:22] + bytes(16) + pkt[38:]
    assert hmac.new(SECRET, zeroed, hashlib.md5).digest() == pkt[22:38]
    assert struct.unpack("!H", pkt[2:4])[0] == len(pkt)


def _responder(secret: bytes, code: int):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))

    def serve():
        req, addr = s.recvfrom(4096)
        attrs = struct.pack("!BB", 18, 4) + b"ok"          # Reply-Message
        head = struct.pack("!BBH", code, req[1], 20 + len(attrs))
        resp_auth = hashlib.md5(head + req[4:20] + attrs + secret).digest()
        s.sendto(head + resp_auth + attrs, addr)
        s.close()

    threading.Thread(target=serve, daemon=True).start()
    return s.getsockname()[1]


def test_access_request_roundtrip_accept():
    port = _responder(SECRET, probe.ACCESS_ACCEPT)
    r = probe.access_request("127.0.0.1", port, SECRET, "u", "p", "192.0.2.1", "t1", timeout=1, tries=1)
    assert r is not None and r.name == "Access-Accept" and (18, b"ok") in r.attrs


def test_wrong_secret_reply_ignored():
    port = _responder(b"other-secret", probe.ACCESS_ACCEPT)
    assert probe.status_server("127.0.0.1", port, SECRET, "t1", timeout=0.5, tries=1) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_probe.py -q`
Expected: FAIL — `ImportError: cannot import name 'probe'`

- [ ] **Step 3: Implement**

`backend/app/radius/probe.py`:
```python
"""Minimal RADIUS *test client* (RFC 2865, RFC 5997 Status-Server). Stdlib only.

Used by `bngctl radius test` and the RADIUS health check. It is not a RADIUS
server and never logs the secret or the password.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import socket
import struct
import time
from dataclasses import dataclass

ACCESS_REQUEST, ACCESS_ACCEPT, ACCESS_REJECT, ACCESS_CHALLENGE, STATUS_SERVER = 1, 2, 3, 11, 12
USER_NAME, USER_PASSWORD, NAS_IP_ADDRESS, NAS_IDENTIFIER, MESSAGE_AUTHENTICATOR = 1, 2, 4, 32, 80
_NAMES = {ACCESS_ACCEPT: "Access-Accept", ACCESS_REJECT: "Access-Reject", ACCESS_CHALLENGE: "Access-Challenge"}


@dataclass
class Reply:
    code: int
    rtt_ms: float
    attrs: list[tuple[int, bytes]]

    @property
    def name(self) -> str:
        return _NAMES.get(self.code, f"code {self.code}")


def _attr(t: int, value: bytes) -> bytes:
    if len(value) > 253:
        raise ValueError("RADIUS attribute too long")
    return struct.pack("!BB", t, len(value) + 2) + value


def pap_hide(password: bytes, secret: bytes, authenticator: bytes) -> bytes:
    if len(password) > 128:
        raise ValueError("password too long")
    padded = password.ljust(max(16, -(-len(password) // 16) * 16), b"\0")
    out, prev = b"", authenticator
    for i in range(0, len(padded), 16):
        key = hashlib.md5(secret + prev).digest()
        prev = bytes(a ^ b for a, b in zip(padded[i:i + 16], key))
        out += prev
    return out


def build(code: int, ident: int, secret: bytes, attrs: list[tuple[int, bytes]], authenticator: bytes) -> bytes:
    body = _attr(MESSAGE_AUTHENTICATOR, bytes(16)) + b"".join(_attr(t, v) for t, v in attrs)
    header = struct.pack("!BBH", code, ident, 20 + len(body)) + authenticator
    mac = hmac.new(secret, header + body, hashlib.md5).digest()
    return header + body[:2] + mac + body[18:]


def valid_response(resp: bytes, request: bytes, secret: bytes) -> bool:
    if len(resp) < 20 or resp[1] != request[1]:
        return False
    length = struct.unpack("!H", resp[2:4])[0]
    if not 20 <= length <= len(resp):
        return False
    expected = hashlib.md5(resp[:4] + request[4:20] + resp[20:length] + secret).digest()
    return hmac.compare_digest(expected, resp[4:20])


def parse_attrs(data: bytes) -> list[tuple[int, bytes]]:
    attrs, i = [], 0
    while i + 2 <= len(data):
        t, ln = data[i], data[i + 1]
        if ln < 2 or i + ln > len(data):
            break
        attrs.append((t, data[i + 2:i + ln]))
        i += ln
    return attrs


def _send(host: str, port: int, secret: bytes, packet: bytes, timeout: float, tries: int) -> Reply | None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        for _ in range(tries):
            t0 = time.monotonic()
            s.sendto(packet, (host, port))
            try:
                while True:
                    data, addr = s.recvfrom(4096)
                    if addr[0] == host and valid_response(data, packet, secret):
                        length = struct.unpack("!H", data[2:4])[0]
                        return Reply(data[0], (time.monotonic() - t0) * 1000, parse_attrs(data[20:length]))
            except socket.timeout:
                continue
    return None


def status_server(host: str, port: int, secret: bytes, nas_identifier: str,
                  timeout: float = 3.0, tries: int = 2) -> Reply | None:
    auth = os.urandom(16)
    pkt = build(STATUS_SERVER, auth[0], secret, [(NAS_IDENTIFIER, nas_identifier.encode())], auth)
    return _send(host, port, secret, pkt, timeout, tries)


def access_request(host: str, port: int, secret: bytes, user: str, password: str, nas_ip: str,
                   nas_identifier: str, timeout: float = 3.0, tries: int = 2) -> Reply | None:
    auth = os.urandom(16)
    attrs = [(USER_NAME, user.encode()), (USER_PASSWORD, pap_hide(password.encode(), secret, auth)),
             (NAS_IP_ADDRESS, socket.inet_aton(nas_ip)), (NAS_IDENTIFIER, nas_identifier.encode())]
    return _send(host, port, secret, build(ACCESS_REQUEST, auth[0], secret, attrs, auth), timeout, tries)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_probe.py -q`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/radius backend/tests/test_probe.py
git commit -m "feat: stdlib RADIUS test client (Status-Server, Access-Request)"
```

---

### Task 4: accel-cmd adapter and service control

**Files:**
- Create: `backend/app/accel/cmd.py`
- Test: `backend/tests/test_cmd.py`

**Interfaces:**
- Produces: `AccelError(RuntimeError)`; `SID_RE`; `SESSION_COLUMNS` (tuple, `username` last); `parse_sessions(text: str, columns) -> list[dict[str, str]]`; `AccelCmd(host="127.0.0.1", port=2001, binary="/usr/local/bin/accel-cmd", timeout=10.0)` with `run(*args) -> str`, `sessions(match: tuple[str, str] | None = None) -> list[dict]`, `terminate(sid: str, hard: bool = False)`, `reload()`, `stat() -> str`, `version() -> str`, `pppoe_interfaces() -> str`; `AccelService(accel: AccelCmd, unit="accel-ppp.service")` with `is_active() -> bool`, `start()`, `stop()`, `restart()`, `reload()`.

1.14.0 facts: `show sessions [columns] [order <col>] [match <col> <regexp>]`; table is ` a | b | c ` rows with a `---+---` separator; `reload` prints `failed` on parse error; `terminate sid <sid> soft|hard`. accel-cmd joins its argv into one CLI line, so every argument we pass must be free of whitespace — enforced here.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_cmd.py`:
```python
import pytest

from app.accel.cmd import SESSION_COLUMNS, AccelCmd, AccelError, parse_sessions

HEADER = " " + " | ".join(SESSION_COLUMNS) + "\n"
SEP = "-" * 20 + "+" + "-" * 20 + "\n"


def row(**kw):
    vals = {c: "" for c in SESSION_COLUMNS} | kw
    return " " + " | ".join(vals[c] for c in SESSION_COLUMNS) + "\n"


def test_parse_sessions():
    text = HEADER + SEP + row(sid="abc123", ifname="ppp0", ip="100.64.0.2", username="alice")
    rows = parse_sessions(text, SESSION_COLUMNS)
    assert rows == [dict.fromkeys(SESSION_COLUMNS, "") | {"sid": "abc123", "ifname": "ppp0",
                                                         "ip": "100.64.0.2", "username": "alice"}]


def test_username_with_pipe_stays_in_last_column():
    rows = parse_sessions(HEADER + SEP + row(sid="1", username="evil|user"), SESSION_COLUMNS)
    assert rows[0]["username"] == "evil|user" and rows[0]["sid"] == "1"


def test_empty_output():
    assert parse_sessions("", SESSION_COLUMNS) == []
    assert parse_sessions(HEADER + SEP, SESSION_COLUMNS) == []


def test_unexpected_header_fails_safe():
    with pytest.raises(AccelError, match="header"):
        parse_sessions(" sid | ip\n", SESSION_COLUMNS)


def test_arguments_with_whitespace_refused():
    with pytest.raises(ValueError):
        AccelCmd(binary="/nonexistent").sessions(("username", "a\nterminate all"))
    with pytest.raises(ValueError):
        AccelCmd(binary="/nonexistent").terminate("12 34")


def test_missing_binary_is_accel_error():
    with pytest.raises(AccelError):
        AccelCmd(binary="/nonexistent/accel-cmd").version()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_cmd.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.accel.cmd'`

- [ ] **Step 3: Implement**

`backend/app/accel/cmd.py`:
```python
"""The only module that talks to accel-pppd (accel-cmd over 127.0.0.1:2001).

1.14.0 has no machine-readable session interface (the JSON metrics module is
post-1.14.0), so `show sessions` table output is parsed here and nowhere else.
If the header differs from what we asked for, we fail instead of guessing.
"""
from __future__ import annotations

import re
import subprocess

from app.accel.render import CLI_TCP


class AccelError(RuntimeError):
    pass


SID_RE = re.compile(r"^[0-9A-Za-z]{1,32}$")
# username is last: the subscriber chooses it and may put '|' in it;
# splitting with maxsplit keeps it whole in the final column.
SESSION_COLUMNS = ("sid", "ifname", "ip", "ip6", "ip6-dp", "calling-sid", "called-sid", "state",
                   "uptime-raw", "rx-bytes-raw", "tx-bytes-raw", "rate-limit", "username")
_NO_SPACE = re.compile(r"^\S+$")


def parse_sessions(text: str, columns) -> list[dict[str, str]]:
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    header = [h.strip() for h in lines[0].split("|")]
    if header != list(columns):
        raise AccelError(f"unexpected 'show sessions' header: {header}")
    rows = []
    for line in lines[1:]:
        if set(line.strip()) <= set("-+"):
            continue
        cells = [c.strip() for c in line.split("|", len(columns) - 1)]
        if len(cells) != len(columns):
            raise AccelError(f"unexpected 'show sessions' row: {line!r}")
        rows.append(dict(zip(columns, cells)))
    return rows


class AccelCmd:
    def __init__(self, host: str | None = None, port: int | None = None,
                 binary: str = "/usr/local/bin/accel-cmd", timeout: float = 10.0):
        default_host, default_port = CLI_TCP.split(":")
        self._argv = [binary, "-H", host or default_host, "-p", str(port or default_port)]
        self._timeout = timeout

    def run(self, *args: str) -> str:
        for a in args:
            if not _NO_SPACE.match(a):
                raise ValueError(f"accel-cmd argument contains whitespace: {a!r}")
        try:
            p = subprocess.run([*self._argv, *args], capture_output=True, text=True, timeout=self._timeout)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise AccelError(f"accel-cmd: {e}") from e
        if p.returncode != 0:
            raise AccelError(f"accel-cmd exit {p.returncode}: {(p.stderr or p.stdout).strip()}")
        return p.stdout

    def sessions(self, match: tuple[str, str] | None = None) -> list[dict[str, str]]:
        args = ["show", "sessions", ",".join(SESSION_COLUMNS)]
        if match:
            column, regex = match
            if column not in SESSION_COLUMNS:
                raise ValueError(f"unknown column {column!r}")
            args += ["match", column, regex]
        return parse_sessions(self.run(*args), SESSION_COLUMNS)

    def terminate(self, sid: str, hard: bool = False) -> None:
        if not SID_RE.match(sid):
            raise ValueError("invalid session id")
        self.run("terminate", "sid", sid, "hard" if hard else "soft")

    def reload(self) -> None:
        out = self.run("reload")
        if "failed" in out.lower():
            raise AccelError("accel-ppp rejected the configuration on reload (old config kept)")

    def stat(self) -> str:
        return self.run("show", "stat")

    def version(self) -> str:
        return self.run("show", "version").strip()

    def pppoe_interfaces(self) -> str:
        return self.run("pppoe", "interface", "show")


class AccelService:
    """systemd + CLI control of accel-pppd, as driven by ConfigManager."""

    def __init__(self, accel: AccelCmd, unit: str = "accel-ppp.service"):
        self.accel, self.unit = accel, unit

    def _systemctl(self, verb: str) -> None:
        p = subprocess.run(["systemctl", verb, self.unit], capture_output=True, text=True, timeout=60)
        if p.returncode != 0:
            raise AccelError(f"systemctl {verb} {self.unit}: {p.stderr.strip()}")

    def is_active(self) -> bool:
        return subprocess.run(["systemctl", "is-active", "--quiet", self.unit], timeout=10).returncode == 0

    def start(self) -> None:
        self._systemctl("start")

    def stop(self) -> None:
        self._systemctl("stop")

    def restart(self) -> None:
        self._systemctl("restart")

    def reload(self) -> None:
        self.accel.reload()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_cmd.py -q`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/accel/cmd.py backend/tests/test_cmd.py
git commit -m "feat: accel-cmd adapter with fail-safe session table parser"
```

---

### Task 5: Safe config manager (validate → diff → backup → apply → verify → rollback)

**Files:**
- Create: `backend/app/config/manager.py`
- Test: `backend/tests/test_manager.py`

**Interfaces:**
- Consumes: `load`, `BngConfig` (T1); `render`, `Rendered` (T2); `validate_text` (T2); a daemon object with `is_active/start/stop/restart/reload` (T4 `AccelService`); `health(cfg) -> list[str]` (failures; T6 `critical_failures`).
- Produces: `Paths(etc: Path, state: Path)` with `.config .accel_conf .secret .secrets_include .versions .backups .audit_log`; `ApplyError`; `changed_sections(old: str, new: str) -> set[str]`; `ConfigManager(paths, daemon, health)` with `build(candidate) -> (BngConfig, Rendered)`, `diff(candidate) -> str`, `apply(candidate, admin, source, allow_restart=False) -> str`, `rollback(version: int | None, admin, source, allow_restart=False) -> str`, `history() -> list[dict]`, `audit(**event)`, `backup_archive() -> Path`, `restore_archive(archive, admin, source, allow_restart=False) -> str`.

Rules implemented: `[modules]`, `[core]`, `[cli]` are only read at start, so a change there needs a restart (drops sessions) → refused without `allow_restart`. Stopped daemon → start. Health failure → restore the three files byte-for-byte, re-activate the old config the same way, audit `rolled_back`, raise.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_manager.py`:
```python
import json
import tarfile

import pytest

from app.config.manager import ApplyError, ConfigManager, Paths, changed_sections
from tests.conftest import write_cfg

SECRET = "S3cr3t-XYZ!"


class FakeDaemon:
    def __init__(self):
        self.active, self.calls = False, []

    def is_active(self):
        return self.active

    def start(self):
        self.calls.append("start"); self.active = True

    def stop(self):
        self.calls.append("stop"); self.active = False

    def restart(self):
        self.calls.append("restart")

    def reload(self):
        self.calls.append("reload")


@pytest.fixture
def env(tmp_path):
    paths = Paths(tmp_path / "etc", tmp_path / "state")
    daemon, failures = FakeDaemon(), []
    mgr = ConfigManager(paths, daemon, lambda cfg: list(failures))
    return mgr, paths, daemon, failures, tmp_path


def audit(paths):
    return [json.loads(line) for line in paths.audit_log.read_text().splitlines()]


def test_first_apply_starts_daemon_and_versions(env, base_cfg):
    mgr, paths, daemon, _, tmp = env
    assert mgr.apply(write_cfg(tmp / "c.yaml", base_cfg), "root", "test") == "applied as version 1"
    assert daemon.calls == ["start"]
    assert "interface=veth0" in paths.accel_conf.read_text()
    assert (paths.versions / "0001" / "accel-ppp.conf").exists()
    assert audit(paths)[-1]["result"] == "applied"


def test_reloadable_change_reloads(env, base_cfg):
    mgr, paths, daemon, _, tmp = env
    mgr.apply(write_cfg(tmp / "c.yaml", base_cfg), "root", "t")
    base_cfg["ppp"] = {"mtu": 1480}
    assert mgr.apply(write_cfg(tmp / "c.yaml", base_cfg), "root", "t") == "applied as version 2"
    assert daemon.calls == ["start", "reload"]
    assert "+mtu=1480" in audit(paths)[-1]["diff"]


def test_unchanged_is_noop(env, base_cfg):
    mgr, _, daemon, _, tmp = env
    f = write_cfg(tmp / "c.yaml", base_cfg)
    mgr.apply(f, "root", "t")
    assert mgr.apply(f, "root", "t") == "no changes"
    assert daemon.calls == ["start"]


def test_restart_needed_is_refused(env, base_cfg):
    mgr, paths, daemon, _, tmp = env
    mgr.apply(write_cfg(tmp / "c.yaml", base_cfg), "root", "t")
    before = paths.accel_conf.read_text()
    base_cfg["thread_count"] = 4
    with pytest.raises(ApplyError, match="allow-restart"):
        mgr.apply(write_cfg(tmp / "c.yaml", base_cfg), "root", "t")
    assert paths.accel_conf.read_text() == before and daemon.calls == ["start"]
    assert mgr.apply(tmp / "c.yaml", "root", "t", allow_restart=True) == "applied as version 2"
    assert daemon.calls[-1] == "restart"


def test_failed_health_rolls_back(env, base_cfg):
    mgr, paths, daemon, failures, tmp = env
    mgr.apply(write_cfg(tmp / "c.yaml", base_cfg), "root", "t")
    good_conf, good_yaml = paths.accel_conf.read_text(), paths.config.read_text()
    failures.append("PPPoE: not serving veth9")
    base_cfg["pppoe"]["interfaces"] = [{"name": "veth9"}]
    with pytest.raises(ApplyError, match="restored"):
        mgr.apply(write_cfg(tmp / "c.yaml", base_cfg), "root", "t")
    assert paths.accel_conf.read_text() == good_conf and paths.config.read_text() == good_yaml
    assert daemon.calls == ["start", "reload", "reload"]
    assert audit(paths)[-1]["result"] == "rolled_back"
    assert [m["version"] for m in mgr.history()] == [1]


def test_failed_first_apply_stops_daemon(env, base_cfg):
    mgr, paths, daemon, failures, tmp = env
    failures.append("boom")
    with pytest.raises(ApplyError):
        mgr.apply(write_cfg(tmp / "c.yaml", base_cfg), "root", "t")
    assert daemon.calls == ["start", "stop"] and not paths.accel_conf.exists()


def test_invalid_candidate_rejected_and_audited(env, base_cfg):
    mgr, paths, daemon, _, tmp = env
    base_cfg["pppoe"]["ac_name"] = "bad name"
    with pytest.raises(ApplyError, match="invalid config"):
        mgr.apply(write_cfg(tmp / "c.yaml", base_cfg), "root", "t")
    assert daemon.calls == [] and audit(paths)[-1]["result"] == "rejected"


def test_rollback_to_previous_version(env, base_cfg):
    mgr, paths, _, _, tmp = env
    mgr.apply(write_cfg(tmp / "c.yaml", base_cfg), "root", "t")
    base_cfg["ppp"] = {"mtu": 1480}
    mgr.apply(write_cfg(tmp / "c.yaml", base_cfg), "root", "t")
    assert mgr.rollback(None, "root", "t") == "applied as version 3"
    assert "mtu=1492" in paths.accel_conf.read_text()


def test_secret_never_leaks(env, radius_cfg):
    mgr, paths, _, _, tmp = env
    paths.secret.parent.mkdir(parents=True)
    paths.secret.write_text(SECRET + "\n")
    mgr.apply(write_cfg(tmp / "c.yaml", radius_cfg), "root", "t")
    assert SECRET in paths.secrets_include.read_text()
    assert oct(paths.secrets_include.stat().st_mode & 0o777) in ("0o600", "0o666")  # 0o666 on Windows
    for f in [paths.accel_conf, paths.config, paths.audit_log, *paths.versions.rglob("*")]:
        if f.is_file():
            assert SECRET not in f.read_text(), f


def test_backup_and_restore(env, base_cfg):
    mgr, paths, _, _, tmp = env
    mgr.apply(write_cfg(tmp / "c.yaml", base_cfg), "root", "t")
    archive = mgr.backup_archive()
    with tarfile.open(archive) as t:
        assert "etc/config.yaml" in t.getnames()
    base_cfg["ppp"] = {"mtu": 1480}
    mgr.apply(write_cfg(tmp / "c.yaml", base_cfg), "root", "t")
    assert mgr.restore_archive(archive, "root", "t") == "applied as version 3"
    assert "mtu=1492" in paths.accel_conf.read_text()


def test_changed_sections():
    assert changed_sections("[a]\nx=1\n[b]\ny=1\n", "[a]\nx=1\n[b]\ny=2\n[c]\n") == {"b", "c"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_manager.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.config.manager'`

- [ ] **Step 3: Implement**

`backend/app/config/manager.py`:
```python
"""Safe configuration pipeline for one node.

validate → diff → backup → apply (start / reload / restart) → health check →
automatic rollback. Every outcome is appended to the audit log. Versions hold
config.yaml + accel-ppp.conf only; secrets are re-rendered from
/etc/bng-platform/secrets/radius.secret and never stored in a version.
"""
from __future__ import annotations

import difflib
import json
import os
import shutil
import tarfile
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import yaml

from app.accel.render import Rendered, render
from app.accel.validate import validate_text
from app.config.model import BngConfig, load

# accel-pppd reads these only at start; `accel-cmd reload` does not apply them.
RESTART_SECTIONS = {"modules", "core", "cli"}


class ApplyError(Exception):
    pass


@dataclass(frozen=True)
class Paths:
    etc: Path = Path("/etc/bng-platform")
    state: Path = Path("/var/lib/bng-platform")

    @property
    def config(self) -> Path: return self.etc / "config.yaml"
    @property
    def accel_conf(self) -> Path: return self.etc / "accel-ppp" / "accel-ppp.conf"
    @property
    def secret(self) -> Path: return self.etc / "secrets" / "radius.secret"
    @property
    def secrets_include(self) -> Path: return self.etc / "secrets" / "radius.conf"
    @property
    def versions(self) -> Path: return self.state / "versions"
    @property
    def backups(self) -> Path: return self.state / "backups"
    @property
    def audit_log(self) -> Path: return self.state / "audit.jsonl"


def _sections(text: str) -> dict[str | None, list[str]]:
    out: dict[str | None, list[str]] = {}
    cur = None
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("[") and s.endswith("]"):
            cur = s[1:-1]
            out.setdefault(cur, [])
        else:
            out.setdefault(cur, []).append(s)
    return out


def changed_sections(old: str, new: str) -> set[str]:
    a, b = _sections(old), _sections(new)
    return {k for k in a.keys() | b.keys() if a.get(k) != b.get(k) and k is not None}


def _read(p: Path) -> str | None:
    return p.read_text(encoding="utf-8") if p.exists() else None


def _write(p: Path, text: str, mode: int) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, mode)
    os.replace(tmp, p)


class ConfigManager:
    def __init__(self, paths: Paths, daemon, health: Callable[[BngConfig], list[str]]):
        self.paths, self.daemon, self.health = paths, daemon, health

    # --- read-only -------------------------------------------------------
    def _secret(self) -> str | None:
        return _read(self.paths.secret).strip() if self.paths.secret.exists() else None

    def build(self, candidate: Path) -> tuple[BngConfig, Rendered]:
        try:
            cfg = load(candidate)
            rendered = render(cfg, self._secret() if cfg.aaa == "radius" else None)
        except (OSError, ValueError, yaml.YAMLError) as e:
            raise ApplyError(f"invalid config {candidate}: {e}") from e
        errors = validate_text(rendered.main)
        if errors:
            raise ApplyError("generated accel-ppp.conf failed validation:\n  " + "\n  ".join(errors))
        return cfg, rendered

    def diff(self, candidate: Path) -> str:
        _, new = self.build(candidate)
        old = _read(self.paths.accel_conf) or ""
        return "".join(difflib.unified_diff(old.splitlines(True), new.main.splitlines(True),
                                            "active/accel-ppp.conf", "candidate/accel-ppp.conf"))

    def history(self) -> list[dict]:
        if not self.paths.versions.exists():
            return []
        return [json.loads((d / "meta.json").read_text()) for d in sorted(self.paths.versions.iterdir())]

    def audit(self, **event) -> None:
        self.paths.state.mkdir(parents=True, exist_ok=True)
        event = {"timestamp": datetime.now(timezone.utc).isoformat(), "component": "bngctl", **event}
        with open(self.paths.audit_log, "a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")

    # --- mutating --------------------------------------------------------
    def _managed(self) -> list[Path]:
        return [self.paths.config, self.paths.accel_conf, self.paths.secrets_include]

    def _backup(self) -> Path:
        dest = self.paths.backups / f"apply-{time.time_ns()}"
        dest.mkdir(parents=True)
        for i, f in enumerate(self._managed()):
            if f.exists():
                shutil.copy2(f, dest / str(i))
        return dest

    def _restore(self, backup: Path) -> None:
        for i, f in enumerate(self._managed()):
            saved = backup / str(i)
            if saved.exists():
                shutil.copy2(saved, f)
            elif f.exists():
                f.unlink()

    def apply(self, candidate: Path, admin: str, source: str, allow_restart: bool = False) -> str:
        who = {"admin": admin, "source": source, "action": "config_apply"}
        try:
            cfg, new = self.build(candidate)
        except ApplyError as e:
            self.audit(**who, result="rejected", detail=str(e))
            raise
        candidate_text = Path(candidate).read_text(encoding="utf-8")
        old_main = _read(self.paths.accel_conf)
        if old_main == new.main and (_read(self.paths.secrets_include) or "") == new.secrets \
                and _read(self.paths.config) == candidate_text:
            return "no changes"

        running = self.daemon.is_active()
        restart_for = changed_sections(old_main or "", new.main) & RESTART_SECTIONS if running else set()
        if restart_for and not allow_restart:
            raise ApplyError(f"changes in {sorted(restart_for)} need an accel-ppp restart, which drops "
                             "every PPPoE session; re-run with --allow-restart")

        diff = "".join(difflib.unified_diff((old_main or "").splitlines(True), new.main.splitlines(True)))
        backup = self._backup()
        _write(self.paths.config, candidate_text, 0o640)
        _write(self.paths.accel_conf, new.main, 0o640)
        if new.secrets:
            self.paths.secrets_include.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            _write(self.paths.secrets_include, new.secrets, 0o600)
        elif self.paths.secrets_include.exists():
            self.paths.secrets_include.unlink()

        try:
            if not running:
                self.daemon.start()
            elif restart_for:
                self.daemon.restart()
            else:
                self.daemon.reload()
            failures = self.health(cfg)
        except Exception as e:  # any activation error means: roll back
            failures = [str(e)]

        if failures:
            self._restore(backup)
            try:
                if old_main is None or not running:
                    self.daemon.stop()
                elif restart_for:
                    self.daemon.restart()
                else:
                    self.daemon.reload()
            except Exception as e:
                failures.append(f"re-activating previous config failed: {e}")
            self.audit(**who, result="rolled_back", detail=failures, diff=diff)
            raise ApplyError("health check failed, previous configuration restored:\n  " + "\n  ".join(failures))

        version = len(self.history()) + 1
        vdir = self.paths.versions / f"{version:04d}"
        meta = {"version": version, "timestamp": datetime.now(timezone.utc).isoformat(),
                "admin": admin, "source": source, "restart": bool(restart_for)}
        _write(vdir / "config.yaml", candidate_text, 0o640)
        _write(vdir / "accel-ppp.conf", new.main, 0o640)
        _write(vdir / "meta.json", json.dumps(meta), 0o640)
        self.audit(**who, result="applied", version=version, restart=bool(restart_for), diff=diff)
        return f"applied as version {version}"

    def rollback(self, version: int | None, admin: str, source: str, allow_restart: bool = False) -> str:
        versions = [m["version"] for m in self.history()]
        if version is None:
            if len(versions) < 2:
                raise ApplyError("no previous version to roll back to")
            version = versions[-2]
        if version not in versions:
            raise ApplyError(f"version {version} does not exist")
        return self.apply(self.paths.versions / f"{version:04d}" / "config.yaml", admin, source, allow_restart)

    def backup_archive(self) -> Path:
        self.paths.backups.mkdir(parents=True, exist_ok=True)
        dest = self.paths.backups / f"bng-config-{time.strftime('%Y%m%dT%H%M%S')}.tar.gz"
        with tarfile.open(dest, "w:gz") as t:
            t.add(self.paths.etc, arcname="etc")
        os.chmod(dest, 0o600)
        return dest

    def restore_archive(self, archive: Path, admin: str, source: str, allow_restart: bool = False) -> str:
        old_secret = _read(self.paths.secret)
        with tempfile.TemporaryDirectory() as tmp:
            with tarfile.open(archive) as t:
                t.extractall(tmp, filter="data")
            etc = Path(tmp) / "etc"
            new_secret = _read(etc / "secrets" / "radius.secret")
            if new_secret is not None:
                _write(self.paths.secret, new_secret, 0o600)
            try:
                return self.apply(etc / "config.yaml", admin, source, allow_restart)
            except ApplyError:
                if old_secret is not None:
                    _write(self.paths.secret, old_secret, 0o600)
                raise
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add backend/app/config/manager.py backend/tests/test_manager.py
git commit -m "feat: safe config apply with backup, health check and automatic rollback"
```

---

### Task 6: Health checks, NIC discovery, host firewall

**Files:**
- Create: `backend/app/monitoring/health.py`, `backend/app/networking/interfaces.py`, `backend/app/networking/firewall.py`
- Test: `backend/tests/test_health.py`

**Interfaces:**
- Consumes: `AccelCmd`, `AccelError`, `AccelService` (T4); `BngConfig` (T1); `probe.status_server` (T3).
- Produces: `Check(name, status, detail="")`; `run_all(cfg, accel, secret: str | None) -> list[Check]`; `critical_failures(cfg, accel) -> list[str]`; `format_report(checks) -> str`; `list_interfaces(root: Path = Path("/sys/class/net")) -> list[dict]`; `firewall.render(cfg) -> str`, `firewall.apply(cfg)`, `firewall.confirm()`, `firewall.CONFIRM_SECONDS = 120`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_health.py`:
```python
from app.config.model import BngConfig
from app.monitoring import health
from app.networking import firewall
from app.networking.interfaces import list_interfaces


def test_format_report_aligns_and_keeps_skip():
    text = health.format_report([health.Check("ACCEL-PPP", "PASS"), health.Check("NAT", "SKIP", "Phase 2")])
    assert text.splitlines() == ["ACCEL-PPP       PASS", "NAT             SKIP  Phase 2"]


def test_pppoe_check_requires_interface_listed(base_cfg, monkeypatch):
    cfg = BngConfig.model_validate(base_cfg)
    monkeypatch.setattr(health, "_operstate", lambda n: "up")

    class A:
        def pppoe_interfaces(self):
            return "eth5: ...\n"

    assert health.check_pppoe(cfg, A()).status == "FAIL"
    A.pppoe_interfaces = lambda self: "veth0\n"
    assert health.check_pppoe(cfg, A()).status == "PASS"


def test_list_interfaces_from_fake_sysfs(tmp_path):
    d = tmp_path / "ens18"
    (d / "statistics").mkdir(parents=True)
    (d / "queues" / "rx-0").mkdir(parents=True)
    (d / "queues" / "tx-0").mkdir()
    for f, v in {"address": "bc:24:11:51:87:d2", "operstate": "up", "mtu": "1500"}.items():
        (d / f).write_text(v + "\n")
    (d / "statistics" / "rx_bytes").write_text("42\n")
    [nic] = list_interfaces(tmp_path)
    assert nic["name"] == "ens18" and nic["rx_bytes"] == 42 and nic["rx_queues"] == 1
    assert nic["speed_mbps"] is None and nic["tx_errors"] == 0


def test_firewall_render(base_cfg, radius_cfg):
    lab = firewall.render(BngConfig.model_validate(base_cfg))
    assert "policy drop" in lab and "tcp dport 22 accept" in lab and "udp dport" not in lab
    assert lab.splitlines()[1:3] == ["table inet bng_filter", "delete table inet bng_filter"]
    rad = firewall.render(BngConfig.model_validate(radius_cfg))
    assert "udp dport 3799 ip saddr { 192.0.2.10/32 } accept" in rad
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_health.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.monitoring.health'`

- [ ] **Step 3: Implement health**

`backend/app/monitoring/health.py`:
```python
"""`bngctl health`: one line per subsystem. SKIP means "not built yet", never PASS."""
from __future__ import annotations

import json
import re
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from app.accel.cmd import AccelCmd, AccelError
from app.config.model import BngConfig
from app.radius import probe


@dataclass(frozen=True)
class Check:
    name: str
    status: str  # PASS | FAIL | SKIP
    detail: str = ""


def _run(*argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=10)


def _operstate(ifname: str) -> str:
    try:
        return Path(f"/sys/class/net/{ifname}/operstate").read_text().strip()
    except OSError:
        return "missing"


def _ok(name: str, ok: bool, detail: str = "") -> Check:
    return Check(name, "PASS" if ok else "FAIL", detail)


def check_service() -> Check:
    ok = _run("systemctl", "is-active", "--quiet", "accel-ppp.service").returncode == 0
    return _ok("ACCEL-PPP", ok, "" if ok else "accel-ppp.service is not active")


def check_cli(accel: AccelCmd, wait_s: float = 5.0) -> Check:
    deadline = time.monotonic() + wait_s
    while True:
        try:
            return Check("ACCEL-CLI", "PASS", accel.version())
        except AccelError as e:
            if time.monotonic() >= deadline:
                return Check("ACCEL-CLI", "FAIL", str(e))
            time.sleep(0.5)


def check_pppoe(cfg: BngConfig, accel) -> Check:
    try:
        listed = accel.pppoe_interfaces()
    except AccelError as e:
        return Check("PPPoE", "FAIL", str(e))
    names = [i.name for i in cfg.pppoe.interfaces]
    bad = [n for n in names
           if _operstate(n) not in ("up", "unknown") or not re.search(rf"(^|\s){re.escape(n)}([\s:,]|$)", listed, re.M)]
    return _ok("PPPoE", not bad, f"not serving: {', '.join(bad)}" if bad else ", ".join(names))


def check_radius(cfg: BngConfig, secret: str | None) -> Check:
    if cfg.aaa != "radius":
        return Check("RADIUS", "SKIP", "aaa=lab (local chap-secrets)")
    if not secret:
        return Check("RADIUS", "FAIL", "secret unreadable (run as root) or missing")
    parts, ok = [], False
    for s in cfg.radius.servers:
        r = probe.status_server(str(s.address), s.auth_port, secret.encode(), cfg.radius.nas_identifier,
                                timeout=2.0, tries=1)
        ok = ok or r is not None
        parts.append(f"{s.address} {r.rtt_ms:.0f} ms" if r else f"{s.address} no Status-Server reply")
    return _ok("RADIUS", ok, "; ".join(parts))


def check_nic(cfg: BngConfig) -> Check:
    state = _operstate(cfg.uplink)
    return _ok("NIC", state == "up", f"{cfg.uplink} {state}")


def check_route() -> Check:
    p = _run("ip", "-j", "route", "show", "default")
    routes = json.loads(p.stdout or "[]") if p.returncode == 0 else []
    return _ok("Internet route", bool(routes), f"via {routes[0].get('gateway')} dev {routes[0].get('dev')}" if routes else "no default route")


def check_dns() -> Check:
    try:
        socket.getaddrinfo("github.com", 443)
        return Check("DNS", "PASS")
    except OSError as e:
        return Check("DNS", "FAIL", str(e))


def check_nftables() -> Check:
    ok = _run("nft", "list", "table", "inet", "bng_filter").returncode == 0
    return _ok("nftables", ok, "" if ok else "table inet bng_filter not loaded")


def check_conntrack() -> Check:
    base = Path("/proc/sys/net/netfilter")
    try:
        count, limit = (int((base / f).read_text()) for f in ("nf_conntrack_count", "nf_conntrack_max"))
    except OSError:
        return Check("conntrack", "SKIP", "nf_conntrack not loaded")
    return _ok("conntrack", count < 0.9 * limit, f"{count}/{limit}")


def critical_failures(cfg: BngConfig, accel: AccelCmd) -> list[str]:
    """Checks that gate a config apply; anything failing here triggers rollback."""
    checks = [check_service(), check_cli(accel), check_pppoe(cfg, accel)]
    return [f"{c.name}: {c.detail}" for c in checks if c.status == "FAIL"]


def run_all(cfg: BngConfig, accel: AccelCmd, secret: str | None) -> list[Check]:
    return [
        check_service(), check_cli(accel, wait_s=0), check_pppoe(cfg, accel), check_radius(cfg, secret),
        check_nic(cfg), check_route(), check_dns(), check_nftables(), check_conntrack(),
        Check("NAT", "SKIP", "Phase 2"), Check("CAKE", "SKIP", "Phase 3"),
        Check("API", "SKIP", "Phase 5"), Check("Database", "SKIP", "Phase 5"),
    ]


def format_report(checks: list[Check]) -> str:
    return "\n".join(f"{c.name:<16}{c.status:<6}{c.detail}".rstrip() for c in checks)
```

- [ ] **Step 4: Implement NIC discovery**

`backend/app/networking/interfaces.py`:
```python
"""NIC inventory from sysfs; no interface names are hard-coded."""
from __future__ import annotations

from pathlib import Path

COUNTERS = ("rx_bytes", "tx_bytes", "rx_packets", "tx_packets", "rx_errors", "tx_errors", "rx_dropped", "tx_dropped")


def _read(p: Path) -> str:
    try:
        return p.read_text().strip()
    except OSError:  # e.g. speed on virtio returns EINVAL
        return ""


def _int(s: str) -> int:
    return int(s) if s.lstrip("-").isdigit() else 0


def list_interfaces(root: Path = Path("/sys/class/net")) -> list[dict]:
    nics = []
    for d in sorted(root.iterdir()):
        speed = _int(_read(d / "speed"))
        nics.append({
            "name": d.name,
            "mac": _read(d / "address"),
            "state": _read(d / "operstate"),
            "mtu": _int(_read(d / "mtu")),
            "speed_mbps": speed if speed > 0 else None,
            "duplex": _read(d / "duplex") or None,
            "rx_queues": len(list((d / "queues").glob("rx-*"))),
            "tx_queues": len(list((d / "queues").glob("tx-*"))),
            **{k: _int(_read(d / "statistics" / k)) for k in COUNTERS},
        })
    return nics
```

- [ ] **Step 5: Implement the firewall**

`backend/app/networking/firewall.py`:
```python
"""Host input firewall (nftables `inet bng_filter`) with confirm-or-revert.

`apply` loads the new rules from a pending file and arms a systemd timer that
restores the last *confirmed* rules after CONFIRM_SECONDS. Only `confirm`
(run from a fresh SSH session, proving SSH still works) stops the timer and
persists the rules, so a rule that locks out SSH undoes itself.
Forwarded subscriber traffic is not filtered here (Phase 2).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from app.config.model import BngConfig

RULES = Path("/etc/bng-platform/nftables/filter.nft")
PENDING = Path("/var/lib/bng-platform/filter.nft.pending")
REVERT_UNIT = "bng-fw-revert"
CONFIRM_SECONDS = 120
REVERT_SCRIPT = (f"nft delete table inet bng_filter; "
                 f"if [ -f {RULES} ]; then nft -f {RULES}; fi; rm -f {PENDING}")


def render(cfg: BngConfig) -> str:
    dae = ""
    if cfg.aaa == "radius":
        srcs = ", ".join(str(n) for n in cfg.radius.dae_sources())
        dae = f"        udp dport {cfg.radius.coa_port} ip saddr {{ {srcs} }} accept\n"
    return (
        "# generated by bng-platform - do not edit\n"
        "table inet bng_filter\n"
        "delete table inet bng_filter\n"
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
        "}\n"
    )


def _run(*argv: str) -> None:
    p = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    if p.returncode != 0:
        raise RuntimeError(f"{' '.join(argv[:2])}: {p.stderr.strip()}")


def _disarm() -> None:
    subprocess.run(["systemctl", "stop", f"{REVERT_UNIT}.timer"], capture_output=True, timeout=30)
    subprocess.run(["systemctl", "reset-failed", f"{REVERT_UNIT}.service"], capture_output=True, timeout=30)


def apply(cfg: BngConfig) -> None:
    PENDING.parent.mkdir(parents=True, exist_ok=True)
    PENDING.write_text(render(cfg))
    _run("nft", "-c", "-f", str(PENDING))
    _disarm()
    _run("systemd-run", f"--unit={REVERT_UNIT}", f"--on-active={CONFIRM_SECONDS}", "/bin/sh", "-c", REVERT_SCRIPT)
    try:
        _run("nft", "-f", str(PENDING))
    except RuntimeError:
        _disarm()
        raise


def confirm() -> None:
    if not PENDING.exists():
        raise RuntimeError("no pending firewall change (already confirmed, or it was reverted)")
    _disarm()
    RULES.parent.mkdir(parents=True, exist_ok=True)
    RULES.write_text(PENDING.read_text())
    PENDING.unlink()
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && python -m pytest -q`
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add backend/app/monitoring backend/app/networking backend/tests/test_health.py
git commit -m "feat: health checks, NIC discovery and confirm-or-revert host firewall"
```

---

### Task 7: `bngctl` CLI

**Files:**
- Create: `backend/app/cli.py`
- Test: `backend/tests/test_cli.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `main(argv: list[str] | None = None) -> int`; console script `bngctl`.

Commands: `status`, `sessions [--search TERM] [--json]`, `session show SID`, `session disconnect SID [--hard]`, `radius test [--user U]`, `config validate|diff [FILE]`, `config apply [FILE] [--allow-restart]`, `config rollback [VERSION] [--allow-restart]`, `config history`, `health`, `interfaces`, `backup`, `restore ARCHIVE [--allow-restart]`, `firewall apply|confirm`. QoS/NAT/benchmark commands arrive with their phases.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_cli.py`:
```python
from pathlib import Path

from app.cli import main
from tests.conftest import write_cfg

EXAMPLE = Path(__file__).resolve().parents[2] / "system" / "config.example.yaml"


def test_validate_example(capsys):
    assert main(["config", "validate", str(EXAMPLE)]) == 0
    assert "valid" in capsys.readouterr().out


def test_validate_rejects_bad_file(tmp_path, base_cfg, capsys):
    base_cfg["pppoe"]["interfaces"] = []
    assert main(["config", "validate", str(write_cfg(tmp_path / "c.yaml", base_cfg))]) == 1
    assert "invalid config" in capsys.readouterr().err


def test_search_input_is_restricted(capsys):
    assert main(["sessions", "--search", "a b"]) == 1
    assert "search may contain only" in capsys.readouterr().err


def test_bad_sid_rejected(capsys):
    assert main(["session", "show", "../etc"]) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_cli.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.cli'`

- [ ] **Step 3: Implement**

`backend/app/cli.py`:
```python
"""bngctl - operator CLI for one BNG node.

Each sub-command is a typed operation; no operator text reaches a shell.
Mutating commands need root and are written to the audit log.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
from pathlib import Path

from app.accel.cmd import SID_RE, AccelCmd, AccelService
from app.config.manager import ApplyError, ConfigManager, Paths
from app.config.model import load
from app.monitoring import health
from app.networking import firewall
from app.networking.interfaces import list_interfaces
from app.radius import probe

SEARCH_RE = re.compile(r"^[A-Za-z0-9_.@:\-]{1,64}$")


def _admin() -> str:
    return os.environ.get("SUDO_USER") or getpass.getuser()


def _source() -> str:
    ssh = os.environ.get("SSH_CLIENT")
    return f"cli:{ssh.split()[0]}" if ssh else "cli:local"


def _require_root() -> None:
    if os.geteuid() != 0:
        raise PermissionError("this command changes the system; run it with sudo")


def _secret(paths: Paths) -> str | None:
    try:
        return paths.secret.read_text().strip()
    except OSError:
        return None


def _show(v: bytes) -> str:
    try:
        s = v.decode()
        return s if s.isprintable() else v.hex()
    except UnicodeDecodeError:
        return v.hex()


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bngctl", description="BNG node control")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    x = sub.add_parser("sessions")
    x.add_argument("--search")
    x.add_argument("--json", action="store_true")
    ses = sub.add_parser("session").add_subparsers(dest="action", required=True)
    ses.add_parser("show").add_argument("sid")
    x = ses.add_parser("disconnect")
    x.add_argument("sid")
    x.add_argument("--hard", action="store_true")
    rad = sub.add_parser("radius").add_subparsers(dest="action", required=True)
    rad.add_parser("test").add_argument("--user")
    cfg = sub.add_parser("config").add_subparsers(dest="action", required=True)
    for name in ("validate", "diff", "apply"):
        x = cfg.add_parser(name)
        x.add_argument("file", nargs="?", type=Path)
        if name == "apply":
            x.add_argument("--allow-restart", action="store_true")
    x = cfg.add_parser("rollback")
    x.add_argument("version", nargs="?", type=int)
    x.add_argument("--allow-restart", action="store_true")
    cfg.add_parser("history")
    sub.add_parser("health")
    sub.add_parser("interfaces")
    sub.add_parser("backup")
    x = sub.add_parser("restore")
    x.add_argument("archive", type=Path)
    x.add_argument("--allow-restart", action="store_true")
    fw = sub.add_parser("firewall").add_subparsers(dest="action", required=True)
    fw.add_parser("apply")
    fw.add_parser("confirm")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    paths = Paths()
    accel = AccelCmd()
    mgr = ConfigManager(paths, AccelService(accel), lambda c: health.critical_failures(c, accel))
    try:
        return _dispatch(args, paths, accel, mgr)
    except (ApplyError, RuntimeError, ValueError, OSError) as e:
        print(f"bngctl: {e}", file=sys.stderr)
        return 1


def _dispatch(a, paths: Paths, accel: AccelCmd, mgr: ConfigManager) -> int:
    who = {"admin": _admin(), "source": _source()}

    if a.cmd == "status":
        print("accel-ppp.service:", "active" if AccelService(accel).is_active() else "INACTIVE")
        print(accel.version())
        print(accel.stat())
        return 0

    if a.cmd == "sessions":
        match = None
        if a.search:
            if not SEARCH_RE.match(a.search):
                raise ValueError("search may contain only letters, digits and _.@:-")
            match = ("username", a.search)
        rows = accel.sessions(match)
        if a.json:
            print(json.dumps(rows, indent=2))
        else:
            cols = ("sid", "username", "ip", "calling-sid", "ifname", "state", "uptime-raw", "rate-limit")
            print("  ".join(f"{c:<18}" for c in cols))
            for r in rows:
                print("  ".join(f"{r[c]:<18}" for c in cols))
            print(f"{len(rows)} session(s)")
        return 0

    if a.cmd == "session":
        if not SID_RE.match(a.sid):
            raise ValueError("invalid session id")
        if a.action == "show":
            rows = accel.sessions(("sid", f"^{a.sid}$"))
            if not rows:
                raise ValueError(f"no session {a.sid}")
            for k, v in rows[0].items():
                print(f"{k:<14}{v}")
            return 0
        _require_root()
        accel.terminate(a.sid, hard=a.hard)
        mode = "hard" if a.hard else "soft"
        mgr.audit(**who, action="session_disconnect", result="ok", session_id=a.sid, mode=mode)
        print(f"session {a.sid} terminated ({mode})")
        return 0

    if a.cmd == "radius":
        cfg = load(paths.config)
        if cfg.aaa != "radius":
            raise ValueError("aaa=lab: no RADIUS servers configured")
        secret = _secret(paths)
        if not secret:
            raise ValueError(f"cannot read {paths.secret} (run with sudo)")
        password = getpass.getpass(f"password for {a.user}: ") if a.user else None
        rc = 1
        for s in cfg.radius.servers:
            host, key, r = str(s.address), secret.encode(), None
            if a.user:
                r = probe.access_request(host, s.auth_port, key, a.user, password,
                                         str(cfg.radius.nas_ip_address), cfg.radius.nas_identifier)
            else:
                r = probe.status_server(host, s.auth_port, key, cfg.radius.nas_identifier)
            if r is None:
                print(f"{host}:{s.auth_port}  no valid reply (timeout, wrong secret, or Status-Server unsupported)")
                continue
            rc = 0
            print(f"{host}:{s.auth_port}  {r.name}  {r.rtt_ms:.0f} ms")
            for t, v in r.attrs:
                print(f"    attr {t}: {_show(v)}")
        return rc

    if a.cmd == "config":
        if a.action == "history":
            for m in mgr.history():
                print(f"v{m['version']:<5}{m['timestamp']}  {m['admin']:<12}{m['source']}"
                      + ("  (restart)" if m.get("restart") else ""))
            return 0
        if a.action == "rollback":
            _require_root()
            print(mgr.rollback(a.version, who["admin"], who["source"], a.allow_restart))
            return 0
        f = a.file or paths.config
        if a.action == "validate":
            mgr.build(f)
            print(f"{f}: valid")
            return 0
        if a.action == "diff":
            print(mgr.diff(f) or "no changes")
            return 0
        _require_root()
        print(mgr.apply(f, who["admin"], who["source"], a.allow_restart))
        return 0

    if a.cmd == "health":
        checks = health.run_all(load(paths.config), accel, _secret(paths))
        print(health.format_report(checks))
        return 1 if any(c.status == "FAIL" for c in checks) else 0

    if a.cmd == "interfaces":
        print(f"{'name':<12}{'state':<9}{'mac':<19}{'mtu':>6}{'speed':>8}{'q rx/tx':>9}"
              f"{'rx bytes':>16}{'tx bytes':>16}{'err':>7}{'drop':>7}")
        for n in list_interfaces():
            speed = f"{n['speed_mbps']}M" if n["speed_mbps"] else "-"
            print(f"{n['name']:<12}{n['state']:<9}{n['mac']:<19}{n['mtu']:>6}{speed:>8}"
                  f"{str(n['rx_queues']) + '/' + str(n['tx_queues']):>9}{n['rx_bytes']:>16}{n['tx_bytes']:>16}"
                  f"{n['rx_errors'] + n['tx_errors']:>7}{n['rx_dropped'] + n['tx_dropped']:>7}")
        return 0

    if a.cmd == "backup":
        _require_root()
        print(mgr.backup_archive())
        return 0

    if a.cmd == "restore":
        _require_root()
        print(mgr.restore_archive(a.archive, who["admin"], who["source"], a.allow_restart))
        return 0

    if a.cmd == "firewall":
        _require_root()
        if a.action == "apply":
            firewall.apply(load(paths.config))
            print(f"firewall loaded. It reverts in {firewall.CONFIRM_SECONDS}s unless you open a NEW ssh "
                  "session and run: sudo bngctl firewall confirm")
        else:
            firewall.confirm()
            print("firewall confirmed and persisted")
        mgr.audit(**who, action=f"firewall_{a.action}", result="ok")
        return 0
    return 2
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add backend/app/cli.py backend/tests/test_cli.py
git commit -m "feat: bngctl operator CLI"
```

---

### Task 8: systemd unit, installer, uninstaller, deploy, lab scripts

**Files:**
- Create: `system/systemd/accel-ppp.service`, `scripts/install.sh`, `scripts/uninstall.sh`, `scripts/health-check.sh`, `scripts/deploy.sh`, `scripts/lab/lab-up.sh`, `scripts/lab/lab-down.sh`, `scripts/lab/lab-test.sh`

**Interfaces:**
- Consumes: `bngctl` (T7); render paths `LAB_CHAP_SECRETS`, `/etc/bng-platform/accel-ppp/accel-ppp.conf` (T2/T5).
- Produces: installed `/usr/local/sbin/accel-pppd`, `/usr/local/bin/accel-cmd`, `/usr/local/sbin/bngctl`, `accel-ppp.service`; lab namespace `bnglab`, veth `bnglab0`↔`bnglab1`, `/etc/bng-platform/lab/{chap-secrets,pppd-auth,password}`.

- [ ] **Step 1: systemd unit**

`system/systemd/accel-ppp.service`:
```ini
[Unit]
Description=ACCEL-PPP BNG daemon (config generated by bng-platform)
After=network-online.target
Wants=network-online.target

[Service]
Type=forking
ExecStartPre=/usr/bin/install -d -m 0750 /var/log/accel-ppp
ExecStart=/usr/local/sbin/accel-pppd -d -p /run/accel-pppd.pid -c /etc/bng-platform/accel-ppp/accel-ppp.conf
ExecReload=/bin/kill -USR1 $MAINPID
PIDFile=/run/accel-pppd.pid
Restart=on-failure
RestartSec=2
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: installer**

`scripts/install.sh`:
```bash
#!/usr/bin/env bash
# bng-platform installer (Phase 1). Idempotent. Never edits netplan or NIC config.
set -euo pipefail
ACCEL_PPP_VERSION=1.14.0
SRC=$(cd "$(dirname "$0")/.." && pwd)
PREFIX=/opt/bng-platform
ETC=/etc/bng-platform
STATE=/var/lib/bng-platform

[ "$(id -u)" -eq 0 ] || { echo "install.sh: run as root" >&2; exit 1; }
log() { printf '\n==> %s\n' "$*"; }

log "System"
. /etc/os-release
echo "OS:     $PRETTY_NAME"
echo "Kernel: $(uname -r)"
echo "CPU:    $(nproc) x $(lscpu | sed -n 's/^Model name: *//p')"
echo "RAM:    $(free -h | awk '/^Mem:/{print $2}')"
echo "NICs:"; ip -br link | grep -v '^lo ' | sed 's/^/        /'
case "$ID" in ubuntu|debian) ;; *) echo "unsupported distribution: $ID" >&2; exit 1 ;; esac

log "Kernel modules"
missing=0
for m in ppp_generic pppox pppoe sch_cake ifb act_mirred sch_htb cls_u32 nf_conntrack nf_nat nft_nat 8021q; do
  if modinfo "$m" >/dev/null 2>&1; then printf '  %-14s ok\n' "$m"; else printf '  %-14s MISSING\n' "$m"; missing=1; fi
done
[ "$missing" -eq 0 ] || { echo "required kernel modules missing" >&2; exit 1; }
echo pppoe > /etc/modules-load.d/bng-platform.conf
modprobe pppoe

log "Packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q build-essential cmake git libssl-dev libpcre2-dev python3-venv \
  nftables iproute2 ethtool ppp iperf3 tcpdump

log "ACCEL-PPP $ACCEL_PPP_VERSION"
if /usr/local/sbin/accel-pppd -V 2>/dev/null | grep -qx "accel-ppp $ACCEL_PPP_VERSION"; then
  echo "  already installed"
else
  B=$(mktemp -d)
  git clone -q --depth 1 --branch "$ACCEL_PPP_VERSION" https://github.com/accel-ppp/accel-ppp "$B/src"
  cmake -S "$B/src" -B "$B/build" -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/usr/local \
    -DRADIUS=TRUE -DSHAPER=TRUE -DLOG_PGSQL=FALSE -DNETSNMP=FALSE -DLUA=FALSE \
    -DBUILD_IPOE_DRIVER=FALSE -DBUILD_VLAN_MON_DRIVER=FALSE
  cmake --build "$B/build" -j"$(nproc)"
  cmake --install "$B/build"
  install -D -m 0644 "$B/build/install_manifest.txt" "$PREFIX/accel-ppp.manifest"
  rm -rf "$B"
  ldconfig
fi
/usr/local/sbin/accel-pppd -V

log "systemd"
install -m 0644 "$SRC/system/systemd/accel-ppp.service" /etc/systemd/system/accel-ppp.service
systemctl daemon-reload
systemctl enable accel-ppp.service   # started by the first 'bngctl config apply'

log "bngctl"
python3 -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install -q --upgrade pip
"$PREFIX/venv/bin/pip" install -q "$SRC/backend"
ln -sf "$PREFIX/venv/bin/bngctl" /usr/local/sbin/bngctl

log "Configuration"
install -d -m 0755 "$ETC" "$ETC/accel-ppp" "$ETC/nftables"
install -d -m 0700 "$ETC/secrets" "$STATE"
if [ ! -f "$ETC/config.yaml" ]; then
  UPLINK=$(ip route show default | awk '{print $5; exit}')
  sed "s/^uplink: .*/uplink: ${UPLINK}            # default-route interface at install time/" \
    "$SRC/system/config.example.yaml" > "$ETC/config.yaml"
  chmod 0640 "$ETC/config.yaml"
  echo "  created $ETC/config.yaml (aaa: lab, uplink: $UPLINK)"
else
  echo "  keeping existing $ETC/config.yaml"
fi

log "nftables persistence"
INC='include "/etc/bng-platform/nftables/*.nft"'
if ! grep -qxF "$INC" /etc/nftables.conf; then
  cp -a /etc/nftables.conf "/etc/nftables.conf.bng-backup.$(date +%s)"
  echo "$INC" >> /etc/nftables.conf
fi
systemctl enable nftables.service

log "Health"
bngctl health || true
cat <<EOF

Next:
  sudo bash $SRC/scripts/lab/lab-up.sh      # veth/netns lab subscriber
  sudo bngctl config apply                  # first start of accel-ppp
  sudo bngctl firewall apply                # then confirm from a NEW ssh session
EOF
```

- [ ] **Step 3: uninstall, health-check, deploy**

`scripts/uninstall.sh`:
```bash
#!/usr/bin/env bash
# Remove bng-platform. Stopping accel-ppp drops every PPPoE session.
# Keeps /etc/bng-platform and /var/lib/bng-platform unless --purge.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "uninstall.sh: run as root" >&2; exit 1; }
PURGE=0; YES=0
for a in "$@"; do case "$a" in --purge) PURGE=1 ;; --yes) YES=1 ;; esac; done
if [ "$YES" -ne 1 ]; then
  read -r -p "This stops accel-ppp and disconnects all subscribers. Continue? [y/N] " ans
  [ "$ans" = y ] || exit 1
fi
bash "$(dirname "$0")/lab/lab-down.sh" || true
systemctl disable --now accel-ppp.service 2>/dev/null || true
rm -f /etc/systemd/system/accel-ppp.service
systemctl daemon-reload
if [ -f /opt/bng-platform/accel-ppp.manifest ]; then xargs -d '\n' rm -f < /opt/bng-platform/accel-ppp.manifest; fi
nft delete table inet bng_filter 2>/dev/null || true
sed -i '\#^include "/etc/bng-platform/nftables/\*\.nft"$#d' /etc/nftables.conf
rm -f /usr/local/sbin/bngctl /etc/modules-load.d/bng-platform.conf
rm -rf /opt/bng-platform
if [ "$PURGE" -eq 1 ]; then
  rm -rf /etc/bng-platform /var/lib/bng-platform /var/log/accel-ppp
else
  echo "kept /etc/bng-platform and /var/lib/bng-platform (use --purge to remove)"
fi
```

`scripts/health-check.sh`:
```bash
#!/usr/bin/env bash
exec /usr/local/sbin/bngctl health "$@"
```

`scripts/deploy.sh`:
```bash
#!/usr/bin/env bash
# Copy the working tree to a node: scripts/deploy.sh [ssh-host]   (default bng01)
set -euo pipefail
HOST=${1:-bng01}
cd "$(dirname "$0")/.."
tar --exclude='__pycache__' --exclude='*.egg-info' --exclude='.pytest_cache' -czf - backend system scripts \
  | ssh "$HOST" 'sudo rm -rf /opt/bng-platform/src && sudo mkdir -p /opt/bng-platform/src && sudo tar xzf - -C /opt/bng-platform/src'
echo "deployed to $HOST:/opt/bng-platform/src"
```

- [ ] **Step 4: lab scripts**

`scripts/lab/lab-up.sh`:
```bash
#!/usr/bin/env bash
# Isolated PPPoE access network for functional tests. accel-ppp serves the host
# end of a veth pair (bnglab0); pppd in network namespace "bnglab" on the peer
# end (bnglab1) is the subscriber. The uplink/management NIC is never touched.
set -euo pipefail
NS=bnglab; HOST_IF=bnglab0; PEER_IF=bnglab1
LAB=/etc/bng-platform/lab
LAB_USER=${LAB_USER:-labuser}
LAB_RATE_KBIT=${LAB_RATE_KBIT:-20480}

ip netns list | grep -qw "$NS" || ip netns add "$NS"
ip link show "$HOST_IF" >/dev/null 2>&1 || ip link add "$HOST_IF" type veth peer name "$PEER_IF" netns "$NS"
ip link set "$HOST_IF" up
ip -n "$NS" link set lo up
ip -n "$NS" link set "$PEER_IF" up

umask 077
install -d -m 0700 "$LAB"
[ -s "$LAB/password" ] || head -c 18 /dev/urandom | base64 | tr -d '/+=' > "$LAB/password"
PW=$(cat "$LAB/password")
# chap-secrets: user server password ip rate   ('*' ip = allocate from the default pool; rate = down/up kbit/s)
printf '%s * %s * %s/%s\n' "$LAB_USER" "$PW" "$LAB_RATE_KBIT" "$LAB_RATE_KBIT" > "$LAB/chap-secrets"
printf 'user %s\npassword %s\n' "$LAB_USER" "$PW" > "$LAB/pppd-auth"
echo "lab up: $HOST_IF <-> $NS:$PEER_IF  user=$LAB_USER  rate=${LAB_RATE_KBIT} kbit/s"
```

`scripts/lab/lab-down.sh`:
```bash
#!/usr/bin/env bash
set -uo pipefail
ip netns pids bnglab 2>/dev/null | xargs -r kill 2>/dev/null
sleep 1
ip netns del bnglab 2>/dev/null    # removes bnglab1 and, with it, bnglab0
echo "lab down"
```

`scripts/lab/lab-test.sh`:
```bash
#!/usr/bin/env bash
# Phase 1 end-to-end test on the veth lab:
# PPPoE discovery -> auth (chap-secrets) -> IPCP from pool -> visible in accel
# -> gateway reachable -> per-session shaper present and enforced both ways
# -> disconnect via bngctl. Prints PASS/FAIL per step; exit 1 on any FAIL.
set -uo pipefail
NS=bnglab; PEER_IF=bnglab1; LAB=/etc/bng-platform/lab
PLUGIN=$(ls /usr/lib/pppd/*/rp-pppoe.so /usr/lib/pppd/*/pppoe.so 2>/dev/null | head -1)
RATE=$(awk '{split($5, r, "/"); print r[1]; exit}' "$LAB/chap-secrets")
fail=0
step() { printf '%-34s %s\n' "$1" "$2"; [ "$2" = PASS ] || fail=1; }
cleanup() {
  ip netns pids "$NS" 2>/dev/null | xargs -r kill 2>/dev/null
  pkill -f 'iperf3 -s -1 -B' 2>/dev/null
  for h in $(nft -a list chain inet bng_filter input 2>/dev/null | awk '/bng-lab/{print $NF}'); do
    nft delete rule inet bng_filter input handle "$h"
  done
}
trap cleanup EXIT

timeout 60 ip netns exec "$NS" pppd plugin "$PLUGIN" "nic-$PEER_IF" file "$LAB/pppd-auth" \
  noauth nodefaultroute noipdefault updetach maxfail 1 linkname bnglab mtu 1492 mru 1492 \
  lcp-echo-interval 10 lcp-echo-failure 3 > /tmp/bnglab-pppd.log 2>&1 \
  && step "PPPoE session up" PASS || { step "PPPoE session up" FAIL; cat /tmp/bnglab-pppd.log; exit 1; }

read -r CLIENT_IP GW < <(ip -n "$NS" -j -4 addr show dev ppp0 | python3 -c \
  'import json,sys; a=json.load(sys.stdin)[0]["addr_info"][0]; print(a["local"], a["address"])')
step "IP assigned ($CLIENT_IP, gw $GW)" "$([ -n "${CLIENT_IP:-}" ] && echo PASS || echo FAIL)"

SES=$(bngctl sessions --json --search labuser)
read -r SID IFNAME SIP < <(echo "$SES" | python3 -c \
  'import json,sys; s=json.load(sys.stdin)[0]; print(s["sid"], s["ifname"], s["ip"])')
step "Session in accel ($SID on $IFNAME)" "$([ "${SIP:-}" = "$CLIENT_IP" ] && echo PASS || echo FAIL)"

ip netns exec "$NS" ping -c 3 -W 2 "$GW" >/dev/null && step "Gateway reachable" PASS || step "Gateway reachable" FAIL

tc qdisc show dev "$IFNAME" | grep -q tbf && step "Download shaper (tbf) on $IFNAME" PASS || step "Download shaper (tbf) on $IFNAME" FAIL
tc filter show dev "$IFNAME" ingress | grep -q police && step "Upload policer on $IFNAME" PASS || step "Upload policer on $IFNAME" FAIL

nft list table inet bng_filter >/dev/null 2>&1 && \
  nft insert rule inet bng_filter input iifname "ppp*" tcp dport 5201 accept comment '"bng-lab"'
measure() {  # $1 = extra iperf3 client flag (-R = download); prints Mbit/s received by the subscriber side
  iperf3 -s -1 -B "$GW" >/dev/null 2>&1 &
  sleep 1
  ip netns exec "$NS" iperf3 -c "$GW" -t 8 -O 2 -J $1 | python3 -c \
    'import json,sys; print(round(json.load(sys.stdin)["end"]["sum_received"]["bits_per_second"]/1e6, 2))'
}
LIMIT=$(python3 -c "print($RATE/1000)")
for dir in download upload; do
  flag=$([ $dir = download ] && echo -R || echo "")
  mbps=$(measure "$flag")
  ok=$(python3 -c "print('PASS' if 0.5*$LIMIT <= $mbps <= 1.10*$LIMIT else 'FAIL')")
  step "$dir ${mbps} Mbit/s (limit ${LIMIT})" "$ok"
done

bngctl session disconnect "$SID" >/dev/null
for _ in $(seq 20); do [ -z "$(bngctl sessions --json --search labuser | python3 -c 'import json,sys; print(json.load(sys.stdin) or "")')" ] && break; sleep 0.5; done
[ -z "$(bngctl sessions --json --search labuser | python3 -c 'import json,sys; print(json.load(sys.stdin) or "")')" ] \
  && step "Disconnect via bngctl" PASS || step "Disconnect via bngctl" FAIL

echo; [ "$fail" -eq 0 ] && echo "LAB RESULT: PASS" || echo "LAB RESULT: FAIL"
exit "$fail"
```

- [ ] **Step 5: Syntax-check scripts**

Run: `for f in scripts/*.sh scripts/lab/*.sh; do bash -n "$f" && echo "ok $f"; done`
Expected: `ok` for all 7 scripts

- [ ] **Step 6: Commit**

```bash
git add system/systemd scripts
git commit -m "feat: installer, uninstaller, deploy and veth PPPoE lab scripts"
```

---

### Task 9: Install on bng01 and pin real accel-cmd output

**Files:**
- Test: `backend/tests/fixtures/show_sessions_1.14.0.txt`, `backend/tests/test_cmd.py` (add one test)
- Modify (only if Step 3 shows different paths): `backend/app/accel/cmd.py` default `binary`, `backend/app/accel/render.py` `DICTIONARY`

- [ ] **Step 1: Deploy and install**

Run (Git Bash, repo root): `bash scripts/deploy.sh && ssh bng01 'sudo bash /opt/bng-platform/src/scripts/install.sh'`
Expected: module table all `ok`, build completes, `accel-ppp 1.14.0` printed, health shows `ACCEL-PPP FAIL` (not started yet) — that is correct at this point.

- [ ] **Step 2: Verify installed paths the code relies on**

Run: `ssh bng01 'ls -l /usr/local/sbin/accel-pppd /usr/local/bin/accel-cmd /usr/local/share/accel-ppp/radius/dictionary /usr/local/lib/accel-ppp/ | head -30; ls /usr/lib/pppd/*/ | grep -i pppoe'`
Expected: all three files exist; module dir contains `libpppoe.so`, `libchap-secrets.so`, `libradius.so`, `libshaper.so`, `libippool.so`; pppd plugin `rp-pppoe.so` present. If any path differs, update the constant in `cmd.py` / `render.py` and the unit file, re-run tests, redeploy.

- [ ] **Step 3: First apply + real session table**

Run: `ssh bng01 'sudo bash /opt/bng-platform/src/scripts/lab/lab-up.sh && sudo bngctl config apply && sudo bngctl health'`
Expected: `applied as version 1`; health `ACCEL-PPP PASS`, `ACCEL-CLI PASS accel-ppp version 1.14.0`, `PPPoE PASS bnglab0`, `RADIUS SKIP`, `nftables FAIL` (not applied yet).

Then bring a lab session up and capture the raw table:
```bash
ssh bng01 'P=$(ls /usr/lib/pppd/*/rp-pppoe.so | head -1); sudo timeout 60 ip netns exec bnglab pppd plugin $P nic-bnglab1 file /etc/bng-platform/lab/pppd-auth noauth nodefaultroute noipdefault updetach maxfail 1 linkname bnglab >/dev/null && /usr/local/bin/accel-cmd -H 127.0.0.1 -p 2001 show sessions sid,ifname,ip,ip6,ip6-dp,calling-sid,called-sid,state,uptime-raw,rx-bytes-raw,tx-bytes-raw,rate-limit,username; sudo ip netns pids bnglab | xargs -r sudo kill' > backend/tests/fixtures/show_sessions_1.14.0.txt
```
Expected: file contains a header line, a `---+---` line, and one row with `labuser`.

- [ ] **Step 4: Add the regression test against real output**

Append to `backend/tests/test_cmd.py`:
```python
from pathlib import Path

REAL = Path(__file__).parent / "fixtures" / "show_sessions_1.14.0.txt"


def test_parses_real_1_14_0_output():
    rows = parse_sessions(REAL.read_text(), SESSION_COLUMNS)
    assert len(rows) == 1
    r = rows[0]
    assert r["username"] == "labuser" and r["ifname"].startswith("ppp") and r["ip"].startswith("100.64.0.")
    assert r["state"] == "active" and r["rx-bytes-raw"].isdigit()
```
Run: `cd backend && python -m pytest tests/test_cmd.py -q`
Expected: PASS. If it fails, the parser is wrong about the real format: fix `parse_sessions` (not the fixture) until this and the synthetic tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/tests
git commit -m "test: pin accel-cmd 1.14.0 show sessions output from bng01"
```

---

### Task 10: Firewall, end-to-end lab, rollback drill, results

**Files:**
- Create: `docs/phase1-results.md`

- [ ] **Step 1: Firewall with confirm-or-revert**

Run: `ssh bng01 'sudo bngctl firewall apply'` then, in a **separate** new connection within 120 s: `ssh bng01 'sudo bngctl firewall confirm && sudo nft list table inet bng_filter'`
Expected: second SSH connects; rules listed with `policy drop` and `tcp dport 22 accept`. Then `ssh bng01 'sudo bngctl health'` shows `nftables PASS`, `conntrack PASS n/…`.

- [ ] **Step 2: Prove the revert timer works**

Run: `ssh bng01 'sudo bngctl firewall apply; sleep 125; sudo nft list table inet bng_filter | head -3; ls /var/lib/bng-platform/filter.nft.pending 2>&1'`
Expected: after the timer the confirmed ruleset is back (table present, loaded from `/etc/bng-platform/nftables/filter.nft`) and the pending file is gone.

- [ ] **Step 3: End-to-end lab**

Run: `ssh bng01 'sudo bash /opt/bng-platform/src/scripts/lab/lab-test.sh'`
Expected: every step PASS and `LAB RESULT: PASS`. Download/upload between 10.2 and 22.5 Mbit/s for the 20480 kbit/s lab rate. If shaping measures outside the band, investigate the rate unit (`[shaper] rate-multiplier`, chap-secrets rate field) before changing thresholds.

- [ ] **Step 4: Rollback drill (bad interface)**

Run:
```bash
ssh bng01 'sudo cp /etc/bng-platform/config.yaml /tmp/bad.yaml && sudo sed -i "s/name: bnglab0/name: bnglab9/" /tmp/bad.yaml && sudo bngctl config diff /tmp/bad.yaml; sudo bngctl config apply /tmp/bad.yaml; echo rc=$?; grep -c bnglab0 /etc/bng-platform/accel-ppp/accel-ppp.conf; sudo tail -1 /var/lib/bng-platform/audit.jsonl; sudo bngctl health | head -3'
```
Expected: diff shows `-interface=bnglab0` / `+interface=bnglab9`; apply prints `health check failed, previous configuration restored` with `PPPoE: not serving: bnglab9`; `rc=1`; grep prints `1`; last audit line has `"result": "rolled_back"`; health `PPPoE PASS`.

- [ ] **Step 5: Restart guard drill**

Run: `ssh bng01 'sudo sed "s/^node: bng01/node: bng01\nthread_count: 4/" /etc/bng-platform/config.yaml > /tmp/t.yaml; sudo bngctl config apply /tmp/t.yaml; echo rc=$?'`
Expected: refused with `need an accel-ppp restart ... --allow-restart`, `rc=1`, no version added (`sudo bngctl config history` unchanged).

- [ ] **Step 6: Backup/restore + data-plane independence**

Run: `ssh bng01 'A=$(sudo bngctl backup) && echo $A && sudo bngctl restore $A'` → Expected: `no changes`.
Data-plane independence: bring the lab session up (as in Task 9 Step 3), then `sudo mv /opt/bng-platform/venv /opt/bng-platform/venv.off`, run `ip netns exec bnglab ping -c 5 <gw>` → Expected: 0% loss (management code absent, session unaffected); `sudo mv /opt/bng-platform/venv.off /opt/bng-platform/venv`.

- [ ] **Step 7: Record results**

Write `docs/phase1-results.md` with: date; accel-pppd version; each lab-test step and measured Mbit/s; firewall confirm/revert outcome; rollback drill outcome; restart-guard outcome; data-plane independence outcome; and an **Open items** list: (1) Jaze: add NAS, set `aaa: radius`, put secret in `radius.secret`, run `bngctl radius test` and `bngctl radius test --user`, verify Accounting Start/Interim/Stop arrive in Jaze; (2) confirm which attribute Jaze sends the rate in (`shaper.attr`); (3) second isolated vNIC + multiqueue on Proxmox. Nothing in this file says a Jaze-dependent item passed.

- [ ] **Step 8: Commit**

```bash
git add docs/phase1-results.md
git commit -m "docs: Phase 1 lab results on bng01"
```

---

### Task 11: Documentation

**Files:**
- Create: `README.md`, `docs/installation.md`, `docs/configuration.md`

- [ ] **Step 1: Write docs**

`README.md`: one-paragraph purpose; the control-plane/data-plane diagram from the brief; current phase status table (Phase 1 done / 2–8 pending); links to `ARCHITECTURE_ASSESSMENT.md`, `docs/installation.md`, `docs/configuration.md`, `docs/phase1-results.md`; statement that no subscriber-count or throughput claim is made until Phase 8 benchmarks.

`docs/installation.md`: prerequisites (Ubuntu 24.04 / Debian 12+, root, internet); `scripts/deploy.sh` from a workstation; `sudo bash scripts/install.sh`; lab bring-up; first `bngctl config apply`; firewall apply + confirm from a new SSH session; `health-check.sh`; uninstall (`--purge`, `--yes`); Proxmox notes (VirtIO, multiqueue `queues=N` then `ethtool -L <nic> combined N`, host CPU type, no overcommit during benchmarks, isolated bridge for the access NIC, passthrough/SR-IOV + q35 for real throughput) with the explicit warning that virtual-NIC results do not indicate 40G capability.

`docs/configuration.md`: every `config.yaml` key with type/default/range (from `model.py`); where each lands in `accel-ppp.conf`; secret handling (`radius.secret` 0600 → `radius.conf` include; never in versions/audit); the safe-apply pipeline and which sections force a restart; `config diff/apply/rollback/history`; audit log format (`/var/lib/bng-platform/audit.jsonl` fields); switching from `aaa: lab` to `aaa: radius` step by step; the chap-secrets lab module is lab-only.

- [ ] **Step 2: Commit**

```bash
git add README.md docs/installation.md docs/configuration.md
git commit -m "docs: README, installation and configuration guides"
```

---

## Self-review notes

- Spec Phase 1 coverage: Linux setup (T8/T9), ACCEL-PPP build (T8/T9), PPPoE (T2/T10), Jaze RADIUS config + test client (T2/T3/T7; live verification is an open item — Jaze not ready), IP assignment (T2/T10), session establishment (T10), accounting (config T2; verification needs Jaze → open item), health check (T6/T7/T10). Safe apply (spec §9, §24, §25) T5/T10. Backup/restore (§46) T5/T7/T10. Firewall on public host (assessment §9.3) T6/T10. CLI subset (§38) T7; qos/nat/benchmark deferred to their phases.
- Deferred by design: PostgreSQL audit/versions (Phase 5 imports the JSONL + version dirs), sysctl/forwarding (Phase 2), tuning module (later phase), VLAN-mon (needs out-of-tree module; with the second vNIC).
- Type/name consistency checked: `Rendered.main/.secrets`, `critical_failures(cfg, accel) -> list[str]`, `ConfigManager.audit(**event)`, `AccelService` methods used by manager (`is_active/start/stop/restart/reload`), `SESSION_COLUMNS`/`SID_RE` used by CLI.
