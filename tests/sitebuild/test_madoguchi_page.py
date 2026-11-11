"""お問い合わせと照会(html/contact/)とプライバシーポリシー(html/privacy/)の頁が、
サイトの決まりと docs/窓口仕様書.md 11.3 を守っているかの試験。

番号なし(サイトの決まりの確かめ。test_door3d.py と同じ扱い)。スクリプトの動きの
うち、DOM に頼らない関数は test_madoguchi_js.py が node で確かめる。
"""
import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HTML = ROOT / "html"
CONTACT = HTML / "contact" / "index.html"
PRIVACY = HTML / "privacy" / "index.html"
JS = HTML / "js" / "madoguchi.js"
CSS = HTML / "css" / "madoguchi.css"
sys.path.insert(0, str(ROOT / "tools"))

from build.site_nav import FOOTER_LINKS, footer_html  # noqa: E402

HOME_CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "font-src 'self'; img-src 'self' data:; connect-src 'self'")
CONTACT_CSP = HOME_CSP + " https://auth.aiseed.dev"
PB = "https://auth.aiseed.dev"
ISSUES = "https://github.com/aiseed-dev/digitaldoor/issues"
TODO_MARK = "【運営者が書く"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _csp(text: str) -> list[str]:
    return re.findall(r'<meta http-equiv="Content-Security-Policy" content="([^"]*)">', text)


def _strip_hidden(text: str) -> str:
    """`hidden` の付いた要素(section、p、div)を除いた残り。入れ子の同じ名前の要素は無い前提。"""
    for tag in ("section", "div", "p"):
        text = re.sub(rf"<{tag}\b[^>]*\shidden\b[^>]*>.*?</{tag}>", "", text, flags=re.S)
    return text


# --- /contact/ ---------------------------------------------------------------

def test_contact_head():
    """題、canonical、doctype、lang。"""
    text = _read(CONTACT)
    assert text.startswith("<!DOCTYPE html>")
    assert '<html lang="ja">' in text
    assert "<title>お問い合わせと照会 | デジタルドア</title>" in text
    assert '<link rel="canonical" href="https://door.aiseed.dev/contact/">' in text


def test_contact_csp_adds_only_auth_to_connect_src():
    """/contact/ の CSP は 5.2 の値。ほかの頁は今のまま(connect-src 'self')で、auth.aiseed.dev を持つのは contact/ だけ。"""
    assert _csp(_read(CONTACT)) == [CONTACT_CSP]
    for page in sorted(HTML.rglob("*.html")):
        if page == CONTACT:
            continue
        text = _read(page)
        assert PB not in " ".join(_csp(text)), page
        for value in _csp(text):
            assert "connect-src 'self';" in value + ";" or value.endswith("connect-src 'self'"), page


def test_contact_has_one_empty_own_script_and_no_event_attributes():
    """<script> は src="/js/madoguchi.js"(版の印を除く)の一つだけで中身が空。on...= の属性と javascript: が無い。"""
    text = _read(CONTACT)
    scripts = re.findall(r"<script([^>]*)>(.*?)</script>", text, flags=re.S)
    srcs = [re.sub(r"\?v=[0-9a-f]+", "", attrs.strip()) for attrs, _ in scripts]
    assert srcs == ['src="/js/madoguchi.js"']
    assert all(body.strip() == "" for _, body in scripts)
    assert not re.search(r"\son[a-z]+\s*=", text)
    assert "javascript:" not in text


def test_contact_loads_nothing_external():
    """外の URL の <script>、<link rel="stylesheet"> が無い。読むのは自分のサイトの物だけ。"""
    text = _read(CONTACT)
    for attrs in re.findall(r"<script([^>]*)>", text):
        assert not re.search(r'src="(?:https?:)?//', attrs)
    sheets = re.findall(r'<link rel="stylesheet" href="([^"]+)">', text)
    assert [re.sub(r"\?v=[0-9a-f]+", "", h) for h in sheets] == ["/css/style.css", "/css/madoguchi.css"]
    assert "form-render.js" not in text and "form.css" not in text


def test_contact_main_has_data_pb():
    """main に data-pb="https://auth.aiseed.dev" がある(スクリプトは基の URL をここから読む)。"""
    assert re.search(r'<main\b[^>]*\sdata-pb="https://auth\.aiseed\.dev"', _read(CONTACT))


def test_contact_every_field_has_a_label():
    """input、select、textarea の全部に、for が同じ id の <label> がある。"""
    text = _read(CONTACT)
    ids = re.findall(r'<(?:input|select|textarea)\b[^>]*\sid="([^"]+)"', text)
    fields = re.findall(r"<(?:input|select|textarea)\b", text)
    assert len(ids) == len(fields) == 4
    labels = set(re.findall(r'<label for="([^"]+)">', text))
    assert set(ids) <= labels


def test_contact_form_fields_match_spec():
    """種類の値と既定、件名と本文の maxlength(3.3 の例)、返事の既定は印あり。"""
    text = _read(CONTACT)
    options = re.findall(r'<option value="([^"]+)"', text)
    assert options == ["質問", "相談", "教室", "そのほか"]
    assert re.search(r'<option value="質問" selected>', text)
    assert re.search(r'<input type="text" id="madoguchi-subject" maxlength="200"', text)
    assert re.search(r'<textarea id="madoguchi-body" maxlength="5000"', text)
    assert re.search(r'<input type="checkbox" id="madoguchi-reply" checked>', text)
    assert re.search(r'<button type="submit"[^>]*>送る</button>', text)
    for i in ("madoguchi-form", "madoguchi-list", "madoguchi-number", "madoguchi-sheet",
              "madoguchi-signin", "madoguchi-nojs"):
        assert f'id="{i}"' in text


def test_contact_readable_without_script():
    """hidden の無い部分に、題、/privacy/ へのリンク、Issues へのリンクと、JavaScript が要る案内がある。<noscript> がある。
    サインインとサインインの後の部分は hidden。"""
    text = _read(CONTACT)
    main = re.search(r"<main\b.*?</main>", text, flags=re.S).group(0)
    visible = _strip_hidden(main)
    assert "<h1>お問い合わせと照会</h1>" in visible
    assert 'href="/privacy/"' in visible
    assert f'href="{ISSUES}"' in visible
    assert 'id="madoguchi-nojs"' in visible
    assert "<noscript>" in text
    assert 'id="madoguchi-signin"' not in visible
    assert 'id="madoguchi-app"' not in visible
    assert 'id="madoguchi-form"' not in visible


def test_contact_status_regions():
    """状態の知らせは role="status" の要素に入れる(5.6)。"""
    text = _read(CONTACT)
    assert re.search(r'id="madoguchi-status"[^>]*role="status"', text)
    assert re.search(r'id="madoguchi-signin-status"[^>]*role="status"', text)


# --- madoguchi.js ------------------------------------------------------------

def test_script_has_no_unsafe_sinks_or_storage():
    """localStorage、document.cookie、innerHTML(outerHTML、insertAdjacentHTML、document.write も)、eval、new Function が無い。"""
    js = _read(JS)
    for bad in ("localStorage", "document.cookie", "innerHTML", "outerHTML", "insertAdjacentHTML",
                "document.write", "new Function"):
        assert bad not in js, bad
    assert not re.search(r"\beval\s*\(", js)
    assert not re.search(r"setTimeout\(\s*[\"']", js)


def test_script_has_no_browser_dialogs():
    """confirm、alert、prompt の問いかけの窓を使わない(消す時の確かめは頁の中の二段)。"""
    js = _read(JS)
    assert not re.search(r"\b(?:window\.)?(?:confirm|alert|prompt)\s*\(", js)
    assert "本当に消す" in js and "やめる" in js


def test_delete_confirm_focuses_cancel():
    """確かめの間は「やめる」が先で、焦点も「やめる」に置く(二度押しで消えない)。"""
    js = _read(JS)
    assert re.search(r'\[\{ action: "cancel", label: "やめる" \}, \{ action: "do", label: "本当に消す" \}\]', js)
    assert """querySelector('button[data-action="cancel"]')""" in js
    assert """querySelector('button[data-action="do"]')""" not in js


def test_script_has_the_redirect_and_apple_flows():
    """4.2 の形(sessionStorage、history.replaceState、auth-with-oauth2)と、apple の時だけの 4.3 の形がある。"""
    js = _read(JS)
    for need in ("sessionStorage", "history.replaceState", "auth-with-oauth2", "auth-refresh",
                 "madoguchi.token", "madoguchi.oauth2", "location.assign",
                 "EventSource", "/api/oauth2-redirect", "@oauth2", '"apple"', "PB_CONNECT"):
        assert need in js, need


def test_script_has_no_url_and_reads_base_from_page():
    """https:// で始まる URL を書いていない。基の URL は data-pb から読む。"""
    js = _read(JS)
    assert "https://" not in js
    assert "http://" not in js
    assert 'getAttribute("data-pb")' in js


def test_script_has_preparing_text():
    """提供者が一つも無い時の文がある。"""
    assert "今はサインインを用意している所です" in _read(JS)


def test_script_sends_status_new():
    """問い合わせを送る時、status に「新しい」を入れる(3.3)。"""
    assert re.search(r'status:\s*"新しい"', _read(JS))


def test_css_uses_site_variables():
    """見た目は style.css の変数を使う(色を直に書かない)。"""
    css = _read(CSS)
    assert "var(--rule)" in css and "var(--accent)" in css and "var(--ink)" in css
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b", css)


# --- 版の印と入口 --------------------------------------------------------------

def _version(rel: str) -> str:
    return hashlib.sha256((HTML / rel).read_bytes()).hexdigest()[:8]


def test_asset_versions_are_stamped():
    """madoguchi.css と madoguchi.js の参照に、今の中身の版の印が付いている(ビルドが PAGE_ASSETS で付ける)。"""
    from build import config
    assert "css/madoguchi.css" in config.PAGE_ASSETS
    assert "js/madoguchi.js" in config.PAGE_ASSETS
    text = _read(CONTACT)
    assert f'href="/css/madoguchi.css?v={_version("css/madoguchi.css")}"' in text
    assert f'src="/js/madoguchi.js?v={_version("js/madoguchi.js")}"' in text


def test_static_pages_for_assets_lists_both_pages():
    """build_article.py の STATIC_PAGES_FOR_ASSETS に contact/ と privacy/ がある。"""
    src = _read(ROOT / "tools" / "build_article.py")
    block = re.search(r"STATIC_PAGES_FOR_ASSETS = \[(.*?)\]", src, flags=re.S).group(1)
    assert '"contact/index.html"' in block
    assert '"privacy/index.html"' in block


def test_sitemap_has_contact_and_privacy():
    """sitemap.xml に /contact/ と /privacy/ がある。"""
    sm = _read(HTML / "sitemap.xml")
    assert "<loc>https://door.aiseed.dev/contact/</loc>" in sm
    assert "<loc>https://door.aiseed.dev/privacy/</loc>" in sm


def test_footer_links_include_contact_and_privacy():
    """奥付は「このサイトについて、お問い合わせ、プライバシーポリシー、ライセンス、RSS」の順(5.7)。
    全部の頁で同じ形かは test_site_nav.py が見る。"""
    assert [label for label, _ in FOOTER_LINKS] == [
        "このサイトについて", "お問い合わせ", "プライバシーポリシー", "ライセンス", "RSS"]
    for page in (CONTACT, PRIVACY, HTML / "index.html"):
        assert footer_html() in re.sub(r">\s+<", "><", _read(page))


def test_consult_page_links_to_contact():
    """相談の頁の「条件と窓口」から、窓口の頁へ入れる。"""
    text = _read(HTML / "consult" / "index.html")
    part = text.split("<h2>条件と窓口</h2>", 1)[1]
    assert '<a href="/contact/">お問い合わせと照会</a>' in part


# --- /privacy/ ---------------------------------------------------------------

PRIVACY_SECTIONS = ["この方針について", "受け取る物", "使い道", "置き場", "通り道", "ほかへ渡す事",
                    "残す期間", "本人ができる事", "アクセスの記録", "クッキーと端末に置く物", "守り",
                    "変える時", "問い合わせ先", "日付"]


def test_privacy_head_and_no_script():
    """題、canonical、CSP はホームと同じ値。<script> が無い。"""
    text = _read(PRIVACY)
    assert text.startswith("<!DOCTYPE html>")
    assert "<title>プライバシーポリシー | デジタルドア</title>" in text
    assert '<link rel="canonical" href="https://door.aiseed.dev/privacy/">' in text
    assert _csp(text) == [HOME_CSP]
    assert _csp(_read(HTML / "index.html")) == [HOME_CSP]
    assert "<script" not in text
    assert not re.search(r"\son[a-z]+\s*=", text)


def test_privacy_has_sections():
    """6 章の節の見出しが、この順にある。"""
    heads = re.findall(r"<h2>([^<]+)</h2>", _read(PRIVACY))
    assert heads == PRIVACY_SECTIONS


def test_privacy_operator_marks(capsys):
    """【運営者が書く】の印が残っている間は、数を出すだけで落とさない。"""
    n = _read(PRIVACY).count(TODO_MARK)
    with capsys.disabled():
        print(f"\n/privacy/ に残っている【運営者が書く】の印: {n}")
