"""判断 — 火災・停電・避難・二人同時・強要を、接点と記録に落とす。

判断の表(既定):
- 火災信号: 電動部を解放する(避難のため)。閉鎖は扉のクローザーの仕事。記録「非常/火災信号」
- 停電: 既定は解放(fail-safe)。設定で保持(fail-secure)にできる。復電で元に戻す
- 認証済み: 有効な資格情報が来たら解錠。二人同時が要る扉では窓の秒数内に N 人揃って解錠
- 強要: 強要の印が付いた資格情報は解錠し、記録に「強要」を残す(外への通報は上の層)
- 自動施錠: 解錠から autolock 秒で、扉が閉じていれば施錠
- 手動リリース・改ざん: 記録だけ
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .audit import Audit
from .contacts import Contacts


@dataclass
class Config:
    two_person: int = 1            # 解錠に要る人数
    two_person_window_s: float = 10.0
    autolock_s: float = 5.0
    power_policy: str = "release"  # release(停電で解放) / hold(停電で保持)
    fire_policy: str = "release"


@dataclass
class Controller:
    contacts: Contacts
    audit: Audit
    config: Config = field(default_factory=Config)
    now: float = 0.0
    synced: bool = False
    locked: bool = True
    mode: str = "通常"             # 通常/火災/停電/通行
    unlocked_at: float | None = None
    pending: list[tuple[float, str]] = field(default_factory=list)  # 二人同時の待ち

    # ---- 時刻 ----
    def set_time(self, now: float, synced: bool = True) -> None:
        old = self.now
        self.now, self.synced = now, synced
        self._log("時刻", "同期", detail=f"{old}->{now}")

    def _log(self, kind: str, event: str, **kw) -> None:
        self.audit.append(at=self.now, synced=self.synced, kind=kind, event=event, **kw)

    # ---- 出力 ----
    def _unlock(self, reason: str, subject: str = "", route: str = "") -> None:
        self.contacts.set_lock(False)
        self.locked = False
        self.unlocked_at = self.now
        self._log("操作", "解錠", result="成功", subject=subject, route=route, detail=reason)

    def _lock(self, reason: str) -> None:
        self.contacts.set_lock(True)
        self.locked = True
        self.unlocked_at = None
        self._log("操作", "施錠", result="成功", detail=reason)

    # ---- 入力 ----
    def on_fire(self, active: bool) -> None:
        self._log("非常", "火災信号", result="入" if active else "復旧")
        if active:
            self.mode = "火災"
            if self.config.fire_policy == "release" and self.locked:
                self._unlock("火災信号")
        else:
            self.mode = "通常"

    def on_mains(self, present: bool) -> None:
        self._log("非常", "停電" if not present else "復電")
        if not present:
            if self.mode != "火災":
                self.mode = "停電"
            if self.config.power_policy == "release" and self.locked:
                self._unlock("停電")
        elif self.mode == "停電":
            self.mode = "通常"

    def on_auth(self, subject: str, route: str = "系統1", *, valid: bool = True, reason: str = "",
                duress: bool = False) -> str:
        """読取機か上の層から「この資格情報が提示された」と来る。有効性の判定は上の層(発行者)の仕事。"""
        if not valid:
            self._log("認証", "拒否", result=reason or "無効", subject=subject, route=route)
            return "拒否"
        if self.mode == "火災":
            self._log("認証", "成功", result="火災中は解放済み", subject=subject, route=route)
            return "解放済み"
        if duress:
            self._log("認証", "強要コード", result="解錠して通報", subject=subject, route=route)
            self._unlock("強要", subject, route)
            return "強要"
        self._log("認証", "成功", subject=subject, route=route)
        if self.config.two_person > 1:
            self.pending = [(t, s) for t, s in self.pending if self.now - t <= self.config.two_person_window_s
                            and s != subject]
            self.pending.append((self.now, subject))
            if len(self.pending) < self.config.two_person:
                self._log("認証", "二人同時 待ち", result=f"{len(self.pending)}/{self.config.two_person}", subject=subject)
                return "待ち"
            names = "+".join(s for _, s in self.pending)
            self.pending.clear()
            self._unlock("二人同時", names, route)
            return "解錠"
        if self.locked:
            self._unlock("認証", subject, route)
        return "解錠"

    def on_door(self, open_: bool) -> None:
        self._log("物理", "開扉" if open_ else "閉扉")

    def on_manual_release(self, means: str) -> None:
        self._log("非常", "非常手段", result=means)
        self.locked = False
        self.unlocked_at = self.now

    def on_tamper(self, what: str) -> None:
        self._log("異常", what)

    def lock_request(self, by: str = "上位") -> str:
        if self.mode in ("火災", "停電") and self.config.power_policy == "release":
            self._log("操作", "施錠", result="拒否", detail=f"{self.mode}中", subject=by)
            return "拒否"
        if self.contacts.door_open():
            self._log("操作", "施錠", result="拒否", detail="扉が開いている", subject=by)
            return "拒否"
        self._lock(f"要求({by})")
        return "施錠"

    def unlock_request(self, by: str = "上位") -> str:
        return self.on_auth(by, route="系統2")

    # ---- 時計 ----
    def tick(self, now: float) -> None:
        self.now = now
        if (not self.locked and self.unlocked_at is not None and self.mode == "通常"
                and now - self.unlocked_at >= self.config.autolock_s and not self.contacts.door_open()):
            self._lock("自動施錠")

    def state(self) -> dict:
        return {"locked": self.locked, "mode": self.mode, "door_open": self.contacts.door_open(),
                "bolt_out": self.contacts.bolt_out(), "fire": self.contacts.fire(), "mains": self.contacts.mains(),
                "now": self.now, "synced": self.synced, "audit_seq": len(self.audit.entries)}
