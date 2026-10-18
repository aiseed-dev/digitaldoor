"""発行者の署名つき資格情報と、信頼の一覧の変更の要求(扉コントローラ仕様書 5.2a)。

発行者の側: `issue`(資格情報を作る)、`trust_change`(管理者が変更の要求を作る)。試験と発行者の実装の見本。
扉の側: `verify_credential`(資格情報を確かめる)、`verify_trust_change`(変更の要求を確かめる)。

資格情報は `{"body": "<署名したバイト列そのもの。UTF-8 の JSON の文字列>", "sig": "<Ed25519 の署名の 16 進>"}`。
扉は `body` の文字列を UTF-8 にしたバイト列そのもので署名を検べてから、中を読む。並べ直しや書き直しはしない。

確かめる順番(最初に外れた所で拒む): 1 形、2 発行者が一覧にあるか、3 署名、4 この扉が `doors` と一覧の範囲の両方にあるか、
5 時計が同期済みか、6 期限の内か、7 一回限りなら未使用か。

信頼の一覧は `{issuer: {"public_key": "<16 進>", "doors": [扉の名前, ...]}}`。issuer は公開鍵の識別(`slips.key_id`)。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .audit import Ed25519Signer, Ed25519Verifier
from .slips import key_id

CREDENTIAL_FORMAT = "digitaldoor-credential/1"
TRUST_CHANGE_FORMAT = "digitaldoor-trust-change/1"

# 拒否の理由の言葉(記録の result に入る)
BAD_FORM = "形式が違う"
UNKNOWN_ISSUER = "知らない発行者"
BAD_SIGNATURE = "署名が合わない"
OUT_OF_RANGE = "範囲の外"
UNSYNCED = "時刻が未同期"
OUT_OF_PERIOD = "期限の外"
ALREADY_USED = "使用済み"
NO_ADMIN_KEY = "管理者の鍵が無い"
WRONG_DOOR = "扉の名前が違う"


# ---- 発行者の側 ----
def issue(signer: Ed25519Signer, *, cred_id: str, subject: str, doors: list[str], not_before: int, not_after: int,
          once: bool = False) -> dict:
    """資格情報を作る。`body` の文字列を UTF-8 にしたバイト列に署名する。"""
    body = json.dumps({
        "format": CREDENTIAL_FORMAT,
        "issuer": key_id(signer.public_key()),
        "id": cred_id,
        "subject": subject,
        "doors": list(doors),
        "not_before": not_before,
        "not_after": not_after,
        "once": once,
    }, ensure_ascii=False)
    return {"body": body, "sig": signer.sign(body.encode("utf-8"))}


def trust_change(admin_signer: Ed25519Signer, *, door: str, action: str, issuer_public_key: bytes,
                 doors: list[str] | None = None, req_id: str) -> dict:
    """管理者の署名つきの、信頼の一覧の変更の要求を作る。action は add か remove。add のときは doors(範囲)が要る。"""
    if action not in ("add", "remove"):
        raise ValueError("action は add か remove です")
    body: dict[str, Any] = {"format": TRUST_CHANGE_FORMAT, "door": door, "action": action,
                            "issuer": key_id(issuer_public_key), "id": req_id}
    if action == "add":
        if doors is None:
            raise ValueError("add には doors が要ります")
        body["public_key"] = issuer_public_key.hex()
        body["doors"] = list(doors)
    text = json.dumps(body, ensure_ascii=False)
    return {"body": text, "sig": admin_signer.sign(text.encode("utf-8"))}


# ---- 扉の側: 資格情報 ----
@dataclass
class Verdict:
    """資格情報を確かめた結果。reason が空なら通った。detail と subject は記録に残す物。"""

    reason: str = ""
    body: dict = field(default_factory=dict)
    detail: str = ""
    subject: str = ""

    @property
    def ok(self) -> bool:
        return not self.reason

    @property
    def once(self) -> bool:
        return bool(self.body.get("once"))

    @property
    def cred_id(self) -> str:
        return self.body["id"]


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_str_list(v: Any) -> bool:
    return isinstance(v, list) and all(isinstance(x, str) for x in v)


def _parse_pair(obj: Any) -> tuple[str, str, dict] | None:
    """{"body": 文字列, "sig": 文字列} で、body が JSON の辞書なら (body, sig, 中身)。そうでなければ None。"""
    if not isinstance(obj, dict) or not isinstance(obj.get("body"), str) or not isinstance(obj.get("sig"), str):
        return None
    try:
        inner = json.loads(obj["body"])
        obj["body"].encode("utf-8")
    except (ValueError, UnicodeEncodeError):
        return None
    return (obj["body"], obj["sig"], inner) if isinstance(inner, dict) else None


def _credential_form_ok(b: dict) -> bool:
    return (b.get("format") == CREDENTIAL_FORMAT
            and all(isinstance(b.get(k), str) for k in ("issuer", "id", "subject"))
            and _is_str_list(b.get("doors"))
            and _is_int(b.get("not_before")) and _is_int(b.get("not_after"))
            and isinstance(b.get("once"), bool))


def _verifier(public_hex: Any) -> Ed25519Verifier | None:
    try:
        return Ed25519Verifier(bytes.fromhex(public_hex))
    except (ValueError, TypeError):
        return None


def verify_credential(credential: Any, *, door: str, trust: dict, now: float, synced: bool,
                      used_ids: set[str] | frozenset[str]) -> Verdict:
    """資格情報を、仕様の順番で確かめる。最初に外れた所の理由を返す。"""
    parsed = _parse_pair(credential)
    if parsed is None or not _credential_form_ok(parsed[2]):
        return Verdict(BAD_FORM)
    text, sig, body = parsed
    detail = f"issuer={body['issuer']} id={body['id']}"
    entry = trust.get(body["issuer"])
    if not isinstance(entry, dict):
        return Verdict(UNKNOWN_ISSUER, body, detail)
    verifier = _verifier(entry.get("public_key"))
    if verifier is None or not verifier.verify(text.encode("utf-8"), sig):
        return Verdict(BAD_SIGNATURE, body, detail)
    scope = entry.get("doors")
    if door not in body["doors"] or not isinstance(scope, list) or door not in scope:
        return Verdict(OUT_OF_RANGE, body, detail, body["subject"])
    if not synced:
        return Verdict(UNSYNCED, body, detail, body["subject"])
    if not body["not_before"] <= now <= body["not_after"]:
        return Verdict(OUT_OF_PERIOD, body, detail, body["subject"])
    if body["once"] and body["id"] in used_ids:
        return Verdict(ALREADY_USED, body, detail, body["subject"])
    return Verdict("", body, detail, body["subject"])


# ---- 扉の側: 信頼の一覧の変更 ----
def _change_form_ok(b: dict) -> bool:
    if (b.get("format") != TRUST_CHANGE_FORMAT or b.get("action") not in ("add", "remove")
            or not all(isinstance(b.get(k), str) for k in ("door", "issuer", "id"))):
        return False
    if b["action"] == "remove":
        return True
    if not isinstance(b.get("public_key"), str) or not _is_str_list(b.get("doors")):
        return False
    try:
        public = bytes.fromhex(b["public_key"])
    except ValueError:
        return False
    return len(public) == 32 and key_id(public) == b["issuer"]


def verify_trust_change(request: Any, *, door: str, admin_key: bytes | None,
                        used_ids: set[str] | frozenset[str]) -> tuple[str, dict]:
    """変更の要求を確かめる。(理由, 中身)。理由が空なら受けてよい。
    順番: 形、管理者の鍵、署名、扉の名前、使用済み。"""
    parsed = _parse_pair(request)
    if parsed is None or not _change_form_ok(parsed[2]):
        return BAD_FORM, {}
    text, sig, body = parsed
    if admin_key is None:
        return NO_ADMIN_KEY, body
    if not Ed25519Verifier(admin_key).verify(text.encode("utf-8"), sig):
        return BAD_SIGNATURE, body
    if body["door"] != door:
        return WRONG_DOOR, body
    if body["id"] in used_ids:
        return ALREADY_USED, body
    return "", body


def apply_change(trust: dict, body: dict) -> dict:
    """変更を当てた新しい一覧を返す(元は変えない)。remove で無い発行者を消すときは、そのまま。"""
    new = {k: {"public_key": v["public_key"], "doors": list(v["doors"])} for k, v in trust.items()}
    if body["action"] == "add":
        new[body["issuer"]] = {"public_key": body["public_key"], "doors": list(body["doors"])}
    else:
        new.pop(body["issuer"], None)
    return new


# ---- 記録の詳細(受けた変更) ----
_AFTER_ID = re.compile(r" action=\S+ issuer=\S+ 前 ")


def change_detail(body: dict, before: dict, after: dict) -> str:
    """受けた変更の記録の詳細。識別と、前と新しい一覧を、記録だけから読み戻せる形で入れる。"""
    return (f"id={json.dumps(body['id'])} action={body['action']} issuer={body['issuer']} "
            f"前 {json.dumps(before, sort_keys=True)} 新 {json.dumps(after, sort_keys=True)}")


def parse_change_detail(detail: str) -> tuple[str, dict] | None:
    """change_detail で書いた詳細から (要求の識別, 新しい一覧) を読み戻す。読めなければ None。"""
    if not detail.startswith("id="):
        return None
    dec = json.JSONDecoder()
    try:
        req_id, i = dec.raw_decode(detail, 3)
        m = _AFTER_ID.match(detail, i)
        if not m or not isinstance(req_id, str):
            return None
        _, j = dec.raw_decode(detail, m.end())
        if detail[j:j + 3] != " 新 ":
            return None
        after, _ = dec.raw_decode(detail, j + 3)
    except ValueError:
        return None
    return (req_id, after) if isinstance(after, dict) else None
