import json

import pytest
from pydantic import ValidationError

from app.config.model import BngConfig
from app.qos import cake

QOS = {"cake": {"upload": {"bandwidth_mbit": 650}, "download": {"bandwidth_mbit": 480}}}
CAPS = {"cake": True, "cake_mq": False, "options": set(cake.KNOWN_OPTIONS)}

# `tc qdisc add dev lo root cake help` on bng01 (iproute2 6.1)
HELP = """Usage: ... cake [ bandwidth RATE | unlimited* | autorate-ingress ]
                [ rtt TIME | datacentre | lan | metro | regional |
                  internet* | oceanic | satellite | interplanetary ]
                [ besteffort | diffserv8 | diffserv4 | diffserv3* ]
                [ flowblind | srchost | dsthost | hosts | flows |
                  dual-srchost | dual-dsthost | triple-isolate* ]
                [ nat | nonat* ]
                [ wash | nowash* ]
                [ split-gso* | no-split-gso ]
                [ ack-filter | ack-filter-aggressive | no-ack-filter* ]
                [ memlimit LIMIT ]
                [ fwmark MASK ]
                [ ptm | atm | noatm* ] [ overhead N | conservative | raw* ]
                [ mpu N ] [ ingress | egress* ]
                (* marks defaults)"""


def cfg(base_cfg, **cake_over):
    q = json.loads(json.dumps(QOS))
    q["cake"].update(cake_over)
    base_cfg["qos"] = q
    return BngConfig.model_validate(base_cfg)


def test_model_defaults(base_cfg):
    c = cfg(base_cfg).qos.cake
    assert c.enabled and c.mode == "cake" and c.nat and c.rtt_ms == 100
    assert c.upload.isolation == "dual-srchost" and c.download.isolation == "dual-dsthost"
    assert c.upload.diffserv == "besteffort" and c.upload.ack_filter == "no-ack-filter"


@pytest.mark.parametrize("over", [{"rtt_ms": 0}, {"overhead": 300}, {"link_layer": "adsl"}, {"ecn": True}])
def test_model_rejects(base_cfg, over):
    with pytest.raises(ValidationError):
        cfg(base_cfg, **over)


def test_parse_capabilities():
    opts = cake.parse_capabilities(HELP)
    assert {"dual-dsthost", "ack-filter-aggressive", "fwmark", "mpu", "ingress", "nat"} <= opts
    assert "ecn" not in opts


def test_render_both_directions(base_cfg):
    s = cake.render(cfg(base_cfg, overhead=18, mpu=64), CAPS)
    lines = s.splitlines()
    up = next(l for l in lines if l.startswith("tc qdisc replace dev eth0 root cake"))
    down = next(l for l in lines if l.startswith("tc qdisc replace dev bngifb0 root cake"))
    assert "bandwidth 650mbit" in up and "dual-srchost nat" in up and "ingress" not in up
    assert "bandwidth 480mbit" in down and "dual-dsthost nat" in down and down.endswith("ingress")
    assert "overhead 18 mpu 64" in up and "rtt 100ms" in up and "nowash" in up
    redirect = next(i for i, l in enumerate(lines) if "mirred egress redirect dev bngifb0" in l)
    assert redirect > lines.index(down)          # redirect only after the IFB queue exists
    assert "ppp" not in s                         # aggregate only: nothing per subscriber


def test_render_ack_filter_only_where_set(base_cfg):
    c = cfg(base_cfg, upload={"bandwidth_mbit": 650, "ack_filter": "ack-filter"})
    s = cake.render(c, CAPS)
    assert " ack-filter " in next(l for l in s.splitlines() if "dev eth0 root cake" in l) + " "
    assert "no-ack-filter" in next(l for l in s.splitlines() if "dev bngifb0 root cake" in l)


def test_render_disabled_only_clears(base_cfg):
    s = cake.render(cfg(base_cfg, enabled=False), CAPS)
    assert "tc qdisc del dev eth0 root" in s and "ip link del bngifb0" in s
    assert "root cake" not in s and "ip link add" not in s


def test_cake_mq_refused_when_not_detected(base_cfg):
    with pytest.raises(ValueError, match="cake_mq"):
        cake.render(cfg(base_cfg, mode="cake_mq"), CAPS)


def test_unsupported_option_refused(base_cfg):
    caps = dict(CAPS, options=set(CAPS["options"]) - {"dual-dsthost"})
    with pytest.raises(ValueError, match="dual-dsthost"):
        cake.render(cfg(base_cfg), caps)


def _q(**over):
    q = {"kind": "cake", "root": True, "options": {"bandwidth": 12500000},
         "bytes": 1000, "packets": 10, "drops": 2, "overlimits": 5, "backlog": 0,
         "tins": [{"ecn_mark": 3, "ack_drops": 1, "peak_delay_us": 900, "avg_delay_us": 120},
                  {"ecn_mark": 1, "ack_drops": 0, "peak_delay_us": 400, "avg_delay_us": 300}]}
    q.update(over)
    return q


def test_parse_cake_json():
    s = cake.parse_cake_json(json.dumps([{"kind": "ingress", "root": False}, _q()]))
    assert s == {"bandwidth_mbit": 100.0, "bytes": 1000, "packets": 10, "drops": 2, "overlimits": 5,
                 "backlog": 0, "ecn_mark": 4, "ack_drops": 1, "peak_delay_us": 900, "avg_delay_us": 300}


def test_parse_cake_json_absent():
    assert cake.parse_cake_json(json.dumps([{"kind": "fq_codel", "root": True}])) is None
    assert cake.parse_cake_json("") is None


def test_health_cake_skip_without_qos(base_cfg):
    from app.monitoring import health
    assert health.check_cake(BngConfig.model_validate(base_cfg)).status == "SKIP"


def test_parse_real_tc_json():
    # captured on bng01 (iproute2 6.1) from the IFB download queue after a 100 Mbit load test
    from pathlib import Path
    s = cake.parse_cake_json((Path(__file__).parent / "fixtures" / "tc_cake_ifb.json").read_text())
    assert s["bandwidth_mbit"] == 100.0 and s["drops"] > 0 and s["packets"] > 0
