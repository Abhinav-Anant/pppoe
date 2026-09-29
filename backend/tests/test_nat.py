import json
from pathlib import Path

from app.config.model import BngConfig
from app.monitoring import health
from app.networking import firewall

NAT = {"pools": [{"name": "lab", "subscribers": ["100.64.0.0/24"], "public_start": "192.0.2.1"}]}


def test_render_forward_and_snat(base_cfg):
    base_cfg["nat"] = NAT
    text = firewall.render(BngConfig.model_validate(base_cfg))
    fwd = text.split("chain forward")[1].split("\n    }\n")[0]
    assert "policy drop" in fwd
    assert "tcp flags & (syn | rst) == syn tcp option maxseg size set rt mtu" in fwd
    assert 'iifname "ppp*" oifname "eth0" ip saddr { 100.64.0.0/24 } counter accept' in fwd
    assert "table ip bng_nat\ndelete table ip bng_nat\n" in text
    # nft: a port range is only valid after a transport-protocol match
    assert ('oifname "eth0" ip saddr { 100.64.0.0/24 } meta l4proto { tcp, udp } counter '
            'snat to 192.0.2.1:1024-65535 persistent comment "nat-pool lab"') in text
    assert ('oifname "eth0" ip saddr { 100.64.0.0/24 } counter snat to 192.0.2.1 persistent '
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
        {"rule": {"family": "ip", "table": "bng_nat", "chain": "postrouting", "handle": 4,
                  "comment": "nat-pool lab",
                  "expr": [{"counter": {"packets": 2, "bytes": 168}}, {"snat": {}}]}},
    ]}
    assert firewall.parse_nat_counters(json.dumps(doc)) == [{"pool": "lab", "packets": 7, "bytes": 588}]


def test_nat_health_skip_without_nat(base_cfg):
    assert health.check_nat(BngConfig.model_validate(base_cfg)).status == "SKIP"


REAL = Path(__file__).parent / "fixtures" / "nft_bng_nat.json"


def test_parse_real_nft_json():
    # captured on bng01 (nftables 1.0.9) after the lab NAT test
    [c] = firewall.parse_nat_counters(REAL.read_text())
    assert c["pool"] == "lab" and c["packets"] > 0 and c["bytes"] > 0
