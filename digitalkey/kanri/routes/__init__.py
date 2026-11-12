# SPDX-License-Identifier: AGPL-3.0-or-later
"""経路の差し替えのインターフェース `Route` と登録簿(設計書 19.8、20.8)。

経路は、どれも同じ四つの働き(出す、止める、効くまで、照らす)を持ちます。経路は伝票を書かず、結果を返し、
伝票は `Unit` が書きます。四つ(署名つき資格情報、門番を通す錠、物理キー、管理者のインターフェース)は組み込みで、ほかの経路は
pip で入れたパッケージが entry point の群 `digitalkey.kanri.routes` で足します(名前 = 経路の名、値 = Route の
クラスか物。錠の登録簿 `digitalkey.locks` と同じ形)。
"""
from __future__ import annotations

from datetime import datetime
from importlib.metadata import entry_points
from typing import Protocol

CREDENTIAL = "署名つき資格情報"
GATE = "門番を通す錠"
PHYSICAL = "物理キー"
ACCESS = "管理者のインターフェース"   # 機器の権限だけの経路(20.8)。扉の経路の一覧には書けない


class Route(Protocol):
    """経路の差し替えのインターフェース。"""

    name: str

    def issue(self, unit, perm, *, doors: list[str], start: str, end: str, once: bool,
              key_number: str): ...   # 出す → RouteIssue

    def stop(self, unit, entity, *, at: datetime): ...   # 止める → Stop

    def effective_by(self, unit, entity): ...   # 効くまで → EffectiveBy

    def reconcile(self, unit, entity, *, at: datetime): ...   # 照らす → Reconcile


def available() -> dict[str, Route]:
    """使える経路 {経路の名: Route}。組み込みの四つと、entry point `digitalkey.kanri.routes` で足した物。"""
    from .access import AccessRoute
    from .credential import CredentialRoute
    from .gate import GateRoute
    from .physical import PhysicalRoute

    out: dict[str, Route] = {CREDENTIAL: CredentialRoute(), GATE: GateRoute(), PHYSICAL: PhysicalRoute(),
                             ACCESS: AccessRoute()}
    for ep in entry_points(group="digitalkey.kanri.routes"):
        obj = ep.load()
        out[ep.name] = obj() if isinstance(obj, type) else obj
    return out
