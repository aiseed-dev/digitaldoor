"""判断 — 火災・停電・避難・二人同時・強要を、接点と記録に落とす。

判断の表(既定):
- 火災信号: 電動部を解放する(避難のため)。閉鎖は扉のクローザーの仕事。記録「非常/火災信号」
- 停電: 既定は解放(fail-safe)。設定で保持(fail-secure)にできる。復電で元に戻す
- 認証済み: 有効な資格情報が来たら解錠。二人同時が要る扉では窓の秒数内に N 人揃って解錠
- 強要: 強要の印が付いた資格情報は解錠し、記録に「強要」を残す(外への通報は上の層)
- 自動施錠: 解錠から autolock 秒で、扉が閉じていれば施錠。0 なら自動施錠しない
- 施錠の要求: 火災中は受け付けない。停電中は解放の設定なら受け付けない
- 時刻: 同期のたびに前と新しい時刻を残す。同期済みから time_jump_s を超えて動いたら「異常/時刻のずれ」
- 設定: configure で変える。項目ごとに「設定/(項目名)」に前と新しい値を残す。無い項目と許されない値は全体を拒む
- 手動リリース・改ざん: 記録だけ
- 署名つき資格情報: present で、信頼する発行者の署名と範囲と期限を扉が確かめ、通れば上の判断(火災、二人同時、自動施錠)に渡す。
  一回限りの使用済みの一覧は、起動のときに記録(資格情報/使用)から作り直す(仕様書 5.2a)
- 信頼の一覧: change_trust で、据付のときに入れた管理者の鍵で署名した変更の要求だけを受ける。受けた変更は記録から読み戻す
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields, replace

from . import credentials
from .audit import Audit, Ed25519Verifier
from .contacts import Contacts
from .slips import key_id

TRUST_EVENT = "信頼する発行者"


@dataclass
class Config:
    two_person: int = 1            # 解錠に要る人数
    two_person_window_s: float = 10.0
    autolock_s: float = 5.0
    power_policy: str = "release"  # release(停電で解放) / hold(停電で保持)
    fire_policy: str = "release"
    time_jump_s: float = 300.0     # 同期済みの時計がこれを超えて動いたら「時刻のずれ」

    def check(self) -> None:
        """許されない値なら ValueError。"""
        for f in fields(self):
            v = getattr(self, f.name)
            kind = type(f.default)
            if isinstance(v, bool) or not isinstance(v, (int, float) if kind is float else kind):
                raise ValueError(f"{f.name}: 型が違います({v!r})")
        if self.power_policy not in ("release", "hold") or self.fire_policy not in ("release", "hold"):
            raise ValueError("power_policy と fire_policy は release か hold です")
        if self.two_person < 1:
            raise ValueError("two_person は 1 以上です")
        if min(self.two_person_window_s, self.autolock_s, self.time_jump_s) < 0:
            raise ValueError("秒数は 0 以上です")


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
    # 署名つき資格情報(仕様書 5.2a)。既存の引数の順番を変えないよう、キーワードだけで受ける
    name: str = field(default="door", kw_only=True)        # 扉の名前。据付のときに必ず設定する(既定は試験のため)
    admin_key: bytes | None = field(default=None, kw_only=True)   # 管理者の公開鍵(32 バイト)
    trust: dict | None = field(default=None, kw_only=True)   # {issuer: {"public_key": 16進, "doors": [...]}}
    _used: set[str] = field(default_factory=set, init=False, repr=False)       # 使用済みの一回限りの資格情報の識別
    _trust_ids: set[str] = field(default_factory=set, init=False, repr=False)  # 受けた変更の要求の識別

    def __post_init__(self) -> None:
        if self.admin_key is not None:
            Ed25519Verifier(self.admin_key)  # 長さが違えばここで ValueError
        self.trust = {k: {"public_key": v["public_key"], "doors": list(v["doors"])}
                      for k, v in (self.trust or {}).items()}
        self._rebuild_from_record()

    def _rebuild_from_record(self) -> None:
        """起動のとき、記録から使用済みの一覧と、受けた変更を作り直す(追記だけの記録が正)。

        受けた変更があれば、信頼の一覧は最後に受けた変更の後の一覧にする(消した発行者が再起動で戻らないように)。
        """
        for e in self.audit.entries:
            if e.kind == "資格情報" and e.event == "使用" and e.detail.startswith("id="):
                self._used.add(e.detail[3:])
            elif e.kind == "設定" and e.event == TRUST_EVENT and e.result == "受理":
                got = credentials.parse_change_detail(e.detail)
                if got is None:
                    continue
                self._trust_ids.add(got[0])
                if all(isinstance(v, dict) and isinstance(v.get("public_key"), str)
                       and isinstance(v.get("doors"), list) for v in got[1].values()):
                    self.trust = got[1]

    # ---- 時刻 ----
    def set_time(self, now: float, synced: bool = True) -> None:
        old, was_synced = self.now, self.synced
        self.now, self.synced = now, synced
        self._log("時刻", "同期", detail=f"前 {old} 新 {now}")
        if was_synced and abs(now - old) > self.config.time_jump_s:
            self._log("異常", "時刻のずれ", result=f"{now - old:+.0f}秒", detail=f"前 {old} 新 {now}")

    # ---- 設定 ----
    def configure(self, by: str, **changes) -> None:
        """設定を変える。一つでも無い項目か許されない値があれば ValueError で、何も変えない。"""
        names = {f.name for f in fields(Config)}
        unknown = set(changes) - names
        if unknown:
            raise ValueError(f"無い項目です: {', '.join(sorted(unknown))}")
        new = replace(self.config, **changes)
        new.check()
        old, self.config = self.config, new
        for name in changes:
            before, after = getattr(old, name), getattr(new, name)
            if before != after:
                self._log("設定", name, result=str(after), subject=by, detail=f"前 {before} 新 {after}")

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
                duress: bool = False, detail: str = "") -> str:
        """読取機か上の層から「この資格情報が提示された」と来る。有効性の判定は上の層(発行者)の仕事。"""
        if not valid:
            self._log("認証", "拒否", result=reason or "無効", subject=subject, route=route, detail=detail)
            return "拒否"
        if self.mode == "火災":
            self._log("認証", "成功", result="火災中は解放済み", subject=subject, route=route,
                      detail=detail)
            return "解放済み"
        if duress:
            self._log("認証", "強要コード", result="解錠して通報", subject=subject, route=route,
                      detail=detail)
            self._unlock("強要", subject, route)
            return "強要"
        self._log("認証", "成功", subject=subject, route=route, detail=detail)
        if self.config.two_person > 1:
            self.pending = [(t, s) for t, s in self.pending if self.now - t <= self.config.two_person_window_s
                            and s != subject]
            self.pending.append((self.now, subject))
            if len(self.pending) < self.config.two_person:
                self._log("認証", "二人同時 待ち", result=f"{len(self.pending)}/{self.config.two_person}",
                          subject=subject, detail=detail)
                return "待ち"
            names = "+".join(s for _, s in self.pending)
            self.pending.clear()
            self._unlock("二人同時", names, route)
            return "解錠"
        if self.locked:
            self._unlock("認証", subject, route)
        return "解錠"

    # ---- 署名つき資格情報(仕様書 5.2a) ----
    def present(self, credential, route: str = "遠隔") -> str:
        """発行者の署名つき資格情報を、扉が自分で確かめて受ける。網も発行者のサーバーも要らない。

        確かめて外れたら、理由を結果にして認証の拒否を記録する。通れば on_auth の判断(火災、二人同時、自動施錠)を通る。
        一回限りの資格情報は、通ったら「使用」を記録に足す。強要の印は持たせない(読取機の判定結果で受ける)。
        """
        v = credentials.verify_credential(credential, door=self.name, trust=self.trust, now=self.now,
                                          synced=self.synced, used_ids=self._used)
        if not v.ok:
            return self.on_auth(v.subject, route, valid=False, reason=v.reason, detail=v.detail)
        result = self.on_auth(v.subject, route, detail=v.detail)
        if v.once:
            self._used.add(v.cred_id)
            self._log("資格情報", "使用", detail=f"id={v.cred_id}")
        return result

    def change_trust(self, request) -> str:
        """管理者の署名つきの要求で、信頼の一覧を変える。受理か拒否を返す。時計の同期は要らない。"""
        reason, body = credentials.verify_trust_change(request, door=self.name, admin_key=self.admin_key,
                                                       used_ids=self._trust_ids)
        if reason:
            self._log("設定", TRUST_EVENT, result="拒否", detail=reason)
            return "拒否"
        before, after = self.trust, credentials.apply_change(self.trust, body)
        self.trust = after
        self._trust_ids.add(body["id"])
        self._log("設定", TRUST_EVENT, result="受理", subject=key_id(self.admin_key),
                  detail=credentials.change_detail(body, before, after))
        return "受理"

    def on_door(self, open_: bool) -> None:
        self._log("物理", "開扉" if open_ else "閉扉")

    def on_manual_release(self, means: str) -> None:
        self._log("非常", "非常手段", result=means)
        self.locked = False
        self.unlocked_at = self.now

    def on_tamper(self, what: str) -> None:
        self._log("異常", what)

    def lock_request(self, by: str = "上位") -> str:
        if self.mode == "火災" or (self.mode == "停電" and self.config.power_policy == "release"):
            self._log("操作", "施錠", result="拒否", detail=f"{self.mode}中", subject=by)
            return "拒否"
        if self.contacts.door_open():
            self._log("操作", "施錠", result="拒否", detail="扉が開いている", subject=by)
            return "拒否"
        self._lock(f"要求({by})")
        return "施錠"

    def refuse_remote(self, action: str, addr: str) -> None:
        """同じ機械以外から /lock /unlock が来たときの拒否を記録する(口が拒んだ事実だけで、状態は動かさない)。"""
        self._log("操作", "解錠" if action == "unlock" else "施錠", result="拒否", route="網",
                  detail=f"同じ機械以外からの要求 {addr}")

    def unlock_request(self, by: str = "上位") -> str:
        return self.on_auth(by, route="系統2")

    # ---- 時計 ----
    def tick(self, now: float) -> None:
        self.now = now
        if (self.config.autolock_s > 0 and not self.locked and self.unlocked_at is not None and self.mode == "通常"
                and now - self.unlocked_at >= self.config.autolock_s and not self.contacts.door_open()):
            self._lock("自動施錠")

    def state(self) -> dict:
        return {"locked": self.locked, "mode": self.mode, "door_open": self.contacts.door_open(),
                "bolt_out": self.contacts.bolt_out(), "fire": self.contacts.fire(), "mains": self.contacts.mains(),
                "now": self.now, "synced": self.synced, "audit_seq": len(self.audit.entries)}
