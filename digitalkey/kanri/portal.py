# SPDX-License-Identifier: AGPL-3.0-or-later
"""窓口の照会(設計書 22 章、段 5a)。名簿の主体と窓口の番号を結ぶ・外す、今の結び、照会用の写し。`Unit` のメソッドになる。

窓口の番号は、窓口の頁(お問い合わせと照会)にサインインした本人に出る番号(PocketBase の users のレコードの id)です。
本人が名簿の係に伝え、名簿の係が本人を対面かビデオ通話で確かめてから、様式 主体_結び で結びます。窓口から台帳へは
何も入らず、窓口の登録で主体は増えません(R-EB-37)。メールアドレスは台帳に載せず、人と番号の対応は名簿だけが持ちます
(R-LG-07)。

照会用の写しは、そのつど台帳から導いた本人が見てよい物だけの列です(権限の番号、対象、期間、状態と、鍵の経路、扉か機器、
状態、効くまで)。呼び名、仮名、理由、作業の文、申請者、操作者は入れず、署名つき資格情報と門番を通す錠の識別は先頭の
4 文字に「…」を付けた形で出します。写しはファイルに出すところまでで、このソフトは網を使いません(R-AC-19)。開く所へ
置きに行くのは別の道具 `digitalkey madoguchi push` です(窓口仕様書 8 章)。写しは写しで、正は台帳、扉は写しを見ずに
自分で確かめます。
"""
from __future__ import annotations

import re
from datetime import datetime

from . import reasons as R
from .model import FORMAT, Link, local
from .reasons import Refused
from .routes import ACCESS, PHYSICAL

ROSTER = {"名簿の係"}
SHEET_FORMAT = "digitalkey-shoukai/1"
ELLIPSIS = "…"
SHOWN = ("有効", "停止")            # 写しに出す主体の状態。退出は出さない
WHOLE = (PHYSICAL, ACCESS)         # 識別をそのまま出す経路(鍵の番号、口の名)
ACCOUNT_RE = re.compile(r"[A-Za-z0-9]+")


def shown_id(route: str, ident: str) -> str:
    """写しに出す識別。物理キーの鍵の番号と管理者の口の名はそのまま、ほかの経路は先頭 4 文字に「…」(22.7)。"""
    return ident if route in WHOLE else ident[:4] + ELLIPSIS


class PortalMixin:
    """窓口の結びと照会用の写しのメソッド。"""

    # ---- 読む(役を取らない) ----
    def _links(self) -> dict[str, Link]:
        """主体 → 今の結び。並びは 主体_登録 を台帳に書いた順。主体ごとの最後の 主体_結び が 結ぶ の物だけ。"""
        book = self.book()
        if "links" not in book.memo:
            out: dict[str, Link] = {}
            for ident in self._subjects():
                slips = book.where("主体_結び", "主体", ident)
                if slips and slips[-1]["状態"] == "結ぶ":
                    last = slips[-1]
                    out[ident] = Link(ident, last["窓口の番号"], last["確かめ方"], last.id)
            book.memo["links"] = out
        return book.memo["links"]

    def portal_account(self, subject: str) -> str:
        """その主体の今の窓口の番号。結んでいなければ空。名簿に無い主体は SUBJECT_UNKNOWN。"""
        if subject not in self._subjects():
            raise Refused(R.SUBJECT_UNKNOWN)
        link = self._links().get(subject)
        return link.窓口の番号 if link is not None else ""

    def portal_links(self) -> list[Link]:
        """今の結びの全部。主体の状態を問わない(停止、退出も入る)。主体_登録 を台帳に書いた順。"""
        return list(self._links().values())

    def portal_export(self, *, at: datetime | None = None) -> list[dict]:
        """照会用の写しの列(22.7)。結びのある主体のうち、状態が 有効 か 停止 の物を、主体_登録 を書いた順に。
        一人分は {"owner", "issuer", "made_at", "sheet"}。at は 19.5 の読み方(None は今。時差つきは地方時に直す)で、
        権限と鍵の状態はこの at で読む。台帳に何も書かない。"""
        at = local(at)
        when = at.strftime(FORMAT)
        subjects = self._subjects()
        entities = self._entities()
        out = []
        for link in self._links().values():
            if subjects[link.主体].状態 not in SHOWN:
                continue
            perms = [{"番号": p.番号, "対象の種類": p.対象の種類, "対象": p.対象, "開始": p.開始, "終了": p.終了 or "",
                      "状態": p.状態} for p in self.permissions(link.主体, at=at)]
            keys = [{"権限": entities[r.実体].権限, "経路": r.経路, "扉": list(r.扉), "機器": r.機器,
                     "識別": shown_id(r.経路, r.識別), "状態": r.状態, "効くまで": r.効くまで}
                    for r in self.progress(link.主体, at=at).行]
            out.append({"owner": link.窓口の番号, "issuer": self.issuer, "made_at": when,
                        "sheet": {"形式": SHEET_FORMAT, "時刻": when, "権限": perms, "鍵": keys}})
        return out

    # ---- 書く(名簿の係) ----
    def link_portal(self, subject: str, account: str, how: str, *, by: str, role: str):
        """主体_結び(状態 結ぶ)を書き、名簿の主体に窓口の番号を結ぶ(名簿の係)。how は本人を確かめた方法
        (対面 か ビデオ通話)。確かめ方が空、番号が英数字だけでない時は FORM。主体が名簿に無ければ SUBJECT_UNKNOWN、
        有効でなければ SUBJECT_INACTIVE。ほかの主体に結ばれている番号は ACCOUNT_LINKED(詳細にその主体の識別。
        退出の主体に結ばれたままの番号も拒むので、先に外す)。同じ主体に結び直すと、新しい一枚が今の結びになる。"""
        self._begin(ROSTER, by, role)
        v = self._clean("主体_結び", {"主体": subject, "窓口の番号": account, "状態": "結ぶ", "確かめ方": how,
                                     "役": role})
        if not v["確かめ方"]:
            raise Refused(R.FORM, "確かめ方: 必須です")
        if not ACCOUNT_RE.fullmatch(v["窓口の番号"]):
            raise Refused(R.FORM, "窓口の番号: 英数字だけで書いてください")
        who = self._subjects().get(v["主体"])
        if who is None:
            raise Refused(R.SUBJECT_UNKNOWN)
        if who.状態 != "有効":
            raise Refused(R.SUBJECT_INACTIVE)
        for other in self._links().values():
            if other.主体 != v["主体"] and other.窓口の番号 == v["窓口の番号"]:
                raise Refused(R.ACCOUNT_LINKED, other.主体)
        return self._write("主体_結び", {**v, "理由": ""}, by=by, role=role, to=v["主体"])

    def unlink_portal(self, subject: str, *, reason: str, by: str, role: str):
        """主体_結び(状態 外す)を書き、今の結びを外す(名簿の係)。外す伝票にも外した番号を書く。主体の状態は問わない
        (停止、退出の主体も外せる)。名簿に無い主体は SUBJECT_UNKNOWN、結びが無ければ NOT_LINKED、理由が空なら
        NEED_REASON。"""
        self._begin(ROSTER, by, role)
        v = self._clean("主体_結び", {"主体": subject, "理由": reason, "役": role})
        if v["主体"] not in self._subjects():
            raise Refused(R.SUBJECT_UNKNOWN)
        link = self._links().get(v["主体"])
        if link is None:
            raise Refused(R.NOT_LINKED)
        if not v["理由"]:
            raise Refused(R.NEED_REASON)
        return self._write("主体_結び", {**v, "窓口の番号": link.窓口の番号, "状態": "外す", "確かめ方": ""},
                           by=by, role=role, to=v["主体"])
