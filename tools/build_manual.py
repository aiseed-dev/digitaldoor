#!/usr/bin/env python3
"""コードからユーザーマニュアルを作る。

    python3 tools/build_manual.py            # docs/manual.md と html/manual/index.html

読む物: 各パッケージの docstring(digitalkey、site、mobile、door、入れた錠ドライバ)、コマンドの --help、
設定ファイルの雛形(site.config.sample)、API の一覧(FastAPI のルート)、様式(forms_data/*.adoc)。
コードを直せばマニュアルも変わる。手で書き足す文はこのスクリプトの TEXT にだけ置く。
"""
from __future__ import annotations

import io
import json
import re
import sys
from contextlib import redirect_stdout
from datetime import datetime
from html import escape
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SITE))

TEXT = {
    "title": "デジタルドア ユーザーマニュアル",
    "intro": "このマニュアルは、ソフトウェアのコードから自動で生成しています。対象は、事業所のサーバーを運用する管理者、受付の担当者、鍵を使う利用者、保守の担当者です。",
    "roles": [
        ("管理者", "サーバーの設定、鍵の発行と失効、扉の施錠、記録の閲覧と検査。"),
        ("受付", "鍵の発行、扉の施錠、操作の記録の閲覧。失効はできません。"),
        ("利用者", "自分の鍵の確認と、自分の鍵での解錠。"),
        ("保守", "扉の状態と監査記録の閲覧、記録の検査、施錠。"),
    ],
}


def help_of(main, argv=("--help",)) -> str:
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            main(list(argv))
    except SystemExit:
        pass
    return buf.getvalue().strip()


def doc(obj) -> str:
    return (obj.__doc__ or "").strip()


def section_cli() -> str:
    from digitalkey import cli as top
    from digitalkey.door import cli as door
    from digitalkey.entrance import cli as entrance
    from digitalkey.site import cli as site
    from importlib.metadata import entry_points
    out = ["## コマンド一覧", "", "サーバーやパソコンで使うコマンドです。`digitalkey` の後に対象を付けます。", ""]
    out += ["```", top.USAGE.replace("使い方:\n", "").strip(), "```", ""]
    mains = [("site", site.main), ("entrance", entrance.main), ("door", door.main)]
    for ep in entry_points(group="digitalkey.commands"):
        mains.append((ep.name, ep.load()))
    for name, main in mains:
        out += [f"### digitalkey {name}", "", "```", help_of(main), "```", ""]
    return "\n".join(out)


def section_site() -> str:
    from fastapi.routing import APIRoute

    from digitalkey.site import api, config
    from digitalkey.site.service import Site
    from digitalkey.site import __doc__ as site_doc
    out = ["## 事業所サーバー", "", site_doc.strip(), ""]
    out += ["### 導入", "", "`deploy/install.sh` を root で実行すると、専用ユーザーの作成、仮想環境の作成、設定の雛形の生成、systemd への登録まで行います。設定は `/var/lib/digitaldoor/site.toml` です。", "",
            "```", "sh deploy/install.sh", "systemctl status digitaldoor-site", "```", ""]
    out += ["### 設定ファイル(site.toml)", "", "`digitalkey site init <ディレクトリ>` で次の雛形が作られます。トークンは自動生成されます。", "", "```toml", config.sample("本社", "/var/lib/digitaldoor/vault").strip(), "```", ""]
    out += ["### 役割", "", "| 役割 | できること |", "|---|---|"] + [f"| {r} | {d} |" for r, d in TEXT["roles"]] + [""]
    # API from the app's routes
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "site.toml"; f.write_text(config.sample("本社", d + "/vault"))
        app = api.create_app(Site(config.load(f)))
    out += ["### HTTP API", "", "アプリと業務システムはこの API を使います。認証は `Authorization: Bearer <トークン>` です。", "", "| メソッド | パス | 説明 |", "|---|---|---|"]
    desc = {}
    for line in doc(api).splitlines():
        m = re.match(r"\s*(GET|POST|DELETE)\s+(\S+)\s*(\{[^}]*\})?\s+(.*)", line)
        if m:
            desc[(m.group(1), m.group(2))] = (m.group(3) or "") + " " + m.group(4)
    for r in app.routes:
        if isinstance(r, APIRoute):
            for m in sorted(r.methods):
                out.append(f"| {m} | `{r.path}` | {desc.get((m, r.path), '').strip()} |")
    out.append("")
    return "\n".join(out)


def section_mobile() -> str:
    from digitalkey import mobile
    from digitalkey.mobile import main as m
    out = ["## スマートフォンアプリ", "", doc(mobile), ""]
    body = doc(m)
    i = body.find("画面:")
    if i >= 0:
        out += ["### 画面", "", "```", body[i:].strip(), "```", ""]
    return "\n".join(out)


def section_door() -> str:
    from digitalkey.door import controller
    return "\n".join(["## 扉のコントローラの動作", "", doc(controller).replace("判断 — ", ""), ""])


def section_forms() -> str:
    from digitalkey.entrance import forms as F
    out = ["## 台帳の様式", "", "台帳に記録される伝票の種類です。項目名は様式ファイル(digitalkey/entrance/forms_data/*.adoc)がそのまま定義です。", ""]
    for name, y in F.load_all().items():
        who = y.attrs.get("書ける", "")
        out += [f"### {y.title}({name})", "", f"書ける役割: {who}" if who else "", "", "| 項目 | 型と制約 |", "|---|---|"]
        for k in y.fields:
            spec = k.raw.strip() or k.sql_type
            out.append(f"| {k.name} | {spec}{(' — ' + k.description) if k.description else ''} |")
        out.append("")
    return "\n".join(out)


def section_drivers() -> str:
    from digitalkey.locks import available
    out = ["## 錠のドライバ", "", "site.toml の `lock` に書ける種類と提供元です。種類は pip で入れたドライバのパッケージで増えます。", "", "| 種類 | 提供元 |", "|---|---|"]
    out += [f"| {k} | {v} |" for k, v in available().items()]
    out.append("")
    try:
        import digitalkey_sesame
        from digitalkey_sesame import locks as L
        out += ["### digitalkey-sesame", "", doc(digitalkey_sesame), "", "```", doc(L), "```", ""]
    except ImportError:
        out += ["digitalkey-sesame は入っていません。CANDY HOUSE Sesame を使うときは `pip install digitalkey-sesame`。", ""]
    return "\n".join(out)


def md_to_html(md: str) -> str:
    from markdown_it import MarkdownIt
    return MarkdownIt("commonmark", {"html": False}).enable("table").render(md)


def main() -> int:
    parts = [f"# {TEXT['title']}", "", TEXT["intro"], "", f"生成日: {datetime.now().strftime('%Y-%m-%d')}", "",
             section_site(), section_mobile(), section_cli(), section_door(), section_forms(), section_drivers()]
    md = "\n".join(parts)
    (SITE / "docs" / "manual.md").write_text(md, encoding="utf-8")
    css_v = re.search(r'style\.css\?v=([0-9a-f]+)', (SITE / "html/index.html").read_text(encoding="utf-8"))
    css = "/css/style.css" + (f"?v={css_v.group(1)}" if css_v else "")
    page = f'''<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ユーザーマニュアル | デジタルドア</title>
    <meta name="description" content="デジタルドアのソフトウェア(事業所サーバー、スマートフォンアプリ、コマンド)のユーザーマニュアル。コードから自動生成しています。">
    <link rel="canonical" href="https://door.aiseed.dev/manual/">
    <link rel="icon" href="/favicon.ico" sizes="any">
    <link rel="stylesheet" href="{css}">
</head>
<body>
    <header class="site-header">
        <a href="/" class="brand">デジタルドア</a>
        <nav><a href="/guide/">解説</a><a href="/blog/">ブログ</a><a href="/news/">ニュース</a><a href="/manual/">マニュアル</a></nav>
    </header>
    <main class="index article-body">
{md_to_html(md)}
    </main>
    <footer class="site-footer"><p>デジタルドア — aiseed · <a href="/license/">ライセンス</a> · <a href="/feed.xml">RSS</a></p></footer>
</body>
</html>
'''
    out = SITE / "html" / "manual" / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    print(f"Built manual: docs/manual.md, {out} ({len(md)} chars)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
