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
    """模擬の接点。試験と開発機用。

    故障注入(仕様書 5.5): fault_lock が "応答しない" の間、施錠と解錠の命令は錠に届かない。
    錠の位置は動かさず、failures を一つ増やし、outputs には足さない(digitalkey の LockDriverContacts と同じ振る舞い)。
    """

    _fire: bool = False
    _mains: bool = True
    _door_open: bool = False
    _bolt_out: bool = True
    outputs: list[str] = field(default_factory=list)
    fault_lock: str = "正常"       # 正常 / 応答しない
    failures: int = 0              # 届かなかった命令の数

    def fire(self) -> bool:
        return self._fire

    def mains(self) -> bool:
        return self._mains

    def door_open(self) -> bool:
        return self._door_open

    def bolt_out(self) -> bool:
        return self._bolt_out

    def set_lock(self, locked: bool) -> None:
        if self.fault_lock == "応答しない":
            self.failures += 1
            return
        self.outputs.append("lock" if locked else "unlock")
        self._bolt_out = locked
