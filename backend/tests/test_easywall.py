from app.config.model import BngConfig
from app.monitoring import health
from app.networking import firewall

EW_TOML = b"""
socket_path = "/run/easywall/core.sock"

[routing]
mode     = "%s"
networks = []

[firewall]
"""


def test_default_host_input_is_bng(base_cfg):
    cfg = BngConfig.model_validate(base_cfg)
    assert cfg.firewall.host_input == "bng"
    assert "chain input" in firewall.render(cfg)


def test_easywall_owns_input(base_cfg):
    base_cfg["firewall"] = {"host_input": "easywall"}
    text = firewall.render(BngConfig.model_validate(base_cfg))
    assert "chain input" not in text
    assert "chain forward" in text and "policy drop" in text   # data plane stays with bng


def test_easywall_guard(tmp_path, monkeypatch):
    p = tmp_path / "easywall.toml"
    monkeypatch.setattr(health, "EASYWALL_TOML", p)
    assert health.check_easywall().status == "SKIP"            # not installed
    p.write_bytes(EW_TOML % b"open")
    monkeypatch.setattr(health, "_run", lambda *a: type("P", (), {"returncode": 0})())
    assert health.check_easywall().status == "PASS"
    p.write_bytes(EW_TOML % b"closed")
    c = health.check_easywall()
    assert c.status == "FAIL" and "routing.mode" in c.detail
