# SPDX-License-Identifier: AGPL-3.0-or-later
"""失効の一覧、一覧を扉へ配った記録、署名つき資格情報の進み具合(設計書 21 章、段 2a)。`Unit` のメソッドになる。

失効の一覧は、発行者の鍵で署名した一覧(扉コントローラ仕様書 5.2c の digitaldoor-revocation/1)です。止めてあって
期限を過ぎていない署名つき資格情報の識別の全部を入れ、世代は一枚ごとに 1 ずつ上がり、発行時刻は世代の順に必ず増えます。
一人の全経路を止める操作(revoke_subject、revoke_permission、change_device)は、署名つき資格情報の実体を止めた時、
同じ呼び出しの中で一覧を一枚作ります(R-AC-15)。ネットワークの届かない扉へは、人や利用者の端末が一覧を運びます(R-AC-22)。
このソフトはネットワークを使いません(R-AC-19)。一覧はファイルに出し、扉が受けた事は人が確かめて 失効_配布 に書きます。

台帳に書くのは一覧の中身(世代、発行時刻、識別)だけで、署名は持ちません。署名つきの一覧は、そのつど発行者の鍵で
組み立て直します(Ed25519 の署名は、同じ鍵と同じ入力で同じ値になります)。

署名つき資格情報の進み具合は、扉に届いた記録と、扉ごとの「失効が効くまでの最長の時間」(扉の 失効の最長の分数。
R-AC-14)から読みます。扉は、その発行者の一覧を最後に受けてから、その一覧の issued_at から M 分を過ぎると、長さが
M 分より長い資格情報を「失効の通知が古い」で拒みます。そのため、届いた記録が無くても、遅くとも
「その識別を最初に入れた一覧の発行時刻 + M 分」には扉で効きます。この見立ては、扉の設定が台帳の値と同じである前提の
物です(照らし合わせは段 3)。
"""
from __future__ import annotations

from datetime import datetime

from digitaldoor_controller.credentials import revocation_list

from . import reasons as R
from .model import Published, RevList, Stop, ended, form_time, local, minute_up, unix
from .reasons import Refused
from .roster import split
from .routes import CREDENTIAL

PUBLISHERS = {"管理者", "運用"}


class RevocationsMixin:
    """失効の一覧と、配った記録のメソッド。"""

    # ---- 読む(役を取らない) ----
    def _revlists(self) -> list[RevList]:
        book = self.book()
        if "revlists" not in book.memo:
            book.memo["revlists"] = [RevList(s.id, int(s["世代"]), int(s["発行時刻"]), split(s["識別"]))
                                     for s in book.of("失効_一覧")]
        return book.memo["revlists"]

    def _first_list(self) -> dict[str, RevList]:
        """識別 → その識別を入れた 失効_一覧 のうち、台帳に書いた順で最初の一枚(21.8 の g0 と t0)。"""
        book = self.book()
        if "first_list" not in book.memo:
            out: dict[str, RevList] = {}
            for r in self._revlists():
                for ident in r.識別:
                    out.setdefault(ident, r)
            book.memo["first_list"] = out
        return book.memo["first_list"]

    def revocations(self) -> list[RevList]:
        """失効_一覧の全部を、台帳に書いた順に。"""
        return list(self._revlists())

    def revocation(self, generation: int | None = None) -> dict:
        """その世代の署名つきの一覧 {"body", "sig"} を、台帳の値から発行者の鍵で組み立て直す。None は台帳に書いた順で
        最後の一枚。一覧が一枚も無ければ NO_REVOCATION_LIST、その世代が無ければ UNKNOWN_GENERATION(詳細に世代の数)。"""
        lists = self._revlists()
        if not lists:
            raise Refused(R.NO_REVOCATION_LIST)
        if generation is None:
            r = lists[-1]
        else:
            r = next((x for x in lists if x.世代 == generation), None)
            if r is None:
                raise Refused(R.UNKNOWN_GENERATION, str(generation))
        return self._signed(r.世代, r.発行時刻, r.識別)

    def door_generation(self, door: str) -> int:
        """その扉に届いたと記録された一番大きい世代。無ければ 0。台帳に無い扉、撤去した扉にも、記録から読む。"""
        return max((int(s["世代"]) for s in self.book().where("失効_配布", "扉", door)), default=0)

    # ---- 一覧を作る ----
    def _signed(self, generation: int, issued_at: int, ids: list[str]) -> dict:
        return revocation_list(self.signer, generation=generation, issued_at=issued_at, revoked=ids)

    def _stopped(self, e) -> bool:
        """止めてある: 実体_失効があるか、権限の状態が 失効。"""
        return bool(e.失効) or self._revoked(e.権限)

    def _publish(self, by: str, role: str, at: datetime) -> Published:
        """一覧を一枚作って 失効_一覧 を書く(役の確かめは呼び手が済ませる)。21.6 の中身の決め方。"""
        ids = [e.識別 for e in self.entities()
               if e.経路 == CREDENTIAL and self._stopped(e) and not ended(e.終了, at)]
        lists = self._revlists()
        generation = lists[-1].世代 + 1 if lists else 1
        issued_at = int(at.timestamp())
        if lists and issued_at <= lists[-1].発行時刻:
            issued_at = lists[-1].発行時刻 + 1
        slip = self._write("失効_一覧", {"世代": str(generation), "発行時刻": str(issued_at), "識別": ", ".join(ids),
                                        "操作者": by, "役": role}, by=by, role=role, machine=True)
        return Published(slip, generation, self._signed(generation, issued_at, ids))

    def publish_revocations(self, *, by: str, role: str, at: datetime | None = None) -> Published:
        """失効の一覧を一枚作り、失効_一覧 を書く(管理者、運用。伝票の発行者は 発行者)。

        識別は、署名つき資格情報の実体のうち、止めてあって(実体_失効か、権限の失効)、at で終了を過ぎていない物の全部
        (主体を問わず、実体_発行を台帳に書いた順)。世代は台帳の最後の一覧 + 1(無ければ 1)。発行時刻は at の unix 秒
        (秒の端数は切り捨て)で、前の一覧の発行時刻以下になる時は、その発行時刻 + 1 にする(時刻が古くても拒まない)。
        失効が一件も無くても作れる(扉の通知を新しく保つため)。Published(伝票, 世代, 署名つきの一覧) を返す。"""
        self._begin(PUBLISHERS, by, role)
        return self._publish(by, role, local(at))

    def _list_after_stop(self, stopped: list, by: str, role: str, at: datetime):
        """失効の操作の 2 の後: 署名つき資格情報の実体に実体_失効を一枚でも書いたら、同じ at で一覧を一枚作る(21.6)。
        書いた 失効_一覧 の伝票か None。役の確かめは失効の操作の役で済んでいる。"""
        if not any(s["経路"] == CREDENTIAL for s in stopped):
            return None
        return self._publish(by, role, at).伝票

    # ---- 配った記録 ----
    def record_delivery(self, door: str, generation: int, *, by: str, role: str, at=None, note: str = ""):
        """一覧のその世代が扉に届いたと確かめた事を、失効_配布 に書く(管理者、運用。伝票の発行者は by)。

        at は扉が受けたと確かめた日時(YYYY-MM-DDTHH:MM の文字列か datetime。None は今)、note は運び方などの備考。
        扉の GET /revocation の応答(その発行者の generation)や扉の画面を、人が見て書く。
        door がこの置き場の使用の扉でなければ UNKNOWN_DOOR(詳細に扉の名)、その世代の一覧が無ければ
        UNKNOWN_GENERATION(詳細に世代の数)。前に記録した世代より小さい世代も書ける(扉の今の世代は下がらない)。"""
        self._begin(PUBLISHERS, by, role)
        v = self._clean("失効_配布", {"扉": door, "世代": "" if generation is None else str(generation),
                                     "日時": form_time(at if at is not None else datetime.now()), "備考": note,
                                     "役": role})
        d = self._doors().get(v["扉"])
        if d is None or d.状態 != "使用":
            raise Refused(R.UNKNOWN_DOOR, v["扉"])
        found = next((r for r in self._revlists() if r.世代 == int(v["世代"])), None)
        if found is None:
            raise Refused(R.UNKNOWN_GENERATION, v["世代"])
        return self._write("失効_配布", v, by=by, role=role, basis=[found.番号])

    # ---- 署名つき資格情報の時刻(21.8) ----
    def _door_time(self, door: str, e, t: int) -> int:
        """T(扉, t): 扉の今の最長の分数 M で、実体の長さが M 分より長ければ min(終了, t + M 分)、ほかは終了(unix 秒)。"""
        end = unix(e.終了)
        d = self._doors().get(door)
        m = d.失効の最長の分数 if d is not None else None
        if m is not None and end - unix(e.開始) > m * 60:
            return min(end, t + m * 60)
        return end

    def _latest(self, doors: list[str], e, t: int) -> int:
        return max((self._door_time(d, e, t) for d in doors), default=unix(e.終了))

    def _credential_stop(self, e, at: datetime) -> Stop:
        """経路の「止める」: 終了を過ぎたら 止まった。ほかは実体の扉の全部の T(扉, at) の一番遅い物まで 待ち。"""
        if ended(e.終了, at):
            return Stop("止まった", "", "")
        return Stop("待ち", minute_up(self._latest(e.扉, e, int(at.timestamp()))), "")

    def _undelivered(self, e) -> list[str]:
        """その識別を入れた最初の一覧の世代 g0 が、まだ届いた記録に無い扉(実体の扉の列の順)。"""
        first = self._first_list().get(e.識別)
        g0 = first.世代 if first is not None else 0
        return [d for d in e.扉 if self.door_generation(d) < g0]

    def _credential_row(self, e, at: datetime) -> Stop:
        """21.8 の進み具合の五つの順。"""
        if ended(e.終了, at):
            return Stop("止まった", "", "")
        if not self._stopped(e):
            return Stop("効いている", "", "")
        first = self._first_list().get(e.識別)
        if first is None:
            return Stop("待ち", e.終了, "")
        left = self._undelivered(e)
        if not left:
            return Stop("止まった", "", "")
        when = minute_up(self._latest(left, e, first.発行時刻))
        if datetime.fromisoformat(when) <= at:
            return Stop("止まった", "", "")
        return Stop("待ち", when, "")
