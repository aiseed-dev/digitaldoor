"""発行者の署名つき資格情報と、信頼の一覧の変更の要求と、失効の一覧(扉コントローラ仕様書 5.2a、5.2c)。

発行者の側: `issue`(資格情報を作る)、`trust_change`(管理者が変更の要求を作る)、`revocation_list`(失効の一覧を作る)。
試験と発行者の実装の見本。
扉の側: `verify_credential`(資格情報を確かめる)、`verify_trust_change`(変更の要求を確かめる)、
`verify_revocation`(失効の一覧を確かめる)。

資格情報は `{"body": "<署名したバイト列そのもの。UTF-8 の JSON の文字列>", "sig": "<Ed25519 の署名の 16 進>"}`。
扉は `body` の文字列を UTF-8 にしたバイト列そのもので署名を検べてから、中を読む。並べ直しや書き直しはしない。

確かめる順番(最初に外れた所で拒む): 1 形、2 発行者が一覧にあるか、3 署名、4 この扉が `doors` と一覧の範囲の両方にあるか、
5 失効の一覧にあるか、6 時計が同期済みか、7 期限の内か、8 失効の知らせの古さ、9 一回限りなら未使用か。
5 は時計と期限より前に見るので、失効した資格情報の理由は、時計が未同期でも期限の外でも `失効済み` になる。

失効の一覧も同じ包み方で、発行者の鍵で署名する。`body` は `format`、`issuer`、`generation`(1 以上)、`issued_at`、
`revoked`(失効した資格情報の `id` の列)。全部を送る形で、扉は受理したら、その発行者の前の一覧を丸ごと置き換える。
受ける順番: 1 形、2 発行者が信頼の一覧にあるか、3 署名、4 世代(小さければ `古い世代`、同じなら `同じ`、大きければ受理)。
知らせの古さは、扉が受け取った時刻ではなく、署名の下にある `issued_at` で量る(古い一覧の送り直しで新しくならない)。

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
REVOCATION_FORMAT = "digitaldoor-revocation/1"

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
REVOKED = "失効済み"
STALE_REVOCATION = "失効の知らせが古い"
OLD_GENERATION = "古い世代"
SAME = "同じ"            # 失効の一覧の結果。拒否ではなく、記録もしない


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


def revocation_list(signer: Ed25519Signer, *, generation: int, issued_at: int, revoked: list[str]) -> dict:
    """失効の一覧を作る。その発行者が失効させた資格情報の全部を入れる(差分ではない)。世代が 1 より小さければ ValueError。"""
    if not _is_int(generation) or generation < 1:
        raise ValueError("generation は 1 以上の整数です")
    body = json.dumps({
        "format": REVOCATION_FORMAT,
        "issuer": key_id(signer.public_key()),
        "generation": generation,
        "issued_at": issued_at,
        "revoked": list(revoked),
    }, ensure_ascii=False)
    return {"body": body, "sig": signer.sign(body.encode("utf-8"))}


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


def _is_stale(body: dict, revocations: dict | None, now: float, max_age: float) -> bool:
    """順番の 8。設定が 0 より大きく、その発行者の知らせが無いか古く、資格情報の長さが設定より長いとき。"""
    if max_age <= 0 or body["not_after"] - body["not_before"] <= max_age:
        return False
    held = (revocations or {}).get(body["issuer"])
    return held is None or now - held["issued_at"] > max_age


def verify_credential(credential: Any, *, door: str, trust: dict, now: float, synced: bool,
                      used_ids: set[str] | frozenset[str], revocations: dict | None = None,
                      max_age: float = 0.0) -> Verdict:
    """資格情報を、仕様の順番で確かめる。最初に外れた所の理由を返す。

    revocations は発行者ごとの失効の状態(`{issuer: {"generation", "issued_at", "revoked"}}`)。
    None なら順番の 5 を見ない。max_age が 0 なら順番の 8 を見ない。
    """
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
    held = (revocations or {}).get(body["issuer"])
    if held is not None and body["id"] in held["revoked"]:
        return Verdict(REVOKED, body, detail, body["subject"])
    if not synced:
        return Verdict(UNSYNCED, body, detail, body["subject"])
    if not body["not_before"] <= now <= body["not_after"]:
        return Verdict(OUT_OF_PERIOD, body, detail, body["subject"])
    if _is_stale(body, revocations, now, max_age):
        return Verdict(STALE_REVOCATION, body, detail, body["subject"])
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


# ---- 扉の側: 失効の一覧(仕様書 5.2c) ----
def _revocation_form_ok(b: dict) -> bool:
    """真偽は整数として受けない。ほかの欄は読まない。"""
    return (b.get("format") == REVOCATION_FORMAT and isinstance(b.get("issuer"), str)
            and _is_int(b.get("generation")) and b["generation"] >= 1
            and _is_int(b.get("issued_at")) and _is_str_list(b.get("revoked")))


def verify_revocation(lst: Any, *, trust: dict, current: dict) -> tuple[str, dict]:
    """失効の一覧を確かめる。(理由か `同じ` か空, 中身)。空なら受理してよい。current は変えない。
    順番: 形、発行者、署名、世代。まだ一覧を持たない発行者は、どの世代(1 以上)も受理する。"""
    parsed = _parse_pair(lst)
    if parsed is None or not _revocation_form_ok(parsed[2]):
        return BAD_FORM, {}
    text, sig, body = parsed
    entry = trust.get(body["issuer"])
    if not isinstance(entry, dict):
        return UNKNOWN_ISSUER, body
    verifier = _verifier(entry.get("public_key"))
    if verifier is None or not verifier.verify(text.encode("utf-8"), sig):
        return BAD_SIGNATURE, body
    held = current.get(body["issuer"])
    if held is not None and body["generation"] < held["generation"]:
        return OLD_GENERATION, body
    if held is not None and body["generation"] == held["generation"]:
        return SAME, body                     # 同じ世代は中身を見ない(発行者は世代を使い回さない)
    return "", body


def revocation_state(body: dict) -> dict:
    """扉が持つ発行者ごとの状態。受理した一覧の中身をそのまま持ち、受けた時刻は持たない。"""
    return {"generation": body["generation"], "issued_at": body["issued_at"], "revoked": list(body["revoked"])}


_REVOCATION_DETAIL = re.compile(r"issuer=(\S+) generation=(\d+) issued_at=(-?\d+) revoked=(.*)", re.DOTALL)


def revocation_detail(body: dict) -> str:
    """受理した一覧の記録の詳細。記録だけから状態を読み戻せる形にする。配列は詳細の終わりまで。"""
    return (f"issuer={body['issuer']} generation={body['generation']} issued_at={body['issued_at']} "
            f"revoked={json.dumps(list(body['revoked']), ensure_ascii=False)}")


def parse_revocation_detail(detail: str) -> dict | None:
    """revocation_detail で書いた詳細から {"issuer", "generation", "issued_at", "revoked"} を読み戻す。読めなければ None。"""
    m = _REVOCATION_DETAIL.fullmatch(detail)
    if not m:
        return None
    try:
        revoked = json.loads(m.group(4))
    except ValueError:
        return None
    if not _is_str_list(revoked):
        return None
    return {"issuer": m.group(1), "generation": int(m.group(2)), "issued_at": int(m.group(3)), "revoked": revoked}
