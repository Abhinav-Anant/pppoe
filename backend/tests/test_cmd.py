import pytest

from app.accel.cmd import SESSION_COLUMNS, AccelCmd, AccelError, parse_sessions

HEADER = " " + " | ".join(SESSION_COLUMNS) + "\n"
SEP = "-" * 20 + "+" + "-" * 20 + "\n"


def row(**kw):
    vals = {c: "" for c in SESSION_COLUMNS} | kw
    return " " + " | ".join(vals[c] for c in SESSION_COLUMNS) + "\n"


def test_parse_sessions():
    text = HEADER + SEP + row(sid="abc123", ifname="ppp0", ip="100.64.0.2", username="alice")
    rows = parse_sessions(text, SESSION_COLUMNS)
    assert rows == [dict.fromkeys(SESSION_COLUMNS, "") | {"sid": "abc123", "ifname": "ppp0",
                                                         "ip": "100.64.0.2", "username": "alice"}]


def test_username_with_pipe_stays_in_last_column():
    rows = parse_sessions(HEADER + SEP + row(sid="1", username="evil|user"), SESSION_COLUMNS)
    assert rows[0]["username"] == "evil|user" and rows[0]["sid"] == "1"


def test_empty_output():
    assert parse_sessions("", SESSION_COLUMNS) == []
    assert parse_sessions(HEADER + SEP, SESSION_COLUMNS) == []


def test_unexpected_header_fails_safe():
    with pytest.raises(AccelError, match="header"):
        parse_sessions(" sid | ip\n", SESSION_COLUMNS)


def test_arguments_with_whitespace_refused():
    with pytest.raises(ValueError):
        AccelCmd(binary="/nonexistent").sessions(("username", "a\nterminate all"))
    with pytest.raises(ValueError):
        AccelCmd(binary="/nonexistent").terminate("12 34")


def test_missing_binary_is_accel_error():
    with pytest.raises(AccelError):
        AccelCmd(binary="/nonexistent/accel-cmd").version()
