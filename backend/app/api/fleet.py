"""Multi-BNG: this console manages remote BNG nodes through their own bng-api.

Each node exposes bng-api over TLS (bng-api-remote.service, self-signed certificate).
Registering a node pins that certificate: the console trusts exactly that
certificate and nothing else (TOFU; compare the fingerprint with
`bngctl tls fingerprint` on the node). The console calls the node with a service token
(`bngctl token create` on the node), and names the acting admin and role in
X-BNG-Admin / X-BNG-Role; the node limits the token to that role and audits the
admin as <admin>@<token>. The node's own RBAC and safe-apply pipeline stay in charge.

Token and certificate files: /etc/bng-platform/secrets/nodes/<name>.{token,crt} (0600).
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
import ssl
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import websockets
from fastapi import Depends, HTTPException, Query, Request, WebSocket
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import auth
from app.api.auth import Principal, audit, get_db, require
from app.db import BngNode

NAME = r"^[A-Za-z0-9_.\-]{1,64}$"
URL = r"^https://[A-Za-z0-9.\-]+(:\d{1,5})?$"
PROXY_PATH = re.compile(r"^(?!auth/|nodes|fleet|ws/)[a-z0-9_\-/.]{1,200}$")
TIMEOUT = 10.0
RECHECK_S = 60.0  # how often a proxied WebSocket re-validates the admin's session


def secrets_dir() -> Path:
    return Path(os.environ.get("BNG_NODE_SECRETS", "/etc/bng-platform/secrets/nodes"))


def fetch_cert(url: str) -> tuple[str, str]:
    """-> (PEM, sha256 fingerprint of DER) of whatever certificate the URL presents."""
    u = urlsplit(url)
    pem = ssl.get_server_certificate((u.hostname, u.port or 443), timeout=5)
    return pem, fingerprint(pem)


def fingerprint(pem: str) -> str:
    return hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()


def pinned_context(pem: str) -> ssl.SSLContext:
    """Trust only this certificate (it is self-signed; host names are not checked because
    the pin already identifies the node exactly)."""
    ctx = ssl.create_default_context(cadata=pem)
    ctx.check_hostname = False
    ctx.verify_flags |= getattr(ssl, "VERIFY_X509_PARTIAL_CHAIN", 0)
    return ctx


class Remote:
    def __init__(self, node: BngNode):
        d = secrets_dir()
        self.name, self.url = node.name, node.url
        try:
            self.token = (d / f"{node.name}.token").read_text().strip()
            self.ctx = pinned_context((d / f"{node.name}.crt").read_text())
        except OSError as e:
            raise HTTPException(503, f"node {node.name}: credentials missing ({e.filename})") from e

    def headers(self, p: Principal | None) -> dict:
        h = {"authorization": f"Bearer {self.token}"}
        if p:  # the node narrows the token to this admin's role and audits them by name
            h.update({"x-bng-admin": p.username.split("@")[0][:64], "x-bng-role": p.role})
        return h

    async def request(self, method: str, path: str, p: Principal | None, **kw) -> httpx.Response:
        async with httpx.AsyncClient(verify=self.ctx, timeout=TIMEOUT) as c:
            return await c.request(method, f"{self.url}/api/{path}", headers={**self.headers(p), **kw.pop("headers", {})}, **kw)


def _remote(db: Session, name: str) -> Remote:
    n = db.scalar(select(BngNode).where(BngNode.name == name))
    if not n:
        raise HTTPException(404, f"no node {name}")
    return Remote(n)


class ProbeIn(BaseModel):
    url: str = Field(pattern=URL)


class NodeIn(BaseModel):
    name: str = Field(pattern=NAME)
    url: str = Field(pattern=URL)
    token: str = Field(pattern=r"^bngt_[A-Za-z0-9_\-]{20,100}$")
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


def add_routes(app, ws_principal) -> None:
    def local_name(request: Request) -> str:
        return request.app.state.node.config().node

    @app.get("/api/nodes", tags=["fleet"])
    def nodes(request: Request, _: Principal = Depends(require()), db: Session = Depends(get_db)):
        """This node first (local), then the registered remote nodes."""
        return [{"name": local_name(request), "local": True, "url": None, "fingerprint": None}] + [
            {"name": n.name, "local": False, "url": n.url, "fingerprint": n.fingerprint}
            for n in db.scalars(select(BngNode).order_by(BngNode.name))]

    @app.post("/api/nodes/probe", tags=["fleet"])
    def probe(body: ProbeIn, _: Principal = Depends(require("manage_nodes"))):
        """Step 1: show the certificate fingerprint the URL presents, to compare with the node."""
        try:
            return {"fingerprint": fetch_cert(body.url)[1]}
        except (OSError, ssl.SSLError, ValueError) as e:
            raise HTTPException(502, f"cannot fetch certificate: {e}") from e

    @app.post("/api/nodes", tags=["fleet"], status_code=201)
    async def node_add(body: NodeIn, request: Request, p: Principal = Depends(require("manage_nodes")),
                       db: Session = Depends(get_db)):
        """Step 2: pin the certificate the operator confirmed, store the token, prove both work."""
        if body.name == local_name(request) or db.scalar(select(BngNode).where(BngNode.name == body.name)):
            raise HTTPException(409, "node name in use")
        try:
            pem, fp = await run_in_threadpool(fetch_cert, body.url)
        except (OSError, ssl.SSLError, ValueError) as e:
            raise HTTPException(502, f"cannot fetch certificate: {e}") from e
        if fp != body.fingerprint:
            raise HTTPException(409, f"certificate fingerprint is {fp}, not the confirmed one")
        try:
            async with httpx.AsyncClient(verify=pinned_context(pem), timeout=TIMEOUT) as c:
                r = await c.get(f"{body.url}/api/auth/me", headers={"authorization": f"Bearer {body.token}"})
        except httpx.HTTPError as e:
            raise HTTPException(502, f"node unreachable: {e}") from e
        if r.status_code != 200:
            raise HTTPException(409, f"node rejected the token ({r.status_code})")
        d = secrets_dir()
        d.mkdir(mode=0o700, parents=True, exist_ok=True)
        for ext, text in (("token", body.token), ("crt", pem)):
            f = d / f"{body.name}.{ext}"
            f.write_text(text)
            os.chmod(f, 0o600)
        db.add(BngNode(name=body.name, url=body.url, fingerprint=fp))
        db.commit()
        audit(db, "node_add", "ok", admin=p.username, ip=p.ip, target=body.name, url=body.url, fingerprint=fp,
              node_role=r.json().get("role"))
        return {"name": body.name, "url": body.url, "fingerprint": fp, "node_role": r.json().get("role")}

    @app.delete("/api/nodes/{name}", tags=["fleet"])
    def node_delete(name: str, p: Principal = Depends(require("manage_nodes")), db: Session = Depends(get_db)):
        n = db.scalar(select(BngNode).where(BngNode.name == name))
        if not n:
            raise HTTPException(404, "no such node")
        db.delete(n)
        db.commit()
        for ext in ("token", "crt"):
            (secrets_dir() / f"{name}.{ext}").unlink(missing_ok=True)
        audit(db, "node_delete", "ok", admin=p.username, ip=p.ip, target=name)
        return {"ok": True}

    # --- fleet views ------------------------------------------------------
    @app.get("/api/fleet", tags=["fleet"])
    async def fleet(request: Request, p: Principal = Depends(require()), db: Session = Depends(get_db)):
        """Every node's live snapshot side by side (unreachable nodes are reported, not hidden)."""
        remotes = list(db.scalars(select(BngNode).order_by(BngNode.name)))

        async def one(n: BngNode) -> dict:
            try:
                r = await Remote(n).request("GET", "metrics", p)
                r.raise_for_status()
                return {"name": n.name, "local": False, "ok": True, "live": r.json()}
            except (httpx.HTTPError, HTTPException) as e:
                return {"name": n.name, "local": False, "ok": False, "error": str(getattr(e, "detail", e))}

        local = await run_in_threadpool(request.app.state.node.live)
        rest = await asyncio.gather(*(one(n) for n in remotes))
        return [{"name": local["node"], "local": True, "ok": True, "live": local}, *rest]

    @app.get("/api/fleet/sessions", tags=["fleet"])
    async def fleet_sessions(request: Request, search: str = Query(pattern=r"^[A-Za-z0-9_.@:\-]{1,64}$"),
                             p: Principal = Depends(require("view_sessions")), db: Session = Depends(get_db)):
        """Find a subscriber on any BNG (first 100 matches per node)."""
        remotes = list(db.scalars(select(BngNode).order_by(BngNode.name)))
        params = {"search": search, "page_size": 100}

        def local() -> list[dict]:
            node = request.app.state.node
            s = search.lower()
            return [r for r in node.sessions()
                    if any(s in (r[k] or "").lower() for k in ("username", "ip", "mac", "ifname"))][:100]

        async def one(n: BngNode):
            try:
                r = await Remote(n).request("GET", "sessions", p, params=params)
                r.raise_for_status()
                return n.name, r.json()["items"], None
            except (httpx.HTTPError, HTTPException) as e:
                return n.name, [], str(getattr(e, "detail", e))

        results = [(local_name(request), await run_in_threadpool(local), None),
                   *await asyncio.gather(*(one(n) for n in remotes))]
        return {"items": [{**row, "node": name} for name, rows, _ in results for row in rows],
                "errors": {name: err for name, _, err in results if err}}

    # --- proxy to one node ------------------------------------------------
    @app.api_route("/api/nodes/{name}/{path:path}", methods=["GET", "POST", "PUT", "PATCH"], tags=["fleet"])
    async def node_proxy(name: str, path: str, request: Request, p: Principal = Depends(require()),
                         db: Session = Depends(get_db)):
        """The same API, on node `name`. Login, node and fleet management are not proxied."""
        if not PROXY_PATH.match(path) or ".." in path:
            raise HTTPException(404, "not proxied")
        remote = _remote(db, name)
        headers = {k: v for k, v in request.headers.items() if k.lower() in ("content-type", "accept")}
        try:
            r = await remote.request(request.method, path, p, params=request.query_params,
                                     content=await request.body(), headers=headers)
        except httpx.HTTPError as e:
            raise HTTPException(502, f"node {name} unreachable: {e}") from e
        keep = {k: v for k, v in r.headers.items() if k.lower() in ("content-type", "content-disposition")}
        return Response(r.content, status_code=r.status_code, headers=keep)

    @app.websocket("/api/nodes/{name}/ws/{kind}")
    async def node_ws(ws: WebSocket, name: str, kind: str):
        """Pipe the browser's socket to the node's /api/ws/<kind>."""
        if kind not in ("metrics", "system", "sessions"):
            await ws.close(code=1008)
            return
        p = await ws_principal(ws, "view_sessions" if kind == "sessions" else None)
        if not p:
            return
        maker = ws.app.state.sessionmaker

        def lookup() -> Remote:
            with maker() as db:
                return _remote(db, name)
        try:
            remote = await run_in_threadpool(lookup)
        except HTTPException:
            await ws.close(code=1008)
            return
        url = remote.url.replace("https://", "wss://", 1) + f"/api/ws/{kind}"
        try:
            async with websockets.connect(url, ssl=remote.ctx, additional_headers=remote.headers(p),
                                          open_timeout=TIMEOUT) as up:
                await ws.accept()

                async def down():
                    async for msg in up:
                        await ws.send_text(msg if isinstance(msg, str) else msg.decode())

                async def upward():
                    while True:
                        await up.send(await ws.receive_text())
                async def recheck():  # logout, disable or expiry must end the stream, as for local sockets
                    while await ws_principal(ws, "view_sessions" if kind == "sessions" else None, touch=False):
                        await asyncio.sleep(RECHECK_S)
                    await up.close()

                tasks = [asyncio.create_task(down()), asyncio.create_task(upward()), asyncio.create_task(recheck())]
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for t in tasks:
                    t.cancel()
        except (OSError, websockets.WebSocketException, asyncio.TimeoutError):
            pass
        try:
            await ws.close()
        except RuntimeError:
            pass
