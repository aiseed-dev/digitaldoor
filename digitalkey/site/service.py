"""事業所サーバーの中身。扉ごとのコントローラと、鍵の台帳を一つに束ねる。

予約と資格情報の発行(使い方.md「予約と資格情報の発行のインターフェース」): 予約、予約からの鍵の発行、予約の取消(出した鍵を失効)、
鍵からの署名つき資格情報(扉コントローラ仕様書 5.2a)。記録は台帳の伝票で、追記だけ。
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime

from digitaldoor_controller import credentials as cred
from digitaldoor_controller.audit import Audit, Ed25519Signer, load_signer
from digitaldoor_controller.slips import key_id as pub_key_id
from digitaldoor_controller.contacts import SimContacts
from digitaldoor_controller.controller import Config as DoorCfg, Controller
from ..entrance.keyring import Keyring, LockDriver
from ..locks import make_lock as make_lock_by_spec
from ..entrance.ledger import Ledger, Slip
from .config import DoorConfig, SiteConfig, TokenConfig, token_hash

# 役割 → 台帳に書ける名前(様式の :書ける: に合わせる)
ROLE_WRITES = {"管理者": {"経営者", "AI社員", "錠"}, "受付": {"AI社員", "錠"}, "利用者": {"錠"}, "保守": {"錠"}}


ISSUER_FILE = "発行者.ed25519"


class Conflict(Exception):
    """今の記録の状態ではできない操作(取り消した予約、失効した鍵など)。HTTP では 409。"""


def load_issuer(vault) -> Ed25519Signer:
    """金庫の発行者の鍵を開く。無ければ作る(権限 600、上書きしない)。取り出せない所へ移すときは同じインターフェースで差し替える。"""
    p = vault / ISSUER_FILE
    if not p.exists():
        signer = Ed25519Signer.generate()
        try:
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "wb") as f:
                f.write(signer.private_bytes())
    raw = p.read_bytes()
    if len(raw) != 32:
        raise ValueError(f"発行者の鍵の形が違います: {p}")
    return Ed25519Signer(raw)


def _ts(text: str) -> int:
    return int(datetime.fromisoformat(text).timestamp())


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).isoformat(timespec="seconds")


def make_lock(door: DoorConfig) -> LockDriver:
    return make_lock_by_spec(door.id, door.lock)


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
        self.issuer = load_issuer(cfg.vault)
        self.issuer_id = pub_key_id(self.issuer.public_key())
        trust = {self.issuer_id: {"public_key": self.issuer.public_key().hex(), "doors": [d.id for d in cfg.doors]}}
        self.doors: dict[str, Door] = {}
        for d in cfg.doors:
            drv = (locks or {}).get(d.id) or make_lock(d)
            audit = Audit(load_signer(cfg.vault / "扉" / d.id), cfg.vault / "扉" / d.id / "audit.jsonl")
            ctl = Controller(SimContacts(), audit, DoorCfg(two_person=d.two_person, autolock_s=d.autolock_s,
                                                          power_policy=d.power_policy),
                             name=d.id, trust=trust)
            ctl.set_time(time.time(), True)
            self.doors[d.id] = Door(d, ctl, drv, threading.Lock())
        self.keyring = Keyring(self.ledger, {d: door.driver for d, door in self.doors.items()})
        self.tokens = {t.token_sha256: t for t in cfg.tokens}

    # ---- 認証 ----
    def who(self, token: str) -> TokenConfig | None:
        """届いたトークンを SHA-256 にしてから照らす。設定はトークンそのものを持たない。"""
        if not token:
            return None
        return self.tokens.get(token_hash(token))

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

    # ---- 予約 ----
    def reserve(self, *, subject: str, layer: str, arrival: str, departure: str, door_id: str, contact: str,
                by: TokenConfig) -> Slip:
        if door_id not in self.doors:
            raise KeyError(door_id)
        if _ts(departure) <= _ts(arrival):
            raise ValueError("出発は到着より後にしてください")
        return self.ledger.append("予約", {"主体": subject, "層": layer, "到着": arrival, "出発": departure,
                                           "錠": door_id, "連絡先": contact, "受付コード": secrets.token_urlsafe(16)},
                                  issuer=by.name)

    def _reservation(self, rid: str) -> Slip:
        y = self.ledger.get(rid)
        if y.form != "予約":
            raise KeyError(rid)
        return y

    def _cancel_of(self, rid: str) -> str:
        rows = self.ledger.select("予約_取消", where={"予約番号": rid})
        return rows[0].id if rows else ""

    def _reservation_dict(self, y: Slip) -> dict:
        return {"id": y.id, "subject": y["主体"], "layer": y["層"], "arrival": y["到着"], "departure": y["出発"],
                "door": y["錠"], "contact": y["連絡先"], "checkin_code": y["受付コード"],
                "cancelled": self._cancel_of(y.id)}

    def reservations(self) -> list[dict]:
        return [self._reservation_dict(y) for y in self.ledger.select("予約")]

    def reservation(self, rid: str) -> dict:
        out = self._reservation_dict(self._reservation(rid))
        out["keys"] = [{"id": k.id, "route": k["経路"], "start": k["開始"], "end": k["終了"],
                        "revoked": self.keyring.revoked(k.id)}
                       for k in self.ledger.select("鍵_発行", where={"予約番号": rid})]
        return out

    def issue_for_reservation(self, rid: str, *, route: str, by: TokenConfig) -> Slip:
        y = self._reservation(rid)
        if self._cancel_of(rid):
            raise Conflict("取り消した予約です")
        return self.keyring.issue(subject=y["主体"], lock_id=y["錠"], start=y["到着"], end=y["出発"], route=route,
                                  reservation_id=rid, basis=[rid], issuer=by.name)

    def cancel_reservation(self, rid: str, reason: str, by: TokenConfig) -> tuple[Slip, list[str]]:
        self._reservation(rid)
        if self._cancel_of(rid):
            raise Conflict("もう取り消した予約です")
        c = self.ledger.append("予約_取消", {"予約番号": rid, "理由": reason}, issuer=by.name, basis=[rid])
        revoked = []
        for k in self.ledger.select("鍵_発行", where={"予約番号": rid}):
            if not self.keyring.revoked(k.id):
                self.keyring.revoke(k.id, "予約の取消", issuer=by.name)
                revoked.append(k.id)
        return c, revoked

    # ---- 署名つき資格情報(扉コントローラ仕様書 5.2a) ----
    def issuer_info(self) -> dict:
        return {"issuer": self.issuer_id, "public_key": self.issuer.public_key().hex(),
                "doors": list(self.doors), "max_hours": self.cfg.credential_max_hours}

    def credential(self, key_id: str, *, once: bool, by: TokenConfig, now: float | None = None) -> dict:
        """鍵から資格情報を作る。終わりは、鍵の終わりと now + max_hours の早い方。主体は鍵番号(仮名)にする。"""
        k = self.ledger.get(key_id)
        if k.form != "鍵_発行":
            raise KeyError(key_id)
        if self.keyring.revoked(key_id):
            raise Conflict("失効した鍵です")
        now = time.time() if now is None else now
        start, end = _ts(k["開始"]), _ts(k["終了"])
        until = min(end, int(now + self.cfg.credential_max_hours * 3600))
        if until <= max(start, int(now)):
            raise Conflict("期限の外の鍵です(終わったか、始まりが最長の有効時間より先)")
        cid = "C-" + secrets.token_hex(8)
        c = cred.issue(self.issuer, cred_id=cid, subject=key_id, doors=[k["錠"]], not_before=start,
                       not_after=until, once=once)
        self.ledger.append("資格情報_発行", {"鍵番号": key_id, "錠": k["錠"], "識別": cid, "発行者": self.issuer_id,
                                              "開始": _iso(start), "終了": _iso(until), "一回限り": "はい" if once else "いいえ"},
                           issuer=by.name, basis=[key_id])
        return {"credential": c, "id": cid, "door": k["錠"], "not_before": start, "not_after": until, "once": once}

    async def present(self, door_id: str, credential, by: TokenConfig) -> dict:
        """資格情報を扉のコントローラに見せる。扉が自分で確かめ、通れば錠を動かす。結果は鍵の操作の伝票に残す。"""
        door = self.doors.get(door_id)
        if door is None:
            raise KeyError(door_id)
        with door.lock:
            door.controller.tick(time.time())
            verdict = door.controller.present(credential, "遠隔")
            last = door.controller.audit.entries[-1]
        key = self._key_of(credential)
        if verdict == "拒否":
            reason, result = last.result or "拒否", "拒否"
        elif verdict == "待ち":
            reason, result = "二人同時 待ち", "拒否"
        else:
            reason, result = "", "成功"
            try:
                await door.driver.unlock()
            except Exception as e:  # 機器の失敗も記録に残す
                result, reason = "失敗", str(e)[:180]
        slip = self.ledger.append("鍵_操作", {"鍵番号": key, "錠": door_id,
                                            "操作": "解錠", "結果": result, "理由": reason, "主体": by.name},
                                  issuer=by.name)
        return {"result": verdict, "reason": reason, "slip": slip.id, "door": door.controller.state()}

    def _key_of(self, credential) -> str:
        """資格情報の主体が、この台帳の鍵番号ならそれを返す(読めない物や外の発行者の物は空)。"""
        try:
            subject = json.loads(credential["body"])["subject"]
            return subject if self.ledger.get(subject).form == "鍵_発行" else ""
        except Exception:
            return ""

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
