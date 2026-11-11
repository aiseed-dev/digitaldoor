# SPDX-License-Identifier: AGPL-3.0-or-later
"""申請、承認、発行、外の一覧の取り込み、権限の状態の導き方、経路の実体を出す(設計書 19.7、19.8、20.8)。
`Unit` のメソッドになる。

権限の番号は、権限_申請の伝票の番号です。出すのは人が確かめた後だけで(設計書 6 章の原則)、`issue` は承認の伝票の番号を
一つ目に取り、その承認と申請の値が渡した値と同じ時だけ書きます。外の一覧から作れるのは申請までです(R-EB-37)。
業者と連携先には、終了の無い権限と、設定 業者の最長の日数 を超える権限を出しません(R-AC-10)。機器の権限も同じです。
機器の権限(対象の種類 機器)は、その機器の管理者の口に入る権限で、経路 管理者の口 の実体だけを出せます。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable

from . import reasons as R
from .model import Entity, ImportResult, Issued, Permission, ended, form_time, local, parse
from .reasons import Refused
from .roster import split, yes
from .routes import ACCESS, CREDENTIAL, PHYSICAL, available

ROW_KEYS = ("識別", "対象の種類", "対象", "開始", "終了", "理由")


def _state(rev, dec, iss, at: datetime) -> str:
    """19.7 の表を上の行から当てはめる。"""
    if rev is not None:
        return "失効"
    if dec is not None and dec["判断"] == "却下":
        return "却下"
    if iss is not None and ended(iss["終了"], at):
        return "期限切れ"
    if iss is not None:
        return "発行済み"
    if dec is not None and dec["判断"] == "承認":
        return "承認"
    return "申請中"


class PermissionsMixin:
    """権限と実体のメソッド。"""

    # ---- 読む ----
    def _perm(self, pid: str, at: datetime) -> Permission | None:
        book = self.book()
        r = book.slip(pid, "権限_申請")
        if r is None:
            return None
        rev = book.first("権限_失効", "権限", pid)
        dec = book.first("権限_承認", "申請", pid)
        iss = book.first("権限_発行", "権限", pid)
        return Permission(pid, r["主体"], r["対象の種類"], r["対象"], r["開始"], r["終了"], r["理由"], r["出どころ"],
                          r.issuer, _state(rev, dec, iss, at), dec.id if dec else "", iss.id if iss else "",
                          split(iss["扉"]) if iss else [], rev.id if rev else "")

    def permission(self, pid: str, *, at: datetime | None = None) -> Permission:
        """権限の今の値と、at での状態。申請の伝票の番号でなければ PERMISSION_UNKNOWN。"""
        p = self._perm(pid, local(at))
        if p is None:
            raise Refused(R.PERMISSION_UNKNOWN)
        return p

    def permissions(self, subject: str | None = None, *, at: datetime | None = None) -> list[Permission]:
        """権限の一覧。申請を台帳に書いた順。subject で絞れる。"""
        at = local(at)
        slips = self.book().of("権限_申請")
        return [self._perm(s.id, at) for s in slips if subject is None or s["主体"] == subject]

    def _entities(self) -> dict[str, Entity]:
        book = self.book()
        if "entities" not in book.memo:
            out = {}
            for s in book.of("実体_発行"):
                rev = book.first("実体_失効", "実体", s.id)
                ret = book.first("物理キー_回収", "実体", s.id) or book.first("管理者の口_削除", "実体", s.id)
                out[s.id] = Entity(s.id, s["権限"], s["主体"], s["経路"], split(s["扉"]), s["識別"], s["発行者"],
                                   s["開始"], s["終了"], s["一回限り"] == "true", rev.id if rev else "",
                                   ret.id if ret else "", s["機器"])
            book.memo["entities"] = out
        return book.memo["entities"]

    def entities(self, subject: str | None = None) -> list[Entity]:
        """経路の実体の一覧。実体_発行を台帳に書いた順。subject で絞ると、一人の全経路の一覧になる(R-AC-15)。"""
        return [e for e in self._entities().values() if subject is None or e.主体 == subject]

    def _route(self, name: str):
        return self.routes.get(name) or available().get(name)

    def _revoked(self, pid: str) -> bool:
        return self.book().first("権限_失効", "権限", pid) is not None

    def reconcile(self, entity_id: str, *, at: datetime | None = None):
        """経路の「照らす」の結果(段 1 は扉の記録を受け取らないので、署名つき資格情報は 照らせない)。"""
        e = self._entities().get(entity_id)
        if e is None:
            raise Refused(R.ENTITY_UNKNOWN)
        return self._route(e.経路).reconcile(self, e, at=local(at))

    # ---- 申請 ----
    def _check_vendor(self, subject, start: str, end: str) -> None:
        if not subject.業者:
            return
        if not end:
            raise Refused(R.VENDOR_NO_END)
        days = self.setting("業者の最長の日数")
        if days and parse(end) - parse(start) > timedelta(days=int(days)):
            raise Refused(R.VENDOR_TOO_LONG)

    def _check_request(self, v: dict) -> None:
        """申請の確かめ(19.7、20.8): 主体、有効、扉か群か機器、期間、業者の順。"""
        who = self._subjects().get(v["主体"])
        if who is None:
            raise Refused(R.SUBJECT_UNKNOWN)
        if who.状態 != "有効":
            raise Refused(R.SUBJECT_INACTIVE)
        self._target_doors(v["対象の種類"], v["対象"])
        if v["終了"] and parse(v["終了"]) <= parse(v["開始"]):
            raise Refused(R.BAD_PERIOD)
        self._check_vendor(who, v["開始"], v["終了"])

    def _request_values(self, subject, kind, target, start, end, reason, role, source="") -> dict:
        return self._clean("権限_申請", {
            "主体": subject, "対象の種類": kind, "対象": target, "開始": form_time(start), "終了": form_time(end),
            "理由": reason, "出どころ": "外の一覧" if source else "手入力", "一覧の名": source, "役": role})

    def request(self, subject: str, kind: str, target: str, start, end, reason: str, *, by: str, role: str):
        """権限_申請を書く(本人、名簿の係)。kind は 扉、群、機器(その機器の管理者の口に入る権限)。end="" は期限なし。
        機器の権限の対象は、状態が 準備、使用、休止 の機器だけ(ほかは UNKNOWN_DEVICE)。
        本人の役では、自分の分も、受け入れる来客や業者の分も書ける。申請者は伝票の発行者 by として残る。"""
        self._begin({"本人", "名簿の係"}, by, role)
        v = self._request_values(subject, kind, target, start, end, reason, role)
        self._check_request(v)
        return self._write("権限_申請", v, by=by, role=role, to=v["主体"])

    def import_requests(self, rows: Iterable[dict[str, str]], source_name: str, *, by: str, role: str) -> ImportResult:
        """外の一覧(ディレクトリ、予約の仕組み、人事の届けの書き出し)の行から、権限_申請だけを書く(名簿の係、R-EB-37)。

        一行は {識別, 対象の種類, 対象, 開始, 終了, 理由}。行ごとに request と同じ確かめをし、外れた行は書かずに
        飛ばした に (行の番号, 理由) を足す。名簿に無い識別の行は 名簿にありません。人も承認も鍵も増やさない。"""
        self._begin({"名簿の係"}, by, role)
        if not str(source_name or "").strip():
            raise Refused(R.FORM, "一覧の名: 必須です")
        source = self._clean("権限_申請", {"一覧の名": source_name})["一覧の名"]
        written, skipped = [], []
        for n, row in enumerate(rows, 1):
            try:
                if any(row.get(k) is None for k in ROW_KEYS):
                    raise Refused(R.FORM)
                v = self._request_values(row["識別"], row["対象の種類"], row["対象"], row["開始"], row["終了"],
                                         row["理由"], role, source)
                self._check_request(v)
                written.append(self._write("権限_申請", v, by=by, role=role, to=v["主体"]))
            except Refused as e:
                skipped.append((n, e.理由))
        return ImportResult(written, skipped)

    # ---- 承認 ----
    def decide(self, request_id: str, decision: str, reason: str = "", *, by: str, role: str):
        """権限_承認を書く(承認する人)。decision は 承認 か 却下。一つの申請に判断は一枚。
        自分の申請と、自分が主体の申請は、設定 自己承認 が 許す の時だけ判断でき、その時は 自己承認 はい の印を書く。"""
        self._begin({"承認する人"}, by, role)
        v = self._clean("権限_承認", {"申請": request_id, "判断": decision, "理由": reason, "役": role})
        req = self.book().slip(v["申請"], "権限_申請")
        if req is None:
            raise Refused(R.NO_REQUEST)
        p = self._perm(req.id, local(None))
        if p.状態 == "失効":
            raise Refused(R.REVOKED)
        if p.承認:
            raise Refused(R.ALREADY_DECIDED)
        if self.setting("自己承認") != "許す":
            if by == req.issuer:
                raise Refused(R.SELF_REQUEST)
            if by == req["主体"]:
                raise Refused(R.SELF_SUBJECT)
        v["自己承認"] = "はい" if by in (req.issuer, req["主体"]) else "いいえ"
        return self._write("権限_承認", v, by=by, role=role, to=req["主体"], basis=[req.id])

    # ---- 発行 ----
    def _approval(self, approval_id: str):
        """承認の伝票と申請の伝票。その申請の最初の判断の伝票でなければ NO_APPROVAL。"""
        book = self.book()
        appr = book.slip(str(approval_id or ""), "権限_承認")
        req = book.slip(appr["申請"], "権限_申請") if appr is not None else None
        if req is None or book.first("権限_承認", "申請", req.id).id != appr.id:
            raise Refused(R.NO_APPROVAL)
        return appr, req

    def issue(self, approval_id: str, subject: str, kind: str, target: str, start, end, *, by: str, role: str,
              at: datetime | None = None):
        """権限_発行を書く(管理者。伝票の発行者は 発行者)。承認の伝票の番号を一つ目に取り、承認と申請の値
        (主体、対象の種類、対象、開始、終了)が渡した値と同じ時だけ書く。at は期限の確かめに使う時刻(None は今)。
        権限の扉の列は、この時に対象から作って伝票に書き、後で群を変えても変わらない。機器の権限の扉の列は空。"""
        self._begin({"管理者"}, by, role)
        v = self._clean("権限_発行", {"主体": subject, "対象の種類": kind, "対象": target,
                                     "開始": form_time(start), "終了": form_time(end)})
        at = local(at)
        appr, req = self._approval(approval_id)
        if appr["判断"] == "却下":
            raise Refused(R.REJECTED)
        if appr.issuer in (req.issuer, req["主体"]):
            if not (self.setting("自己承認") == "許す" and appr["自己承認"] == "はい"):
                raise Refused(R.SELF_REQUEST if appr.issuer == req.issuer else R.SELF_SUBJECT)
        for k in ("主体", "対象の種類", "対象", "開始", "終了"):
            if v[k] != req[k]:
                raise Refused(R.MISMATCH, k)
        p = self._perm(req.id, at)
        if p.状態 == "失効":
            raise Refused(R.REVOKED)
        if p.発行:
            raise Refused(R.ALREADY_ISSUED)
        who = self._subjects().get(v["主体"])
        if who is None:
            raise Refused(R.SUBJECT_UNKNOWN)
        if who.状態 != "有効":
            raise Refused(R.SUBJECT_INACTIVE)
        self._check_vendor(who, v["開始"], v["終了"])
        doors = self._target_doors(v["対象の種類"], v["対象"])
        if ended(v["終了"], at):
            raise Refused(R.EXPIRED)
        values = {"権限": req.id, "承認": appr.id, **v, "扉": ", ".join(doors), "操作者": by, "役": role}
        return self._write("権限_発行", values, by=by, role=role, to=v["主体"], basis=[req.id, appr.id],
                           machine=True)

    # ---- 経路の実体を出す ----
    def issue_entity(self, pid: str, route: str, *, by: str, role: str, until=None, once: bool = False,
                     key_number: str = "", account: str = "", at: datetime | None = None) -> Issued:
        """発行済みの権限から、一つの経路の実体を出して実体_発行を書く(管理者。物理キーは名簿の係も)。

        扉は、権限の扉の列のうち今使用で、その経路を持つ扉に絞る。until は実体の終了(権限の終了より後にはできない)。
        once(一回限り)は署名つき資格情報だけ。物理キーは key_number(鍵の番号)が要る。
        経路 管理者の口 は機器の権限だけで(扉の権限は DEVICE_ONLY)、account(口の名。アカウントの名や鍵の名で、
        秘密の値ではない)が要る(無ければ NEED_ACCOUNT)。管理者の口では key_number を使わず、ほかの経路では account を
        使わない。同じ機器と同じ口の名の実体を何人分でも出せる(知らせ 一つの口を複数の人が使っています に挙がる)。
        署名つき資格情報そのものは台帳に書かず、戻り値の 資格情報 で返すだけ。"""
        r = self.routes.get(route)
        if r is None:
            raise Refused(R.UNKNOWN_ROUTE, route)
        self._begin({"管理者", "名簿の係"} if route == PHYSICAL else {"管理者"}, by, role)
        given = {"権限": pid, "終了": form_time(until), "一回限り": yes(once)}
        ident = account if route == ACCESS else key_number
        if ident:
            given["識別"] = ident
        v = self._clean("実体_発行", given)
        at = local(at)
        p = self._perm(v["権限"], at)
        if p is None:
            raise Refused(R.PERMISSION_UNKNOWN)
        if p.状態 == "失効":
            raise Refused(R.REVOKED)
        if p.状態 in ("申請中", "承認", "却下"):
            raise Refused(R.NOT_ISSUED)
        if p.状態 == "期限切れ":
            raise Refused(R.EXPIRED)
        who = self._subjects().get(p.主体)
        if who is None or who.状態 != "有効":
            raise Refused(R.SUBJECT_INACTIVE)
        device = ""
        if route == ACCESS:
            if p.対象の種類 != "機器":
                raise Refused(R.DEVICE_ONLY)
            device = self._live_device(p.対象).名前
            use: list[str] = []
        else:
            doors = self._doors()
            use = [d for d in p.扉 if d in doors and doors[d].状態 == "使用" and route in doors[d].経路]
            if not use:
                raise Refused(R.NO_DOOR_FOR_ROUTE)
        if once and route != CREDENTIAL:
            raise Refused(R.ONCE_CREDENTIAL_ONLY)
        until = v["終了"]
        if until and p.終了 and parse(until) > parse(p.終了):
            raise Refused(R.UNTIL_TOO_LATE)
        end = until or p.終了
        if end and parse(end) <= parse(p.開始):
            raise Refused(R.BAD_PERIOD)
        got = r.issue(self, p, doors=use, start=p.開始, end=end, once=bool(once), key_number=v.get("識別", ""))
        slip = self._write("実体_発行", {
            "権限": p.番号, "主体": p.主体, "経路": route, "扉": ", ".join(use), "機器": device, "識別": got.識別,
            "発行者": got.発行者,
            "開始": p.開始, "終了": end, "一回限り": yes(once), "操作者": by, "役": role},
            by=by, role=role, to=p.主体, basis=[p.発行], machine=True)
        return Issued(slip, self._entities()[slip.id], got.資格情報)
