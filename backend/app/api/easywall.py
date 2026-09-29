"""easywall's own web console, served inside the BNG console at /easywall/.

easywall-web (github.com/jp1337/easywall) listens on https://127.0.0.1:12227 and
builds every URL from the site root, refuses framing and has its own login. This
proxy puts it under /easywall/ on our origin:
  - only for our logged-in admins holding manage_firewall (easywall's own login and
    CSRF protection still apply behind it);
  - root-relative URLs in its HTML/CSS/JS, redirects and cookie paths get the prefix;
  - X-Frame-Options is replaced by frame-ancestors 'self' so our page can frame it;
  - our bng_session cookie is never forwarded to easywall.
"""
from __future__ import annotations

import os
import re

import httpx
from fastapi import Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from app.api import auth

UPSTREAM = os.environ.get("BNG_EASYWALL_URL", "https://127.0.0.1:12227")
PREFIX = "/easywall"
_UP = httpx.URL(UPSTREAM)
_UP_ORIGIN = f"{_UP.scheme}://{_UP.netloc.decode()}"

_HTML = re.compile(r'(\s(?:href|src|action|formaction)=")/(?!/)')
_CSS = re.compile(r"url\(/(?!/)")
_JS = re.compile(r"'/(?=[A-Za-z])")  # easywall 2.25 app.js writes its paths as '/apply/status' etc.
_DROP_REQ = {"host", "cookie", "accept-encoding", "content-length", "connection", "origin", "referer"}
_DROP_RESP = {"content-length", "content-encoding", "transfer-encoding", "connection", "x-frame-options",
              "strict-transport-security", "set-cookie", "location", "content-security-policy"}


def rewrite_body(body: bytes, ctype: str) -> bytes:
    if not any(t in ctype for t in ("text/html", "text/css", "javascript")):
        return body
    text = body.decode("utf-8", errors="surrogateescape")
    if "html" in ctype:
        text = _HTML.sub(rf"\1{PREFIX}/", text)
    text = _CSS.sub(f"url({PREFIX}/", text)
    if "javascript" in ctype or "html" in ctype:
        text = _JS.sub(f"'{PREFIX}/", text)
    return text.encode("utf-8", errors="surrogateescape")


def rewrite_location(loc: str) -> str:
    if loc.startswith(_UP_ORIGIN):
        loc = loc[len(_UP_ORIGIN):] or "/"
    return PREFIX + loc if loc.startswith("/") and not loc.startswith("//") else loc


def rewrite_cookie(c: str) -> str:
    c = re.sub(r"(?i)(;\s*path=)/", rf"\1{PREFIX}/", c)
    return c if re.search(r"(?i);\s*path=", c) else c + f"; Path={PREFIX}/"


def _cookies_without_ours(header: str) -> str:
    return "; ".join(p for p in (x.strip() for x in header.split(";"))
                     if p and not p.startswith(f"{auth.COOKIE}="))


# ponytail: TLS to easywall is not verified - it is loopback-only (127.0.0.1) with a
# self-signed certificate; pin /etc/easywall/ssl if easywall ever moves off-host.
_client = httpx.AsyncClient(base_url=UPSTREAM, verify=False, timeout=30, follow_redirects=False)


def add_routes(app) -> None:
    @app.get(PREFIX, include_in_schema=False)
    def easywall_root():
        return RedirectResponse(f"{PREFIX}/")

    @app.api_route(PREFIX + "/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                   include_in_schema=False)
    async def easywall_proxy(path: str, request: Request, db: Session = Depends(auth.get_db)):
        from fastapi.concurrency import run_in_threadpool

        # our cookie auth, without our CSRF header (easywall's forms carry easywall's own token;
        # our cookie is SameSite=Strict, so cross-site requests never get here authenticated)
        try:
            p = await run_in_threadpool(auth.authenticate, request, db, False)
        except HTTPException:
            return RedirectResponse("/") if request.method == "GET" else Response(status_code=401)
        if "manage_firewall" not in p.permissions:
            raise HTTPException(403, f"role {p.role} lacks manage_firewall")

        headers = {k: v for k, v in request.headers.items() if k.lower() not in _DROP_REQ}
        if cookie := _cookies_without_ours(request.headers.get("cookie", "")):
            headers["cookie"] = cookie
        headers.update({"x-forwarded-for": p.ip, "x-forwarded-proto": request.url.scheme,
                        "accept-encoding": "identity"})
        # easywall compares Origin/Referer with its own origin for CSRF: present it ours, un-prefixed
        for h in ("origin", "referer"):
            if v := request.headers.get(h):
                v = re.sub(r"^https?://[^/]+", _UP_ORIGIN, v)
                headers[h] = v.replace(_UP_ORIGIN + PREFIX, _UP_ORIGIN, 1)
        try:
            up = await _client.request(request.method, "/" + path, params=request.query_params,
                                       content=await request.body(), headers=headers)
        except httpx.HTTPError as e:
            raise HTTPException(502, f"easywall-web unreachable: {e}") from e

        ctype = up.headers.get("content-type", "")
        resp = Response(rewrite_body(up.content, ctype), status_code=up.status_code,
                        headers={k: v for k, v in up.headers.items() if k.lower() not in _DROP_RESP})
        if loc := up.headers.get("location"):
            resp.headers["location"] = rewrite_location(loc)
        for c in up.headers.get_list("set-cookie"):
            resp.headers.append("set-cookie", rewrite_cookie(c))
        csp = up.headers.get("content-security-policy")
        resp.headers["content-security-policy"] = (csp + "; " if csp else "") + "frame-ancestors 'self'"
        return resp
