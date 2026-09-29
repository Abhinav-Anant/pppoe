import json
import tarfile

import pytest

from app.config.manager import ApplyError, ConfigManager, Paths, changed_sections
from tests.conftest import write_cfg

SECRET = "S3cr3t-XYZ!"


class FakeDaemon:
    def __init__(self):
        self.active, self.calls = False, []

    def is_active(self):
        return self.active

    def start(self):
        self.calls.append("start"); self.active = True

    def stop(self):
        self.calls.append("stop"); self.active = False

    def restart(self):
        self.calls.append("restart")

    def reload(self):
        self.calls.append("reload")

    def pppoe_add(self, opt):
        self.calls.append(f"add:{opt}")

    def pppoe_del(self, name):
        self.calls.append(f"del:{name}")


@pytest.fixture
def env(tmp_path):
    paths = Paths(tmp_path / "etc", tmp_path / "state")
    daemon, failures = FakeDaemon(), []
    mgr = ConfigManager(paths, daemon, lambda cfg: list(failures))
    return mgr, paths, daemon, failures, tmp_path


def audit(paths):
    return [json.loads(line) for line in paths.audit_log.read_text().splitlines()]


def test_first_apply_starts_daemon_and_versions(env, base_cfg):
    mgr, paths, daemon, _, tmp = env
    assert mgr.apply(write_cfg(tmp / "c.yaml", base_cfg), "root", "test") == "applied as version 1"
    assert daemon.calls == ["start"]
    assert "interface=veth0" in paths.accel_conf.read_text()
    assert (paths.versions / "0001" / "accel-ppp.conf").exists()
    assert audit(paths)[-1]["result"] == "applied"


def test_reloadable_change_reloads(env, base_cfg):
    mgr, paths, daemon, _, tmp = env
    mgr.apply(write_cfg(tmp / "c.yaml", base_cfg), "root", "t")
    base_cfg["ppp"] = {"mtu": 1480}
    assert mgr.apply(write_cfg(tmp / "c.yaml", base_cfg), "root", "t") == "applied as version 2"
    assert daemon.calls == ["start", "reload"]
    assert "+mtu=1480" in audit(paths)[-1]["diff"]


def test_unchanged_is_noop(env, base_cfg):
    mgr, _, daemon, _, tmp = env
    f = write_cfg(tmp / "c.yaml", base_cfg)
    mgr.apply(f, "root", "t")
    assert mgr.apply(f, "root", "t") == "no changes"
    assert daemon.calls == ["start"]


def test_restart_needed_is_refused(env, base_cfg):
    mgr, paths, daemon, _, tmp = env
    mgr.apply(write_cfg(tmp / "c.yaml", base_cfg), "root", "t")
    before = paths.accel_conf.read_text()
    base_cfg["thread_count"] = 4
    with pytest.raises(ApplyError, match="allow-restart"):
        mgr.apply(write_cfg(tmp / "c.yaml", base_cfg), "root", "t")
    assert paths.accel_conf.read_text() == before and daemon.calls == ["start"]
    assert mgr.apply(tmp / "c.yaml", "root", "t", allow_restart=True) == "applied as version 2"
    assert daemon.calls[-1] == "restart"


def test_failed_health_rolls_back(env, base_cfg):
    mgr, paths, daemon, failures, tmp = env
    mgr.apply(write_cfg(tmp / "c.yaml", base_cfg), "root", "t")
    good_conf, good_yaml = paths.accel_conf.read_text(), paths.config.read_text()
    failures.append("PPPoE: not serving veth9")
    base_cfg["pppoe"]["interfaces"].append({"name": "veth9"})
    with pytest.raises(ApplyError, match="restored"):
        mgr.apply(write_cfg(tmp / "c.yaml", base_cfg), "root", "t")
    assert paths.accel_conf.read_text() == good_conf and paths.config.read_text() == good_yaml
    assert daemon.calls == ["start", "reload", "add:veth9", "reload", "del:veth9"]
    assert audit(paths)[-1]["result"] == "rolled_back"
    assert [m["version"] for m in mgr.history()] == [1]


def test_failed_first_apply_stops_daemon(env, base_cfg):
    mgr, paths, daemon, failures, tmp = env
    failures.append("boom")
    with pytest.raises(ApplyError):
        mgr.apply(write_cfg(tmp / "c.yaml", base_cfg), "root", "t")
    assert daemon.calls == ["start", "stop"] and not paths.accel_conf.exists()


def test_invalid_candidate_rejected_and_audited(env, base_cfg):
    mgr, paths, daemon, _, tmp = env
    base_cfg["pppoe"]["ac_name"] = "bad name"
    with pytest.raises(ApplyError, match="invalid config"):
        mgr.apply(write_cfg(tmp / "c.yaml", base_cfg), "root", "t")
    assert daemon.calls == [] and audit(paths)[-1]["result"] == "rejected"


def test_rollback_to_previous_version(env, base_cfg):
    mgr, paths, _, _, tmp = env
    mgr.apply(write_cfg(tmp / "c.yaml", base_cfg), "root", "t")
    base_cfg["ppp"] = {"mtu": 1480}
    mgr.apply(write_cfg(tmp / "c.yaml", base_cfg), "root", "t")
    assert mgr.rollback(None, "root", "t") == "applied as version 3"
    assert "mtu=1492" in paths.accel_conf.read_text()


def test_secret_never_leaks(env, radius_cfg):
    mgr, paths, _, _, tmp = env
    paths.secret.parent.mkdir(parents=True)
    paths.secret.write_text(SECRET + "\n")
    mgr.apply(write_cfg(tmp / "c.yaml", radius_cfg), "root", "t")
    assert SECRET in paths.secrets_include.read_text()
    assert oct(paths.secrets_include.stat().st_mode & 0o777) in ("0o600", "0o666")  # 0o666 on Windows
    for f in [paths.accel_conf, paths.config, paths.audit_log, *paths.versions.rglob("*")]:
        if f.is_file():
            assert SECRET not in f.read_text(), f


def test_backup_and_restore(env, base_cfg):
    mgr, paths, _, _, tmp = env
    mgr.apply(write_cfg(tmp / "c.yaml", base_cfg), "root", "t")
    archive = mgr.backup_archive()
    with tarfile.open(archive) as t:
        assert "etc/config.yaml" in t.getnames()
    base_cfg["ppp"] = {"mtu": 1480}
    mgr.apply(write_cfg(tmp / "c.yaml", base_cfg), "root", "t")
    assert mgr.restore_archive(archive, "root", "t") == "applied as version 3"
    assert "mtu=1492" in paths.accel_conf.read_text()


def test_new_pppoe_interface_added_live(env, base_cfg):
    # accel-ppp 1.14.0 reads [pppoe] interface= only at start; reload ignores it
    mgr, _, daemon, _, tmp = env
    mgr.apply(write_cfg(tmp / "c.yaml", base_cfg), "root", "t")
    base_cfg["pppoe"]["interfaces"].append({"name": "eth0.4044", "padi_limit": 100})
    assert mgr.apply(write_cfg(tmp / "c.yaml", base_cfg), "root", "t") == "applied as version 2"
    assert daemon.calls == ["start", "reload", "add:eth0.4044,padi-limit=100"]


def test_removing_pppoe_interface_needs_allow_restart(env, base_cfg):
    mgr, _, daemon, _, tmp = env
    base_cfg["pppoe"]["interfaces"].append({"name": "veth1"})
    mgr.apply(write_cfg(tmp / "c.yaml", base_cfg), "root", "t")
    base_cfg["pppoe"]["interfaces"].pop()
    with pytest.raises(ApplyError, match="veth1.*allow-restart"):
        mgr.apply(write_cfg(tmp / "c.yaml", base_cfg), "root", "t")
    assert mgr.apply(tmp / "c.yaml", "root", "t", allow_restart=True) == "applied as version 2"
    assert daemon.calls == ["start", "reload", "del:veth1"]


def test_changing_pppoe_interface_options_readds(env, base_cfg):
    mgr, _, daemon, _, tmp = env
    mgr.apply(write_cfg(tmp / "c.yaml", base_cfg), "root", "t")
    base_cfg["pppoe"]["interfaces"][0]["padi_limit"] = 50
    mgr.apply(write_cfg(tmp / "c.yaml", base_cfg), "root", "t", allow_restart=True)
    assert daemon.calls == ["start", "reload", "del:veth0", "add:veth0,padi-limit=50"]


def test_changed_sections():
    assert changed_sections("[a]\nx=1\n[b]\ny=1\n", "[a]\nx=1\n[b]\ny=2\n[c]\n") == {"b", "c"}
