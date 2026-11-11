"""サイトの上の並び(ナビゲーション)と奥付(フッター)の定義。全部の頁で同じにする。

ここが正。記事のビルド(build_article の雛形 index.html・chapter.html)、
build_manual.py、build_check.py、build_news.py はここから読む。手書きの頁
(html/index.html、html/consult/、html/license/、html/404.html)は、
nav_html() と footer_html() と同じ形を手で書く。ずれは
tests/sitebuild/test_site_nav.py が見つける。

依存は無し(標準ライブラリも使わない)。道具のどれからでも import できる。
"""

# 上の並び: (表示, 行き先)。この順に出す。
NAV = [
    ("解説", "/guide/"),
    ("立体図", "/3d/"),
    ("始め方", "/check/"),
    ("マニュアル", "/manual/"),
    ("ニュース", "/news/"),
    ("ブログ", "/blog/"),
    ("相談と教室", "/consult/"),
]

# 奥付: 名前の後に、この順でリンクを「 · 」でつなぐ。
FOOTER_NAME = "デジタルドア — aiseed"
FOOTER_LINKS = [
    ("このサイトについて", "/#about"),
    ("ライセンス", "/license/"),
    ("RSS", "/feed.xml"),
]


def nav_html() -> str:
    """`<header class="site-header">` の中に置く `<nav>…</nav>`。"""
    return "<nav>" + "".join(f'<a href="{href}">{label}</a>' for label, href in NAV) + "</nav>"


def footer_inner_html() -> str:
    """奥付の `<p>` の中身。"""
    links = " · ".join(f'<a href="{href}">{label}</a>' for label, href in FOOTER_LINKS)
    return f"{FOOTER_NAME} · {links}"


def footer_html() -> str:
    """頁の最後に置く `<footer class="site-footer">…</footer>`。"""
    return f'<footer class="site-footer"><p>{footer_inner_html()}</p></footer>'
