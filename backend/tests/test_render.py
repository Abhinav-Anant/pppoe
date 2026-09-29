import pytest

from app.accel.render import SECRETS_INCLUDE, render
from app.accel.validate import validate_text
from app.config.model import BngConfig

SECRET = "S3cr3t-XYZ!"


def test_lab_render_is_valid(base_cfg):
    r = render(BngConfig.model_validate(base_cfg), None)
    assert validate_text(r.main) == []
    assert r.secrets == ""
    assert "chap-secrets" in r.main.split("[core]")[0]      # module loaded
    assert "interface=veth0" in r.main
    assert "100.64.0.1-254,name=p" in r.main
    assert "tcp=127.0.0.1:2001" in r.main


@pytest.mark.parametrize("network, lines", [
    ("100.64.0.0/23", ["100.64.0.1-254,name=p", "100.64.1.1-254,name=p"]),
    ("100.64.0.0/30", ["100.64.0.1-3,name=p"]),
    ("100.64.0.252/30", ["100.64.0.252-254,name=p"]),
    ("100.64.0.7/32", ["100.64.0.7-7,name=p"]),
])
def test_pools_skip_dot0_and_dot255(base_cfg, network, lines):
    # accel-ppp 1.14.0 ippool.c hands out every CIDR address incl. .0/.255
    base_cfg["ip_pools"]["pools"][0]["network"] = network
    r = render(BngConfig.model_validate(base_cfg), None)
    assert [l for l in r.main.splitlines() if l.startswith("100.64.")] == lines
    assert validate_text(r.main) == []


def test_radius_secret_only_in_include(radius_cfg):
    r = render(BngConfig.model_validate(radius_cfg), SECRET)
    assert validate_text(r.main) == []
    assert SECRET not in r.main
    assert f"server=192.0.2.10,{SECRET},auth-port=1812,acct-port=1813" in r.secrets
    assert f"dae-server=192.0.2.1:3799,{SECRET}" in r.secrets
    assert "[" not in r.secrets                                # no section header
    radius = r.main.split("[radius]")[1].split("\n[")[0]
    assert radius.strip().endswith(f"$include {SECRETS_INCLUDE}")
    assert "dae-allowed=192.0.2.10/32" in radius


def test_radius_mode_requires_secret(radius_cfg):
    with pytest.raises(ValueError, match="secret"):
        render(BngConfig.model_validate(radius_cfg), None)


@pytest.mark.parametrize("secret", ["short", "has space", "a,b-cdefgh", "line\nbreak"])
def test_bad_secret_rejected(radius_cfg, secret):
    with pytest.raises(ValueError, match="secret"):
        render(BngConfig.model_validate(radius_cfg), secret)


def test_backup_server_flag(radius_cfg):
    radius_cfg["radius"]["servers"].append({"address": "192.0.2.11", "backup": True})
    r = render(BngConfig.model_validate(radius_cfg), SECRET)
    assert r.secrets.splitlines()[1].endswith(",backup")


GOOD = "[modules]\npppoe\nippool\n[pppoe]\ninterface=eth1\n[ip-pool]\ngw-ip-address=10.0.0.1\n10.1.0.0/24,name=a\n[cli]\ntcp=127.0.0.1:2001\n"


def test_validator_accepts_minimal():
    assert validate_text(GOOD) == []


@pytest.mark.parametrize("bad, msg", [
    (GOOD + "[pppoe]\nac-name=x\n", "duplicate section"),
    (GOOD.replace("interface=eth1", "interfase=eth1"), "unknown directive 'interfase'"),
    (GOOD + "[bogus]\n", "unknown section"),
    (GOOD.replace("ippool", "ipool"), "unknown module"),
    (GOOD + "[radius]\nserver=1.2.3.4,secret\n", "include file"),
    (GOOD + "$include /etc/passwd\n", "$include"),
    (GOOD.replace("10.1.0.0/24,name=a", "10.1.0.0/33,name=a"), "bad pool"),
    (GOOD.replace("10.1.0.0/24,name=a", "10.1.0.0/24,colour=a"), "bad pool"),
    ("[pppoe]\ninterface=eth1\n", "missing section [modules]"),
    (GOOD + "[ppp]\nmtu=" + "1" * 1100 + "\n", "1023"),
])
def test_validator_rejects(bad, msg):
    errors = validate_text(bad)
    assert any(msg in e for e in errors), errors
