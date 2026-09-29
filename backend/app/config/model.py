"""Desired state of one BNG node (/etc/bng-platform/config.yaml).

Every string that ends up in accel-ppp.conf is constrained to TOKEN/IFNAME,
so no value can smuggle ',', '=', whitespace or a newline into the generated
file. Secrets are deliberately absent from this model.
"""
from __future__ import annotations

from ipaddress import IPv4Address, IPv4Network
from itertools import combinations
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
