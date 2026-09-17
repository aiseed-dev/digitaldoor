"""台帳 — 追記だけの伝票の束。

構造で保証するのは三つだけ: 伝票を運ぶ・記録を変えない・持ち主以外に書かせない。
一枚の伝票は自己完結した AsciiDoc(表題+属性+項目)で、書いたら変えない(ファイルは読み取り専用、
訂正は新しい伝票で前の番号を根拠に示す)。SQLite は索引にすぎず、正は .adoc ファイル。
各伝票は前の伝票のハッシュを持ち、連鎖が切れれば改ざん・削除・挿入が分かる。
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import forms as Y

_ATTR = re.compile(r"^:([^:]+):\s*(.*)$")
_ITEM = re.compile(r"^([^:]+?)::\s?(.*)$")


@dataclass
class Slip:
    id: str
    form: str
    issued_at: str
    issuer: str
    to: str
    basis: list[str]
    values: dict[str, str]
    prev_hash: str
    hash: str
    path: Path

    def __getitem__(self, name: str) -> str:
        return self.values.get(name, "")


def _one_line(v: str) -> str:
    return " ".join(str(v).splitlines()).strip()


def render(form: str, did: str, issuer: str, to: str, basis: list[str], issued_at: str,
           prev_hash: str, values: dict[str, str]) -> str:
    lines = [
        f"= {form} {did}",
        f":様式: {form}",
        f":番号: {did}",
        f":発行者: {issuer}",
        f":宛先: {to}",
        f":発行日時: {issued_at}",
        f":根拠: {', '.join(basis)}",
        f":前ハッシュ: {prev_hash}",
        "",
    ]
    for k, v in values.items():
        lines.append(f"{k}:: {_one_line(v)}")
    return "\n".join(lines) + "\n"


def parse_denpyo(text: str) -> tuple[dict[str, str], dict[str, str]]:
    attrs: dict[str, str] = {}
    values: dict[str, str] = {}
    for line in text.splitlines():
        s = line.rstrip()
        if s.startswith("= ") or not s:
            continue
        if (m := _ATTR.match(s)):
            attrs[m.group(1)] = m.group(2)
            continue
        if (m := _ITEM.match(s)):
            values[m.group(1).strip()] = m.group(2).strip()
    return attrs, values


class Ledger:
    """一つの金庫(ディレクトリ)に対する台帳。"""

    def __init__(self, root: Path | str, *, roles: dict[str, set[str]] | None = None,
                 form_set: dict[str, Y.FormSpec] | None = None) -> None:
        self.root = Path(root)
        (self.root / "伝票").mkdir(parents=True, exist_ok=True)
        self.roles = roles or {}
        self.form_set = form_set or Y.load_all()
        self._lock = threading.Lock()
        self.db = sqlite3.connect(str(self.root / "ledger.sqlite"), check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        with self.db:
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS slip("
                "seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL, form TEXT NOT NULL,"
                "issued_at TEXT NOT NULL, issuer TEXT NOT NULL, \"to\" TEXT, path TEXT NOT NULL,"
                "prev_hash TEXT NOT NULL, hash TEXT NOT NULL)")
            self.db.execute("CREATE TABLE IF NOT EXISTS atai(id TEXT NOT NULL, name TEXT NOT NULL, value TEXT)")
            self.db.execute("CREATE INDEX IF NOT EXISTS atai_name ON atai(name, value)")

    # ---- 権限(持ち主以外に書かせない) ----
    def may_write(self, y: Y.FormSpec, issuer: str) -> bool:
        allowed = y.attrs.get("書ける")
        if not allowed:
            return True
        names = {a.strip() for a in allowed.split(",") if a.strip()}
        if issuer in names:
            return True
        return bool(self.roles.get(issuer, set()) & names)

    # ---- 追記 ----
    def append(self, yoshiki_name: str, values: dict[str, object], *, issuer: str, to: str = "",
               basis: list[str] | tuple[str, ...] = ()) -> Slip:
        y = self.form_set.get(yoshiki_name)
        if y is None:
            raise ValueError(f"様式がありません: {yoshiki_name}")
        if not self.may_write(y, issuer):
            raise PermissionError(f"{issuer} は様式 {y.name} に書けません(書ける: {y.attrs.get('書ける')})")
        clean, problems = Y.validate(y, values)
        if problems:
            raise ValueError("様式に合いません: " + "; ".join(problems))
        with self._lock:
            row = self.db.execute("SELECT hash FROM slip ORDER BY seq DESC LIMIT 1").fetchone()
            prev_hash = row[0] if row else ""
            now = datetime.now().astimezone()
            for _ in range(20):
                did = now.strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(3)
                if not self.db.execute("SELECT 1 FROM slip WHERE id=?", (did,)).fetchone():
                    break
            issued_at = now.isoformat(timespec="seconds")
            text = render(y.name, did, issuer, to, list(basis), issued_at, prev_hash, clean)
            data = text.encode("utf-8")
            h = hashlib.sha256(data).hexdigest()
            d = self.root / "伝票" / y.name
            d.mkdir(parents=True, exist_ok=True)
            p = d / f"{did}.adoc"
            with open(p, "xb") as f:  # 既存のファイルは決して上書きしない
                f.write(data)
            os.chmod(p, 0o440)
            rel = str(p.relative_to(self.root))
            with self.db:
                self.db.execute(
                    "INSERT INTO slip(id,form,issued_at,issuer,\"to\",path,prev_hash,hash) VALUES (?,?,?,?,?,?,?,?)",
                    (did, y.name, issued_at, issuer, to, rel, prev_hash, h))
                self.db.executemany("INSERT INTO atai(id,name,value) VALUES (?,?,?)",
                                    [(did, k, v) for k, v in clean.items()])
            return Slip(did, y.name, issued_at, issuer, to, list(basis), clean, prev_hash, h, p)

    # ---- 読み(権限があれば自由) ----
    def get(self, did: str) -> Slip:
        row = self.db.execute("SELECT id,form,issued_at,issuer,\"to\",path,prev_hash,hash FROM slip WHERE id=?",
                              (did,)).fetchone()
        if row is None:
            raise KeyError(did)
        p = self.root / row[5]
        attrs, values = parse_denpyo(p.read_text(encoding="utf-8"))
        basis = [b.strip() for b in attrs.get("根拠", "").split(",") if b.strip()]
        return Slip(row[0], row[1], row[2], row[3], row[4] or "", basis, values, row[6], row[7], p)

    def select(self, form: str | None = None, *, where: dict[str, str] | None = None,
               since: str | None = None, until: str | None = None) -> list[Slip]:
        sql, args = "SELECT id FROM slip WHERE 1=1", []
        if form:
            sql += " AND form=?"
            args.append(form)
        if since:
            sql += " AND issued_at>=?"
            args.append(since)
        if until:
            sql += " AND issued_at<?"
            args.append(until)
        sql += " ORDER BY seq"
        out = [self.get(r[0]) for r in self.db.execute(sql, args)]
        if where:
            out = [d for d in out if all(d.values.get(k) == v for k, v in where.items())]
        return out

    def count(self) -> int:
        return self.db.execute("SELECT count(*) FROM slip").fetchone()[0]

    # ---- 検査(連鎖) ----
    def verify_chain(self) -> tuple[bool, int, str]:
        prev, n = "", 0
        for did, rel, prev_hash, h in self.db.execute("SELECT id,path,prev_hash,hash FROM slip ORDER BY seq"):
            p = self.root / rel
            if not p.exists():
                return False, n, f"{did}: ファイルがありません"
            data = p.read_bytes()
            if hashlib.sha256(data).hexdigest() != h:
                return False, n, f"{did}: 内容が変わっています"
            if prev_hash != prev:
                return False, n, f"{did}: 連鎖が切れています"
            attrs, _ = parse_denpyo(data.decode("utf-8"))
            if attrs.get("前ハッシュ", "") != prev_hash:
                return False, n, f"{did}: 前ハッシュの記載が索引と違います"
            prev, n = h, n + 1
        return True, n, ""
