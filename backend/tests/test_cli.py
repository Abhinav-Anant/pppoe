from pathlib import Path

from app.cli import main
from tests.conftest import write_cfg

EXAMPLE = Path(__file__).resolve().parents[2] / "system" / "config.example.yaml"


def test_validate_example(capsys):
    assert main(["config", "validate", str(EXAMPLE)]) == 0
    assert "valid" in capsys.readouterr().out


def test_validate_rejects_bad_file(tmp_path, base_cfg, capsys):
    base_cfg["pppoe"]["interfaces"] = []
    assert main(["config", "validate", str(write_cfg(tmp_path / "c.yaml", base_cfg))]) == 1
    assert "invalid config" in capsys.readouterr().err


def test_search_input_is_restricted(capsys):
    assert main(["sessions", "--search", "a b"]) == 1
    assert "search may contain only" in capsys.readouterr().err


def test_bad_sid_rejected(capsys):
    assert main(["session", "show", "../etc"]) == 1
