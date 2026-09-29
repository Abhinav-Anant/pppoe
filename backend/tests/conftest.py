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
