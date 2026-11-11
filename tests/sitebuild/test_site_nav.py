"""サイトの上の並びと奥付が、全部の頁で同じ形かの試験。

正は tools/build/site_nav.py。記事のビルドと build_check / build_manual / build_news は
そこから組み、手書きの頁(html/index.html、html/consult/、html/license/、html/404.html)は
手で同じ形に書く。手書きの頁が後でずれた時に、ここで気づく。
"""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HTML = ROOT / "html"
sys.path.insert(0, str(ROOT / "tools"))

from build.site_nav import NAV, footer_html, nav_html  # noqa: E402


def _is_article(path: Path) -> bool:
    """記事の頁(html/guide/<slug>/、html/blog/<slug>/)。上は masthead の形で、この試験の外。"""
    rel = path.relative_to(HTML).parts
    return len(rel) == 3 and rel[0] in ("guide", "blog") and rel[2] == "index.html"


def _squash(s: str) -> str:
    return re.sub(r">\s+<", "><", s.strip())


def _is_app(path: Path) -> bool:
    """一画面のアプリの頁(html/3d/)。上の並びと奥付を持たず、ホームへのリンクを一つ持つ。
    その形は tests/sitebuild/test_door3d.py が見る。"""
    return path.relative_to(HTML).parts[0] == "3d"


PAGES = sorted(p for p in HTML.rglob("*.html") if not _is_article(p) and not _is_app(p))
ARTICLES = sorted(p for p in HTML.rglob("index.html") if _is_article(p))


@pytest.mark.parametrize("page", PAGES, ids=lambda p: str(p.relative_to(HTML)))
def test_header_nav_and_footer_are_the_same(page):
    """記事でない頁は、上の並びと奥付が site_nav の定義と同じ。"""
    text = page.read_text(encoding="utf-8")
    header = re.findall(r'<header class="site-header">(.*?)</header>', text, flags=re.S)
    assert len(header) == 1, "上の並び(site-header)が一つではありません"
    nav = re.findall(r"<nav>.*?</nav>", header[0], flags=re.S)
    assert [_squash(n) for n in nav] == [nav_html()]
    footer = re.findall(r'<footer class="site-footer">.*?</footer>', text, flags=re.S)
    assert [_squash(f) for f in footer] == [footer_html()]


@pytest.mark.parametrize("page", ARTICLES, ids=lambda p: str(p.relative_to(HTML)))
def test_article_footer_nav_matches(page):
    """記事の頁の footer-nav(サイト内リンク)の「サイト」の列は、上の並びと同じ六つを同じ順に並べる。"""
    text = page.read_text(encoding="utf-8")
    m = re.search(r"<h4>サイト</h4>\s*<ul>(.*?)</ul>", text, flags=re.S)
    assert m, "footer-nav の「サイト」の列がありません"
    links = re.findall(r'<a href="([^"]+)">([^<]+)</a>', m.group(1))
    assert [(label, href) for href, label in links] == NAV


@pytest.mark.parametrize("rel", ["manual/index.html", "check/index.html"])
def test_tables_are_in_scroll_frame(rel):
    """記事でない頁(manual と check)の main の中の表は、どれも横に送れる枠(.table-wrap)の中にある。"""
    text = (HTML / rel).read_text(encoding="utf-8")
    main = re.search(r"<main[^>]*>(.*)</main>", text, flags=re.S).group(1)
    tables = len(re.findall(r"<table[\s>]", main))
    wrapped = len(re.findall(r'<div class="table-wrap">\s*<table[\s>]', main))
    assert tables and wrapped == tables
    css = (HTML / "css" / "style.css").read_text(encoding="utf-8")
    assert re.search(r"\.table-wrap\s*\{[^}]*overflow-x:\s*auto", css)


def test_pages_found():
    assert PAGES and ARTICLES
