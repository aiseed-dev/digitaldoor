"""事業所サーバーの HTTP API(FastAPI)。Bearer トークンで役割を分ける。

  GET  /health                       誰でも
  GET  /state                        管理者・受付・保守
  GET  /doors                        全員(扉の一覧)
  POST /doors/{id}/unlock {key}      鍵を持つ人(利用者は自分の鍵だけ)、管理者・受付
  POST /doors/{id}/lock              管理者・受付・保守
  GET  /keys                         管理者・受付は全部、利用者は自分の分
  POST /keys {subject,door,start,end,route}   管理者・受付
  DELETE /keys/{id}?reason=          管理者
  GET  /events/{door}?since=         管理者・保守(扉の監査記録)
  GET  /operations                   管理者・受付(鍵の操作の記録)
  GET  /verify                       管理者・保守(記録の連鎖の検査)
"""
from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel

from .config import TokenConfig
from .service import Site


class IssueBody(BaseModel):
    subject: str
    door: str
    start: str
    end: str
    route: str = "遠隔解錠"


class UnlockBody(BaseModel):
    key: str


def create_app(site: Site) -> FastAPI:
    app = FastAPI(title=f"digitaldoor site: {site.cfg.name}", docs_url=None, redoc_url=None)
    app.state.site = site

    def who(request: Request) -> TokenConfig:
        auth = request.headers.get("authorization", "")
        tok = auth[7:] if auth.lower().startswith("bearer ") else ""
        t = site.who(tok)
        if t is None:
            raise HTTPException(401, "トークンが違います")
        return t

    def need(*roles: str):
        def dep(t: TokenConfig = Depends(who)) -> TokenConfig:
            if t.role not in roles:
                raise HTTPException(403, f"この操作は {'・'.join(roles)} だけです")
            return t
        return dep

    @app.get("/health")
    def health():
        return {"ok": True, "site": site.cfg.name}

    @app.get("/state")
    def state(t: TokenConfig = Depends(need("管理者", "受付", "保守"))):
        return site.state()

    @app.get("/doors")
    def doors(t: TokenConfig = Depends(who)):
        return [{"id": d.cfg.id, "locked": d.controller.locked, "mode": d.controller.mode} for d in site.doors.values()]

    @app.post("/doors/{door_id}/unlock")
    async def unlock(door_id: str, body: UnlockBody, t: TokenConfig = Depends(who)):
        try:
            return await site.unlock(door_id, body.key, t)
        except KeyError:
            raise HTTPException(404, "扉がありません")

    @app.post("/doors/{door_id}/lock")
    async def lock(door_id: str, t: TokenConfig = Depends(need("管理者", "受付", "保守"))):
        try:
            return await site.lock(door_id, t)
        except KeyError:
            raise HTTPException(404, "扉がありません")

    @app.get("/keys")
    def keys(t: TokenConfig = Depends(who)):
        if t.role == "利用者":
            return site.keys(subject=t.subject or t.name)
        return site.keys()

    @app.post("/keys")
    def issue(body: IssueBody, t: TokenConfig = Depends(need("管理者", "受付"))):
        try:
            s = site.issue(subject=body.subject, door_id=body.door, start=body.start, end=body.end, by=t, route=body.route)
        except KeyError:
            raise HTTPException(404, "扉がありません")
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"id": s.id, "subject": s["主体"], "door": s["錠"], "start": s["開始"], "end": s["終了"]}

    @app.delete("/keys/{key_id}")
    def revoke(key_id: str, reason: str = "失効", t: TokenConfig = Depends(need("管理者"))):
        try:
            s = site.revoke(key_id, reason, t)
        except KeyError:
            raise HTTPException(404, "鍵がありません")
        return {"id": s.id, "key": key_id}

    @app.get("/events/{door_id}")
    def events(door_id: str, since: int = 0, t: TokenConfig = Depends(need("管理者", "保守"))):
        try:
            return site.events(door_id, since)
        except KeyError:
            raise HTTPException(404, "扉がありません")

    @app.get("/operations")
    def operations(limit: int = 100, t: TokenConfig = Depends(need("管理者", "受付"))):
        return site.operations(limit)

    @app.get("/verify")
    def verify(t: TokenConfig = Depends(need("管理者", "保守"))):
        return site.verify()

    return app
