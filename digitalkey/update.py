# SPDX-License-Identifier: AGPL-3.0-or-later
"""更新の配布 — 署名つきの包みを、段階配布の表に沿って当て、失敗したら戻す(使い方.md「更新の配布」、R-DV-30〜40)。

製造者の側:
  digitalkey update keygen --out maker.key                 署名の鍵を作る(権限 600)。公開鍵を JSON で出す
  digitalkey update pack --key maker.key --version 1.2.0 --reason 不具合 --summary "..." --from <置き場> --out x.ddupd
                       [--compat 'matter-controller=1.4,1.5'] ...
  digitalkey update index --key maker.key --serial N --entry 1.2.0:全棟[:2027-12-31] [--halt 1.1.0]
                       [--emergency 1.2.1:誰:なぜ] --out index.json     配布の表(index.json と index.sig)を作る
サーバーの側(設定は update.toml):
  digitalkey update status [--json]                        今のバージョン、サポート期限、互換の警告
  digitalkey update apply --package x.ddupd --index index.json   確かめて当てる(ネットワークの無い現場は USB から同じ命令)
  digitalkey update rollback                               直前のバージョンへ戻す
  digitalkey update notice --package x.ddupd               管理会社へ送る事前の通知の文
  digitalkey update fleet <status の JSON を集めた置き場>     サポートの切れたバージョンで動くサーバーの一覧

update.toml:
  product = "digitalkey"
  root = "/opt/digitaldoor/update"     # releases/、current、backups/、update-log.jsonl を置く
  data = "/var/lib/digitaldoor"         # 当てる前に tar で取るデータの置き場
  stage = "全棟"                         # 開発 | 試験導入棟 | 全棟
  trusted_keys = ["<製造者の公開鍵の 16 進>"]
  check = "systemctl is-active digitaldoor-site"    # 付け替えた後の確かめ(省けば確かめない)
  restart = "systemctl restart digitaldoor-site"    # 付け替えた後に回す命令(省けば回さない)
  notify = "logger -t digitaldoor-update"           # 自動で戻したとき、文を標準入力に渡す命令
  block_on_incompatible = false
  [components]                           # このサーバーの部品のバージョン(互換表と照らす)
  matter-controller = "1.4"

当てる順: 表の署名 → 表の通し番号 → 包みの署名と中身の SHA-256 → 段と止めと緊急 → 互換 → 控え → 展開 →
付け替え → 再起動 → 確かめ → (失敗なら)戻して知らせる。どこで拒んでも、置き場の current は変わらない。
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shlex
import subprocess
import sys
import tarfile
import time
import tomllib
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path, PurePosixPath

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

FORMAT = "digitaldoor-update/1"
INDEX_FORMAT = "digitaldoor-update-index/1"
STAGES = ("開発", "試験導入棟", "全棟")
REASONS = ("不具合", "規格改定", "セキュリティ")
# サーバーの段 → 受ける表の段
ACCEPTS = {"開発": set(STAGES), "試験導入棟": {"試験導入棟", "全棟"}, "全棟": {"全棟"}}


class Refused(Exception):
    """当てない理由。何も変えずに止まる。"""


# ---- 署名 ----
def _pub(hexkey: str) -> Ed25519PublicKey:
    return Ed25519PublicKey.from_public_bytes(bytes.fromhex(hexkey))


def sign(key: Ed25519PrivateKey, data: bytes) -> str:
    return key.sign(data).hex()


def verify(data: bytes, sig: str, trusted: list[str]) -> bool:
    for k in trusted:
        try:
            _pub(k).verify(bytes.fromhex(sig.strip()), data)
            return True
        except (InvalidSignature, ValueError):
            continue
    return False


def read_key(path) -> Ed25519PrivateKey:
    raw = Path(path).read_bytes()
    if len(raw) != 32:
        raise ValueError(f"鍵のファイルの形が違います: {path}")
    return Ed25519PrivateKey.from_private_bytes(raw)


def public_hex(key: Ed25519PrivateKey) -> str:
    from cryptography.hazmat.primitives import serialization
    return key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()


# ---- 製造者の側 ----
def pack(key: Ed25519PrivateKey, *, product: str, version: str, reason: str, summary: str, source: Path,
         out: Path, compat: dict[str, list[str]] | None = None) -> dict:
    """source の下のファイルを全部、包みにする。manifest を返す。"""
    if reason not in REASONS:
        raise ValueError(f"reason は {REASONS} のどれかです")
    files = {}
    for p in sorted(Path(source).rglob("*")):
        if p.is_file():
            files[p.relative_to(source).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    manifest = {"format": FORMAT, "product": product, "version": version, "reason": reason, "summary": summary,
                "files": files, "compat": compat or {}}
    data = json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")
    with tarfile.open(out, "w:gz") as tf:
        _add(tf, "manifest.json", data)
        _add(tf, "manifest.sig", sign(key, data).encode())
        for rel in files:
            tf.add(Path(source) / rel, arcname=f"files/{rel}")
    return manifest


def _add(tf: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size, info.mtime, info.mode = len(data), int(time.time()), 0o644
    tf.addfile(info, io.BytesIO(data))


def make_index(key: Ed25519PrivateKey, *, serial: int, entries: list[dict], out: Path) -> dict:
    """配布の表。entries は {"version", "stage", "halted", "support_until", "emergency"}。"""
    for e in entries:
        if e["stage"] not in STAGES:
            raise ValueError(f"段は {STAGES} のどれかです: {e['stage']}")
    index = {"format": INDEX_FORMAT, "serial": serial, "issued_at": datetime.now().astimezone().isoformat(timespec="seconds"),
             "versions": entries}
    data = json.dumps(index, ensure_ascii=False, sort_keys=True).encode("utf-8")
    Path(out).write_bytes(data)
    sig_path(Path(out)).write_text(sign(key, data))
    return index


def sig_path(index_path: Path) -> Path:
    s = str(index_path)
    return Path(s[:-5] + ".sig" if s.endswith(".json") else s + ".sig")


# ---- サーバーの側 ----
@dataclass
class Config:
    product: str
    root: Path
    data: Path | None
    stage: str
    trusted_keys: list[str]
    check: str = ""
    restart: str = ""
    notify: str = ""
    block_on_incompatible: bool = False
    components: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path) -> "Config":
        raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
        if raw.get("stage") not in STAGES:
            raise ValueError(f"stage は {STAGES} のどれかです")
        keys = raw.get("trusted_keys") or []
        if not keys or not all(isinstance(k, str) and len(k) == 64 for k in keys):
            raise ValueError("trusted_keys に製造者の公開鍵(64 桁の 16 進)を一つ以上書いてください")
        return cls(product=raw.get("product", "digitalkey"), root=Path(raw["root"]),
                   data=Path(raw["data"]) if raw.get("data") else None, stage=raw["stage"], trusted_keys=keys,
                   check=raw.get("check", ""), restart=raw.get("restart", ""), notify=raw.get("notify", ""),
                   block_on_incompatible=bool(raw.get("block_on_incompatible", False)),
                   components={k: str(v) for k, v in raw.get("components", {}).items()})


class Log:
    """更新の記録。一行一件の JSON で、前の行のハッシュを持つ。追記だけ(R-DV-38)。"""

    def __init__(self, path: Path) -> None:
        self.path = path

    def rows(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(l) for l in self.path.read_text(encoding="utf-8").splitlines() if l.strip()]

    def append(self, **row) -> dict:
        rows = self.rows()
        row = {"seq": len(rows) + 1, "at": datetime.now().astimezone().isoformat(timespec="seconds"),
               "prev": rows[-1]["hash"] if rows else "", **row}
        row["hash"] = hashlib.sha256(json.dumps(row, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row

    def verify(self) -> tuple[bool, int, str]:
        prev = ""
        for i, r in enumerate(self.rows(), 1):
            h = r.get("hash")
            body = {k: v for k, v in r.items() if k != "hash"}
            if r.get("seq") != i or r.get("prev") != prev:
                return False, i, "連番か前のハッシュが合いません"
            if hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode()).hexdigest() != h:
                return False, i, "中身が変わっています"
            prev = h
        return True, len(self.rows()), ""


class Updater:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.root = cfg.root
        self.log = Log(self.root / "update-log.jsonl")

    # ---- 今の様子 ----
    def current(self) -> str:
        p = self.root / "current"
        return p.resolve().name if p.is_symlink() else ""

    def previous(self) -> str:
        p = self.root / "previous"
        return p.resolve().name if p.is_symlink() else ""

    def read_index(self, index_path: Path) -> dict:
        """表の署名と通し番号を確かめて返す。前に受けた表より小さい番号は拒む(古い表で戻させない)。"""
        data = Path(index_path).read_bytes()
        sp = sig_path(Path(index_path))
        if not sp.exists() or not verify(data, sp.read_text(), self.cfg.trusted_keys):
            raise Refused("配布の表の署名が合いません")
        index = json.loads(data)
        if index.get("format") != INDEX_FORMAT or not isinstance(index.get("serial"), int):
            raise Refused("配布の表の形が違います")
        seen = self._seen_serial()
        if index["serial"] < seen:
            raise Refused(f"配布の表が古いです(通し番号 {index['serial']} < 受けた {seen})")
        return index

    def _seen_serial(self) -> int:
        p = self.root / "index.serial"
        return int(p.read_text()) if p.exists() else 0

    def status(self, index: dict | None = None) -> dict:
        cur = self.current()
        out = {"product": self.cfg.product, "stage": self.cfg.stage, "version": cur, "previous": self.previous(),
               "index_serial": self._seen_serial(), "support_until": "", "out_of_support": False}
        entry = _entry(index, cur) if index else self._last_entry(cur)
        if entry and entry.get("support_until"):
            out["support_until"] = entry["support_until"]
            out["out_of_support"] = date.fromisoformat(entry["support_until"]) < date.today()
        return out

    def _last_entry(self, version: str) -> dict | None:
        p = self.root / "index.json"
        if p.exists():
            return _entry(json.loads(p.read_text(encoding="utf-8")), version)
        return None

    # ---- 包み ----
    def open_package(self, path: Path) -> tuple[dict, tarfile.TarFile]:
        """包みの署名と中身の SHA-256 を確かめる(R-DV-30)。道の外へ出る名前、知らない名前は拒む。"""
        try:
            tf = tarfile.open(path, "r:gz")
            data = tf.extractfile("manifest.json").read()
            sig = tf.extractfile("manifest.sig").read().decode()
        except (OSError, KeyError, tarfile.TarError, AttributeError):
            raise Refused("包みを読めません") from None
        if not verify(data, sig, self.cfg.trusted_keys):
            raise Refused("包みの署名が合いません")
        m = json.loads(data)
        if m.get("format") != FORMAT or m.get("product") != self.cfg.product:
            raise Refused("このサーバーの物の包みではありません")
        names = {"manifest.json", "manifest.sig"} | {f"files/{r}" for r in m["files"]}
        for member in tf.getmembers():
            if member.isdir():
                continue
            if member.name not in names or not member.isfile():
                raise Refused(f"包みに知らない物があります: {member.name}")
        for rel, want in m["files"].items():
            pp = PurePosixPath(rel)
            if pp.is_absolute() or ".." in pp.parts:
                raise Refused(f"包みの道が外を指しています: {rel}")
            got = hashlib.sha256(tf.extractfile(f"files/{rel}").read()).hexdigest()
            if got != want:
                raise Refused(f"中身が署名と合いません: {rel}")
        return m, tf

    def compat_warnings(self, manifest: dict) -> list[str]:
        """互換表と、このサーバーの部品のバージョンを照らす(R-DV-36)。"""
        out = []
        for comp, ok in manifest.get("compat", {}).items():
            have = self.cfg.components.get(comp)
            if have is None:
                out.append(f"{comp}: このサーバーのバージョンが設定にありません(合うバージョン: {', '.join(ok)})")
            elif have not in ok:
                out.append(f"{comp}: このサーバーは {have}、合うバージョンは {', '.join(ok)}")
        return out

    # ---- 当てる ----
    def apply(self, package: Path, index_path: Path) -> dict:
        t0 = time.monotonic()
        try:
            index = self.read_index(index_path)
            m, tf = self.open_package(package)
            entry = _entry(index, m["version"])
            if entry is None:
                raise Refused(f"配布の表にバージョン {m['version']} がありません")
            if entry.get("halted"):
                raise Refused(f"バージョン {m['version']} は配布を止めています")
            emergency = entry.get("emergency") or None
            if not emergency and entry["stage"] not in ACCEPTS[self.cfg.stage]:
                raise Refused(f"バージョン {m['version']} はまだ段「{entry['stage']}」までで、このサーバー(段「{self.cfg.stage}」)には届きません")
            if m["version"] == self.current():
                raise Refused(f"バージョン {m['version']} はもう当たっています")
            warnings = self.compat_warnings(m)
            if warnings and self.cfg.block_on_incompatible:
                raise Refused("互換表と合いません: " + " / ".join(warnings))
        except Refused as e:
            self.log.append(event="拒否", package=str(package), reason=str(e))
            raise
        self._accept_index(index, index_path)
        backup = self._backup()
        dest = self.root / "releases" / m["version"]
        if not dest.exists():
            tmp = dest.with_name(dest.name + ".tmp")
            if tmp.exists():
                _rmtree(tmp)
            tmp.mkdir(parents=True)
            for rel in m["files"]:
                target = tmp / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(tf.extractfile(f"files/{rel}").read())
            tmp.rename(dest)
        before = self.current()
        self._switch(dest)
        ok, why = self._restart_and_check()
        seconds = round(time.monotonic() - t0, 2)
        base = {"version": m["version"], "from": before, "reason": m["reason"], "summary": m["summary"],
                "emergency": emergency, "compat_warnings": warnings, "backup": backup, "seconds": seconds}
        if ok:
            row = self.log.append(event="適用", **base)
            return {"result": "適用", **base, "log": row["seq"]}
        # 失敗: 直前のバージョンへ戻す(R-DV-32)
        if before:
            self._switch(self.root / "releases" / before)
            self._restart_and_check()
        row = self.log.append(event="自動で戻した", why=why, **base)
        self._notify(f"更新 {m['version']} を当てた後の確かめに失敗し、{before or '(前のバージョンなし)'} へ戻しました: {why}")
        return {"result": "自動で戻した", "why": why, **base, "log": row["seq"]}

    def rollback(self) -> dict:
        prev, cur = self.previous(), self.current()
        if not prev:
            raise Refused("戻す先のバージョンがありません")
        self._switch(self.root / "releases" / prev)
        ok, why = self._restart_and_check()
        row = self.log.append(event="手で戻した", version=prev, **{"from": cur}, check=ok, why=why)
        return {"result": "手で戻した", "version": prev, "from": cur, "check": ok, "log": row["seq"]}

    # ---- 中の手順 ----
    def _accept_index(self, index: dict, index_path: Path) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "index.json").write_bytes(Path(index_path).read_bytes())
        (self.root / "index.serial").write_text(str(index["serial"]))

    def _backup(self) -> str:
        """当てる前にデータの置き場を tar で取る(R-DV-32、R-DV-21)。"""
        if not self.cfg.data or not self.cfg.data.exists():
            return ""
        d = self.root / "backups"
        d.mkdir(parents=True, exist_ok=True)
        name = d / f"{datetime.now().strftime('%Y%m%dT%H%M%S')}-{self.current() or 'none'}.tar.gz"
        with tarfile.open(name, "w:gz") as tf:
            tf.add(self.cfg.data, arcname=self.cfg.data.name)
        return str(name)

    def _switch(self, dest: Path) -> None:
        """current を一度に付け替え、前の current を previous にする。"""
        cur = self.root / "current"
        if cur.is_symlink() and cur.resolve() != dest.resolve():
            _relink(self.root / "previous", cur.resolve())
        _relink(cur, dest)

    def _restart_and_check(self) -> tuple[bool, str]:
        for name, cmd in (("restart", self.cfg.restart), ("check", self.cfg.check)):
            if not cmd:
                continue
            try:
                r = subprocess.run(shlex.split(cmd), capture_output=True, text=True, timeout=120,
                                   cwd=self.root / "current")
            except (OSError, subprocess.TimeoutExpired) as e:
                return False, f"{name}: {type(e).__name__}"
            if r.returncode != 0:
                return False, f"{name}: 終了コード {r.returncode} {(r.stdout + r.stderr).strip()[:160]}"
        return True, ""

    def _notify(self, text: str) -> None:
        if not self.cfg.notify:
            return
        try:
            subprocess.run(shlex.split(self.cfg.notify), input=text, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            pass  # 通知の不調で戻しを止めない。記録には残っている


def _entry(index: dict | None, version: str) -> dict | None:
    for e in (index or {}).get("versions", []):
        if e.get("version") == version:
            return e
    return None


def _relink(link: Path, target: Path) -> None:
    tmp = link.with_name(link.name + ".new")
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    os.symlink(target.resolve(), tmp)
    os.replace(tmp, link)   # 記号リンクの付け替えは一度に起きる


def _rmtree(p: Path) -> None:
    import shutil
    shutil.rmtree(p)


def notice(manifest: dict, entry: dict | None) -> str:
    """管理会社へ送る事前の通知の文(R-DV-38)。送るのは人。"""
    lines = [f"更新の通知: {manifest['product']} {manifest['version']}",
             f"理由: {manifest['reason']}", f"内容: {manifest['summary']}"]
    if entry:
        lines.append(f"配布の段: {entry['stage']}" + ("(緊急の経路)" if entry.get("emergency") else ""))
        if entry.get("emergency"):
            lines.append(f"緊急の判断: {entry['emergency'].get('by', '')} — {entry['emergency'].get('why', '')}")
        if entry.get("support_until"):
            lines.append(f"このバージョンのサポートの終わり: {entry['support_until']}")
    if manifest.get("compat"):
        lines.append("合う部品のバージョン: " + "; ".join(f"{k} {', '.join(v)}" for k, v in manifest["compat"].items()))
    lines.append("錠の解錠の判断は錠と扉のコントローラの中にあり、更新の間も止まりません。")
    return "\n".join(lines)


def fleet(directory: Path) -> list[dict]:
    """status --json を集めた置き場から、サポートの切れたバージョンで動くサーバーを並べる(R-DV-40)。"""
    out = []
    for p in sorted(Path(directory).glob("*.json")):
        try:
            s = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            out.append({"server": p.stem, "problem": "読めません"})
            continue
        if s.get("out_of_support"):
            out.append({"server": p.stem, "version": s.get("version"), "support_until": s.get("support_until")})
    return out


# ---- 命令行 ----
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="digitalkey update", description="更新の配布(署名、段階配布、自動で戻す)",
                                 epilog="設定は update.toml。段は 開発 / 試験導入棟 / 全棟。詳しくはこのモジュールの説明")
    ap.add_argument("--config", default=os.environ.get("DIGITALKEY_UPDATE_CONFIG", "update.toml"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("keygen", help="製造者の署名の鍵を作る(権限 600、上書きしない)")
    k.add_argument("--out", required=True)
    p = sub.add_parser("pack", help="置き場のファイルを、署名つきの包みにする")
    p.add_argument("--key", required=True); p.add_argument("--product", default="digitalkey")
    p.add_argument("--version", required=True); p.add_argument("--reason", required=True, choices=REASONS)
    p.add_argument("--summary", required=True); p.add_argument("--from", dest="source", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--compat", action="append", default=[], metavar="部品=バージョン,バージョン", help="互換表の一行。何度でも")
    i = sub.add_parser("index", help="配布の表(index.json と index.sig)を作る")
    i.add_argument("--key", required=True); i.add_argument("--serial", required=True, type=int)
    i.add_argument("--entry", action="append", default=[], metavar="バージョン:段[:サポートの終わり]")
    i.add_argument("--halt", action="append", default=[], metavar="バージョン")
    i.add_argument("--emergency", action="append", default=[], metavar="バージョン:誰:なぜ")
    i.add_argument("--out", required=True)
    sub.add_parser("status", help="今のバージョンとサポート期限").add_argument("--json", action="store_true")
    a = sub.add_parser("apply", help="確かめてから当てる。失敗したら直前のバージョンへ自動で戻す")
    a.add_argument("--package", required=True); a.add_argument("--index", required=True)
    sub.add_parser("rollback", help="直前のバージョンへ戻す")
    n = sub.add_parser("notice", help="管理会社へ送る事前の通知の文を出す")
    n.add_argument("--package", required=True); n.add_argument("--index")
    f = sub.add_parser("fleet", help="status --json を集めた置き場から、サポートの切れたサーバーを並べる")
    f.add_argument("dir")
    sub.add_parser("log", help="更新の記録と、その連鎖の検査")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "keygen":
            key = Ed25519PrivateKey.generate()
            from cryptography.hazmat.primitives import serialization
            raw = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                    serialization.NoEncryption())
            fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(raw)
            print(json.dumps({"public_key": public_hex(key)}))
            return 0
        if args.cmd == "pack":
            compat = {}
            for c in args.compat:
                name, _, vers = c.partition("=")
                compat[name.strip()] = [v.strip() for v in vers.split(",") if v.strip()]
            m = pack(read_key(args.key), product=args.product, version=args.version, reason=args.reason,
                     summary=args.summary, source=Path(args.source), out=Path(args.out), compat=compat)
            print(f"包みました: {args.out}({len(m['files'])} ファイル)")
            return 0
        if args.cmd == "index":
            entries = []
            for e in args.entry:
                parts = e.split(":")
                entries.append({"version": parts[0], "stage": parts[1], "halted": parts[0] in args.halt,
                                "support_until": parts[2] if len(parts) > 2 else "", "emergency": None})
            for e in args.emergency:
                ver, who, why = (e.split(":", 2) + ["", ""])[:3]
                for x in entries:
                    if x["version"] == ver:
                        x["emergency"] = {"by": who, "why": why}
            make_index(read_key(args.key), serial=args.serial, entries=entries, out=Path(args.out))
            print(f"表を作りました: {args.out}(通し番号 {args.serial})")
            return 0
        if args.cmd == "fleet":
            rows = fleet(Path(args.dir))
            for r in rows:
                print(json.dumps(r, ensure_ascii=False))
            return 1 if rows else 0
        u = Updater(Config.load(args.config))
        if args.cmd == "status":
            s = u.status()
            print(json.dumps(s, ensure_ascii=False) if args.json else
                  f"バージョン {s['version'] or '(なし)'}  段 {s['stage']}  サポートの終わり {s['support_until'] or '?'}"
                  + ("  ※サポート期限切れ" if s["out_of_support"] else ""))
            return 0
        if args.cmd == "apply":
            r = u.apply(Path(args.package), Path(args.index))
            print(json.dumps(r, ensure_ascii=False))
            return 0 if r["result"] == "適用" else 2
        if args.cmd == "rollback":
            print(json.dumps(u.rollback(), ensure_ascii=False))
            return 0
        if args.cmd == "notice":
            m, _ = u.open_package(Path(args.package))
            entry = _entry(u.read_index(Path(args.index)), m["version"]) if args.index else None
            print(notice(m, entry))
            return 0
        if args.cmd == "log":
            for r in u.log.rows():
                print(json.dumps(r, ensure_ascii=False))
            ok, n, why = u.log.verify()
            print(f"{'正常' if ok else '異常'} {n}件 {why}", file=sys.stderr)
            return 0 if ok else 1
    except Refused as e:
        print(f"当てませんでした: {e}", file=sys.stderr)
        return 1
    except FileExistsError as e:
        print(f"ファイルが既にあります(上書きしません): {e.filename}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0
