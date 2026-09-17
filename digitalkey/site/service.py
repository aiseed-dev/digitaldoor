"""事業所サーバーの中身。扉ごとのコントローラと、鍵の台帳を一つに束ねる。"""
from __future__ import annotations

import secrets
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from ..door.audit import Audit, HmacSigner
from ..door.contacts import SimContacts
from ..door.controller import Config as DoorCfg, Controller
from ..entrance.keyring import Keyring, LockDriver
from ..locks import make_lock as make_lock_by_spec
from ..entrance.ledger import Ledger, Slip
from .config import DoorConfig, SiteConfig, TokenConfig

# 役割 → 台帳に書ける名前(様式の :書ける: に合わせる)
ROLE_WRITES = {"管理者": {"経営者", "AI社員", "錠"}, "受付": {"AI社員", "錠"}, "利用者": {"錠"}, "保守": {"錠"}}


def make_lock(door: DoorConfig) -> LockDriver:
    return make_lock_by_spec(door.id, door.lock)


def _signer(keyfile: Path) -> HmacSigner:
    if not keyfile.exists():
        keyfile.parent.mkdir(parents=True, exist_ok=True)
        keyfile.write_bytes(secrets.token_bytes(32))
        keyfile.chmod(0o600)
    return HmacSigner(keyfile.read_bytes())


@dataclass
class Door:
    cfg: DoorConfig
    controller: Controller
    driver: LockDriver
    lock: threading.Lock


class Site:
    def __init__(self, cfg: SiteConfig, *, locks: dict[str, LockDriver] | None = None) -> None:
        self.cfg = cfg
        cfg.vault.mkdir(parents=True, exist_ok=True)
        roles = {t.name: set(ROLE_WRITES[t.role]) for t in cfg.tokens}
        self.ledger = Ledger(cfg.vault, roles=roles)
        self.doors: dict[str, Door] = {}
        for d in cfg.doors:
            drv = (locks or {}).get(d.id) or make_lock(d)
            audit = Audit(_signer(cfg.vault / "扉" / d.id / "audit.key"), cfg.vault / "扉" / d.id / "audit.jsonl")
            ctl = Controller(SimContacts(), audit, DoorCfg(two_person=d.two_person, autolock_s=d.autolock_s,
                                                          power_policy=d.power_policy))
            ctl.set_time(time.time(), True)
            self.doors[d.id] = Door(d, ctl, drv, threading.Lock())
        self.keyring = Keyring(self.ledger, {d: door.driver for d, door in self.doors.items()})
        self.tokens = {t.token: t for t in cfg.tokens}

    # ---- 認証 ----
    def who(self, token: str) -> TokenConfig | None:
        return self.tokens.get(token or "")

    # ---- 鍵 ----
    def issue(self, *, subject: str, door_id: str, start: str, end: str, by: TokenConfig, route: str = "遠隔解錠") -> Slip:
        if door_id not in self.doors:
            raise KeyError(door_id)
        return self.keyring.issue(subject=subject, lock_id=door_id, start=start, end=end, route=route, issuer=by.name)

    def revoke(self, key_id: str, reason: str, by: TokenConfig) -> Slip:
        return self.keyring.revoke(key_id, reason, issuer=by.name)

    def keys(self, subject: str | None = None, at: datetime | None = None) -> list[dict]:
        out = []
        for k in self.keyring.active(at):
            if subject and k["主体"] != subject:
                continue
            out.append({"id": k.id, "subject": k["主体"], "door": k["錠"], "start": k["開始"], "end": k["終了"], "route": k["経路"]})
        return out

    # ---- 扉 ----
    async def unlock(self, door_id: str, key_id: str, by: TokenConfig) -> dict:
        door = self.doors.get(door_id)
        if door is None:
            raise KeyError(door_id)
        ok, reason = self.keyring.is_valid(key_id)
        subject = by.subject or by.name
        if ok:
            try:
                if self.ledger.get(key_id)["錠"] != door_id:
                    ok, reason = False, "この扉の鍵ではありません"
                elif by.role == "利用者" and by.subject and self.ledger.get(key_id)["主体"] != by.subject:
                    ok, reason = False, "他人の鍵です"
            except KeyError:
                ok, reason = False, "発行の記録がありません"
        with door.lock:
            door.controller.tick(time.time())
            verdict = door.controller.on_auth(subject, route="系統2", valid=ok, reason=reason)
        if verdict in ("拒否", "待ち"):
            slip = self.ledger.append("鍵_操作", {"鍵番号": key_id, "錠": door_id, "操作": "解錠", "結果": "拒否",
                                                "理由": reason or verdict, "主体": subject}, issuer=by.name)
            return {"result": verdict, "reason": reason, "slip": slip.id}
        slip = await self.keyring.open(key_id, by=by.name)
        return {"result": slip["結果"], "reason": slip["理由"] if slip["結果"] != "成功" else "", "slip": slip.id,
                "door": door.controller.state()}

    async def lock(self, door_id: str, by: TokenConfig) -> dict:
        door = self.doors.get(door_id)
        if door is None:
            raise KeyError(door_id)
        with door.lock:
            verdict = door.controller.lock_request(by.name)
        result = "拒否" if verdict == "拒否" else "成功"
        reason = ""
        if result == "成功":
            try:
                await door.driver.lock()
            except Exception as e:
                result, reason = "失敗", str(e)[:180]
        slip = self.ledger.append("鍵_操作", {"錠": door_id, "操作": "施錠", "結果": result, "理由": reason, "主体": by.name},
                                  issuer=by.name)
        return {"result": result, "reason": reason, "slip": slip.id, "door": door.controller.state()}

    def state(self) -> dict:
        return {"site": self.cfg.name, "doors": {d: door.controller.state() for d, door in self.doors.items()},
                "slips": self.ledger.count()}

    def events(self, door_id: str, since: int = 0) -> list[dict]:
        door = self.doors.get(door_id)
        if door is None:
            raise KeyError(door_id)
        return [asdict(e) for e in door.controller.audit.since(since)]

    def operations(self, limit: int = 100) -> list[dict]:
        rows = self.ledger.select("鍵_操作")[-limit:]
        return [{"id": s.id, "at": s.issued_at, "door": s["錠"], "op": s["操作"], "result": s["結果"],
                 "reason": s["理由"], "subject": s["主体"], "key": s["鍵番号"]} for s in rows]

    def verify(self) -> dict:
        ok, n, why = self.ledger.verify_chain()
        out = {"ledger": {"ok": ok, "count": n, "detail": why}, "doors": {}}
        for d, door in self.doors.items():
            ok2, n2, why2 = door.controller.audit.verify()
            out["doors"][d] = {"ok": ok2, "count": n2, "detail": why2}
        out["ok"] = ok and all(v["ok"] for v in out["doors"].values())
        return out

    def tick(self) -> None:
        now = time.time()
        for door in self.doors.values():
            with door.lock:
                door.controller.tick(now)
