# SPDX-License-Identifier: AGPL-3.0-or-later
"""失効、進み具合の一枚、物理キーの回収、失効の確認(設計書 9 章、19.10、20.8、21.6、21.8)。`Unit` のメソッドになる。

`revoke_subject` は一度の呼び出しで、一人の全経路を止めます(R-AC-15)。
1 台帳で止める(開いた権限に権限_失効)、2 経路ごとに止める(止まっていない実体に実体_失効)、
2 の後に、署名つき資格情報の実体に実体_失効を書いたら、同じ呼び出しの中で失効の一覧(失効_一覧)を一枚作る、
3 進み具合を一枚に出す。
止めるのは承認なしで済み、二度目の呼び出しは伝票を増やしません。扉の鍵と機器の管理者の口は、同じ一枚に並びます。
物理キーは回収の伝票(物理キー_回収)、管理者の口は削除の伝票(管理者の口_削除)が出るまで 人の作業が要る です。
署名つき資格情報は、一覧が実体の扉の全部に届いた記録(失効_配布)がそろうか、扉の最長の分数から読む時刻を過ぎるか、
終了を過ぎると 止まった です(21.8)。
"""
from __future__ import annotations

from datetime import datetime

from . import reasons as R
from .model import FORMAT, Revocation, Row, Sheet, Stop, ended, local
from .reasons import Refused
from .routes import CREDENTIAL

STOPPERS = {"管理者", "名簿の係"}
OPEN = ("申請中", "承認", "発行済み")


class RevokeMixin:
    """失効と進み具合のメソッド。"""

    def _row(self, e, at: datetime) -> Row:
        """19.10、20.8 の進み具合の表。回収か削除の伝票があれば 止まった。署名つき資格情報は 21.8 の五つの順で読む。
        ほかの経路で、止める理由(実体_失効、権限の失効、終了を過ぎた)がある実体は、経路の「止める」で状態を読む。"""
        if e.回収:
            s = Stop("止まった", "", "")
        elif e.経路 == CREDENTIAL:
            s = self._credential_row(e, at)
        elif e.失効 or self._revoked(e.権限) or ended(e.終了, at):
            route = self._route(e.経路)
            s = route.stop(self, e, at=at) if route is not None else Stop("人の作業が要る", "", "")
        else:
            s = Stop("効いている", "", "")
        return Row(e.番号, e.経路, e.識別, e.扉, s.結果, s.効くまで if s.結果 == "待ち" else "",
                   s.作業 if s.結果 == "人の作業が要る" else "", e.機器)

    def progress(self, subject: str, *, at: datetime | None = None) -> Sheet:
        """一人の全経路の進み具合の一枚。行は実体_発行を台帳に書いた順。名簿に無い主体は SUBJECT_UNKNOWN。"""
        if subject not in self._subjects():
            raise Refused(R.SUBJECT_UNKNOWN)
        at = local(at)
        rows = [self._row(e, at) for e in self.entities(subject)]
        return Sheet(subject, at.strftime(FORMAT), rows, all(r.状態 == "止まった" for r in rows))

    def _revoke(self, subject, perms, entities, scope, reason, by, role, at) -> Revocation:
        written, stopped = self._stop_all(perms, entities, scope, reason, by, role, at)
        listed = self._list_after_stop(stopped, by, role, at)
        return Revocation(written, stopped, self.progress(subject, at=at), listed)

    def _stop_all(self, perms, entities, scope, reason, by, role, at) -> tuple[list, list]:
        """失効の 1 と 2。開いた権限に権限_失効、止まっていない実体に実体_失効を書き、書いた伝票の二つの列を返す。
        実体の条件は、実体_失効がまだ無く、終了を過ぎておらず、回収か削除の伝票が無い物。"""
        written: dict[str, object] = {}
        for p in perms:
            if p.状態 in OPEN:
                written[p.番号] = self._write("権限_失効", {"権限": p.番号, "主体": p.主体, "範囲": scope,
                                                          "理由": reason, "役": role},
                                              by=by, role=role, to=p.主体, basis=[p.番号])
        stopped = []
        for e in entities:
            route = self._route(e.経路)
            if e.失効 or e.回収 or ended(e.終了, at) or route is None:
                continue
            s = route.stop(self, e, at=at)
            basis = [e.番号] + ([written[e.権限].id] if e.権限 in written else [])
            stopped.append(self._write("実体_失効", {
                "実体": e.番号, "主体": e.主体, "経路": e.経路, "結果": s.結果, "効くまで": s.効くまで, "作業": s.作業,
                "理由": reason, "操作者": by, "役": role}, by=by, role=role, to=e.主体, basis=basis, machine=True))
        return list(written.values()), stopped

    def revoke_subject(self, subject: str, reason: str, *, by: str, role: str,
                       at: datetime | None = None) -> Revocation:
        """一人の全経路を止める(管理者、名簿の係)。停止、退出の主体も止められる。承認の伝票は要らない。

        申請中、承認、発行済みの権限の全部に権限_失効(範囲 一人の全経路)を書き、実体_失効がまだ無く、終了を過ぎて
        おらず、回収されていない実体の全部に、経路の「止める」を at で呼んで実体_失効を書く。署名つき資格情報の実体に
        実体_失効を書いたら、同じ at で失効の一覧を一枚作り(失効_一覧。Revocation.失効_一覧)、進み具合を添えて返す。
        一覧を扉へ運ぶのは別の操作(revocation で読み、record_delivery で届いた事を書く)。"""
        self._begin(STOPPERS, by, role)
        v = self._clean("権限_失効", {"主体": subject, "理由": reason, "役": role})
        if v["主体"] not in self._subjects():
            raise Refused(R.SUBJECT_UNKNOWN)
        at = local(at)
        return self._revoke(v["主体"], self.permissions(v["主体"], at=at), self.entities(v["主体"]), "一人の全経路",
                            v["理由"], by, role, at)

    def revoke_permission(self, pid: str, reason: str, *, by: str, role: str,
                          at: datetime | None = None) -> Revocation:
        """一つの権限を止める(管理者、名簿の係。範囲 一つの権限)。却下、期限切れ、失効の権限には権限_失効を書かず、
        その権限の実体のうち止まっていない物だけを止める。署名つき資格情報を止めたら失効の一覧を一枚作る。
        進み具合はその権限の主体の物。"""
        self._begin(STOPPERS, by, role)
        v = self._clean("権限_失効", {"権限": pid, "理由": reason, "役": role})
        at = local(at)
        p = self._perm(v["権限"], at)
        if p is None:
            raise Refused(R.PERMISSION_UNKNOWN)
        mine = [e for e in self.entities(p.主体) if e.権限 == p.番号]
        return self._revoke(p.主体, [p], mine, "一つの権限", v["理由"], by, role, at)

    def return_physical(self, entity_id: str, result: str = "回収", note: str = "", *, by: str, role: str):
        """物理キー_回収を書く(名簿の係、管理者)。result は 回収 か 錠を交換、note は備考。
        回収の後、その行は 止まった。失効の前の返却にも使う(R-AC-15「機械の鍵は回収の記録を残す」)。
        管理者の口の実体は NOT_PHYSICAL(削除は remove_access)。"""
        self._begin({"名簿の係", "管理者"}, by, role)
        v = self._clean("物理キー_回収", {"実体": entity_id, "結果": result, "備考": note, "役": role})
        e = self._entities().get(v["実体"])
        if e is None:
            raise Refused(R.ENTITY_UNKNOWN)
        if e.経路 != "物理キー":
            raise Refused(R.NOT_PHYSICAL)
        if e.回収:
            raise Refused(R.ALREADY_RETURNED)
        return self._write("物理キー_回収", {**v, "鍵番号": e.識別}, by=by, role=role, to=e.主体, basis=[e.番号])

    def confirm_stopped(self, subject: str, *, by: str, role: str, at: datetime | None = None):
        """失効_確認を書く(管理者。設計書 9 章の 4)。at の進み具合で全部の実体が止まった時だけ書ける。
        止まっていなければ NOT_ALL_STOPPED(詳細に止まっていない実体の番号)。"""
        self._begin({"管理者"}, by, role)
        v = self._clean("失効_確認", {"主体": subject, "役": role})
        if v["主体"] not in self._subjects():
            raise Refused(R.SUBJECT_UNKNOWN)
        sheet = self.progress(v["主体"], at=at)
        if not sheet.済み:
            raise Refused(R.NOT_ALL_STOPPED, ", ".join(r.実体 for r in sheet.行 if r.状態 != "止まった"))
        ids = [r.実体 for r in sheet.行]
        return self._write("失効_確認", {**v, "実体": ", ".join(ids) or "なし"}, by=by, role=role, to=v["主体"],
                           basis=ids)
