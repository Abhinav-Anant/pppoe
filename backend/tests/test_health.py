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


def test_dns_check_queries_the_configured_servers(base_cfg):
    import socket
    import threading

    assert health.check_dns(BngConfig.model_validate(base_cfg)).status == "SKIP"  # no dns configured
    srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]

    def answer():
        data, addr = srv.recvfrom(512)
        srv.sendto(data[:2] + b"\x80\x00" + data[4:], addr)  # same id, QR=1

    threading.Thread(target=answer, daemon=True).start()
    base_cfg["dns"] = ["127.0.0.1"]
    cfg = BngConfig.model_validate(base_cfg)
    assert health.check_dns(cfg, port=port, timeout=2).status == "PASS"
    srv.close()
    assert health.check_dns(cfg, port=port, timeout=0.3).status == "FAIL"
