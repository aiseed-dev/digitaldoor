# SPDX-License-Identifier: AGPL-3.0-or-later
"""動かす単位 `Unit`(設計書 19.2、19.3、20.1、21.1、22.1)。置き場の作成と読み込み、役の確かめ、台帳への書き込み。

一つの置き場が、一人の管理者、一つの発行者の鍵、一つの台帳の分です(R-AC-09)。

    <置き場>/kanri.json        形式、発行者の識別、公開鍵、鍵のファイルの道(秘密鍵の値は入れない)
    <置き場>/鍵/発行者.key     鍵を渡さずに作った時だけ。32 バイトの Ed25519 の秘密鍵、権限 600(鍵/ は 700)
    <置き場>/台帳/             digitalkey.entrance.ledger.Ledger の置き場

状態は台帳の伝票から毎回導き、別の表やファイルに持ちません。名簿、扉と群、設定は roster、
申請から実体までは permissions、門番は routes.gate、失効は revoke、区画と機器の台帳は devices、
失効の一覧と配った記録は revocations、窓口の結びと照会用のコピーは portal の各モジュールが、
Unit のメソッドとして持ちます。
"""
from __future__ import annotations

import json
import os
import secrets
from collections import defaultdict
from importlib import resources
from pathlib import Path

from digitaldoor_controller.audit import Ed25519Signer
from digitaldoor_controller.slips import key_id

from digitalkey.entrance import forms as Y
from digitalkey.entrance.ledger import Ledger, Slip

from . import reasons as R
from .devices import DevicesMixin
from .permissions import PermissionsMixin
from .portal import PortalMixin
from .reasons import Refused
from .revocations import RevocationsMixin
from .revoke import RevokeMixin
from .roster import RosterMixin
from .routes import available
from .routes.gate import GateMixin

FORMAT_NAME = "digitalkey-kanri/1"
MACHINE = "発行者"     # システムの伝票(権限_発行、実体_発行、実体_失効、失効_一覧)の伝票の発行者
INIT = "初期化"        # 置き場を作る時の最初の二枚だけの役の印


def load_forms() -> dict[str, Y.FormSpec]:
    """鍵の管理の様式(digitalkey/kanri/forms_data/*.adoc の 23 枚)だけを読む。受け入れの様式は混ぜない。"""
    base = resources.files("digitalkey.kanri").joinpath("forms_data")
    specs = [Y.load_file(Path(str(p))) for p in sorted(base.iterdir(), key=lambda p: p.name)
             if p.name.endswith(".adoc")]
    return {y.name: y for y in specs}


FORMS = load_forms()


class Book:
    """台帳の伝票の組を、様式ごと、番号ごと、項目の値ごとに引ける形にした物。台帳の枚数が変わるまで使い回す。"""

    def __init__(self, slips: list[Slip]) -> None:
        self.slips = slips
        self.by_id = {s.id: s for s in slips}
        self.forms: dict[str, list[Slip]] = defaultdict(list)
        for s in slips:
            self.forms[s.form].append(s)
        self._index: dict[tuple[str, str], dict[str, list[Slip]]] = {}
        self.memo: dict[str, object] = {}

    def of(self, form: str) -> list[Slip]:
        """その様式の伝票を、台帳に書いた順に。"""
        return self.forms.get(form, [])

    def where(self, form: str, item: str, value: str) -> list[Slip]:
        """その様式で、項目 item が value の伝票を、台帳に書いた順に。"""
        key = (form, item)
        if key not in self._index:
            idx: dict[str, list[Slip]] = defaultdict(list)
            for s in self.of(form):
                idx[s[item]].append(s)
            self._index[key] = idx
        return self._index[key].get(value, [])

    def first(self, form: str, item: str, value: str) -> Slip | None:
        found = self.where(form, item, value)
        return found[0] if found else None

    def slip(self, sid: str, form: str) -> Slip | None:
        """番号 sid の伝票が様式 form なら、その伝票。"""
        s = self.by_id.get(sid)
        return s if s is not None and s.form == form else None


def _read_key(path: str | Path) -> Ed25519Signer:
    try:
        data = Path(path).read_bytes()
    except (OSError, ValueError):
        raise Refused(R.NO_KEY) from None
    if len(data) != 32:
        raise Refused(R.NO_KEY)
    return Ed25519Signer(data)


def _check_signer(signer) -> None:
    if not (callable(getattr(signer, "sign", None)) and callable(getattr(signer, "public_key", None))):
        raise Refused(R.NO_KEY)


class Unit(RosterMixin, PermissionsMixin, GateMixin, RevokeMixin, DevicesMixin, RevocationsMixin, PortalMixin):
    """一つの発行者の鍵の管理の置き場。`Unit.create` で作り、`Unit.open` で開く。

    書くメソッドは、どれもキーワード引数 `by`(操作する人。名簿の主体の識別)と `role`(この操作で使う役)を取り、
    役の確かめ(19.3 の 1〜4)、様式の確かめ(5)、操作ごとの確かめ(6)の順に見て、最初に外れた所で `Refused` を投げます。
    拒んだ時は台帳に何も書きません。読むメソッドは役を取りません。

    属性: root(置き場)、ledger(Ledger)、issuer(発行者の識別)、public_key(bytes)、signer、routes({経路の名: Route})。
    """

    def __init__(self, root: Path, info: dict, signer, routes) -> None:
        self.root = root
        self.signer = signer
        self.public_key: bytes = signer.public_key()
        self.issuer: str = info["発行者"]
        self.routes = dict(available() if routes is None else routes)
        self.ledger = Ledger(root / "台帳", roles={}, form_set=FORMS)
        self._book: tuple[int, Book] | None = None

    # ---- 作る、開く ----
    @classmethod
    def create(cls, root, *, admin: str, admin_name: str, admin_kind: str = "社員",
               issuer_key: str | Path | None = None, signer=None, routes=None) -> "Unit":
        """置き場を作り、最初の二枚(主体_登録と役_割当。役 初期化)を書く。

        発行者の鍵は、signer を渡せばそれを使い(鍵のファイルは作らない)、issuer_key を渡せばそのファイルを読み
        (32 バイトの Ed25519 の秘密鍵。読めなければ NO_KEY)、どちらも無ければ 鍵/発行者.key を権限 600 で作ります。
        kanri.json が既にあれば ALREADY_CREATED。
        """
        root = Path(root)
        if (root / "kanri.json").exists():
            raise Refused(R.ALREADY_CREATED)
        if "," in admin:
            raise Refused(R.NAME_HAS_COMMA)
        first = {"識別": admin, "呼び名": admin_name, "種別": admin_kind, "業者": "false",
                 "仮名": secrets.token_hex(8), "役": INIT}
        grant = {"主体": admin, "割り当てる役": "管理者", "判断": "付与", "理由": "置き場を作った", "役": INIT}
        clean("主体_登録", first)
        clean("役_割当", grant)
        keyfile = ""
        if signer is not None:
            _check_signer(signer)
        elif issuer_key is not None:
            signer = _read_key(issuer_key)
            keyfile = str(Path(issuer_key).resolve())
        root.mkdir(parents=True, exist_ok=True)
        if signer is None:
            signer = Ed25519Signer.generate()
            keydir = root / "鍵"
            keydir.mkdir(mode=0o700, exist_ok=True)
            os.chmod(keydir, 0o700)
            write_private(keydir / "発行者.key", signer.private_bytes())
            keyfile = str((keydir / "発行者.key").resolve())
        info = {"形式": FORMAT_NAME, "発行者": key_id(signer.public_key()), "公開鍵": signer.public_key().hex(),
                "鍵のファイル": keyfile}
        unit = cls(root, info, signer, routes)
        unit.ledger.roles = {admin: {"名簿の係"}}
        unit.ledger.append("主体_登録", first, issuer=admin, to=admin)
        unit.ledger.roles = {admin: {"管理者"}}
        unit.ledger.append("役_割当", grant, issuer=admin, to=admin)
        (root / "kanri.json").write_text(json.dumps(info, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        return unit

    @classmethod
    def open(cls, root, *, signer=None, routes=None) -> "Unit":
        """置き場を開く。kanri.json が無ければ NO_UNIT。signer の公開鍵が違えば WRONG_KEY。
        signer を渡さなければ kanri.json の 鍵のファイル から読み、空か読めなければ NO_KEY。"""
        root = Path(root)
        try:
            info = json.loads((root / "kanri.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise Refused(R.NO_UNIT) from None
        if not isinstance(info, dict) or info.get("形式") != FORMAT_NAME:
            raise Refused(R.NO_UNIT)
        if signer is None:
            if not info.get("鍵のファイル"):
                raise Refused(R.NO_KEY)
            signer = _read_key(info["鍵のファイル"])
        else:
            _check_signer(signer)
        if signer.public_key().hex() != info.get("公開鍵"):
            raise Refused(R.WRONG_KEY)
        return cls(root, info, signer, routes)

    # ---- 台帳を読む ----
    def book(self) -> Book:
        """今の台帳の伝票の組。台帳の枚数が同じ間は同じ物を返す(台帳は追記だけなので、枚数で変わりが分かる)。"""
        n = self.ledger.count()
        if self._book is None or self._book[0] != n:
            self._book = (n, Book(self.ledger.select()))
        return self._book[1]

    # ---- 役の確かめと書き込み(19.3) ----
    def _begin(self, allowed: set[str], by: str, role: str) -> None:
        """19.3 の 1〜4: できる役か、by が名簿にあるか、有効か、role を持つか。"""
        if role not in allowed:
            raise Refused(R.ROLE_NOT_ALLOWED)
        who = self._subjects().get(by)
        if who is None:
            raise Refused(R.BY_UNKNOWN)
        if who.状態 != "有効":
            raise Refused(R.BY_INACTIVE)
        if role not in who.持つ役:
            raise Refused(R.NO_ROLE)

    @staticmethod
    def _clean(form: str, values: dict) -> dict:
        return clean(form, values)

    def _write(self, form: str, values: dict, *, by: str, role: str, to: str = "", basis=(),
               machine: bool = False) -> Slip:
        """伝票を一枚書く。人の伝票は発行者 by で、その役だけを通す。システムの伝票は発行者 `発行者` で書く。"""
        self.ledger.roles = {} if machine else {by: {role}}
        try:
            return self.ledger.append(form, values, issuer=MACHINE if machine else by, to=to, basis=list(basis))
        except ValueError as e:
            raise Refused(R.FORM, str(e).split("様式に合いません: ", 1)[-1]) from None
        finally:
            self.ledger.roles = {}


def clean(form: str, values: dict) -> dict:
    """19.3 の 5: 渡した項目だけを様式に照らし、修復した値を返す。合わなければ Refused(FORM)。"""
    spec = FORMS[form]
    part = Y.FormSpec(spec.name, spec.title, spec.attrs, [f for f in spec.fields if f.name in values])
    out, problems = Y.validate(part, values)
    if problems:
        raise Refused(R.FORM, "; ".join(problems))
    return out


def write_private(path: Path, data: bytes) -> None:
    """ファイルを権限 600 で新しく作って書く(既にあれば FileExistsError)。"""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
