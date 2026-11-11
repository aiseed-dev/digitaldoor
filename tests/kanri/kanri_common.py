"""鍵の管理ソフト(段 1)の試験で共に使う値と、時刻の直し方。

設計書 19 章だけを読んで書いた。名前は架空の物(adm、apr、s1、v1、c1、g1)。
時刻は固定の値を使い、今の時計に頼らない(読むメソッドと発行には `at` を渡す)。
"""
from __future__ import annotations

from datetime import datetime, timedelta

# 19.10 の例の権限の期間
T0 = "2026-12-01T09:00"
T1 = "2027-01-01T00:00"

CREDENTIAL = "署名つき資格情報"
GATE = "門番を通す錠"
PHYSICAL = "物理キー"
ALL3 = [CREDENTIAL, GATE, PHYSICAL]

PHYSICAL_WORK = "鍵番号 {} を回収する。戻らない時は錠を交換して回収の伝票に書く"   # 19.8 の表の文


def dt(s: str) -> datetime:
    """様式の形の日時(地方時)を datetime に。"""
    return datetime.fromisoformat(s)


def unix(s: str) -> int:
    """19.5 の unix 秒への直し方。"""
    return int(datetime.fromisoformat(s).timestamp())


def plus(s: str, **kw) -> str:
    """様式の形の日時に足して、様式の形で返す。"""
    return (datetime.fromisoformat(s) + timedelta(**kw)).strftime("%Y-%m-%dT%H:%M")


# ---- 段 1b(設計書 20 章) ----
# 段 1b の言葉と経路の名は、実装より先に書くので、`digitalkey.kanri` から引かずに文字で持つ(20.11「言葉で比べてかまいません」)。

ACCESS = "管理者の口"
ALL4 = [CREDENTIAL, GATE, PHYSICAL, ACCESS]
ACCESS_WORK = "機器 {} の管理者の口 {} を止める。分けられない口はパスワードを替えて、削除の伝票に書く"   # 20.8 の表の文
ISSUE_AT = "2026-11-20T00:00"   # 発行と実体を出す時刻(19.13 の試験と同じく開始の前)

# 20.7 の確かめの項目(様式の項目の順)
CHECK_ITEMS = ["初めのパスワード", "外からの口", "見覚えのない設定とアカウント", "版", "区画", "権限", "記録の送り先",
               "中に残る物", "網から外した"]

# 20.2 の様式の 20 枚
FORMS_1B = ["主体_登録", "主体_変更", "役_割当", "扉", "群", "設定", "権限_申請", "権限_承認", "権限_発行", "権限_失効",
            "実体_発行", "実体_失効", "物理キー_回収", "失効_確認",
            "区画", "機器_登録", "機器_変更", "機器_確かめ", "機器_更新", "管理者の口_削除"]


class RB:
    """20.11 の拒否の言葉と、知らせの言葉。"""
    DUPLICATE_DEVICE = "同じ名前の機器があります"
    UNKNOWN_DEVICE = "この台帳の機器ではありません"
    UNKNOWN_SEGMENT = "登録されていない区画です"
    DEVICE_RETIRED = "廃棄した機器です"
    NOT_OWNER = "この機器の責任者ではありません"
    NEED_ITEM = "確かめの項目が足りません"
    NEED_REASON = "理由が要ります"
    DEVICE_ONLY = "管理者の口は機器の権限だけです"
    NEED_ACCOUNT = "口の名が要ります"
    NOT_ACCESS = "管理者の口ではありません"
    ALREADY_REMOVED = "削除済みです"

    F_NO_OWNER = "責任者がいません"
    F_OWNER_INACTIVE = "責任者が有効ではありません"
    F_NO_SEGMENT = "区画が決まっていません"
    F_MIXED_SEGMENT = "扉の系統とほかの機器が同じ区画です"
    F_NO_SUPPORT_END = "更新の期限が分かりません"
    F_SUPPORT_ENDED = "更新の期限を過ぎています"
    F_SUPPORT_NEAR = "更新の期限が近づいています"
    F_NO_INSTALL_CHECK = "据え付けの確かめがありません"
    F_BAD_ITEM = "確かめの結果が悪いままです"
    F_CHECK_OVERDUE = "点検の間が空いています"
    F_SHARED_ACCOUNT = "一つの口を複数の人が使っています"
    F_NO_DISPOSAL_CHECK = "廃棄の確かめがありません"
    F_ACCESS_LEFT = "止まっていない口があります"


REFUSALS_1B = ["DUPLICATE_DEVICE", "UNKNOWN_DEVICE", "UNKNOWN_SEGMENT", "DEVICE_RETIRED", "NOT_OWNER", "NEED_ITEM",
               "NEED_REASON", "DEVICE_ONLY", "NEED_ACCOUNT", "NOT_ACCESS", "ALREADY_REMOVED"]
FINDINGS_1B = ["F_NO_OWNER", "F_OWNER_INACTIVE", "F_NO_SEGMENT", "F_MIXED_SEGMENT", "F_NO_SUPPORT_END",
               "F_SUPPORT_ENDED", "F_SUPPORT_NEAR", "F_NO_INSTALL_CHECK", "F_BAD_ITEM", "F_CHECK_OVERDUE",
               "F_SHARED_ACCOUNT", "F_NO_DISPOSAL_CHECK", "F_ACCESS_LEFT"]


def findings(u, at, word: str | None = None, device: str | None = None) -> list[tuple[str, str, str]]:
    """device_findings(at=...) を (機器, 知らせ, 詳細) の列にし、知らせの言葉と機器で絞る(20.13「言葉で絞って比べる」)。"""
    if isinstance(at, str):
        at = dt(at)
    out = [(f.機器, f.知らせ, f.詳細) for f in u.device_findings(at=at)]
    return [x for x in out if (word is None or x[1] == word) and (device is None or x[0] == device)]


# ---- 段 2a(設計書 21 章) ----
# 段 2a の言葉も、実装より先に書くので文字で持つ(21.11「言葉で比べてかまいません」)。

# 21.2 の様式の 22 枚(20 枚に 失効_一覧、失効_配布 を足す)
FORMS_2A = FORMS_1B + ["失効_一覧", "失効_配布"]


class R2A:
    """21.11 の拒否の言葉。"""
    NO_REVOCATION_LIST = "失効の一覧がありません"
    UNKNOWN_GENERATION = "その世代の一覧がありません"


REFUSALS_2A = ["NO_REVOCATION_LIST", "UNKNOWN_GENERATION"]


# ---- 段 5a(設計書 22 章) ----
# 段 5a の言葉も、実装より先に書くので文字で持つ(22.8「言葉で比べてかまいません」)。

# 22.2 の様式の 23 枚(22 枚に 主体_結び を足す)
FORMS_5A = FORMS_2A + ["主体_結び"]


class R5A:
    """22.8 の拒否の言葉。"""
    ACCOUNT_LINKED = "その番号は、ほかの主体に結ばれています"
    NOT_LINKED = "結ばれていません"


REFUSALS_5A = ["ACCOUNT_LINKED", "NOT_LINKED"]

SHEET_FORMAT = "digitalkey-shoukai/1"          # 22.7 の 形式
ELLIPSIS = "…"                            # 22.7 の識別の出し方の「…」
EXPORT_KEYS = ["owner", "issuer", "made_at", "sheet"]
SHEET_KEYS = ["形式", "時刻", "権限", "鍵"]
PERM_KEYS = ["番号", "対象の種類", "対象", "開始", "終了", "状態"]
KEY_KEYS = ["権限", "経路", "扉", "機器", "識別", "状態", "効くまで"]


def at_minute_up(t: int) -> str:
    """21.8 の「時刻の書き方」: unix 秒を地方時の datetime に直し、秒があれば次の分に切り上げて 19.5 の形にする。"""
    d = datetime.fromtimestamp(t)
    if d.second or d.microsecond:
        d = d.replace(second=0, microsecond=0) + timedelta(minutes=1)
    return d.strftime("%Y-%m-%dT%H:%M")
