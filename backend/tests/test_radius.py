"""Vendor-neutral RADIUS: per-server secrets/options, rate attributes, write-only secret API."""
import pytest
from pydantic import ValidationError

from app.accel.render import render
from app.config.model import BngConfig
from app.monitoring import health
from tests.test_api import api, login  # noqa: F401  (fixture)

SECRET, OTHER = "s3cret-default", "other-s3cret"


def test_per_server_secrets_and_options(radius_cfg):
    radius_cfg["radius"]["servers"].append({"address": "192.0.2.11", "backup": True, "weight": 3,
                                            "req_limit": 50, "fail_timeout": 30, "max_fail": 5})
    radius_cfg["radius"].update(bind="192.0.2.1", strip_realm=True, default_realm="isp.example")
    r = render(BngConfig.model_validate(radius_cfg), {"": SECRET, "192.0.2.11": OTHER})
    lines = r.secrets.splitlines()
    assert lines[0] == f"server=192.0.2.10,{SECRET},auth-port=1812,acct-port=1813"
    assert lines[1] == f"server=192.0.2.11,{OTHER},auth-port=1812,acct-port=1813,req-limit=50,fail-timeout=30," \
                       "max-fail=5,weight=3,backup"
    assert lines[2].endswith(f",{SECRET}")  # dae-server uses the default secret
    radius = r.main.split("[radius]")[1].split("\n[")[0]
    assert "bind=192.0.2.1" in radius and "strip-realm=1" in radius and "default-realm=isp.example" in radius
    assert SECRET not in r.main and OTHER not in r.main


def test_server_without_any_secret_is_refused(radius_cfg):
    with pytest.raises(ValueError, match="192.0.2.10"):
        render(BngConfig.model_validate(radius_cfg), {"192.0.2.99": SECRET})


@pytest.mark.parametrize("shaper, expect", [
    ({"attr": "Mikrotik-Rate-Limit", "vendor": "Mikrotik"}, ["attr=Mikrotik-Rate-Limit", "vendor=Mikrotik"]),
    ({"attr_down": "WISPr-Bandwidth-Max-Down", "attr_up": "WISPr-Bandwidth-Max-Up", "vendor": "WISPr",
      "rate_multiplier": 0.001},
     ["attr-down=WISPr-Bandwidth-Max-Down", "attr-up=WISPr-Bandwidth-Max-Up", "rate-multiplier=0.001"]),
])
def test_vendor_rate_attributes(radius_cfg, shaper, expect):
    radius_cfg["shaper"] = shaper
    sect = render(BngConfig.model_validate(radius_cfg), SECRET).main.split("[shaper]")[1].split("\n[")[0]
    for line in expect:
        assert line in sect
    assert ("\nattr=" in sect) == ("attr_down" not in shaper)


def test_attr_down_needs_attr_up(radius_cfg):
    radius_cfg["shaper"] = {"attr_down": "WISPr-Bandwidth-Max-Down"}
    with pytest.raises(ValidationError, match="attr_up"):
        BngConfig.model_validate(radius_cfg)


def test_health_skips_when_status_server_unsupported(radius_cfg):
    radius_cfg["radius"]["status_server"] = False
    c = health.check_radius(BngConfig.model_validate(radius_cfg), {"": SECRET})
    assert c.status == "SKIP" and "status_server" in c.detail


def test_secret_api_is_write_only(api, radius_cfg):
    login(api, "noc_operator")
    assert api.put("/api/radius/secret", json={"secret": SECRET}).status_code == 403
    login(api, "network_admin")
    bad = api.put("/api/radius/secret", json={"secret": "has space in it"})
    assert bad.status_code == 422 and "has space" not in bad.text  # rejected value is not echoed
    r = api.put("/api/radius/secret", json={"secret": SECRET})
    assert r.status_code == 200 and SECRET not in r.text and r.json()["secrets"] == {"default": True}
    r = api.put("/api/radius/secret", json={"secret": OTHER, "server": "192.0.2.11"})
    assert r.json()["secrets"] == {"default": True, "192.0.2.11": True}
    paths = api.node.paths
    assert paths.secret.read_text().strip() == SECRET
    assert (paths.radius_secrets / "192.0.2.11.secret").read_text().strip() == OTHER
    status = api.get("/api/radius/status").json()
    assert status["secrets"] == {"default": True, "192.0.2.11": True} and SECRET not in str(status)
    audit = paths.audit_log.read_text()
    assert "radius_secret_set" in audit and SECRET not in audit and OTHER not in audit


def test_shaper_always_resets_vendor(radius_cfg):
    """accel-ppp 1.14.0 keeps a removed vendor= across reloads (shaping silently disabled)."""
    sect = render(BngConfig.model_validate(radius_cfg), SECRET).main.split("[shaper]")[1].split("\n[")[0]
    assert "vendor=0" in sect.splitlines()


def test_public_console_port_in_host_firewall(base_cfg):
    from app.networking import firewall
    base_cfg["firewall"] = {"console_port": 443}
    assert "        tcp dport 443 accept\n" in firewall.render(BngConfig.model_validate(base_cfg))
    base_cfg["firewall"]["console_sources"] = ["198.51.100.0/24"]
    assert "tcp dport 443 ip saddr { 198.51.100.0/24 } accept" in firewall.render(BngConfig.model_validate(base_cfg))
    base_cfg["firewall"] = {}
    assert "dport 443" not in firewall.render(BngConfig.model_validate(base_cfg))
