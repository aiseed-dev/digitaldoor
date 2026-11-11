# SPDX-License-Identifier: AGPL-3.0-or-later
"""データの型(設計書 19.2、20.4、21.4)と、時刻の直し方(19.5、21.8)。

属性の名前は様式の項目の名前をそのまま使います。日時は様式の形 `YYYY-MM-DDTHH:MM`(地方時、時差なし)の文字列、
扉の列は `list[str]` です。状態は台帳の伝票から毎回導くので、どの型も読むだけの物(frozen)です。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

FORMAT = "%Y-%m-%dT%H:%M"


@dataclass(frozen=True)
class Subject:
    """名簿の主体。状態は 有効 / 停止 / 退出。持つ役は役_割当の付与と、有効なら 本人。"""
    識別: str
    呼び名: str
    種別: str
    業者: bool
    状態: str
    仮名: str
    持つ役: set[str]


@dataclass(frozen=True)
class Door:
    """この発行者の扉。状態は 使用 / 撤去。失効の最長の分数は、扉に据付で入れた revocation_max_age_s を分で写した値
    (空は None。R-AC-14)。"""
    名前: str
    経路: list[str]
    場所: str
    記録の公開鍵: str
    状態: str
    失効の最長の分数: int | None


@dataclass(frozen=True)
class Group:
    """扉の群。状態は 使用 / 廃止。"""
    名前: str
    扉: list[str]
    状態: str


@dataclass(frozen=True)
class Permission:
    """権限。番号は権限_申請の伝票の番号。状態は 申請中 / 承認 / 却下 / 発行済み / 期限切れ / 失効(19.7)。"""
    番号: str
    主体: str
    対象の種類: str
    対象: str
    開始: str
    終了: str
    理由: str
    出どころ: str
    申請者: str
    状態: str
    承認: str
    発行: str
    扉: list[str]
    失効: str


@dataclass(frozen=True)
class Entity:
    """経路の実体。番号は実体_発行の伝票の番号。失効は実体_失効の番号か空。回収は、物理キーなら物理キー_回収、
    管理者の口なら管理者の口_削除の番号(どちらも無ければ空)。機器は管理者の口の時の機器の名前(扉の経路では空)。"""
    番号: str
    権限: str
    主体: str
    経路: str
    扉: list[str]
    識別: str
    発行者: str
    開始: str
    終了: str
    一回限り: bool
    失効: str
    回収: str
    機器: str


@dataclass(frozen=True)
class Issued:
    """issue_entity の結果。資格情報は署名つき資格情報の dict(台帳には書かない)。ほかの経路は None。"""
    伝票: Any
    実体: Entity
    資格情報: dict | None


@dataclass(frozen=True)
class Row:
    """進み具合の一行。状態は 効いている / 止まった / 待ち / 人の作業が要る。機器はその実体の 機器(扉の経路では空)。"""
    実体: str
    経路: str
    識別: str
    扉: list[str]
    状態: str
    効くまで: str
    作業: str
    機器: str


@dataclass(frozen=True)
class Sheet:
    """一人の進み具合の一枚。済みは、行の全部が 止まった(行が無ければ真)。"""
    主体: str
    時刻: str
    行: list[Row]
    済み: bool


@dataclass(frozen=True)
class Revocation:
    """失効の結果。二つの列には、その呼び出しで書いた伝票だけが入る。失効_一覧は、その呼び出しで作った 失効_一覧 の
    伝票(署名つき資格情報の実体に実体_失効を書かなかった時は None)。"""
    権限_失効: list
    実体_失効: list
    進み具合: Sheet
    失効_一覧: Any


@dataclass(frozen=True)
class GateVerdict:
    """門番への答え。通れば ok が真で、理由は空、実体は通した実体の番号。"""
    ok: bool
    理由: str
    実体: str


@dataclass(frozen=True)
class ImportResult:
    """外の一覧の取り込みの結果。飛ばしたは (データの行の番号(1 から), 理由の言葉)。"""
    申請: list
    飛ばした: list[tuple[int, str]]


@dataclass(frozen=True)
class RouteIssue:
    """経路の「出す」の結果。伝票は Unit が書く。"""
    識別: str
    発行者: str
    資格情報: dict | None


@dataclass(frozen=True)
class Stop:
    """経路の「止める」の結果。結果は 止まった / 待ち / 人の作業が要る。"""
    結果: str
    効くまで: str
    作業: str


@dataclass(frozen=True)
class EffectiveBy:
    """経路の「効くまで」。種類は 即時 / 期限まで / 人の作業まで / 最長の時間まで。分数は 最長の時間まで の時だけ
    数が入る(扉の失効の最長の分数のうち一番大きい物)。"""
    種類: str
    時刻: str
    分数: int | None = None


@dataclass(frozen=True)
class Reconcile:
    """経路の「照らす」。結果は 一致 / 違い / 照らせない。"""
    結果: str
    詳細: str


# ---- 段 1b(20.4) ----

@dataclass(frozen=True)
class Segment:
    """網の区画。状態は 使用 / 廃止。"""
    名前: str
    説明: str
    状態: str


@dataclass(frozen=True)
class Device:
    """機器の台帳の一台の今の値。状態は 準備 / 使用 / 休止 / 紛失 / 廃棄。更新の期限は YYYY-MM-DD か空。
    パスワードと鍵の値は持たず、秘密の在庫表(kagi/)の番号だけを持つ。"""
    名前: str
    種類: str
    責任者: str
    置き場: str
    区画: str
    扉の系統: bool
    扉: str
    製造者: str
    型: str
    製造番号: str
    網の番号: str
    版: str
    更新の期限: str
    更新の当て方: str
    秘密の在庫の番号: str
    記録の送り先: str
    中に残る物: str
    状態: str


@dataclass(frozen=True)
class DeviceChange:
    """change_device の結果。紛失と廃棄の時は、同じ呼び出しで書いた権限_失効と実体_失効の列(ほかは空の列)。
    失効_一覧は、その呼び出しで作った 失効_一覧 の伝票か None。"""
    伝票: Any
    権限_失効: list
    実体_失効: list
    失効_一覧: Any


@dataclass(frozen=True)
class Check:
    """機器の確かめの一枚。結果は確かめの項目のうち空でない物だけ(鍵は項目の名、様式の項目の順)。書いた人は伝票の発行者。"""
    番号: str
    機器: str
    場面: str
    日時: str
    結果: dict[str, str]
    所見: str
    書いた人: str


@dataclass(frozen=True)
class Finding:
    """台帳だけでできる照らし合わせの知らせ。拒否ではない。知らせは reasons の F_ で始まる言葉。"""
    機器: str
    知らせ: str
    詳細: str


@dataclass(frozen=True)
class Held:
    """業者と連携先に渡している権限の一つ。残りは、その権限の実体の進み具合の行のうち 止まった でない物。"""
    主体: str
    権限: str
    対象の種類: str
    対象: str
    開始: str
    終了: str
    状態: str
    残り: list[Row]


# ---- 段 2a(21.4) ----

@dataclass(frozen=True)
class Published:
    """publish_revocations の結果。伝票は 失効_一覧、一覧は発行者の鍵で署名した一覧 {"body", "sig"}。"""
    伝票: Any
    世代: int
    一覧: dict


@dataclass(frozen=True)
class RevList:
    """台帳の 失効_一覧 の一枚。番号は伝票の番号、発行時刻は unix 秒、識別は失効した資格情報の識別の列。"""
    番号: str
    世代: int
    発行時刻: int
    識別: list[str]


# ---- 時刻(19.5) ----

def local(at: datetime | None) -> datetime:
    """読むメソッドの at を、時差なしの地方時に。None は今。"""
    if at is None:
        return datetime.now()
    return at.astimezone().replace(tzinfo=None) if at.tzinfo else at


def form_time(v: Any) -> Any:
    """メソッドの日時の引数を様式に渡す形に。datetime は地方時に直して秒を落とす。文字列は様式の修復に任せる。"""
    if isinstance(v, datetime):
        return local(v).strftime(FORMAT)
    return "" if v is None else v


def parse(s: str) -> datetime:
    """様式の形の日時を datetime(地方時、時差なし)に。"""
    return datetime.fromisoformat(s)


def unix(s: str) -> int:
    """様式の形の日時を unix 秒に(地方時として読む。digitaldoor-controller の parse_time と同じ)。"""
    return int(parse(s).timestamp())


def ended(end: str, at: datetime) -> bool:
    """終了が空でなく、at >= 終了。権限と実体は終了の時刻ちょうどで切れる。"""
    return bool(end) and at >= parse(end)


def minute_up(t: int) -> str:
    """unix 秒を地方時に直し、秒があれば次の分に切り上げて 19.5 の形に(21.8 の時刻の書き方)。"""
    d = datetime.fromtimestamp(t)
    if d.second or d.microsecond:
        d = d.replace(second=0, microsecond=0) + timedelta(minutes=1)
    return d.strftime(FORMAT)
