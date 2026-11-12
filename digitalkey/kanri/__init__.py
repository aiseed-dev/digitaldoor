# SPDX-License-Identifier: AGPL-3.0-or-later
"""鍵の管理(段 1、1b、2a、5a) — 名簿、役、扉と群、設定、権限の申請・承認・発行・失効、経路の実体、一人の全経路の失効、
ネットワークの区画と機器の台帳、機器の管理者のインターフェース、失効の一覧と、一覧を扉へ配った記録、窓口の照会(主体と窓口の番号の結び、照会用のコピー)。

鍵の管理者が自分のコンピューターで、ネットワークから切り離して動かします(R-AC-19)。一つの置き場が、一人の管理者、一つの発行者の鍵、
一つの台帳の分です(R-AC-09)。名簿が正で、ディレクトリから人と鍵を出し入れしません(R-EB-37)。
出すのは人が承認した権限だけで、止めるのは承認なしで、一人の全経路を一度に止めます(R-AC-15)。
業者と連携先には、終了の無い権限と、管理者の決めた日数を超える権限を出しません(R-AC-10)。
ネットワークにつながる機器は機器の台帳に載せ(区画、責任者、更新の期限、据え付けと点検と廃棄の確かめ、更新の記録)、
足りない所を照らし合わせの通知に挙げます(R-NW-02〜05)。機器の管理者のインターフェースも、扉の鍵と同じ権限の伝票で出し、
同じ一枚で止めます。業者と連携先に渡している物は目録で見えます(R-RS-04)。
署名つき資格情報を止めた時は、同じ操作で発行者の鍵で署名した失効の一覧を作り、ネットワークの届かない扉へは人や端末が運びます
(R-AC-22)。扉ごとの失効が効くまでの最長の時間を台帳に書き、進み具合の見立てと、出す時の表示に使います(R-AC-14)。
本人が自分の権限と鍵を見る窓口のために、名簿の係が本人を確かめてから主体と窓口の番号を結び(R-EB-37)、本人が見てよい物
だけの照会用のコピーをファイルに出します。コピーに呼び名と仮名は入れません(R-LG-07)。開く所へ置きに行くのは別の道具
`digitalkey madoguchi push` です(R-AC-13)。

項目の名前と制約は様式(digitalkey/kanri/forms_data/*.adoc)が正で、振る舞いは設計書 docs/鍵の管理ソフト設計書.md の
19 章(段 1)、20 章(段 1b)、21 章(段 2a)、22 章(段 5a)が正です。使い方は `digitalkey kanri --help`。

    from digitalkey.kanri import Unit
    u = Unit.create("/srv/kanri", admin="adm", admin_name="管理者A")
    u = Unit.open("/srv/kanri")

経路(署名つき資格情報、門番を通す錠、物理キー、管理者のインターフェース)は `digitalkey.kanri.routes` の差し替えのインターフェースで、ほかの経路は
entry point の群 `digitalkey.kanri.routes` で足します。
"""
from __future__ import annotations

from . import reasons
from .model import (Check, Device, DeviceChange, EffectiveBy, Entity, Finding, GateVerdict, Group, Held, ImportResult,
                    Issued, Link, Permission, Published, Reconcile, RevList, Revocation, Door, RouteIssue, Row, Segment,
                    Sheet, Stop, Subject)
from .reasons import Refused
from .routes import ACCESS, CREDENTIAL, GATE, PHYSICAL, Route
from .unit import Unit

__all__ = ["Unit", "Refused", "reasons", "Route", "CREDENTIAL", "GATE", "PHYSICAL", "ACCESS",
           "Subject", "Door", "Group", "Permission", "Entity", "Issued", "Row", "Sheet", "Revocation", "GateVerdict",
           "ImportResult", "RouteIssue", "Stop", "EffectiveBy", "Reconcile", "Link",
           "Segment", "Device", "DeviceChange", "Check", "Finding", "Held", "Published", "RevList"]
