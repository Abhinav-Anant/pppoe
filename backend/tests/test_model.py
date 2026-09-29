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
