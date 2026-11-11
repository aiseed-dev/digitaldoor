"""デジタルドアの立体図(html/3d/index.html)が、サイトの決まりを守っているかの試験。

頁は手で置く一画面のアプリで、スクリプトは html/js/door3d.js、見た目は
html/css/door3d.css、three.js(r128、MIT)は html/js/three.min.js に置く。
CSP は他の頁と同じ <meta> で出し、頁の中にスクリプトを書かず、外から何も読まない。
"""
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HTML = ROOT / "html"
PAGE = HTML / "3d" / "index.html"

CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
       "font-src 'self'; img-src 'self' data:; connect-src 'self'")
THREE_SHA256 = "9274bbcec8d96168626c732b5d31c775aa8cfb7eaa0599bec0c175908a2c1ce2"


def _page() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_page_has_site_head():
    """頁の頭は他の頁と同じ形(doctype、lang、題、説明文、canonical、favicon)。"""
    text = _page()
    assert text.startswith("<!DOCTYPE html>")
    assert '<html lang="ja">' in text
    assert "<title>デジタルドアの立体図 | デジタルドア</title>" in text
    assert re.search(r'<meta name="description" content="[^"]+">', text)
    assert '<link rel="canonical" href="https://door.aiseed.dev/3d/">' in text
    assert '<link rel="icon" href="/favicon.ico" sizes="any">' in text
    assert "claude:" not in text


def test_page_has_same_csp_as_home():
    """CSP の <meta> は、ホームと同じ値。"""
    meta = f'<meta http-equiv="Content-Security-Policy" content="{CSP}">'
    assert meta in _page()
    assert meta in (HTML / "index.html").read_text(encoding="utf-8")


def test_no_inline_script_or_event_attributes():
    """頁の中にスクリプトを書かない。読むのは三つの自分のファイルだけ。イベントの属性も使わない。"""
    text = _page()
    scripts = re.findall(r"<script([^>]*)>(.*?)</script>", text, flags=re.S)
    srcs = [re.sub(r"\?v=[0-9a-f]+", "", attrs.strip()) for attrs, _ in scripts]
    assert srcs == ['src="/js/three.min.js"', 'src="/js/door3d.js"']
    assert all(body.strip() == "" for _, body in scripts)
    assert not re.search(r"\son[a-z]+\s*=", text)
    assert "javascript:" not in text
    assert "<style" not in text


def test_no_external_loads():
    """頁は外の URL から何も読まない(Google Fonts も CDN も使わない)。"""
    text = _page()
    loads = re.findall(r'(?:src|href)="([^"]+)"', text)
    external = [u for u in loads if re.match(r"(?:https?:)?//", u)]
    assert external == ["https://door.aiseed.dev/3d/"]  # canonical だけ
    assert any(u.split("?")[0] == "/css/door3d.css" for u in loads)
    css = (HTML / "css" / "door3d.css").read_text(encoding="utf-8")
    assert "@import" not in css and "url(" not in css
    assert "data-theme" not in css
    js = (HTML / "js" / "door3d.js").read_text(encoding="utf-8")
    assert not re.search(r"https?://", js)
    assert not re.search(r"\b(fetch|XMLHttpRequest|WebSocket|eval)\s*\(", js)


def test_page_assets_carry_version():
    """頁の CSS と JS の参照には、そのファイルの中身の sha256 の先頭 8 文字が ?v= で付いている
    (build_article.py の update_static_page_asset_versions が付ける)。"""
    text = _page()
    for rel in ("css/door3d.css", "js/door3d.js", "js/three.min.js"):
        v = hashlib.sha256((HTML / rel).read_bytes()).hexdigest()[:8]
        assert f'="/{rel}?v={v}"' in text, rel
    assert THREE_SHA256[:8] in text


def test_links_back_to_home_without_site_nav():
    """上の並びには足さない。頁からはホームへの控えめなリンクを一つ持つ。"""
    text = _page()
    assert '<header class="site-header">' not in text
    assert text.count('href="/"') == 1


def test_closing_text():
    """操作の欄の終わりの二つの文は、サイトの頁としての文。"""
    text = _page()
    assert "この画面は、図面も写真も使わず、文章の指示から AI(Claude)が書きました。寸法と色は例です。" in text
    assert "製品の図面と写真があれば、その寸法と色で、一品ずつこの画面にできます。" in text
    assert "Fable" not in text


def test_three_js_is_the_approved_copy():
    """three.js は運営者が認めた r128 のファイルそのもの。先頭のライセンスの注記が残っている。"""
    data = (HTML / "js" / "three.min.js").read_bytes()
    assert hashlib.sha256(data).hexdigest() == THREE_SHA256
    head = data[:200].decode("utf-8")
    assert "Copyright 2010-2021 Three.js Authors" in head
    assert "SPDX-License-Identifier: MIT" in head


def test_license_mentions_three_js():
    """ライセンスの頁と LICENSE に three.js(MIT)を書いている。"""
    for p in (HTML / "license" / "index.html", ROOT / "LICENSE"):
        text = p.read_text(encoding="utf-8")
        assert "three.js" in text and "MIT" in text
        assert "Copyright 2010-2021 Three.js Authors" in text


def test_home_links_to_3d():
    """ホームに、この頁への入口が一つある。"""
    home = (HTML / "index.html").read_text(encoding="utf-8")
    assert home.count('href="/3d/"') == 1


def test_sitemap_lists_3d_and_every_url_is_a_file():
    """sitemap に /3d/ がある。sitemap の URL は全部ファイルとしてある(publish.py の点検と同じ条件)。"""
    sm = (HTML / "sitemap.xml").read_text(encoding="utf-8")
    locs = re.findall(r"<loc>([^<]+)</loc>", sm)
    assert "https://door.aiseed.dev/3d/" in locs
    missing = []
    for loc in locs:
        rel = loc[len("https://door.aiseed.dev/"):]
        if rel == "" or rel.endswith("/"):
            rel += "index.html"
        if not (HTML / rel).is_file():
            missing.append(loc)
    assert missing == []
    assert (HTML / "index.html").is_file() and (HTML / "404.html").is_file()
