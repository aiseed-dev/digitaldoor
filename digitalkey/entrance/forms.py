"""様式プロファイル(AsciiDoc)を読み、検証・DDL・記入用テキストを派生させる。

様式の唯一の定義は AsciiDoc のラベル付きリスト ``項目:: [制約] 説明`` である。
制約の語彙は SQL(varchar(n) / integer / date / not null / in (...) / check (...))。
独自の型体系は作らない。表示の都合(記入例など)は属性にせず、説明文に日本語で書く。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, time
from importlib import resources
from pathlib import Path

_TITLE = re.compile(r"^=\s+(.+?)\s*$")
_ATTR = re.compile(r"^:([^:]+):\s*(.*)$")
_ITEM = re.compile(r"^(?P<name>[^\s:\[/][^:]*?)::\s*(?:\[(?P<cons>[^\]]*)\])?\s*(?P<desc>.*)$")
_ERA = {"令和": 2018, "平成": 1988, "昭和": 1925, "R": 2018, "H": 1988, "S": 1925}
_TYPES = {"text", "integer", "int", "numeric", "real", "date", "datetime", "time", "boolean"}


@dataclass
class Item:
    """様式の一項目。"""

    name: str
    sql_type: str = "text"
    max_len: int | None = None
    not_null: bool = False
    choices: list[str] | None = None
    check: str | None = None
    references: str | None = None
    description: str = ""
    raw: str = ""


@dataclass
class FormSpec:
    name: str
    title: str
    attrs: dict[str, str] = field(default_factory=dict)
    fields: list[Item] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def item(self, name: str) -> Item | None:
        for k in self.fields:
            if k.name == name:
                return k
        return None

    @property
    def names(self) -> list[str]:
        return [k.name for k in self.fields]


def _split_top(s: str) -> list[str]:
    """括弧の外のカンマで分ける。"""
    out, cur, depth = [], [], 0
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    tail = "".join(cur).strip()
    if tail:
        out.append(tail)
    return [p for p in out if p]


def _apply_constraints(k: Item, cons: str) -> None:
    parts = _split_top(cons)
    if not parts:
        return
    head = parts[0].strip().lower()
    m = re.match(r"^(varchar|char)\s*\(\s*(\d+)\s*\)$", head)
    if m:
        k.sql_type, k.max_len = "varchar", int(m.group(2))
        rest = parts[1:]
    elif head in _TYPES:
        k.sql_type = {"int": "integer", "real": "numeric"}.get(head, head)
        rest = parts[1:]
    else:
        rest = parts
    for p in rest:
        pl = p.lower()
        if pl == "not null":
            k.not_null = True
        elif pl.startswith("in ") or pl.startswith("in("):
            inner = p[p.index("("):].strip()[1:-1]
            k.choices = [c.strip().strip("'\"") for c in _split_top(inner)]
        elif pl.startswith("check"):
            k.check = p[p.index("("):].strip()[1:-1].strip()
        elif pl.startswith("references"):
            k.references = p[len("references"):].strip()
        elif pl.startswith("default"):
            pass
        else:
            raise ValueError(f"{k.name}: 不明な制約 {p!r}")


def parse(text: str) -> FormSpec:
    """様式プロファイルの本文を読む。"""
    title, attrs, items, notes = "", {}, [], []
    cur: Item | None = None
    for line in text.splitlines():
        s = line.rstrip()
        if not title and (m := _TITLE.match(s)):
            title = m.group(1)
            continue
        if s.strip().startswith("//"):
            continue
        if (m := _ITEM.match(s)):
            k = Item(name=m.group("name").strip(), description=m.group("desc").strip(),
                        raw=m.group("cons") or "")
            _apply_constraints(k, k.raw)
            items.append(k)
            cur = k
            continue
        if (m := _ATTR.match(s)):
            attrs[m.group(1).strip()] = m.group(2).strip()
            continue
        if not s.strip():
            cur = None
            continue
        if cur is not None and line[:1].isspace():
            cur.description = (cur.description + " " + s.strip()).strip()
            continue
        notes.append(s.strip())
    name = attrs.get("様式") or title
    if not name:
        raise ValueError("様式名がありません(= 表題 か :様式: 属性)")
    if not items:
        raise ValueError(f"{name}: 項目がありません")
    return FormSpec(name=name, title=title or name, attrs=attrs, fields=items, notes=notes)


def load_file(path: Path | str) -> FormSpec:
    return parse(Path(path).read_text(encoding="utf-8"))


def load_all(extra_dir: Path | str | None = None) -> dict[str, FormSpec]:
    """同梱の様式と、あれば追加ディレクトリの様式を読む(名前が同じなら追加側が勝つ)。"""
    out: dict[str, FormSpec] = {}
    base = resources.files("digitalkey.entrance").joinpath("forms_data")
    for p in sorted(base.iterdir()):
        if p.name.endswith(".adoc"):
            y = parse(p.read_text(encoding="utf-8"))
            out[y.name] = y
    if extra_dir:
        for p in sorted(Path(extra_dir).glob("*.adoc")):
            y = load_file(p)
            out[y.name] = y
    return out


# ---- 修復(制約から一意に導ける正規化だけ) ----

def _nfkc(v: str) -> str:
    return unicodedata.normalize("NFKC", v).strip()


def repair_date(v: str) -> str | None:
    s = _nfkc(v)
    m = re.match(r"^(令和|平成|昭和|R|H|S)\s*(\d+|元)\s*[年./-]\s*(\d+)\s*[月./-]\s*(\d+)\s*日?$", s)
    if m:
        y = 1 if m.group(2) == "元" else int(m.group(2))
        y += _ERA[m.group(1)]
        out = f"{y:04d}-{int(m.group(3)):02d}-{int(m.group(4)):02d}"
    else:
        m = re.match(r"^(\d{4})\s*[年./-]\s*(\d{1,2})\s*[月./-]\s*(\d{1,2})\s*日?$", s)
        if m:
            out = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        else:
            m = re.match(r"^(\d{4})(\d{2})(\d{2})$", s)
            if not m:
                return None
            out = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    try:
        date.fromisoformat(out)
    except ValueError:
        return None
    return out


def repair_time(v: str) -> str | None:
    s = _nfkc(v).replace("時", ":").replace("分", "")
    m = re.match(r"^(\d{1,2})\s*:\s*(\d{1,2})(?::\d{1,2})?$", s)
    if not m:
        m = re.match(r"^(\d{2})(\d{2})$", s)
        if not m:
            return None
    h, mi = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        return None
    return f"{h:02d}:{mi:02d}"


def repair_datetime(v: str) -> str | None:
    s = _nfkc(v)
    m = re.match(r"^(.+?)[T\s]+(\d{1,2}[:時]\d{1,2}(?:分|:\d{1,2})?)$", s)
    if not m:
        d = repair_date(s)
        return f"{d}T00:00" if d else None
    d, t = repair_date(m.group(1)), repair_time(m.group(2))
    if d is None or t is None:
        return None
    return f"{d}T{t}"


_BOOL = {"true": "true", "false": "false", "1": "true", "0": "false", "はい": "true", "いいえ": "false",
         "あり": "true", "なし": "false", "yes": "true", "no": "false"}


def _check_ok(k: Item, v: str) -> bool:
    """check 制約のうち、数の範囲(between / 比較)だけ評価する。他は評価しない(事後の突合に回す)。"""
    if not k.check or k.sql_type not in ("integer", "numeric"):
        return True
    try:
        x = float(v)
    except ValueError:
        return False
    expr = k.check.replace(k.name, "").strip()
    m = re.match(r"^between\s+(-?[\d.]+)\s+and\s+(-?[\d.]+)$", expr, re.I)
    if m:
        return float(m.group(1)) <= x <= float(m.group(2))
    m = re.match(r"^(>=|<=|>|<|=)\s*(-?[\d.]+)$", expr)
    if m:
        n = float(m.group(2))
        return {">=": x >= n, "<=": x <= n, ">": x > n, "<": x < n, "=": x == n}[m.group(1)]
    return True


def validate(y: FormSpec, values: dict[str, object]) -> tuple[dict[str, str], list[str]]:
    """値を様式に照らす。返り値は (修復後の値, 問題の一覧)。問題があれば受理しない。"""
    clean: dict[str, str] = {}
    problems: list[str] = []
    for k in y.fields:
        raw = values.get(k.name, "")
        v = _nfkc(str(raw)) if raw is not None else ""
        if v == "":
            if k.not_null:
                problems.append(f"{k.name}: 必須です")
            clean[k.name] = ""
            continue
        if k.sql_type == "integer":
            v2 = v.replace(",", "")
            if not re.fullmatch(r"[+-]?\d+", v2):
                problems.append(f"{k.name}: 整数で書いてください")
                clean[k.name] = v
                continue
            v = str(int(v2))
        elif k.sql_type == "numeric":
            try:
                v = str(float(v.replace(",", "")))
            except ValueError:
                problems.append(f"{k.name}: 数で書いてください")
                clean[k.name] = v
                continue
        elif k.sql_type == "date":
            r = repair_date(v)
            if r is None:
                problems.append(f"{k.name}: 日付が読めません({v})")
                clean[k.name] = v
                continue
            v = r
        elif k.sql_type == "datetime":
            r = repair_datetime(v)
            if r is None:
                problems.append(f"{k.name}: 日時が読めません({v})")
                clean[k.name] = v
                continue
            v = r
        elif k.sql_type == "time":
            r = repair_time(v)
            if r is None:
                problems.append(f"{k.name}: 時刻が読めません({v})")
                clean[k.name] = v
                continue
            v = r
        elif k.sql_type == "boolean":
            b = _BOOL.get(v.lower())
            if b is None:
                problems.append(f"{k.name}: はい/いいえ で書いてください")
                clean[k.name] = v
                continue
            v = b
        if k.max_len is not None and len(v) > k.max_len:
            problems.append(f"{k.name}: {k.max_len}文字以内にしてください")
        if k.choices is not None and v not in k.choices:
            problems.append(f"{k.name}: {'/'.join(k.choices)} のいずれかにしてください")
        if not _check_ok(k, v):
            problems.append(f"{k.name}: 条件({k.check})に合いません")
        clean[k.name] = v
    unknown = [n for n in values if n not in {k.name for k in y.fields}]
    for n in unknown:
        problems.append(f"{n}: 様式にない項目です")
    return clean, problems


def parse_kinyu(y: FormSpec, text: str) -> tuple[dict[str, str], list[str]]:
    """記入済みテキスト(``項目: 値`` が一行ずつ)を寛容に読む。全角コロン・空白の揺れは吸収する。"""
    names = {_nfkc(k.name).replace(" ", ""): k.name for k in y.fields}
    values: dict[str, str] = {}
    unknown: list[str] = []
    for line in unicodedata.normalize("NFKC", text).splitlines():
        s = line.strip()
        if not s or s.startswith("=") or s.startswith("//"):
            continue
        m = re.match(r"^([^:]+?)\s*:+\s*(.*)$", s)
        if not m:
            unknown.append(s)
            continue
        label = m.group(1).replace(" ", "")
        if label in names:
            values[names[label]] = m.group(2).strip()
        else:
            unknown.append(s)
    return values, unknown


def kinyu_text(y: FormSpec) -> str:
    """配布用の記入テキスト。"""
    lines = [f"= {y.title}(記入用)", "// 「項目: 値」の形で一行ずつ書く。空欄は空のまま。", ""]
    for k in y.fields:
        hint = []
        if k.not_null:
            hint.append("必須")
        if k.choices:
            hint.append("/".join(k.choices))
        if k.sql_type == "date":
            hint.append("年-月-日")
        if k.sql_type == "datetime":
            hint.append("年-月-日 時:分")
        if k.sql_type == "time":
            hint.append("時:分")
        if k.description:
            hint.append(k.description)
        if hint:
            lines.append("// " + "、".join(hint))
        lines.append(f"{k.name}: ")
    return "\n".join(lines) + "\n"


def ddl(y: FormSpec) -> str:
    """様式から SQLite/PostgreSQL 共通の CREATE TABLE を派生させる(台帳の索引用)。"""
    cols = ['"番号" TEXT PRIMARY KEY']
    tmap = {"varchar": "TEXT", "text": "TEXT", "integer": "INTEGER", "numeric": "REAL", "boolean": "INTEGER"}
    for k in y.fields:
        col = f'"{k.name}" {tmap.get(k.sql_type, "TEXT")}'
        if k.not_null:
            col += " NOT NULL"
        if k.choices:
            opts = ", ".join("'" + c.replace("'", "''") + "'" for c in k.choices)
            col += f' CHECK ("{k.name}" IN ({opts}))'
        if k.check:
            col += f" CHECK ({k.check})"
        cols.append(col)
    return f'CREATE TABLE IF NOT EXISTS "{y.name}" (\n  ' + ",\n  ".join(cols) + "\n);"
