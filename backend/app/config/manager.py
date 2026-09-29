"""Safe configuration pipeline for one node.

validate → diff → backup → apply (start / reload / restart) → health check →
automatic rollback. Every outcome is appended to the audit log. Versions hold
config.yaml + accel-ppp.conf only; secrets are re-rendered from
/etc/bng-platform/secrets/radius.secret and never stored in a version.
"""
from __future__ import annotations

import difflib
import json
import os
import shutil
import tarfile
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import yaml

from app.accel.render import Rendered, render
from app.accel.validate import validate_text
from app.config.model import BngConfig, load

# accel-pppd reads these only at start; `accel-cmd reload` does not apply them.
RESTART_SECTIONS = {"modules", "core", "cli"}


class ApplyError(Exception):
    pass


@dataclass(frozen=True)
class Paths:
    etc: Path = Path("/etc/bng-platform")
    state: Path = Path("/var/lib/bng-platform")

    @property
    def config(self) -> Path: return self.etc / "config.yaml"
    @property
    def accel_conf(self) -> Path: return self.etc / "accel-ppp" / "accel-ppp.conf"
    @property
    def secret(self) -> Path: return self.etc / "secrets" / "radius.secret"
    @property
    def secrets_include(self) -> Path: return self.etc / "secrets" / "radius.conf"
    @property
    def versions(self) -> Path: return self.state / "versions"
    @property
    def backups(self) -> Path: return self.state / "backups"
    @property
    def audit_log(self) -> Path: return self.state / "audit.jsonl"


def _sections(text: str) -> dict[str | None, list[str]]:
    out: dict[str | None, list[str]] = {}
    cur = None
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("[") and s.endswith("]"):
            cur = s[1:-1]
            out.setdefault(cur, [])
        else:
            out.setdefault(cur, []).append(s)
    return out


def changed_sections(old: str, new: str) -> set[str]:
    a, b = _sections(old), _sections(new)
    return {k for k in a.keys() | b.keys() if a.get(k) != b.get(k) and k is not None}


def _read(p: Path) -> str | None:
    return p.read_text(encoding="utf-8") if p.exists() else None


def _write(p: Path, text: str, mode: int) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, mode)
    os.replace(tmp, p)


class ConfigManager:
    def __init__(self, paths: Paths, daemon, health: Callable[[BngConfig], list[str]]):
        self.paths, self.daemon, self.health = paths, daemon, health

    # --- read-only -------------------------------------------------------
    def _secret(self) -> str | None:
        return _read(self.paths.secret).strip() if self.paths.secret.exists() else None

    def build(self, candidate: Path) -> tuple[BngConfig, Rendered]:
        try:
            cfg = load(candidate)
            rendered = render(cfg, self._secret() if cfg.aaa == "radius" else None)
        except (OSError, ValueError, yaml.YAMLError) as e:
            raise ApplyError(f"invalid config {candidate}: {e}") from e
        errors = validate_text(rendered.main)
        if errors:
            raise ApplyError("generated accel-ppp.conf failed validation:\n  " + "\n  ".join(errors))
        return cfg, rendered

    def diff(self, candidate: Path) -> str:
        _, new = self.build(candidate)
        old = _read(self.paths.accel_conf) or ""
        return "".join(difflib.unified_diff(old.splitlines(True), new.main.splitlines(True),
                                            "active/accel-ppp.conf", "candidate/accel-ppp.conf"))

    def history(self) -> list[dict]:
        if not self.paths.versions.exists():
            return []
        return [json.loads((d / "meta.json").read_text()) for d in sorted(self.paths.versions.iterdir())]

    def audit(self, **event) -> None:
        self.paths.state.mkdir(parents=True, exist_ok=True)
        event = {"timestamp": datetime.now(timezone.utc).isoformat(), "component": "bngctl", **event}
        with open(self.paths.audit_log, "a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")

    # --- mutating --------------------------------------------------------
    def _managed(self) -> list[Path]:
        return [self.paths.config, self.paths.accel_conf, self.paths.secrets_include]

    def _backup(self) -> Path:
        self.paths.backups.mkdir(parents=True, exist_ok=True)
        dest = Path(tempfile.mkdtemp(prefix=f"apply-{time.strftime('%Y%m%dT%H%M%S')}-", dir=self.paths.backups))
        for i, f in enumerate(self._managed()):
            if f.exists():
                shutil.copy2(f, dest / str(i))
        return dest

    def _restore(self, backup: Path) -> None:
        for i, f in enumerate(self._managed()):
            saved = backup / str(i)
            if saved.exists():
                shutil.copy2(saved, f)
            elif f.exists():
                f.unlink()

    def apply(self, candidate: Path, admin: str, source: str, allow_restart: bool = False) -> str:
        who = {"admin": admin, "source": source, "action": "config_apply"}
        try:
            cfg, new = self.build(candidate)
        except ApplyError as e:
            self.audit(**who, result="rejected", detail=str(e))
            raise
        candidate_text = Path(candidate).read_text(encoding="utf-8")
        old_main = _read(self.paths.accel_conf)
        if old_main == new.main and (_read(self.paths.secrets_include) or "") == new.secrets \
                and _read(self.paths.config) == candidate_text:
            return "no changes"

        running = self.daemon.is_active()
        restart_for = changed_sections(old_main or "", new.main) & RESTART_SECTIONS if running else set()
        if restart_for and not allow_restart:
            raise ApplyError(f"changes in {sorted(restart_for)} need an accel-ppp restart, which drops "
                             "every PPPoE session; re-run with --allow-restart")

        diff = "".join(difflib.unified_diff((old_main or "").splitlines(True), new.main.splitlines(True)))
        backup = self._backup()
        _write(self.paths.config, candidate_text, 0o640)
        _write(self.paths.accel_conf, new.main, 0o640)
        if new.secrets:
            self.paths.secrets_include.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            _write(self.paths.secrets_include, new.secrets, 0o600)
        elif self.paths.secrets_include.exists():
            self.paths.secrets_include.unlink()

        try:
            if not running:
                self.daemon.start()
            elif restart_for:
                self.daemon.restart()
            else:
                self.daemon.reload()
            failures = self.health(cfg)
        except Exception as e:  # any activation error means: roll back
            failures = [str(e)]

        if failures:
            self._restore(backup)
            try:
                if old_main is None or not running:
                    self.daemon.stop()
                elif restart_for:
                    self.daemon.restart()
                else:
                    self.daemon.reload()
            except Exception as e:
                failures.append(f"re-activating previous config failed: {e}")
            self.audit(**who, result="rolled_back", detail=failures, diff=diff)
            raise ApplyError("health check failed, previous configuration restored:\n  " + "\n  ".join(failures))

        version = len(self.history()) + 1
        vdir = self.paths.versions / f"{version:04d}"
        meta = {"version": version, "timestamp": datetime.now(timezone.utc).isoformat(),
                "admin": admin, "source": source, "restart": bool(restart_for)}
        _write(vdir / "config.yaml", candidate_text, 0o640)
        _write(vdir / "accel-ppp.conf", new.main, 0o640)
        _write(vdir / "meta.json", json.dumps(meta), 0o640)
        self.audit(**who, result="applied", version=version, restart=bool(restart_for), diff=diff)
        return f"applied as version {version}"

    def rollback(self, version: int | None, admin: str, source: str, allow_restart: bool = False) -> str:
        versions = [m["version"] for m in self.history()]
        if version is None:
            if len(versions) < 2:
                raise ApplyError("no previous version to roll back to")
            version = versions[-2]
        if version not in versions:
            raise ApplyError(f"version {version} does not exist")
        return self.apply(self.paths.versions / f"{version:04d}" / "config.yaml", admin, source, allow_restart)

    def backup_archive(self) -> Path:
        self.paths.backups.mkdir(parents=True, exist_ok=True)
        dest = self.paths.backups / f"bng-config-{time.strftime('%Y%m%dT%H%M%S')}.tar.gz"
        with tarfile.open(dest, "w:gz") as t:
            t.add(self.paths.etc, arcname="etc")
        os.chmod(dest, 0o600)
        return dest

    def restore_archive(self, archive: Path, admin: str, source: str, allow_restart: bool = False) -> str:
        old_secret = _read(self.paths.secret)
        with tempfile.TemporaryDirectory() as tmp:
            with tarfile.open(archive) as t:
                t.extractall(tmp, filter="data")
            etc = Path(tmp) / "etc"
            new_secret = _read(etc / "secrets" / "radius.secret")
            if new_secret is not None:
                _write(self.paths.secret, new_secret, 0o600)
            try:
                return self.apply(etc / "config.yaml", admin, source, allow_restart)
            except ApplyError:
                if old_secret is not None:
                    _write(self.paths.secret, old_secret, 0o600)
                raise
