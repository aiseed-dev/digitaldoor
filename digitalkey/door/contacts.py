"""接点 — コントローラから見た錠と扉と建物は、この入出力の束だけ。

入力: 火災信号、商用電源、扉の開閉、デッドボルトの位置、手動リリース、改ざん検知。
出力: 解錠、施錠。錠の銘柄はこの下に隠れる。開発中は Sim、基板では GPIO やリレーに差し替える。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class Contacts(Protocol):
    def fire(self) -> bool: ...

    def mains(self) -> bool: ...

    def door_open(self) -> bool: ...

    def bolt_out(self) -> bool: ...

    def set_lock(self, locked: bool) -> None: ...


@dataclass
class SimContacts:
    """模擬の接点。試験と開発機用。"""

    _fire: bool = False
    _mains: bool = True
    _door_open: bool = False
    _bolt_out: bool = True
    outputs: list[str] = field(default_factory=list)

    def fire(self) -> bool:
        return self._fire

    def mains(self) -> bool:
        return self._mains

    def door_open(self) -> bool:
        return self._door_open

    def bolt_out(self) -> bool:
        return self._bolt_out

    def set_lock(self, locked: bool) -> None:
        self.outputs.append("lock" if locked else "unlock")
        self._bolt_out = locked
