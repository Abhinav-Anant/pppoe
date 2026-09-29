"""Confirm-or-revert for changes that can cut management access (nftables, tc on
the uplink). `arm` schedules a fixed revert command; only an explicit confirm
(run from a fresh SSH session) disarms it."""
from __future__ import annotations

import subprocess

CONFIRM_SECONDS = 120


def arm(unit: str, revert_sh: str) -> None:
    disarm(unit)
    # default timer AccuracySec is 1 min, which let the revert fire up to 60 s late on bng01
    p = subprocess.run(["systemd-run", f"--unit={unit}", f"--on-active={CONFIRM_SECONDS}",
                        "--timer-property=AccuracySec=1s", "/bin/sh", "-c", revert_sh],
                       capture_output=True, text=True, timeout=30)
    if p.returncode != 0:
        raise RuntimeError(f"systemd-run: {p.stderr.strip()}")


def disarm(unit: str) -> None:
    subprocess.run(["systemctl", "stop", f"{unit}.timer"], capture_output=True, timeout=30)
    subprocess.run(["systemctl", "reset-failed", f"{unit}.service"], capture_output=True, timeout=30)
