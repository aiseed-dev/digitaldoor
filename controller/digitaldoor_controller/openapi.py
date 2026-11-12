"""公開 API の定義 — 扉の HTTP のインターフェースの全部を OpenAPI 3.1 で書く(仕様書 5.5、R-DV-11)。

定義にあるインターフェースと扉が受けるインターフェースは一致させる。インターフェースを足すときは、ここと server.py を一緒に直す。
バージョン 1 の間はインターフェースと欄を足すだけにし、足したらバージョンの二つ目の数を上げる。今あるインターフェースの形と意味を変えるときはバージョン 2 にする。
"""
from __future__ import annotations

API_VERSION = "1.3.0"   # 1.0.0: 状態、命令、出来事、記録、資格情報、信頼の一覧 / 1.1.0: 定義、配信、故障注入
                        # 1.2.0: 故障注入に、錠への通信が途中で切れる、配信が途中で切れる
                        # 1.3.0: 失効の一覧(/revocation と、/credential の欄 revocation)

_OBJ = {"type": "object"}
_ENTRY = {
    "type": "object",
    "description": "監査の記録の一件(仕様書 第4章)",
    "properties": {k: {"type": t} for k, t in [
        ("seq", "integer"), ("at", "number"), ("synced", "boolean"), ("kind", "string"), ("event", "string"),
        ("result", "string"), ("subject", "string"), ("route", "string"), ("detail", "string"),
        ("prev_hash", "string"), ("hash", "string"), ("signature", "string")]},
}
_STATE = {
    "type": "object",
    "properties": {"locked": {"type": "boolean"}, "mode": {"type": "string", "enum": ["通常", "火災", "停電", "通行"]},
                   "door_open": {"type": "boolean"}, "bolt_out": {"type": "boolean"}, "fire": {"type": "boolean"},
                   "mains": {"type": "boolean"}, "now": {"type": "number"}, "synced": {"type": "boolean"},
                   "audit_seq": {"type": "integer"}},
}
_RESULT = {"type": "object", "properties": {"result": {"type": "string"}}}
_SIGNED = {"type": "object", "description": "署名つきの包み {body: 署名したバイト列そのものの JSON の文字列, sig: Ed25519 の署名の 16 進}",
           "properties": {"body": {"type": "string"}, "sig": {"type": "string"}}}
_REVOCATION_RESULT = {"type": "string", "enum": ["受理", "同じ", "拒否"]}
_FAULT = {
    "type": "object",
    "properties": {"lock": {"type": "string", "enum": ["正常", "応答しない", "途中で切断"]},
                   "clock_offset_s": {"type": "number"}, "failures": {"type": "integer"},
                   "actual_bolt_out": {"type": "boolean", "description": "錠の本当の位置(試験の窓。判断には使わない)"},
                   "cut": {"type": "integer", "description": "events に切断を指示したとき、切った配信の数"}},
}


def _ok(schema: dict, text: str = "成功") -> dict:
    return {"200": {"description": text, "content": {"application/json": {"schema": schema}}}}


def _body(schema: dict) -> dict:
    return {"required": False, "content": {"application/json": {"schema": schema}}}


_LOOPBACK = {"403": {"description": "同じコンピューターの相手でない。拒否を記録に残す"}}


def document() -> dict:
    p = {
        "/openapi.json": {"get": {"summary": "この定義", "responses": _ok(_OBJ)}},
        "/state": {"get": {"summary": "いまの状態", "responses": _ok(_STATE)}},
        "/lock": {"post": {"summary": "上位からの施錠(同じコンピューターの相手だけ。Matter の橋のため)",
                           "requestBody": _body({"type": "object", "properties": {"by": {"type": "string"}}}),
                           "responses": {**_ok(_RESULT), **_LOOPBACK}}},
        "/unlock": {"post": {"summary": "上位からの解錠(同じコンピューターの相手だけ。Matter の橋のため)",
                             "requestBody": _body({"type": "object", "properties": {"by": {"type": "string"}}}),
                             "responses": {**_ok(_RESULT), **_LOOPBACK}}},
        "/auth": {"post": {"summary": "リーダーからの認証結果(R-CT-19)",
                           "requestBody": _body({"type": "object", "properties": {
                               "subject": {"type": "string"}, "route": {"type": "string"},
                               "valid": {"type": "boolean"}, "reason": {"type": "string"},
                               "duress": {"type": "boolean"}}}),
                           "responses": _ok({"allOf": [_RESULT, _STATE]})}},
        "/event": {"post": {"summary": "建物と扉の入力(火災、電源、開閉、非常手段、改ざん)",
                            "requestBody": _body({"type": "object", "properties": {
                                "fire": {"type": "boolean"}, "mains": {"type": "boolean"},
                                "door_open": {"type": "boolean"}, "manual": {"type": "string"},
                                "tamper": {"type": "string"}}}),
                            "responses": _ok(_STATE)}},
        "/time": {"post": {"summary": "時刻の同期",
                           "requestBody": _body({"type": "object", "properties": {"now": {"type": "number"}}}),
                           "responses": _ok(_STATE)}},
        "/credential": {"post": {"summary": "発行者の署名つき資格情報(仕様書 5.2a)",
                                 "requestBody": _body({"type": "object", "properties": {
                                     "credential": _OBJ, "route": {"type": "string"},
                                     "revocation": {**_SIGNED, "description": "一緒に運ぶ失効の一覧(任意。仕様書 5.2c)。"
                                                                              "資格情報より先に受ける"}}}),
                                 "responses": _ok({"allOf": [_RESULT, _STATE, {"type": "object", "properties": {
                                     "revocation": {**_REVOCATION_RESULT,
                                                    "description": "一緒に運んだ一覧の結果。入れなかったときは欄が無い"}}}]})}},
        "/trust": {
            "get": {"summary": "信頼の一覧(公開鍵と範囲。秘密は無い)", "responses": _ok(_OBJ)},
            "post": {"summary": "管理者の署名つきの信頼の一覧の変更",
                     "requestBody": _body({"type": "object", "properties": {"request": _OBJ}}),
                     "responses": _ok({"type": "object", "properties": {
                         "result": {"type": "string", "enum": ["受理", "拒否"]}}})},
        },
        "/revocation": {
            "get": {"summary": "発行者ごとの失効の状態(秘密は無い)",
                    "responses": _ok({"type": "object", "additionalProperties": {"type": "object", "properties": {
                        "generation": {"type": "integer"}, "issued_at": {"type": "integer"},
                        "revoked": {"type": "array", "items": {"type": "string"}}}}})},
            "post": {"summary": "発行者の署名つきの失効の一覧(仕様書 5.2c)。list の無い要求は形式が違うの拒否として記録する",
                     "requestBody": _body({"type": "object", "properties": {"list": _SIGNED}}),
                     "responses": _ok({"type": "object", "properties": {"result": _REVOCATION_RESULT}})},
        },
        "/audit": {"get": {"summary": "監査の記録の差分(連番が since より後)",
                           "parameters": [{"name": "since", "in": "query", "schema": {"type": "integer"}}],
                           "responses": _ok({"type": "array", "items": _ENTRY})}},
        "/verify": {"get": {"summary": "連鎖の検査", "responses": _ok({"type": "object", "properties": {
            "ok": {"type": "boolean"}, "count": {"type": "integer"}, "why": {"type": "string"}}})}},
        "/events": {"get": {
            "summary": "出来事の配信(WebSocket)",
            "description": "WebSocket に切り替え、連番が since より後の記録を一件ずつ、/audit の一件と同じ形の JSON の"
                           "テキストの文で送る。追いついた後は増えた物を順に送る。つなぎ直すときは受け取った最後の連番を since に渡す",
            "parameters": [{"name": "since", "in": "query", "schema": {"type": "integer"}}],
            "responses": {"101": {"description": "WebSocket に切り替えた"},
                          "426": {"description": "Upgrade の無い要求(差分だけなら /audit)"},
                          "503": {"description": "同時に受ける相手が上限に達した"}}}},
        "/fault": {
            "get": {"summary": "今の故障注入", "x-dev-only": True, "responses": _ok(_FAULT)},
            "post": {"summary": "故障注入(開発のときだけ。同じコンピューターの相手だけ)", "x-dev-only": True,
                     "requestBody": _body({"type": "object", "additionalProperties": False, "properties": {
                         "lock": {"type": "string", "enum": ["正常", "応答しない", "途中で切断"]},
                         "clock_offset_s": {"type": "number"},
                         "events": {"type": "string", "enum": ["切断"]}}}),
                     "responses": {**_ok(_FAULT), **_LOOPBACK, "400": {"description": "無い項目か許されない値。何も変えない"},
                                   "404": {"description": "開発でない扉"}}},
        },
    }
    return {
        "openapi": "3.1.0",
        "info": {"title": "digitaldoor 扉のコントローラ", "version": API_VERSION,
                 "description": "扉コントローラ仕様書(https://door.aiseed.dev)の HTTP のインターフェース。応答には API-Version の見出しが付く",
                 "license": {"name": "Apache-2.0", "identifier": "Apache-2.0"}},
        "servers": [{"url": "http://127.0.0.1:8797"}],
        "paths": p,
    }
