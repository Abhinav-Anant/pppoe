"""The only module that talks to accel-pppd (accel-cmd over 127.0.0.1:2001).

1.14.0 has no machine-readable session interface (the JSON metrics module is
post-1.14.0), so `show sessions` table output is parsed here and nowhere else.
If the header differs from what we asked for, we fail instead of guessing.
"""
from __future__ import annotations

import re
import subprocess

from app.accel.render import CLI_TCP


class AccelError(RuntimeError):
    pass


SID_RE = re.compile(r"^[0-9A-Za-z]{1,32}$")
# username is last: the subscriber chooses it and may put '|' in it;
# splitting with maxsplit keeps it whole in the final column.
SESSION_COLUMNS = ("sid", "ifname", "ip", "ip6", "ip6-dp", "calling-sid", "called-sid", "state",
                   "uptime-raw", "rx-bytes-raw", "tx-bytes-raw", "rate-limit", "username")
_NO_SPACE = re.compile(r"^\S+$")


def parse_sessions(text: str, columns) -> list[dict[str, str]]:
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    header = [h.strip() for h in lines[0].split("|")]
    if header != list(columns):
        raise AccelError(f"unexpected 'show sessions' header: {header}")
    rows = []
    for line in lines[1:]:
        if set(line.strip()) <= set("-+"):
            continue
        cells = [c.strip() for c in line.split("|", len(columns) - 1)]
        if len(cells) != len(columns):
            raise AccelError(f"unexpected 'show sessions' row: {line!r}")
        rows.append(dict(zip(columns, cells)))
    return rows


class AccelCmd:
    def __init__(self, host: str | None = None, port: int | None = None,
                 binary: str = "/usr/local/bin/accel-cmd", timeout: float = 10.0):
        default_host, default_port = CLI_TCP.split(":")
        self._argv = [binary, "-H", host or default_host, "-p", str(port or default_port)]
        self._timeout = timeout

    def run(self, *args: str) -> str:
        for a in args:
            if not _NO_SPACE.match(a):
                raise ValueError(f"accel-cmd argument contains whitespace: {a!r}")
        try:
            p = subprocess.run([*self._argv, *args], capture_output=True, text=True, timeout=self._timeout)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise AccelError(f"accel-cmd: {e}") from e
        if p.returncode != 0:
            raise AccelError(f"accel-cmd exit {p.returncode}: {(p.stderr or p.stdout).strip()}")
        return p.stdout

    def sessions(self, match: tuple[str, str] | None = None) -> list[dict[str, str]]:
        args = ["show", "sessions", ",".join(SESSION_COLUMNS)]
        if match:
            column, regex = match
            if column not in SESSION_COLUMNS:
                raise ValueError(f"unknown column {column!r}")
            args += ["match", column, regex]
        return parse_sessions(self.run(*args), SESSION_COLUMNS)

    def terminate(self, sid: str, hard: bool = False) -> None:
        if not SID_RE.match(sid):
            raise ValueError("invalid session id")
        self.run("terminate", "sid", sid, "hard" if hard else "soft")

    def reload(self) -> None:
        out = self.run("reload")
        if "failed" in out.lower():
            raise AccelError("accel-ppp rejected the configuration on reload (old config kept)")

    def stat(self) -> str:
        return self.run("show", "stat")

    def version(self) -> str:
        return self.run("show", "version").strip()

    def pppoe_interfaces(self) -> str:
        return self.run("pppoe", "interface", "show")


class AccelService:
    """systemd + CLI control of accel-pppd, as driven by ConfigManager."""

    def __init__(self, accel: AccelCmd, unit: str = "accel-ppp.service"):
        self.accel, self.unit = accel, unit

    def _systemctl(self, verb: str) -> None:
        p = subprocess.run(["systemctl", verb, self.unit], capture_output=True, text=True, timeout=60)
        if p.returncode != 0:
            raise AccelError(f"systemctl {verb} {self.unit}: {p.stderr.strip()}")

    def is_active(self) -> bool:
        return subprocess.run(["systemctl", "is-active", "--quiet", self.unit], timeout=10).returncode == 0

    def start(self) -> None:
        self._systemctl("start")

    def stop(self) -> None:
        self._systemctl("stop")

    def restart(self) -> None:
        self._systemctl("restart")

    def reload(self) -> None:
        self.accel.reload()

    def pppoe_add(self, opt: str) -> None:
        self.accel.run("pppoe", "interface", "add", opt)

    def pppoe_del(self, name: str) -> None:
        self.accel.run("pppoe", "interface", "del", name)
