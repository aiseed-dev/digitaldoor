"""伝票 — 監査の記録を、署名つきの伝票として箱(フォルダ)へ出す。受け手は公開鍵だけで検べる。

一件の記録を一つのファイル `<扉>-<連番10桁>.json` にする。中身は
format(digitaldoor-audit-slip/1)、door、alg(Ed25519)、key_id(公開鍵の SHA-256 の先頭 16 桁)、entry(記録の各欄)。
署名は記録のハッシュの 16 進の文字列に付く。受け手は entry から hash と signature を除いた欄を名前の順の JSON にして
SHA-256 を取り、hash と比べ、据付のときに台帳へ写した公開鍵で署名を検べる。伝票の中の鍵の識別は照合に使うだけで、信じない。

どこまで出したかは装置の側(export.cursor)に持つ。箱が無い間は何も出さず、戻ったら続きから出す。
箱の側で取り込んだ伝票を消しても、装置の記録は変わらない。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict
from pathlib import Path

from .audit import Audit, Ed25519Verifier, Entry, verify_chain

FORMAT = "digitaldoor-audit-slip/1"


def key_id(public: bytes) -> str:
    return hashlib.sha256(public).hexdigest()[:16]


def _read_cursor(cursor: Path) -> int:
    try:
        return int(cursor.read_text(encoding="ascii").strip() or 0)
    except FileNotFoundError:
        return 0


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def export(audit: Audit, box: Path | str, door: str, cursor: Path | str) -> int:
    """出していない記録を伝票にして箱へ出し、出した件数を返す。箱が無いか書けないときは 0 で、続きは次の回に出す。"""
    public_key = getattr(audit.signer, "public_key", None)
    if public_key is None:
        raise ValueError("この署名は受け手が公開鍵で検べられません(HMAC)。伝票は Ed25519 の置き場から出します")
    box, cursor = Path(box), Path(cursor)
    if not box.is_dir():
        return 0
    head = {"format": FORMAT, "door": door, "alg": audit.signer.alg, "key_id": key_id(public_key())}
    done = 0
    for e in audit.since(_read_cursor(cursor)):
        slip = {**head, "entry": asdict(e)}
        try:
            _write_atomic(box / f"{door}-{e.seq:010d}.json", json.dumps(slip, ensure_ascii=False, indent=1).encode("utf-8"))
        except OSError:
            break
        _write_atomic(cursor, str(e.seq).encode("ascii"))
        done += 1
    return done


def check_box(box: Path | str, door: str, public: bytes) -> tuple[bool, int, str]:
    """箱にある扉の伝票を、連番の順に公開鍵で検べる。(合否, 件数か止まった連番, 理由)。"""
    entries = []
    name = re.compile(re.escape(door) + r"-\d{10}\.json")
    for f in sorted(f for f in Path(box).iterdir() if name.fullmatch(f.name)):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            if d.get("format") != FORMAT or d.get("door") != door or d.get("alg") != Ed25519Verifier.alg:
                return False, len(entries), f"形式が違います: {f.name}"
            if d.get("key_id") != key_id(public):
                return False, len(entries), "鍵が違います"
            entries.append(Entry(**d["entry"]))
        except (ValueError, TypeError, KeyError):
            return False, len(entries), f"形式が違います: {f.name}"
    if not entries:
        return True, 0, ""
    first = entries[0]
    prev = "" if first.seq == 1 else first.prev_hash
    return verify_chain(entries, Ed25519Verifier(public), start=first.seq, prev=prev)
