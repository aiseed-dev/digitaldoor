# SPDX-License-Identifier: AGPL-3.0-or-later
"""経路 署名つき資格情報(設計書 19.8、21.8)。

発行者の鍵で署名した資格情報(digitaldoor-credential/1)を本人に渡し、扉が自分で確かめて開けます。
扉はネットワークも発行者のサーバーも要らないので、止めた事は、失効の一覧(段 2a)が扉に届くまで扉に分かりません。
一覧を受けていない扉でも、扉に失効の通知の古さの上限(扉の 失効の最長の分数)があれば、長い資格情報はその時間で
拒まれます。ほかは期限まで効きます。
not_after を終了の 1 秒前にして、扉の判定(not_before <= now <= not_after)と台帳の判定(開始 <= 時刻 < 終了)を
同じ時刻で切ります。

止める、効くまで、照らすは、台帳の一覧、配った記録(失効_配布)、扉の今の最長の分数を unit から読みます。
"""
from __future__ import annotations

import secrets

from digitaldoor_controller import credentials

from .. import reasons as R
from ..model import EffectiveBy, Reconcile, RouteIssue, Stop, unix
from ..reasons import Refused
from . import CREDENTIAL


class CredentialRoute:
    """署名つき資格情報。終了が要る(期限なしの権限から出す時は until を渡す)。"""

    name = CREDENTIAL

    def issue(self, unit, perm, *, doors, start, end, once, key_number) -> RouteIssue:
        if not end:
            raise Refused(R.NEED_END)
        cred_id = secrets.token_hex(8)
        cred = credentials.issue(unit.signer, cred_id=cred_id, subject=unit.subject(perm.主体).仮名, doors=doors,
                                 not_before=unix(start), not_after=unix(end) - 1, once=once)
        return RouteIssue(cred_id, unit.issuer, cred)

    def stop(self, unit, entity, *, at) -> Stop:
        """終了を過ぎたら 止まった。ほかは、実体の扉の全部について、扉の最長の分数から読む時刻の一番遅い物まで 待ち。"""
        return unit._credential_stop(entity, at)

    def effective_by(self, unit, entity) -> EffectiveBy:
        """実体の扉の全部に最長の分数があり、実体の長さがそのどれよりも長ければ 最長の時間まで(分数は一番大きい物)。
        ほかは 期限まで(終了)。"""
        doors = unit._doors()
        mins = [doors[d].失効の最長の分数 if d in doors else None for d in entity.扉]
        if mins and None not in mins and unix(entity.終了) - unix(entity.開始) > max(mins) * 60:
            return EffectiveBy("最長の時間まで", "", max(mins))
        return EffectiveBy("期限まで", entity.終了)

    def reconcile(self, unit, entity, *, at) -> Reconcile:
        """止めてあって 止まった → 一致。止めてあって 待ち → 一覧に入っていないか、届いていない扉を挙げて 違い。
        ほか → 照らせない(扉の記録の受け取りは段 2 の残り)。"""
        if unit._stopped(entity):
            if unit._row(entity, at).状態 == "止まった":
                return Reconcile("一致", "")
            if unit._first_list().get(entity.識別) is None:
                return Reconcile("違い", "失効の一覧に入っていない")
            return Reconcile("違い", "扉に届いていない: " + ", ".join(unit._undelivered(entity)))
        return Reconcile("照らせない", "扉の記録の受け取りは段 2")
