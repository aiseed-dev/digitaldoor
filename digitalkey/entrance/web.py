"""門 — 外から入ってくる伝票の受け口(FastAPI)。

画面は客のチェックイン一枚だけ。経営者の側は画面を持たず、台帳と依頼で回す。
書き込みはこの門だけが行い、検査するのは構造(様式・権限・追記)だけ。
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime
from importlib import resources
from pathlib import Path

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

from .ledger import Ledger
from .identity import Identity, Matcher, Evidence, StubMatcher
from .keyring import Keyring, LockDriver
from .safe import store_image


@dataclass
class Config:
    vault: Path
    house_lat: float
    house_lon: float
    owner_token: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    locks: dict[str, LockDriver] = field(default_factory=dict)
    matcher: Matcher = field(default_factory=StubMatcher)
    radius_m: float = 200.0
    window_s: float = 600.0
    roles: dict[str, set[str]] = field(default_factory=dict)
    auto_open: bool = True


@dataclass
class Frame:
    data: bytes
    at: datetime


def create_app(cfg: Config) -> FastAPI:
    app = FastAPI(title="entrance", docs_url=None, redoc_url=None)
    ledger = Ledger(cfg.vault, roles=cfg.roles)
    keyring = Keyring(ledger, cfg.locks)
    identity = Identity(ledger, cfg.matcher, house_lat=cfg.house_lat, house_lon=cfg.house_lon,
                    radius_m=cfg.radius_m, window_s=cfg.window_s)
    frames: dict[str, Frame] = {}
    app.state.ledger, app.state.keyring, app.state.identity, app.state.frames = ledger, keyring, identity, frames
    page = resources.files("digitalkey.entrance").joinpath("static", "checkin.html").read_text(encoding="utf-8")

    def reservation(token: str):
        rows = ledger.select("予約", where={"受付符号": token})
        if not rows:
            raise HTTPException(404, "予約がありません")
        return rows[-1]

    async def issue_and_open(v, y):
        key = keyring.issue(subject=y["主体"], lock_id=y["錠"], start=y["到着"], end=y["出発"],
                         reservation_id=y.id, basis=[v.id])
        result = ""
        if cfg.auto_open:
            op = await keyring.open(key.id)
            result = op["結果"]
        return key, result

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "伝票": ledger.count()}

    @app.get("/checkin/{token}", response_class=HTMLResponse)
    def checkin_page(token: str):
        reservation(token)
        return page

    @app.post("/checkin/{token}")
    async def checkin(token: str, selfie: UploadFile = File(...), id_image: UploadFile = File(...),
                      lat: float | None = Form(None), lon: float | None = Form(None)):
        y = reservation(token)
        fr = frames.get(y["錠"])
        ev = Evidence(selfie=await selfie.read(), id_image=await id_image.read(),
                    doorbell=fr.data if fr else None, lat=lat, lon=lon,
                    submitted_at=datetime.now().astimezone(), doorbell_at=fr.at if fr else None)
        v = identity.verify(subject=y["主体"], evidence=ev, reservation_id=y.id)
        out = {"判定": v["判定"], "本人確認番号": v.id, "理由": v["理由"]}
        if v["判定"] == "承認":
            key, result = await issue_and_open(v, y)
            out.update({"鍵番号": key.id, "解錠": result})
        return JSONResponse(out)

    @app.post("/doorbell/{lock_id}/frame")
    async def doorbell_frame(lock_id: str, image: UploadFile = File(...), at: str | None = Form(None)):
        data = await image.read()
        when = datetime.fromisoformat(at) if at else datetime.now().astimezone()
        if when.tzinfo is None:
            when = when.astimezone()
        frames[lock_id] = Frame(data, when)
        return {"錠": lock_id, "ハッシュ": store_image(cfg.vault, data), "時刻": when.isoformat(timespec="seconds")}

    @app.post("/decide/{verification_id}")
    async def decide(verification_id: str, decision: str = Form(...), reason: str = Form(""),
                     by: str = Form("経営者"), x_owner_token: str = Header(...)):
        if not secrets.compare_digest(x_owner_token, cfg.owner_token):
            raise HTTPException(403, "経営者の符号が違います")
        if decision not in ("承認", "拒否", "保留"):
            raise HTTPException(400, "判定は 承認/拒否/保留")
        v = identity.decide(verification_id, decision, by=by, reason=reason, issuer="経営者")
        out = {"判定": v["判定"], "本人確認番号": v.id}
        if decision == "承認" and v["予約番号"]:
            y = ledger.get(v["予約番号"])
            key, result = await issue_and_open(v, y)
            out.update({"鍵番号": key.id, "解錠": result})
        return out

    return app
