from app.config.model import BngConfig
from app.monitoring import health


class FakeAccel:
    def __init__(self, rates):
        self.rows = [{"sid": f"s{i}", "username": f"u{i}", "rate-limit": r} for i, r in enumerate(rates)]

    def sessions(self, match=None):
        return self.rows


def cfg(base_cfg, **shaper):
    base_cfg["shaper"] = {"attr": "Filter-Id", **shaper}
    return BngConfig.model_validate(base_cfg)


def test_rates_within_ceiling_pass(base_cfg):
    c = health.check_rates(cfg(base_cfg, max_rate_mbit=1000, require_rate=True),
                           FakeAccel(["100000/20000", "1000000/1000000"]))
    assert c.status == "PASS" and "2 sessions" in c.detail


def test_g_suffix_bug_caught(base_cfg):
    # accel-ppp 1.14.0: "1G/1G" from RADIUS becomes 10000000 kbit (10 Gbit), measured on bng01
    c = health.check_rates(cfg(base_cfg, max_rate_mbit=1000), FakeAccel(["10000000/10000000", "20480/20480"]))
    assert c.status == "FAIL" and "1 above 1000 Mbit" in c.detail and "u0" in c.detail


def test_missing_rate_when_required(base_cfg):
    c = health.check_rates(cfg(base_cfg, require_rate=True), FakeAccel(["", "20480/20480"]))
    assert c.status == "FAIL" and "1 without" in c.detail


def test_missing_rate_allowed_by_default(base_cfg):
    assert health.check_rates(cfg(base_cfg), FakeAccel([""])).status == "PASS"


def test_no_shaper_skips(base_cfg):
    base_cfg["shaper"] = None
    assert health.check_rates(BngConfig.model_validate(base_cfg), FakeAccel([])).status == "SKIP"
