# SPDX-License-Identifier: AGPL-3.0-or-later
"""拒否の理由の言葉(設計書 19.11、20.11、21.11、22.8)と、通知の言葉(20.11。`F_` で始まる定数)と、拒否 `Refused`。

どのメソッドも、拒む時は `Refused(理由, 詳細)` を投げ、台帳に何も書きません。
理由は下の定数のどれかで、命令 `digitalkey kanri` は `拒否: <理由>` か `拒否: <理由>(<詳細>)` を出します。
通知(`device_findings` の `Finding.通知`)は拒否ではなく、台帳だけで分かる足りない所を挙げる言葉です。
"""
from __future__ import annotations

ROLE_NOT_ALLOWED = "その役ではできません"
BY_UNKNOWN = "操作する人が名簿にありません"
BY_INACTIVE = "操作する人が有効ではありません"
NO_ROLE = "その役を持っていません"
FORM = "様式に合いません"
ALREADY_CREATED = "既に作られています"
NO_UNIT = "置き場がありません"
NO_KEY = "発行者の鍵がありません"
WRONG_KEY = "発行者の鍵が違います"
SUBJECT_UNKNOWN = "名簿にありません"
SUBJECT_INACTIVE = "主体が有効ではありません"
DUPLICATE_SUBJECT = "同じ識別の主体があります"
NOTHING_TO_CHANGE = "変える項目がありません"
NAME_HAS_COMMA = "名前に , は使えません"
UNKNOWN_ROUTE = "知らない経路です"
UNKNOWN_DOOR = "この発行者の扉ではありません"
UNKNOWN_GROUP = "登録されていない群です"
BAD_SETTING = "設定の値が読めません"
BAD_PERIOD = "終了は開始より後にしてください"
VENDOR_NO_END = "業者と連携先に期限の無い権限は出せません"
VENDOR_TOO_LONG = "業者と連携先に出せる期間を超えています"
NO_REQUEST = "申請がありません"
ALREADY_DECIDED = "この申請は判断済みです"
SELF_REQUEST = "自分の申請は承認できません"
SELF_SUBJECT = "自分の権限は承認できません"
NO_APPROVAL = "承認がありません"
REJECTED = "却下された申請です"
MISMATCH = "承認と合いません"
ALREADY_ISSUED = "発行済みです"
REVOKED = "失効した権限です"
EXPIRED = "期限が切れています"
PERMISSION_UNKNOWN = "権限がありません"
NOT_ISSUED = "発行されていない権限です"
NO_DOOR_FOR_ROUTE = "この経路を使う扉がありません"
ONCE_CREDENTIAL_ONLY = "一回限りは署名つき資格情報だけです"
UNTIL_TOO_LATE = "権限の終了より後にはできません"
NEED_END = "署名つき資格情報には終了が要ります"
NEED_KEY_NUMBER = "鍵の番号が要ります"
KEY_LENT = "貸し出し中の鍵です"
ENTITY_UNKNOWN = "実体がありません"
NOT_PHYSICAL = "物理キーではありません"
ALREADY_RETURNED = "回収済みです"
NOT_ALL_STOPPED = "止まっていない実体があります"
OUT_EXISTS = "出力のファイルが既にあります"
GATE_NO_PERMISSION = "権限がありません"
GATE_REVOKED = "失効しています"
GATE_OUT_OF_PERIOD = "期間の外"

# 段 1b(20.11): 機器の台帳と管理者のインターフェース
DUPLICATE_DEVICE = "同じ名前の機器があります"
UNKNOWN_DEVICE = "この台帳の機器ではありません"
UNKNOWN_SEGMENT = "登録されていない区画です"
DEVICE_RETIRED = "廃棄した機器です"
NOT_OWNER = "この機器の責任者ではありません"
NEED_ITEM = "確かめの項目が足りません"
NEED_REASON = "理由が要ります"
DEVICE_ONLY = "管理者のインターフェースは機器の権限だけです"
NEED_ACCOUNT = "インターフェースの名が要ります"
NOT_ACCESS = "管理者のインターフェースではありません"
ALREADY_REMOVED = "削除済みです"

# 段 2a(21.11): 失効の一覧
NO_REVOCATION_LIST = "失効の一覧がありません"
UNKNOWN_GENERATION = "その世代の一覧がありません"

# 段 5a(22.8): 窓口の結び
ACCOUNT_LINKED = "その番号は、ほかの主体に結ばれています"
NOT_LINKED = "結ばれていません"

# 通知の言葉(20.9、20.11)。拒否ではない
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
F_SHARED_ACCOUNT = "一つのインターフェースを複数の人が使っています"
F_NO_DISPOSAL_CHECK = "廃棄の確かめがありません"
F_ACCESS_LEFT = "止まっていないインターフェースがあります"


class Refused(ValueError):
    """操作を拒んだ。`理由` は上の言葉のどれか、`詳細` は空でよい。拒んだ時は台帳に何も書いていない。"""

    def __init__(self, 理由: str, 詳細: str = "") -> None:
        self.理由 = 理由
        self.詳細 = 詳細
        super().__init__(f"{理由}({詳細})" if 詳細 else 理由)
