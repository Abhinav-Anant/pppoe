import pytest

from app import tuning


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(tuning, "_run", lambda *cmd: "")
    p = tmp_path / "proc"
    (p / "net").mkdir(parents=True)
    (p / "net/softnet_stat").write_text("0000000a 00000000 00000000\n0000000b 00000002 00000003\n")
    (p / "meminfo").write_text("MemTotal:       16384000 kB\n")
    (p / "softirqs").write_text("  CPU0 CPU1\n NET_TX: 1 2\n NET_RX: 3 4\n")
    (p / "interrupts").write_text("  CPU0 CPU1\n 44: 0 99 PCI-MSI virtio2-input.0\n")
    for k, v in {"net.core.netdev_max_backlog": "1000", "net.core.netdev_budget": "300",
                 "net.core.rmem_max": "212992", "net.core.wmem_max": "8388608",
                 "net.netfilter.nf_conntrack_max": "262144", "net.netfilter.nf_conntrack_buckets": "65536"}.items():
        f = tuning.sysctl_path(k, tmp_path)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(v + "\n")
    nic = tmp_path / "sys/class/net/ens18"
    (nic / "device").mkdir(parents=True)
    (nic / "queues/rx-0").mkdir(parents=True)
    (nic / "queues/rx-0/rps_cpus").write_text("0\n")
    (tmp_path / "sys/class/net/bnglab0/device").mkdir(parents=True)  # lab veth: skipped
    return tmp_path


def test_recommend_from_hardware(root):
    d = tuning.diagnostics(root)
    assert d["cpus"] == 2 and [n["name"] for n in d["nics"]] == ["ens18"]
    recs = {r.key: r for r in tuning.recommend(d)}
    assert recs["net.core.netdev_max_backlog"].recommended == "16384"  # softnet drops seen
    assert recs["net.core.netdev_budget"].recommended == "600"  # time_squeeze seen
    assert "net.core.wmem_max" not in recs  # never lowered
    assert recs["net.netfilter.nf_conntrack_max"].recommended == "1048576"  # 16 GB / 32 / 320 B -> pow2
    assert recs["net.netfilter.nf_conntrack_buckets"].recommended == "262144"
    assert recs["rps:ens18/rx-0"].recommended == "3" and recs["rps:ens18/rx-0"].apply


def test_apply_reapply_rollback(root, tmp_path):
    state, conf = tmp_path / "state", tmp_path / "90-bng.conf"
    recs = tuning.recommend(tuning.diagnostics(root))
    done = tuning.apply(recs, root, state, conf)
    assert len(done) == len(recs)
    assert tuning.sysctl_path("net.core.rmem_max", root).read_text().strip() == "4194304"
    assert "net.core.rmem_max = 4194304" in conf.read_text() and "rps" not in conf.read_text()
    (root / "sys/class/net/ens18/queues/rx-0/rps_cpus").write_text("0\n")  # reboot resets RPS
    assert tuning.reapply(root, state) == len(recs)
    assert (root / "sys/class/net/ens18/queues/rx-0/rps_cpus").read_text().strip() == "3"
    old = tuning.rollback(root, state, conf)
    assert old["net.core.rmem_max"] == "212992" and not conf.exists()
    assert tuning.sysctl_path("net.core.rmem_max", root).read_text().strip() == "212992"


def test_apply_is_all_or_nothing(root, tmp_path, monkeypatch):
    recs = [tuning.Rec("net.core.rmem_max", "212992", "4194304", "", True),
            tuning.Rec("net.core.nope", "1", "2", "", True)]  # does not exist on this kernel
    with pytest.raises(OSError):
        tuning.apply(recs, root, tmp_path / "state", tmp_path / "c.conf")
    assert tuning.sysctl_path("net.core.rmem_max", root).read_text().strip() == "212992"
