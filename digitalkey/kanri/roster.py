# SPDX-License-Identifier: AGPL-3.0-or-later
"""名簿、役の割り当て、扉と群、設定(設計書 19.6、20.2、21.9)。`Unit` のメソッドになる。

名簿がこの台帳の正です(R-EB-37)。ディレクトリから人を足さず、名簿の係が一人ずつ載せます。
扉と群は、この発行者が管理する物だけを書きます(R-AC-09)。設定は管理者が台帳に書く値で、ソフトは値を持ちません。
"""
from __future__ import annotations

import re
import secrets

from . import reasons as R
from .model import Door, Group, Subject
from .reasons import Refused
from .routes import ACCESS

ROSTER = {"名簿の係"}
ADMIN = {"管理者"}
SETTINGS = ("業者の最長の日数", "自己承認", "点検の間の日数", "更新の期限の通知の日数")
DAYS = ("業者の最長の日数", "点検の間の日数", "更新の期限の通知の日数")   # 値が空か 1 以上の整数の項目


def split(v: str) -> list[str]:
    """「, 」でつないだ列を list に。"""
    return [x.strip() for x in v.split(",") if x.strip()]


def yes(v: bool) -> str:
    """bool を様式の boolean の値に。"""
    return "true" if v else "false"


def _subjects(book) -> dict[str, Subject]:
    """主体_登録を書いた順に並べ、主体_変更の空でない値を項目ごとに重ね、役_割当の最後の判断で持つ役を決める。"""
    rows: dict[str, dict] = {}
    for s in book.of("主体_登録"):
        rows.setdefault(s["識別"], {"呼び名": s["呼び名"], "種別": s["種別"], "業者": s["業者"],
                                    "状態": "有効", "仮名": s["仮名"]})
    for s in book.of("主体_変更"):
        if (r := rows.get(s["識別"])) is not None:
            r.update({k: s[k] for k in ("呼び名", "種別", "業者", "状態") if s[k]})
    granted: dict[tuple[str, str], bool] = {}
    for s in book.of("役_割当"):
        granted[(s["主体"], s["割り当てる役"])] = s["判断"] == "付与"
    out = {}
    for ident, r in rows.items():
        held = {role for (who, role), on in granted.items() if who == ident and on}
        if r["状態"] == "有効":
            held.add("本人")
        out[ident] = Subject(ident, r["呼び名"], r["種別"], r["業者"] == "true", r["状態"], r["仮名"], held)
    return out


class RosterMixin:
    """名簿、役、扉と群、設定のメソッド。"""

    # ---- 読む(役を取らない) ----
    def _subjects(self) -> dict[str, Subject]:
        book = self.book()
        if "subjects" not in book.memo:
            book.memo["subjects"] = _subjects(book)
        return book.memo["subjects"]

    def _doors(self) -> dict[str, Door]:
        """扉の名前 → 今の値。並びは最初の伝票を書いた順。"""
        out: dict[str, Door] = {}
        for s in self.book().of("扉"):
            m = s["失効の最長の分数"]
            out[s["名前"]] = Door(s["名前"], split(s["経路"]), s["場所"], s["記録の公開鍵"], s["状態"],
                                 int(m) if m else None)
        return out

    def _groups(self) -> dict[str, Group]:
        out: dict[str, Group] = {}
        for s in self.book().of("群"):
            out[s["名前"]] = Group(s["名前"], split(s["扉"]), s["状態"])
        return out

    def subject(self, ident: str) -> Subject:
        """名簿の主体の今の値。無ければ SUBJECT_UNKNOWN。"""
        who = self._subjects().get(ident)
        if who is None:
            raise Refused(R.SUBJECT_UNKNOWN)
        return who

    def subjects(self) -> list[Subject]:
        """名簿の全員。主体_登録を台帳に書いた順。"""
        return list(self._subjects().values())

    def doors(self) -> list[Door]:
        """扉の今の値。撤去した扉も入る。最初の伝票を台帳に書いた順。"""
        return list(self._doors().values())

    def groups(self) -> list[Group]:
        """群の今の値。最初の伝票を台帳に書いた順。"""
        return list(self._groups().values())

    def setting(self, item: str) -> str:
        """設定の今の値(最後の設定の伝票)。書いていなければ空。自己承認だけは 許さない。"""
        found = self.book().where("設定", "項目", item)
        if found:
            return found[-1]["値"]
        return "許さない" if item == "自己承認" else ""

    def trust_entry(self) -> dict:
        """扉コントローラの信頼の一覧に入れる形 {発行者: {public_key, doors}}。doors は状態が使用の扉(R-AC-09)。"""
        doors = [d.名前 for d in self.doors() if d.状態 == "使用"]
        return {self.issuer: {"public_key": self.public_key.hex(), "doors": doors}}

    def _target_doors(self, kind: str, target: str) -> list[str]:
        """権限の対象から、今使用の扉の列を作る。扉が使用でなければ UNKNOWN_DOOR、群が使用でなければ UNKNOWN_GROUP。
        機器の権限は、対象が準備、使用、休止の機器でなければ UNKNOWN_DEVICE で、扉の列は空(20.8)。"""
        if kind == "機器":
            self._live_device(target)
            return []
        doors = self._doors()
        if kind == "扉":
            if target not in doors or doors[target].状態 != "使用":
                raise Refused(R.UNKNOWN_DOOR, target)
            return [target]
        group = self._groups().get(target)
        if group is None or group.状態 != "使用":
            raise Refused(R.UNKNOWN_GROUP)
        live = [d for d in group.扉 if d in doors and doors[d].状態 == "使用"]
        if not live:
            raise Refused(R.UNKNOWN_DOOR, target)
        return live

    # ---- 書く ----
    def register_subject(self, ident: str, name: str, kind: str, vendor: bool, *, by: str, role: str):
        """主体_登録を書き、鍵の要る人かシステムを一人、名簿に載せる(名簿の係)。仮名はソフトが作る(16 桁の 16 進)。
        識別に「,」は NAME_HAS_COMMA、同じ識別は DUPLICATE_SUBJECT。"""
        self._begin(ROSTER, by, role)
        v = self._clean("主体_登録", {"識別": ident, "呼び名": name, "種別": kind, "業者": yes(vendor), "役": role})
        if "," in v["識別"]:
            raise Refused(R.NAME_HAS_COMMA)
        known = self._subjects()
        if v["識別"] in known:
            raise Refused(R.DUPLICATE_SUBJECT)
        used = {s.仮名 for s in known.values()}
        while (pseudonym := secrets.token_hex(8)) in used:
            pass
        return self._write("主体_登録", {**v, "仮名": pseudonym}, by=by, role=role, to=v["識別"])

    def change_subject(self, ident: str, *, reason: str, by: str, role: str, name: str | None = None,
                       kind: str | None = None, vendor: bool | None = None, state: str | None = None):
        """主体_変更を書く(名簿の係)。渡した項目だけが変わる。state は 有効 / 停止 / 退出。
        停止と退出の主体には、新しい申請と発行を出さない。権限を止めるのは revoke_subject。"""
        self._begin(ROSTER, by, role)
        changes = {"呼び名": name, "種別": kind, "業者": None if vendor is None else yes(vendor), "状態": state}
        v = self._clean("主体_変更", {"識別": ident, **{k: x or "" for k, x in changes.items()},
                                     "理由": reason, "役": role})
        if v["識別"] not in self._subjects():
            raise Refused(R.SUBJECT_UNKNOWN)
        if not any(v[k] for k in changes):
            raise Refused(R.NOTHING_TO_CHANGE)
        return self._write("主体_変更", v, by=by, role=role, to=v["識別"])

    def assign_role(self, subject: str, role_name: str, grant: bool = True, *, reason: str, by: str, role: str):
        """役_割当を書く(管理者)。grant=False は判断 解除。割り当てられるのは 管理者、名簿の係、承認する人、運用、監査。"""
        self._begin(ADMIN, by, role)
        v = self._clean("役_割当", {"主体": subject, "割り当てる役": role_name, "判断": "付与" if grant else "解除",
                                   "理由": reason, "役": role})
        if v["主体"] not in self._subjects():
            raise Refused(R.SUBJECT_UNKNOWN)
        return self._write("役_割当", v, by=by, role=role, to=v["主体"])

    def register_door(self, name: str, routes: list[str], *, by: str, role: str, place: str = "",
                      audit_key: str = "", state: str = "使用", revocation_max_min: int | None = None):
        """扉を書く(管理者)。名前は扉のコントローラに入れた名前と同じにする。routes は unit.routes にある経路の名で、
        扉を開ける経路だけ(管理者のインターフェースは機器の権限だけの経路なので UNKNOWN_ROUTE)。
        同じ名前の二枚目が今の値になる。state=撤去 の扉には、新しい申請と発行を出さない。

        revocation_max_min は、その扉に据付で入れた失効の通知の古さ(digitaldoor-controller の
        --revocation-max-age の秒)を分で書いた値(R-AC-14)。None は空(扉はその規則を使っていない)で、二枚目で省くと
        空に戻る。1 より小さい値と整数でない値は FORM。発行の時には拒まず、進み具合の「効くまで」の見立てと、
        出す時の表示に使う。"""
        self._begin(ADMIN, by, role)
        v = self._clean("扉", {"名前": name, "経路": ", ".join(routes), "場所": place, "記録の公開鍵": audit_key,
                              "状態": state,
                              "失効の最長の分数": "" if revocation_max_min is None else str(revocation_max_min),
                              "役": role})
        if "," in v["名前"]:
            raise Refused(R.NAME_HAS_COMMA)
        for r in routes:
            if r == ACCESS or r not in self.routes:
                raise Refused(R.UNKNOWN_ROUTE, r)
        return self._write("扉", v, by=by, role=role)

    def register_group(self, name: str, doors: list[str], *, by: str, role: str, state: str = "使用"):
        """群を書く(管理者)。入れる扉は、この置き場の使用の扉だけ(外れたら UNKNOWN_DOOR、詳細に扉の名)。"""
        self._begin(ADMIN, by, role)
        v = self._clean("群", {"名前": name, "扉": ", ".join(doors), "状態": state, "役": role})
        if "," in v["名前"]:
            raise Refused(R.NAME_HAS_COMMA)
        live = self._doors()
        for d in doors:
            if d not in live or live[d].状態 != "使用":
                raise Refused(R.UNKNOWN_DOOR, d)
        return self._write("群", v, by=by, role=role)

    def set_setting(self, item: str, value: str, *, reason: str, by: str, role: str):
        """設定を書く(管理者)。業者の最長の日数、点検の間の日数、更新の期限の通知の日数 は空か 1 以上の整数、
        自己承認 は 許す か 許さない。ほかは BAD_SETTING。変えた人、日時、理由は伝票に残る。
        点検の間の日数 と 更新の期限の通知の日数 を書いていない間(値が空の時を含む)は、その通知を出さない。"""
        self._begin(ADMIN, by, role)
        v = self._clean("設定", {"項目": item, "値": value, "理由": reason, "役": role})
        if v["項目"] in DAYS:
            ok = v["値"] == "" or (re.fullmatch(r"[0-9]+", v["値"]) is not None and int(v["値"]) >= 1)
        else:
            ok = v["値"] in ("許す", "許さない")
        if not ok:
            raise Refused(R.BAD_SETTING)
        return self._write("設定", v, by=by, role=role)
