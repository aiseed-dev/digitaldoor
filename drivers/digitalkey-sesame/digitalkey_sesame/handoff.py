"""鍵の受け渡し — 対面で、アプリからアプリへ直接。ネットワーク(LINE、メール、クラウド)を通さない。

渡す側のアプリが QR を画面に出し、受け取る側のアプリが自分のカメラで読みます。端末のふつうのカメラアプリや、
画像を読み取るサービスは通しません。読んだ内容がそちらに残ることがあるためです。読み取りが使えないときは、
渡す側が 32 桁を見せ、受け取る側が打ち込みます(利用者として入ります)。

QR の中身(大文字だけの 1 行。QR の英数字モードに収まり、小さく読みやすい):

    DDSESAME1:<機器の UUID>:<鍵の 32 桁>:U
    DDSESAME1:<機器の UUID>:<鍵の 32 桁>:A:<管理者コード 8 桁>

    バージョン(1)、UUID、32 桁、役(U 利用者、A 管理者)、管理者コード(A のときだけ)。人の名前は入れません。

管理者コードは、管理者として渡すときだけ付けます。鍵と UUID から決まる 8 桁(HMAC-SHA256 の先頭)で、
受け取る側のアプリが形と一致を確かめます。コードの無い鍵は利用者として入り、あとから上がりません。
これは画面の歯止めで、鍵の守りではありません。コードの作り方はこのリポジトリに公開されていて、32 桁を持つ人が
別の道具を使えば歯止めは通ります。防げるのは、受け取った人がこのアプリで鍵をさらにコピーして広げることです。

受け取る側は、Sesame にログインできたときだけ保存します(R-CM-13)。32 桁と管理者コードを、ログ、画面の記録欄、
例外の文、標準出力に出しません(R-CM-12)。人が見られるのは、渡す側の確かめを済ませた表示だけで、閉じると消えます。
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import io
import json
import re
import time
import uuid as uuidlib
from dataclasses import dataclass, field
from typing import Callable, Optional

from .device import SesameError
from .keystore import (AsyncKV, DeviceKey, KeyStore, KeyStoreError, ROLE_ADMIN, ROLE_USER, SECRET_DIGITS,
                       parse_secret_hex)

PREFIX = "DDSESAME"
VERSION = 1

# 細部の選び(作るときに選んだ値。変えるときはここだけ)
SHOW_SECONDS = 60       # 渡す側が QR や 32 桁を画面に出しておく秒数。過ぎたら自動で閉じて消す
SCAN_SECONDS = 8        # 受け取る側が、その Sesame の広告を待つ秒数
CODE_DIGITS = 8         # 管理者コードの桁数(16 進)。取り違いを見つける長さで、強さを求める物ではない
NAME_MAX = 40           # 控えに残す相手の呼び名の最大文字数


class HandoffError(ValueError):
    """QR や管理者コードの形が違う。文に入力は入れない(鍵がログや画面に残らないように)。"""


class ReceiveError(Exception):
    """受け取れなかった。何も保存していない。文に鍵は入れない。"""


# ---- 管理者コード ----
def admin_code(secret: bytes, device_uuid: str) -> str:
    """管理者として渡すときに付けるコード(16 進 8 桁、大文字)。鍵と UUID から決まる。"""
    msg = b"digitaldoor/sesame/admin/v1:" + str(uuidlib.UUID(str(device_uuid))).encode()
    return hmac.new(secret, msg, hashlib.sha256).hexdigest()[:CODE_DIGITS].upper()


def format_admin_code(code: str, group: int = 4) -> str:
    return " ".join(code[i:i + group] for i in range(0, len(code), group))


def normalize_admin_code(text: str) -> str:
    """人が書き写したコードの区切り(空白、ハイフン)を読み飛ばして大文字にする。形が違えば HandoffError(入力は文に入れない)。"""
    digits = re.sub(r"[\s-]", "", text or "").upper()
    if len(digits) != CODE_DIGITS or not re.fullmatch(r"[0-9A-F]+", digits):
        raise HandoffError(f"管理者コードは 16 進 {CODE_DIGITS} 桁です(区切りの空白とハイフンは読み飛ばします)")
    return digits


def check_admin_code(secret: bytes, device_uuid: str, text: str) -> bool:
    try:
        given = normalize_admin_code(text)
    except HandoffError:
        return False
    return hmac.compare_digest(given, admin_code(secret, device_uuid))


# ---- 中身 ----
@dataclass(frozen=True)
class Handoff:
    """読み取った受け渡し。`secret` は repr に出さない。`role` はコードが合ったときだけ管理者。"""
    device_uuid: str
    secret: bytes = field(repr=False)
    role: str = ROLE_USER
    notice: str = ""     # 役を利用者に落とした理由など、人に見せる一文(鍵は入らない)


def encode_handoff(key: DeviceKey, role: str) -> str:
    """保存した鍵から、QR に載せる 1 行を作る。管理者として渡せるのは、管理者の鍵だけ。"""
    if role not in (ROLE_ADMIN, ROLE_USER):
        raise ValueError("役は admin か user です")
    if role == ROLE_ADMIN and not key.is_admin:
        raise ValueError("利用者の鍵は、管理者として渡せません")
    uid = str(uuidlib.UUID(key.device_uuid)).upper()
    text = f"{PREFIX}{VERSION}:{uid}:{key.secret_hex.upper()}:{'A' if role == ROLE_ADMIN else 'U'}"
    if role == ROLE_ADMIN:
        text += ":" + admin_code(key.secret, key.device_uuid)
    return text


def parse_handoff(text: str) -> Handoff:
    """QR から読んだ 1 行を受け渡しにする。形が違えば HandoffError。
    役は、管理者コードが合ったときだけ管理者。コードが無い、または合わないときは利用者で入り、その旨を `notice` に書く。"""
    parts = (text or "").strip().split(":")
    head = parts[0] if parts else ""
    if not head.startswith(PREFIX):
        raise HandoffError("鍵の受け渡しの QR ではありません")
    ver = head[len(PREFIX):]
    if ver != str(VERSION):
        raise HandoffError("このバージョンのアプリでは読めない形の QR です(アプリを新しくしてください)" if ver.isdigit() and len(ver) <= 3
                           else "鍵の受け渡しの QR ではありません")
    if len(parts) not in (4, 5):
        raise HandoffError("鍵の受け渡しの QR の形が違います")
    try:
        device_uuid = str(uuidlib.UUID(parts[1]))
    except ValueError:
        raise HandoffError("鍵の受け渡しの QR の形が違います") from None
    digits = parts[2]
    if len(digits) != SECRET_DIGITS or not re.fullmatch(r"[0-9A-Fa-f]+", digits):
        raise HandoffError("鍵の受け渡しの QR の形が違います")
    secret = bytes.fromhex(digits)
    if parts[3] not in ("U", "A"):
        raise HandoffError("鍵の受け渡しの QR の形が違います")
    if parts[3] == "U":
        return Handoff(device_uuid, secret, ROLE_USER)
    if len(parts) == 5 and check_admin_code(secret, device_uuid, parts[4]):
        return Handoff(device_uuid, secret, ROLE_ADMIN)
    return Handoff(device_uuid, secret, ROLE_USER, "管理者コードが合わないので、利用者として受け取ります")


def typed_handoff(device_uuid: str, text: str) -> Handoff:
    """読み取りが使えないときに、打ち込まれた 32 桁から作る受け渡し。利用者として入る(コードの入口は置かない)。"""
    return Handoff(str(uuidlib.UUID(device_uuid)), parse_secret_hex(text), ROLE_USER)


def qr_png(payload: str, scale: int = 8) -> bytes:
    """QR の PNG(白地に黒、周りの余白 4 モジュール)。segno は pure Python で、Android と iOS の flet build にそのまま入る。
    誤り訂正は M。英数字モードに収まるので、バージョンは 4 前後になる(カメラで読みやすい粗さ)。"""
    import segno
    out = io.BytesIO()
    segno.make(payload, error="m", micro=False).save(out, kind="png", scale=scale, border=4, dark="#000000", light="#ffffff")
    return out.getvalue()


# ---- 渡した相手の控え(追記だけ) ----
LEDGER_KEY = "digitalkey_sesame.handoffs"


class HandoffLedger:
    """渡した相手の控え(誰に、いつ、どの役で、どの道で)。管理者の端末に残し、書き足すだけで、書き換えも消しもしない。
    リセットが要るかを決める材料です。相手の名前は、持ち主が付けた呼び名で十分です。
    リセットして登録し直した場合の扱いは、控えの側ではなく読む側で行う(鍵の registered_at より前の行は「リセット前」)。
    鍵そのものは入れない。"""

    def __init__(self, kv: AsyncKV) -> None:
        self._kv = kv

    async def _rows(self) -> list[dict]:
        raw = await self._kv.get(LEDGER_KEY)
        if not raw:
            return []
        try:
            rows = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            raise KeyStoreError("渡した相手の控えを読めません") from None
        if not isinstance(rows, list):
            raise KeyStoreError("渡した相手の控えを読めません")
        return rows

    async def entries(self, device_uuid: Optional[str] = None) -> list[dict]:
        rows = await self._rows()
        if device_uuid is None:
            return rows
        return [r for r in rows if r.get("device_uuid") == device_uuid.lower()]

    async def record(self, device_uuid: str, to: str, role: str, via: str, at: Optional[float] = None) -> dict:
        row = {"device_uuid": device_uuid.lower(), "to": (to or "").strip()[:NAME_MAX], "role": role,
               "via": via, "at": time.time() if at is None else at}
        rows = await self._rows()
        if await self._kv.set(LEDGER_KEY, json.dumps(rows + [row], ensure_ascii=False)) is False:
            raise KeyStoreError("渡した相手の控えを保存できませんでした")
        return row


# ---- 受け取る ----
async def find_sesame(backend, device_uuid: str, seconds: float = SCAN_SECONDS):
    """その UUID の Sesame の広告を、`seconds` 秒まで待つ。見つかれば (広告, 機器のインターフェース)、見つからなければ (None, None)。"""
    found: dict = {}
    seen = asyncio.Event()

    def on_adv(adv, dev):
        if adv.device_uuid is not None and str(adv.device_uuid) == device_uuid.lower():
            found["adv"], found["dev"] = adv, dev
            seen.set()

    scanner = backend.make_scanner(on_adv)
    await scanner.start()
    try:
        await asyncio.wait_for(seen.wait(), seconds)
    except asyncio.TimeoutError:
        pass
    finally:
        await scanner.stop()
    return found.get("adv"), found.get("dev")


async def receive_key(backend, keystore: KeyStore, handoff: Handoff, *, name: str = "", history_tag: str = "",
                      seconds: float = SCAN_SECONDS, on_log: Optional[Callable[[str], None]] = None) -> DeviceKey:
    """受け取った鍵で、その Sesame にログインできたときだけ保存する(R-CM-13)。できなければ何も保存せず ReceiveError。

    同じ Sesame に同じ鍵が既にあるときは、役を落とさない(管理者の鍵は管理者のまま)。別の鍵(リセット後に渡し直された物)は
    ログインできれば置き換え、役は渡された役にする。保存は keystore の追記・置き換えで、途中で失敗すれば元の鍵は残る。
    """
    adv, dev = await find_sesame(backend, handoff.device_uuid, seconds)
    if dev is None:
        raise ReceiveError("その Sesame が見つかりません。近づけて、もう一度試してください。保存しません")
    if not adv.is_registered:
        raise ReceiveError("この Sesame は未登録です。受け取る鍵がありません。保存しません")
    conn = backend.make_connection(dev, on_log=on_log, history_tag=history_tag or "app")
    try:
        await conn.connect()
        await conn.device.login(handoff.secret)
    except SesameError:
        raise ReceiveError("ログインできませんでした。鍵が違うか、リセットされたか、別の端末がつないでいるかもしれません。保存しません") from None
    except Exception as e:
        raise ReceiveError(f"Sesame につなげませんでした。保存しません({type(e).__name__})") from None
    finally:
        try:
            await conn.disconnect()
        except Exception:
            pass
    existing = await keystore.get(handoff.device_uuid)
    same = existing is not None and existing.secret_hex == handoff.secret.hex()
    role = ROLE_ADMIN if (same and existing.is_admin) else handoff.role
    key = DeviceKey(device_uuid=handoff.device_uuid, model_id=adv.model_id, secret_hex=handoff.secret.hex(),
                    name=(name or "").strip() or (existing.name if existing and existing.name else adv.model_name),
                    address=adv.address, registered_at=existing.registered_at if same else time.time(), role=role)
    await keystore.put(key)
    return key
