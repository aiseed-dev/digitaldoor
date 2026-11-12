# SPDX-License-Identifier: AGPL-3.0-or-later
"""ネットワークの区画と機器の台帳(設計書 7a 章、20.5〜20.10)。`Unit` のメソッドになる。

台帳は事実を写します。機器の状態(準備、使用、休止、紛失、廃棄)は、機器の権限を出す時の機器の有無の確かめのほかは
門にしません。責任者がいない、区画が決まっていない、据え付けの確かめが無い、といった足りない物は、登録と変更で拒まずに、
照らし合わせの通知(`device_findings`)に挙げます。もう使っている機器をまとめて載せても、台帳が実際と違う形に
なりません。

止めるのは自動、機器を変えるのは人です。機器を紛失か廃棄にすると、その機器を対象にした開いた権限を、同じ操作の中で
自動で失効にします。機器の中のアカウントを消すのは人で、消した記録(管理者のインターフェース_削除)が出るまで、進み具合に
人の作業が要る で残ります。

値を載せません。パスワードと鍵の値は様式に項目が無く、載せるのはインターフェースの名と、秘密の在庫表(kagi/)の番号だけです。
点検の間と、更新の期限の何日前に知らせるかは、管理者が設定(点検の間の日数、更新の期限の通知の日数)に書く値で、
ソフトは既定の値を持ちません。設定を書いていない間は、その通知を出しません。

    u.register_segment("扉", note="錠、扉のコントローラ、小さなサーバー", by="adm", role="管理者")
    u.register_device("ルーター1", "ルーター", "使用", owner="t1", segment="事務", support_until="2027-03-31",
                      by="adm", role="管理者")
    u.check_device("ルーター1", "据え付け", {"初めのパスワード": "良い"}, by="t1", role="本人")
    u.device_findings()
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

from . import reasons as R
from .model import (Check, Device, DeviceChange, Finding, Held, Segment, form_time, local, parse)
from .reasons import Refused
from .roster import ADMIN, yes
from .routes import ACCESS

CHECKERS = {"管理者", "運用", "本人"}   # 確かめ、更新、管理者のインターフェースの削除を書ける役
LIVE = ("準備", "使用", "休止")          # 機器の権限を出せる状態
OPEN = ("申請中", "承認", "発行済み")     # 紛失と廃棄の時に失効にする権限の状態
STOP_STATES = ("紛失", "廃棄")           # この状態にすると、その機器の権限と管理者のインターフェースを止める
OWNED = ("準備", "使用", "休止", "紛失")  # 責任者の通知を見る状態

# 機器_確かめの確かめの項目(様式の項目の順)
CHECK_ITEMS = ("初めのパスワード", "外部からのアクセス経路", "見覚えのない設定とアカウント", "バージョン", "区画", "権限", "記録の送り先",
               "中に残る物", "ネットワークから外した")

# 引数の名 → 機器_登録・機器_変更の項目の名(扉の系統と状態のほか)
FIELDS = {"kind": "種類", "owner": "責任者", "place": "置き場", "segment": "区画", "door": "扉", "maker": "製造者",
          "model": "型", "serial": "製造番号", "net_id": "ネットワークの番号", "version": "バージョン", "support_until": "更新の期限",
          "update_mode": "更新の当て方", "secret_ref": "秘密の在庫の番号", "log_to": "記録の送り先", "holds": "中に残る物"}
ITEMS = ("種類", "責任者", "置き場", "区画", "扉の系統", "扉", "製造者", "型", "製造番号", "ネットワークの番号", "バージョン", "更新の期限",
         "更新の当て方", "秘密の在庫の番号", "記録の送り先", "中に残る物", "状態")


def _need(device: Device, scene: str) -> tuple[str, ...]:
    """場面ごとに要る確かめの項目(20.7 の表)。点検で要る項目はルーターだけ(R-NW-04)。"""
    if scene == "据え付け":
        return ("初めのパスワード",)
    if scene == "点検":
        return ("外部からのアクセス経路", "見覚えのない設定とアカウント") if device.種類 == "ルーター" else ()
    return ("中に残る物", "ネットワークから外した")


def _str(v) -> str:
    """更新の期限の引数(年-月-日 の文字列か date)を様式に渡す形に。None は空。"""
    if v is None:
        return ""
    return v.isoformat() if isinstance(v, date) and not isinstance(v, datetime) else v


class DevicesMixin:
    """区画、機器の台帳、確かめと更新、管理者のインターフェースの削除、照らし合わせの通知、業者と連携先の目録のメソッド。"""

    # ---- 読む(役を取らない) ----
    def _segments(self) -> dict[str, Segment]:
        out: dict[str, Segment] = {}
        for s in self.book().of("区画"):
            out[s["名前"]] = Segment(s["名前"], s["説明"], s["状態"])
        return out

    def _devices(self) -> dict[str, Device]:
        """機器の名前 → 今の値。機器_登録の値に、機器_変更の空でない値を台帳に書いた順に重ねる。
        バージョンは、機器_登録、空でないバージョンの機器_変更、結果が 当てた の機器_更新の 後のバージョン を、台帳に書いた順に読んだ最後の物。"""
        book = self.book()
        if "devices" not in book.memo:
            rows: dict[str, dict] = {}
            for s in book.slips:
                if s.form == "機器_登録":
                    rows.setdefault(s["名前"], {k: s[k] for k in ITEMS})
                elif s.form == "機器_変更" and (r := rows.get(s["名前"])) is not None:
                    r.update({k: s[k] for k in ITEMS if s[k]})
                elif s.form == "機器_更新" and s["結果"] == "当てた" and (r := rows.get(s["機器"])) is not None:
                    r["バージョン"] = s["後のバージョン"]
            book.memo["devices"] = {name: Device(name, **{k: (r[k] == "true" if k == "扉の系統" else r[k])
                                                          for k in ITEMS})
                                    for name, r in rows.items()}
        return book.memo["devices"]

    def _device(self, name: str) -> Device:
        d = self._devices().get(name)
        if d is None:
            raise Refused(R.UNKNOWN_DEVICE, name)
        return d

    def _live_device(self, name: str) -> Device:
        """機器の権限の対象の確かめ(20.8)。状態が 準備、使用、休止 の機器でなければ UNKNOWN_DEVICE(詳細に名前)。"""
        d = self._devices().get(name)
        if d is None or d.状態 not in LIVE:
            raise Refused(R.UNKNOWN_DEVICE, name)
        return d

    def segments(self) -> list[Segment]:
        """区画の今の値。廃止した区画も入る。最初の伝票を台帳に書いた順。"""
        return list(self._segments().values())

    def device(self, name: str) -> Device:
        """機器の今の値。無ければ UNKNOWN_DEVICE(詳細に名前)。"""
        return self._device(name)

    def devices(self) -> list[Device]:
        """機器の台帳の全部。機器_登録を台帳に書いた順。廃棄した機器も入る。"""
        return list(self._devices().values())

    def _checks(self, name: str) -> list[Check]:
        return [Check(s.id, s["機器"], s["場面"], s["日時"], {k: s[k] for k in CHECK_ITEMS if s[k]}, s["所見"], s.issuer)
                for s in self.book().where("機器_確かめ", "機器", name)]

    def checks(self, name: str) -> list[Check]:
        """その機器の確かめ(据え付け、点検、廃棄)を、台帳に書いた順に。機器が無ければ UNKNOWN_DEVICE。"""
        self._device(name)
        return self._checks(name)

    # ---- 書く: 区画と機器 ----
    def _check_people_and_places(self, v: dict) -> None:
        """登録と変更の、責任者、区画、扉の確かめ(この順。渡した物だけ)。"""
        if v.get("責任者"):
            who = self._subjects().get(v["責任者"])
            if who is None:
                raise Refused(R.SUBJECT_UNKNOWN)
            if who.状態 != "有効":
                raise Refused(R.SUBJECT_INACTIVE)
        if v.get("区画"):
            seg = self._segments().get(v["区画"])
            if seg is None or seg.状態 != "使用":
                raise Refused(R.UNKNOWN_SEGMENT, v["区画"])
        if v.get("扉"):
            door = self._doors().get(v["扉"])
            if door is None or door.状態 != "使用":
                raise Refused(R.UNKNOWN_DOOR, v["扉"])

    def register_segment(self, name: str, *, by: str, role: str, note: str = "", state: str = "使用"):
        """区画を書く(管理者。R-NW-02)。扉の系統、事務、来客、設備など、分け方は管理者が決める。state は 使用 か 廃止。
        同じ名前の二枚目が今の値になる。廃止した区画には新しく機器を載せられず、そこに載っている使用の機器は
        通知 区画が決まっていません に挙がる。"""
        self._begin(ADMIN, by, role)
        v = self._clean("区画", {"名前": name, "説明": note, "状態": state, "役": role})
        if "," in v["名前"]:
            raise Refused(R.NAME_HAS_COMMA)
        return self._write("区画", v, by=by, role=role)

    def register_device(self, name: str, kind: str, state: str, *, by: str, role: str, owner: str = "",
                        place: str = "", segment: str = "", door_system: bool = False, door: str = "", maker: str = "",
                        model: str = "", serial: str = "", net_id: str = "", version: str = "", support_until="",
                        update_mode: str = "", secret_ref: str = "", log_to: str = "", holds: str = ""):
        """機器_登録を書き、ネットワークにつながる機器を一台、台帳に載せる(管理者)。state は 準備、使用、休止(既定なし)。

        足りない項目(責任者、区画、更新の期限)は空のまま載せ、照らし合わせの通知に挙げる。種類と扉の系統、
        種類と扉の組み合わせは確かめない。support_until は 年-月-日 の文字列か date。secret_ref は秘密の在庫表(kagi/)の
        番号で、パスワードや鍵の値は書かない。名前は廃棄した機器を含めて台帳の中で一つ(DUPLICATE_DEVICE)。"""
        self._begin(ADMIN, by, role)
        given = dict(kind=kind, owner=owner, place=place, segment=segment, door=door, maker=maker, model=model,
                     serial=serial, net_id=net_id, version=version, support_until=_str(support_until),
                     update_mode=update_mode, secret_ref=secret_ref, log_to=log_to, holds=holds)
        v = self._clean("機器_登録", {"名前": name, **{FIELDS[k]: x or "" for k, x in given.items()},
                                     "扉の系統": yes(bool(door_system)), "状態": state, "役": role})
        if "," in v["名前"]:
            raise Refused(R.NAME_HAS_COMMA)
        if v["名前"] in self._devices():
            raise Refused(R.DUPLICATE_DEVICE)
        self._check_people_and_places(v)
        return self._write("機器_登録", v, by=by, role=role)

    def change_device(self, name: str, *, reason: str, by: str, role: str, at: datetime | None = None, kind=None,
                      owner=None, place=None, segment=None, door_system: bool | None = None, door=None, maker=None,
                      model=None, serial=None, net_id=None, version=None, support_until=None, update_mode=None,
                      secret_ref=None, log_to=None, holds=None, state=None) -> DeviceChange:
        """機器_変更を書く(管理者)。渡した項目だけが変わり、空の文字列は None と同じ(前のまま)。
        door_system は None なら前のまま、bool なら はい か いいえ。state は 準備、使用、休止、紛失、廃棄。

        state を紛失か廃棄にすると、同じ呼び出しの中で、その機器を対象にした権限のうち状態(at で読む)が 申請中、承認、
        発行済み の物の全部に権限_失効(範囲 機器の停止、理由 reason)を書き、その権限の実体のうち止まっていない物に
        経路の「止める」を at で呼んで実体_失効を書く。管理者のインターフェースは、削除の伝票が出るまで 人の作業が要る で残る。
        署名つき資格情報の実体を止めた時は失効の一覧を一枚作る(DeviceChange.失効_一覧。機器の権限は署名つき資格情報を
        出せないので、今はいつも None)。
        機器の責任者や権限の主体の、扉の権限は止めない(人を止めるのは revoke_subject)。
        紛失からは戻せるが、失効した権限は戻らない。廃棄した機器は変えられない(DEVICE_RETIRED)。"""
        self._begin(ADMIN, by, role)
        given = dict(kind=kind, owner=owner, place=place, segment=segment, door=door, maker=maker, model=model,
                     serial=serial, net_id=net_id, version=version, support_until=_str(support_until),
                     update_mode=update_mode, secret_ref=secret_ref, log_to=log_to, holds=holds)
        changes = {**{FIELDS[k]: x or "" for k, x in given.items()},
                   "扉の系統": "" if door_system is None else yes(bool(door_system)), "状態": state or ""}
        v = self._clean("機器_変更", {"名前": name, **changes, "理由": reason, "役": role})
        d = self._device(v["名前"])
        if d.状態 == "廃棄":
            raise Refused(R.DEVICE_RETIRED)
        if not any(v[k] for k in changes):
            raise Refused(R.NOTHING_TO_CHANGE)
        self._check_people_and_places(v)
        at = local(at)
        slip = self._write("機器_変更", v, by=by, role=role)
        if v["状態"] not in STOP_STATES:
            return DeviceChange(slip, [], [], None)
        perms = [p for p in self.permissions(at=at) if p.対象の種類 == "機器" and p.対象 == d.名前]
        ids = {p.番号 for p in perms}
        mine = [e for e in self.entities() if e.権限 in ids]
        written, stopped = self._stop_all(perms, mine, "機器の停止", v["理由"], by, role, at)
        return DeviceChange(slip, written, stopped, self._list_after_stop(stopped, by, role, at))

    # ---- 書く: 確かめと更新 ----
    def _owner_check(self, d: Device, by: str, role: str) -> None:
        """本人の役では、by がその機器の今の責任者の時だけ通す(NOT_OWNER)。管理者と運用は責任者を問わない。"""
        if role == "本人" and (not d.責任者 or d.責任者 != by):
            raise Refused(R.NOT_OWNER)

    def check_device(self, name: str, scene: str, results: dict[str, str], *, by: str, role: str, note: str = "",
                     at=None):
        """機器_確かめを書く(管理者、運用、本人。本人は機器の責任者だけ)。R-NW-02〜04。

        scene は 据え付け、点検、廃棄。results は {確かめの項目の名: 良い|悪い|当たらない} で、空は「見ていない」。
        項目は 初めのパスワード、外部からのアクセス経路、見覚えのない設定とアカウント、バージョン、区画、権限、記録の送り先、中に残る物、
        ネットワークから外した。note は所見、at は確かめた日時(年-月-日T時:分 の文字列か datetime。None は今)。
        場面ごとに要る項目: 据え付けは 初めのパスワード、ルーターの点検は 外部からのアクセス経路 と 見覚えのない設定とアカウント、
        廃棄は 中に残る物 と ネットワークから外した(空なら NEED_ITEM。当たらない でも通る)。悪い は拒まず、通知に挙がる。
        廃棄した機器には、場面 廃棄 の確かめだけを書ける。"""
        self._begin(CHECKERS, by, role)
        results = dict(results or {})
        known = {k: x for k, x in results.items() if k in CHECK_ITEMS}
        v = self._clean("機器_確かめ", {"機器": name, "場面": scene,
                                       "日時": form_time(at if at is not None else datetime.now()), **known,
                                       "所見": note, "役": role})
        d = self._device(v["機器"])
        self._owner_check(d, by, role)
        if d.状態 == "廃棄" and v["場面"] != "廃棄":
            raise Refused(R.DEVICE_RETIRED)
        for k in results:
            if k not in CHECK_ITEMS:
                raise Refused(R.FORM, str(k))
        if not any(v[k] for k in known) and not v["所見"]:
            raise Refused(R.FORM, "確かめの項目か所見が要ります")
        for k in _need(d, v["場面"]):
            if not v.get(k):
                raise Refused(R.NEED_ITEM, k)
        return self._write("機器_確かめ", v, by=by, role=role)

    def record_update(self, name: str, version: str, *, by: str, role: str, result: str = "当てた", reason: str = "",
                      at=None):
        """機器_更新を書く(管理者、運用、本人。本人は機器の責任者だけ。R-NW-03)。version は後のバージョン、
        result は 当てた か 当てられなかった(理由が要る)。前のバージョンは、書く時の台帳の今のバージョンをソフトが書く。
        当てた の後は、後のバージョンが機器の今のバージョン。at は日時(年-月-日T時:分 の文字列か datetime。None は今)。"""
        self._begin(CHECKERS, by, role)
        v = self._clean("機器_更新", {"機器": name, "日時": form_time(at if at is not None else datetime.now()),
                                     "後のバージョン": version, "結果": result, "理由": reason, "役": role})
        d = self._device(v["機器"])
        self._owner_check(d, by, role)
        if d.状態 == "廃棄":
            raise Refused(R.DEVICE_RETIRED)
        if v["結果"] == "当てられなかった" and not v["理由"]:
            raise Refused(R.NEED_REASON)
        return self._write("機器_更新", {**v, "前のバージョン": d.バージョン}, by=by, role=role)

    # ---- 書く: 管理者のインターフェースの削除 ----
    def remove_access(self, entity_id: str, result: str = "消した", note: str = "", *, by: str, role: str):
        """管理者のインターフェース_削除を書く(管理者、運用、本人。本人は機器の責任者だけ)。人が機器の中でインターフェースを消した事の記録で、
        ソフトは機器を変えない。result は 消した、パスワードを替えた(一つのインターフェースを複数の人が使う機器で、インターフェースを消せない時)、
        機器を初期化、機器が無い。note は備考。失効の前にも、廃棄した機器にも書ける。削除の後、その行は 止まった。"""
        self._begin(CHECKERS, by, role)
        v = self._clean("管理者のインターフェース_削除", {"実体": entity_id, "結果": result, "備考": note, "役": role})
        e = self._entities().get(v["実体"])
        if e is None:
            raise Refused(R.ENTITY_UNKNOWN)
        if e.経路 != ACCESS:
            raise Refused(R.NOT_ACCESS)
        if role == "本人":
            d = self._devices().get(e.機器)
            if d is None or d.責任者 != by:
                raise Refused(R.NOT_OWNER)
        if e.回収:
            raise Refused(R.ALREADY_REMOVED)
        return self._write("管理者のインターフェース_削除", {**v, "機器": e.機器, "インターフェースの名": e.識別}, by=by, role=role, to=e.主体,
                           basis=[e.番号])

    # ---- 照らし合わせの通知(20.9) ----
    def _days(self, item: str) -> int | None:
        """設定の日数。書いていない、または空なら None(その通知を出さない)。"""
        v = self.setting(item)
        return int(v) if v.isdigit() else None

    def device_findings(self, *, at: datetime | None = None) -> list[Finding]:
        """台帳だけでできる照らし合わせの通知。並びは機器_登録を台帳に書いた順、同じ機器の中では次の順。
        通知は拒否ではなく、台帳に何も書かない。今日は at(None は今)の地方時の日付。

        責任者がいません、責任者が有効ではありません(準備、使用、休止、紛失。R-NW-05)。
        区画が決まっていません、扉の系統とほかの機器が同じ区画です(使用。R-NW-02)。
        更新の期限が分かりません、更新の期限を過ぎています、更新の期限が近づいています(使用。R-NW-03。
        近づいているは、設定 更新の期限の通知の日数 を書いた時だけ)。
        据え付けの確かめがありません、確かめの結果が悪いままです(使用。R-NW-02〜04)。
        点検の間が空いています(使用。設定 点検の間の日数 を書いた時だけ。R-NW-04)。
        一つのインターフェースを複数の人が使っています(使用。R-RS-04)。
        廃棄の確かめがありません(廃棄)。止まっていないインターフェースがあります(紛失、廃棄。R-AC-15)。"""
        at = local(at)
        today = at.date()
        devices = self.devices()
        subjects = self._subjects()
        segments = self._segments()
        near = self._days("更新の期限の通知の日数")
        interval = self._days("点検の間の日数")
        door_segments = {d.区画 for d in devices if d.状態 == "使用" and d.扉の系統 and d.区画}
        access: dict[str, list] = defaultdict(list)
        for e in self.entities():
            if e.経路 == ACCESS:
                access[e.機器].append(e)
        out: list[Finding] = []
        for d in devices:
            def add(word: str, detail: str = "") -> None:
                out.append(Finding(d.名前, word, detail))

            using = d.状態 == "使用"
            if d.状態 in OWNED:
                if not d.責任者:
                    add(R.F_NO_OWNER)
                elif (who := subjects.get(d.責任者)) is None or who.状態 != "有効":
                    add(R.F_OWNER_INACTIVE, d.責任者)
            checks = self._checks(d.名前) if using or d.状態 == "廃棄" else []
            if using:
                seg = segments.get(d.区画)
                if not d.区画 or seg is None or seg.状態 != "使用":
                    add(R.F_NO_SEGMENT, d.区画)
                if not d.扉の系統 and d.区画 and d.区画 in door_segments:
                    add(R.F_MIXED_SEGMENT, d.区画)
                if not d.更新の期限:
                    add(R.F_NO_SUPPORT_END)
                else:
                    left = (date.fromisoformat(d.更新の期限) - today).days
                    if left < 0:
                        add(R.F_SUPPORT_ENDED, d.更新の期限)
                    elif near is not None and left <= near:
                        add(R.F_SUPPORT_NEAR, d.更新の期限)
                if not any(c.場面 == "据え付け" for c in checks):
                    add(R.F_NO_INSTALL_CHECK)
                now: dict[str, str] = {}
                for c in checks:
                    now.update(c.結果)
                for k in CHECK_ITEMS:
                    if now.get(k) == "悪い":
                        add(R.F_BAD_ITEM, k)
                if interval is not None:
                    seen = [c.日時 for c in checks if c.場面 in ("据え付け", "点検")]
                    last = max(seen, key=parse) if seen else ""
                    if not last:
                        add(R.F_CHECK_OVERDUE, "確かめなし")
                    elif at - parse(last) > timedelta(days=interval):
                        add(R.F_CHECK_OVERDUE, last)
                names: dict[str, set[str]] = {}
                for e in access[d.名前]:
                    names.setdefault(e.識別, set())
                    if self._row(e, at).状態 != "止まった":
                        names[e.識別].add(e.主体)
                for account, who in names.items():
                    if len(who) >= 2:
                        add(R.F_SHARED_ACCOUNT, account)
            if d.状態 == "廃棄" and not any(c.場面 == "廃棄" for c in checks):
                add(R.F_NO_DISPOSAL_CHECK)
            if d.状態 in STOP_STATES:
                left_ids = [e.番号 for e in access[d.名前] if self._row(e, at).状態 != "止まった"]
                if left_ids:
                    add(R.F_ACCESS_LEFT, ", ".join(left_ids))
        return out

    # ---- 業者と連携先に渡している物の目録(20.10) ----
    def vendor_inventory(self, *, at: datetime | None = None) -> list[Held]:
        """業者と連携先に渡している権限と鍵の目録(R-RS-04)。今の名簿で業者の主体の権限のうち、状態(at で読む)が
        発行済み の物か、実体に 止まった でない物が残る物を、権限_申請を台帳に書いた順に。扉、群、機器の権限のどれも入る。
        失効した後でも、管理者のインターフェースや物理キーが残っている間は載る。残りは、その実体の進み具合の行のうち 止まった でない物。"""
        at = local(at)
        subjects = self._subjects()
        by_perm: dict[str, list] = defaultdict(list)
        for e in self.entities():
            by_perm[e.権限].append(e)
        out: list[Held] = []
        for p in self.permissions(at=at):
            who = subjects.get(p.主体)
            if who is None or not who.業者:
                continue
            left = [r for r in (self._row(e, at) for e in by_perm[p.番号]) if r.状態 != "止まった"]
            if p.状態 == "発行済み" or left:
                out.append(Held(p.主体, p.番号, p.対象の種類, p.対象, p.開始, p.終了, p.状態, left))
        return out
