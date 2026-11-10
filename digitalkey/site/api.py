"""事業所サーバーの HTTP API(FastAPI)。Bearer トークンで役割を分ける(R-DV-12)。

トークンは事業所サーバーの中だけで確かめる(SHA-256 にしてから設定の値と照らす)。違うトークンは 401、役割の外は 403。
役割と共通コア 7.1 の主体: 管理者=管理者、受付=運用者、利用者=利用者、保守=保守者。

  GET  /health                       誰でも
  GET  /me                           トークンを持つ全員(名前、役割、主体、鍵の主体名)
  GET  /state                        管理者・受付・保守
  GET  /doors                        全員(扉の一覧)
  POST /doors/{id}/unlock {key}      鍵を持つ人(利用者は自分の鍵だけ)、管理者・受付
  POST /doors/{id}/lock              管理者・受付・保守
  GET  /keys                         管理者・受付は全部、利用者は自分の分(保守は見ない)
  POST /keys {subject,door,start,end,route}   管理者・受付
  DELETE /keys/{id}?reason=          管理者
  GET  /events/{door}?since=         管理者・保守(扉の監査記録)
  GET  /operations                   管理者・受付(鍵の操作の記録)
  GET  /verify                       管理者・保守(記録の連鎖の検査)

予約と資格情報(使い方.md「予約と資格情報の発行の口」、R-DV-13):
  POST /reservations {subject,layer,arrival,departure,door,contact}   管理者・受付
  GET  /reservations                 管理者・受付
  GET  /reservations/{id}            管理者・受付(その予約で出した鍵も)
  POST /reservations/{id}/keys {route}       管理者・受付(到着から出発までの鍵)
  POST /reservations/{id}/cancel {reason}    管理者・受付(出した鍵を全部失効)
  POST /keys/{id}/credential {once}          管理者・受付(扉が自分で確かめる署名つき資格情報)
  GET  /issuer                       管理者・受付・保守(発行者の公開鍵、最長の有効時間)
  POST /doors/{id}/credential {credential}   全員(扉が署名、範囲、期限、一回限りを確かめる)
取り消した予約、失効した鍵、期限の外の鍵への操作は 409。
"""
from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel

from .config import TokenConfig
from .service import Conflict, Site


class IssueBody(BaseModel):
    subject: str
    door: str
    start: str
    end: str
    route: str = "遠隔解錠"


class UnlockBody(BaseModel):
    key: str


class ReserveBody(BaseModel):
    subject: str
    layer: str
    arrival: str
    departure: str
    door: str
    contact: str = ""


class RouteBody(BaseModel):
    route: str = "遠隔解錠"


class ReasonBody(BaseModel):
    reason: str = "取消"


class CredentialBody(BaseModel):
    once: bool = False


class PresentBody(BaseModel):
    credential: dict


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

    @app.get("/me")
    def me(t: TokenConfig = Depends(who)):
        return {"name": t.name, "role": t.role, "principal": t.principal, "subject": t.subject}

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
    def keys(t: TokenConfig = Depends(need("管理者", "受付", "利用者"))):
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

    def guard(fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except KeyError:
            raise HTTPException(404, "ありません")
        except Conflict as e:
            raise HTTPException(409, str(e))
        except (ValueError, PermissionError) as e:
            raise HTTPException(400, str(e))

    staff = need("管理者", "受付")

    @app.post("/reservations")
    def reserve(body: ReserveBody, t: TokenConfig = Depends(staff)):
        y = guard(site.reserve, subject=body.subject, layer=body.layer, arrival=body.arrival,
                  departure=body.departure, door_id=body.door, contact=body.contact, by=t)
        return site.reservation(y.id)

    @app.get("/reservations")
    def reservations(t: TokenConfig = Depends(staff)):
        return site.reservations()

    @app.get("/reservations/{rid}")
    def reservation(rid: str, t: TokenConfig = Depends(staff)):
        return guard(site.reservation, rid)

    @app.post("/reservations/{rid}/keys")
    def reservation_key(rid: str, body: RouteBody = RouteBody(), t: TokenConfig = Depends(staff)):
        k = guard(site.issue_for_reservation, rid, route=body.route, by=t)
        return {"id": k.id, "reservation": rid, "subject": k["主体"], "door": k["錠"], "start": k["開始"],
                "end": k["終了"], "route": k["経路"]}

    @app.post("/reservations/{rid}/cancel")
    def cancel(rid: str, body: ReasonBody = ReasonBody(), t: TokenConfig = Depends(staff)):
        c, revoked = guard(site.cancel_reservation, rid, body.reason, t)
        return {"id": c.id, "reservation": rid, "revoked": revoked}

    @app.post("/keys/{key_id}/credential")
    def credential(key_id: str, body: CredentialBody = CredentialBody(), t: TokenConfig = Depends(staff)):
        return guard(site.credential, key_id, once=body.once, by=t)

    @app.get("/issuer")
    def issuer(t: TokenConfig = Depends(need("管理者", "受付", "保守"))):
        return site.issuer_info()

    @app.post("/doors/{door_id}/credential")
    async def present(door_id: str, body: PresentBody, t: TokenConfig = Depends(who)):
        try:
            return await site.present(door_id, body.credential, t)
        except KeyError:
            raise HTTPException(404, "扉がありません")

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
