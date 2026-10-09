"""鍵 — 発行と失効と操作。

鍵は伝票である。発行の伝票に主体・錠・経路・開始・終了が書かれ、失効の伝票がそれを打ち消す。
錠の駆動は買った機器に任せる。ドライバは digitalkey.locks の登録簿から選ぶ(Sesame は別パッケージ digitalkey-sesame)。
解錠するときは「発行済みで、失効しておらず、時間の窓の中」だけを見る。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from .ledger import Ledger, Slip


class LockDriver(Protocol):
    id: str

    async def unlock(self) -> None: ...

    async def lock(self) -> None: ...


@dataclass
class DummyLock:
    """配線確認と試験用。動作を記録するだけ。"""

    id: str
    actions: list[str] = field(default_factory=list)

    async def unlock(self) -> None:
        self.actions.append("unlock")

    async def lock(self) -> None:
        self.actions.append("lock")


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


class Keyring:
    def __init__(self, ledger: Ledger, locks: dict[str, LockDriver] | None = None) -> None:
        self.ledger = ledger
        self.locks: dict[str, LockDriver] = locks or {}

    def issue(self, *, subject: str, lock_id: str, start: str, end: str, route: str = "遠隔解錠",
              reservation_id: str = "", basis: list[str] | tuple[str, ...] = (), issuer: str = "AI社員") -> Slip:
        return self.ledger.append("鍵_発行", {
            "主体": subject, "錠": lock_id, "経路": route, "開始": start, "終了": end,
            "予約番号": reservation_id, "根拠": ", ".join(basis),
        }, issuer=issuer, to=subject, basis=basis)

    def revoke(self, key_id: str, reason: str, *, issuer: str = "経営者") -> Slip:
        self.ledger.get(key_id)  # 無い番号なら KeyError
        return self.ledger.append("鍵_失効", {"鍵番号": key_id, "理由": reason}, issuer=issuer, basis=[key_id])

    def revoked(self, key_id: str) -> bool:
        return bool(self.ledger.select("鍵_失効", where={"鍵番号": key_id}))

    def is_valid(self, key_id: str, at: datetime | None = None) -> tuple[bool, str]:
        try:
            k = self.ledger.get(key_id)
        except KeyError:
            return False, "発行の記録がありません"
        if k.form != "鍵_発行":
            return False, "鍵の伝票ではありません"
        at = at or datetime.now()
        if self.revoked(key_id):
            return False, "失効しています"
        if at < _dt(k["開始"]):
            return False, "まだ有効ではありません"
        if at >= _dt(k["終了"]):
            return False, "期限が切れています"
        return True, ""

    def active(self, at: datetime | None = None, lock_id: str | None = None) -> list[Slip]:
        at = at or datetime.now()
        out = []
        for k in self.ledger.select("鍵_発行"):
            if lock_id and k["錠"] != lock_id:
                continue
            if self.is_valid(k.id, at)[0]:
                out.append(k)
        return out

    async def open(self, key_id: str, *, at: datetime | None = None, by: str = "AI社員") -> Slip:
        """鍵番号で解錠を試み、結果を必ず伝票に残す。"""
        ok, reason = self.is_valid(key_id, at)
        lock_id = ""
        try:
            lock_id = self.ledger.get(key_id)["錠"]
        except KeyError:
            pass
        if not ok:
            return self.ledger.append("鍵_操作", {"鍵番号": key_id, "錠": lock_id or "?", "操作": "解錠",
                                               "結果": "拒否", "理由": reason, "主体": by}, issuer=by)
        drv = self.locks.get(lock_id)
        if drv is None:
            return self.ledger.append("鍵_操作", {"鍵番号": key_id, "錠": lock_id, "操作": "解錠",
                                               "結果": "失敗", "理由": "錠の駆動が設定されていません", "主体": by},
                                      issuer=by, basis=[key_id])
        try:
            await drv.unlock()
        except Exception as e:  # 機器の失敗も記録に残す
            return self.ledger.append("鍵_操作", {"鍵番号": key_id, "錠": lock_id, "操作": "解錠",
                                               "結果": "失敗", "理由": str(e)[:180], "主体": by},
                                      issuer=by, basis=[key_id])
        return self.ledger.append("鍵_操作", {"鍵番号": key_id, "錠": lock_id, "操作": "解錠",
                                           "結果": "成功", "主体": by}, issuer=by, basis=[key_id])
