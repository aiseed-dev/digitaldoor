# SPDX-License-Identifier: AGPL-3.0-or-later
"""経路 管理者の口(設計書 20.8)。機器の権限だけの経路です。

機器の管理者の口(管理の画面、遠隔の管理、SSH など)に入るアカウントや鍵を、口の名(アカウントの名や鍵の名)で
実体_発行に書きます。パスワードと鍵の値は書きません。ソフトは機器の中を変えないので、止めるには人の作業
(機器の中で口を消す。一つの口を複数の人が使う機器では、パスワードを替える)が要り、削除の伝票
(管理者の口_削除)で止まります。物理キーの回収と同じ形です。機械の中との照らし合わせは段 6 で足します。
"""
from __future__ import annotations

from .. import reasons as R
from ..model import EffectiveBy, Reconcile, RouteIssue, Stop
from ..reasons import Refused
from . import ACCESS

WORK = "機器 {} の管理者の口 {} を止める。分けられない口はパスワードを替えて、削除の伝票に書く"


class AccessRoute:
    """管理者の口。key_number に口の名(Unit.issue_entity の account)を受ける。同じ口の名を何人分でも出せる。"""

    name = ACCESS

    def issue(self, unit, perm, *, doors, start, end, once, key_number) -> RouteIssue:
        if not key_number:
            raise Refused(R.NEED_ACCOUNT)
        return RouteIssue(key_number, "", None)

    def stop(self, unit, entity, *, at) -> Stop:
        if entity.回収:
            return Stop("止まった", "", "")
        return Stop("人の作業が要る", "", WORK.format(entity.機器, entity.識別))

    def effective_by(self, unit, entity) -> EffectiveBy:
        return EffectiveBy("人の作業まで", "")

    def reconcile(self, unit, entity, *, at) -> Reconcile:
        if unit._row(entity, at).状態 == "人の作業が要る":
            return Reconcile("違い", "口が残ったまま")
        return Reconcile("照らせない", "機械の中との照らし合わせは段 6")
