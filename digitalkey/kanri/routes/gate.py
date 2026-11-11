# SPDX-License-Identifier: AGPL-3.0-or-later
"""経路 門番を通す錠と、門番が台帳に聞く口 `may_open`、`relay`(設計書 19.8、19.9)。

錠の鍵は門番(`digitalkey door-serve`、事業所サーバーなど)だけが持ち、本人には何も渡しません。
門番は要求のたびに台帳に聞くので、失効の伝票を書いた次の要求から開けません。
"""
from __future__ import annotations

import secrets
from datetime import datetime

from .. import reasons as R
from ..model import EffectiveBy, GateVerdict, Reconcile, RouteIssue, Stop, ended, local, parse
from . import GATE


class GateRoute:
    """門番を通す錠。識別だけを作る。"""

    name = GATE

    def issue(self, unit, perm, *, doors, start, end, once, key_number) -> RouteIssue:
        return RouteIssue(secrets.token_hex(8), "", None)

    def stop(self, unit, entity, *, at) -> Stop:
        return Stop("止まった", "", "")

    def effective_by(self, unit, entity) -> EffectiveBy:
        return EffectiveBy("即時", "")

    def reconcile(self, unit, entity, *, at) -> Reconcile:
        return Reconcile("一致", "")


class GateMixin:
    """門番が台帳に聞く口。読むだけで、台帳に書かない。"""

    def may_open(self, subject: str, door: str, *, at: datetime | None = None) -> GateVerdict:
        """その主体が、その扉を、その時刻に開けてよいか。確かめの順: 扉、主体、門番の実体、失効、期間。
        通れば GateVerdict(True, "", 通した実体の番号)。"""
        at = local(at)
        d = self._doors().get(door)
        if d is None or d.状態 != "使用":
            return GateVerdict(False, R.UNKNOWN_DOOR, "")
        who = self._subjects().get(subject)
        if who is None:
            return GateVerdict(False, R.SUBJECT_UNKNOWN, "")
        if who.状態 != "有効":
            return GateVerdict(False, R.SUBJECT_INACTIVE, "")
        mine = [e for e in self.entities(subject) if e.経路 == GATE and door in e.扉]
        if not mine:
            return GateVerdict(False, R.GATE_NO_PERMISSION, "")
        live = [e for e in mine if not e.失効 and not self._revoked(e.権限)]
        if not live:
            return GateVerdict(False, R.GATE_REVOKED, "")
        for e in live:
            if parse(e.開始) <= at and not ended(e.終了, at):
                return GateVerdict(True, "", e.番号)
        return GateVerdict(False, R.GATE_OUT_OF_PERIOD, "")

    def relay(self, ctl, subject: str, *, at: datetime | None = None) -> str:
        """疑似の扉(digitaldoor_controller の Controller)と門番をつなぐ。may_open(subject, ctl.name) の答えで
        ctl.on_auth を呼び、その戻り値(解錠、拒否 など)を返す。at が None なら扉の時計(ctl.now)で聞く。"""
        when = datetime.fromtimestamp(ctl.now) if at is None else at
        v = self.may_open(subject, ctl.name, at=when)
        who = self._subjects().get(subject)
        return ctl.on_auth(who.仮名 if who else "", route="門番", valid=v.ok, reason=v.理由,
                           detail=f"実体={v.実体}" if v.実体 else "")
