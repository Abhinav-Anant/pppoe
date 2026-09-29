"""Static validation of accel-ppp 1.14.0 configuration text.

accel-pppd 1.14.0 has no config-check mode and silently ignores unknown
directives, so a typo changes behaviour without an error. Every section and
directive here was extracted from the 1.14.0 source (conf_get_opt calls plus
the options its modules read by iterating a section). Update this table only
from the source of the version being deployed.
"""
from __future__ import annotations

import ipaddress
import re

from app.accel.render import SECRETS_INCLUDE

MODULES = {
    "log_file", "log_syslog", "log_tcp", "pppoe", "auth_pap", "auth_chap_md5", "auth_mschap_v1",
    "auth_mschap_v2", "radius", "chap-secrets", "ippool", "shaper", "connlimit", "pppd_compat",
    "ipv6_nd", "ipv6_dhcp", "ipv6pool", "net-snmp", "logwtmp", "sigchld",
}
DIRECTIVES: dict[str, set[str]] = {
    "core": {"log-error", "log-debug", "thread-count"},
    "common": {"check-ip", "max-sessions", "max-starting", "netns-run-dir", "nl-rcv-buffer",
               "nl-snd-buffer", "seq-file", "session-timeout", "sid-case", "sid-source",
               "single-session", "single-session-ignore-case"},
    "ppp": {"accomp", "ccp", "ccp-max-configure", "check-ip", "ipv4", "ipv6", "ipv6-accept-peer-intf-id",
            "ipv6-intf-id", "ipv6-peer-intf-id", "lcp-echo-failure", "lcp-echo-interval",
            "lcp-echo-timeout", "max-configure", "max-failure", "max-mtu", "max-terminate", "min-mtu",
            "mppe", "mru", "mtu", "pcomp", "timeout", "unit-cache", "unit-preallocate", "verbose"},
    "pppoe": {"ac-name", "accept-any-service", "accept-blank-service", "called-sid", "cookie-timeout",
              "ifname", "ifname-in-sid", "interface", "ip-pool", "ipv6-pool", "ipv6-pool-delegate",
              "mac-filter", "mppe", "padi-limit", "pado-delay", "service-name", "session-timeout",
              "sid-uppercase", "tr101", "verbose", "vlan-mon", "vlan-name", "vlan-timeout"},
    "dns": {"dns1", "dns2"},
    "radius": {"acct-delay-start", "acct-delay-time", "acct-interim-interval", "acct-interim-jitter",
               "acct-on", "acct-timeout", "attr-tunnel-type", "bind", "blast-protection", "dae-allowed",
               "default-realm", "dictionary", "fail-time", "fail-timeout", "gw-ip-address",
               "interim-verbose", "max-fail", "max-try", "nas-identifier", "nas-ip-address",
               "nas-port-id-in-req", "req-limit", "require-nas-identification", "sid-in-auth",
               "strip-realm", "timeout", "verbose"},
    "chap-secrets": {"chap-secrets", "encrypted", "gw-ip-address", "username-hash"},
    "ip-pool": {"attr", "gw", "gw-ip-address", "shuffle", "tunnel", "vendor"},
    "shaper": {"attr", "attr-down", "attr-up", "burst-factor", "cburst", "down-burst-factor",
               "down-limiter", "fwmark", "ifb", "latency", "leaf-qdisc", "moderate-quantum", "mpu",
               "mtu", "quantum", "r2q", "rate-limit", "rate-multiplier", "up-burst-factor",
               "up-limiter", "vendor", "verbose"},
    "log": {"color", "copy", "level", "log-debug", "log-emerg", "log-fail-file", "log-file",
            "per-session", "per-session-dir", "per-user-dir", "syslog"},
    "cli": {"history-file", "password", "prompt", "sessions-columns", "tcp", "telnet", "verbose"},
}
SECRET_BEARING = {"server", "dae-server", "auth-server", "acct-server", "dm_coa_secret"}
REQUIRED = ("modules", "pppoe", "ip-pool", "cli")
_POOL_OPT = re.compile(r"^(name|next)=[A-Za-z0-9_.\-]{1,64}$")


def _pool_line_ok(line: str) -> bool:
    first, *opts = line.split(",")
    try:
        if "/" in first:
            ipaddress.IPv4Network(first, strict=True)
        else:
            start, _, last = first.partition("-")
            ipaddress.IPv4Address(start)
            if not last.isdigit() or not 0 <= int(last) <= 255:
                return False
    except ValueError:
        return False
    return all(_POOL_OPT.match(o) for o in opts)


def validate_text(text: str) -> list[str]:
    errors: list[str] = []
    section: str | None = None
    seen: set[str] = set()
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if len(raw) >= 1023:
            errors.append(f"line {n}: longer than accel-ppp's 1023-byte line buffer")
            continue
        if line.startswith("$include"):
            if section != "radius" or line != f"$include {SECRETS_INCLUDE}":
                errors.append(f"line {n}: only '$include {SECRETS_INCLUDE}' inside [radius] is allowed")
            continue
        if line.startswith("["):
            if not line.endswith("]"):
                errors.append(f"line {n}: malformed section header")
                continue
            section = line[1:-1]
            if section != "modules" and section not in DIRECTIVES:
                errors.append(f"line {n}: unknown section [{section}]")
            if section in seen:
                errors.append(f"line {n}: duplicate section [{section}] (1.14.0 rejects re-opening)")
            seen.add(section)
            continue
        if section is None:
            errors.append(f"line {n}: directive outside any section")
        elif section == "modules":
            if line not in MODULES:
                errors.append(f"line {n}: unknown module {line!r}")
        elif section == "ip-pool" and line[0].isdigit():
            if not _pool_line_ok(line):
                errors.append(f"line {n}: bad pool line {line!r}")
        else:
            name, sep, _ = line.partition("=")
            name = name.strip()
            if not sep:
                errors.append(f"line {n}: expected name=value")
            elif section == "radius" and name in SECRET_BEARING:
                errors.append(f"line {n}: {name} carries the RADIUS secret and belongs in the include file")
            elif name not in DIRECTIVES.get(section, set()):
                errors.append(f"line {n}: unknown directive {name!r} in [{section}]")
    errors += [f"missing section [{s}]" for s in REQUIRED if s not in seen]
    return errors
