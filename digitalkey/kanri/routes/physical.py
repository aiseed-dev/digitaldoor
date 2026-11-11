# SPDX-License-Identifier: AGPL-3.0-or-later
"""経路 物理キー(設計書 19.8)。貸し出しは実体_発行(識別に鍵の番号)、戻りは物理キー_回収で書きます。

止めるには人の作業(鍵を回収する。戻らない時は錠を交換する)が要り、回収の伝票で止まります(R-AC-15)。
"""
from __future__ import annotations

from .. import reasons as R
from ..model import EffectiveBy, Reconcile, RouteIssue, Stop
from ..reasons import Refused
from . import PHYSICAL

WORK = "鍵番号 {} を回収する。戻らない時は錠を交換して回収の伝票に書く"


class PhysicalRoute:
    """物理キー。同じ鍵の番号は、回収するまで二度は貸さない。"""

    name = PHYSICAL

    def issue(self, unit, perm, *, doors, start, end, once, key_number) -> RouteIssue:
        if not key_number:
            raise Refused(R.NEED_KEY_NUMBER)
        for e in unit.entities():
            if e.経路 == PHYSICAL and e.識別 == key_number and not e.回収:
                raise Refused(R.KEY_LENT)
        return RouteIssue(key_number, "", None)

    def stop(self, unit, entity, *, at) -> Stop:
        if entity.回収:
            return Stop("止まった", "", "")
        return Stop("人の作業が要る", "", WORK.format(entity.識別))

    def effective_by(self, unit, entity) -> EffectiveBy:
        return EffectiveBy("人の作業まで", "")

    def reconcile(self, unit, entity, *, at) -> Reconcile:
        if unit._row(entity, at).状態 == "人の作業が要る":
            return Reconcile("違い", "貸し出したまま")
        return Reconcile("一致", "")
