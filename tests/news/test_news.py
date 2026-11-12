"""ニュース(aiai と同じ作り方): news/tsukuru.py の頼み文と形の確かめ、tools/build_news.py の読み取りと組み立て。"""
import datetime
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tsukuru = load("tsukuru", ROOT / "news" / "tsukuru.py")
sys.path.insert(0, str(ROOT / "tools"))
build_news = load("build_news", ROOT / "tools" / "build_news.py")

GOOD = """= デジタルドア ニュース 2026-11-11
:日付: 2026-11-11

== CSA が Aliro 1.0 の認証製品を足しました
:分野: 規格
:国・地域: 米国
:種別: 発表

CSA は認証製品の一覧に錠を足しました(CSA の発表です)。
二行目の文です。

出典: CSA の一覧(2026-11-10)、https://csa-iot.org/x 、2026-11-11 に確かめました +
出典: 報道(2026-11-10)、https://example.com/a?b=1 、2026-11-11 に確かめました

== <script>二件目</script>
:分野: 事件と弱点
:国・地域: 日本
:種別: 報道

本文です。

出典: 記事(2026-11-09)、https://example.jp/n 、2026-11-11 に確かめました
"""


def test_request_holds_the_guide_the_tastes_and_recent_headings(tmp_path, monkeypatch):
    """頼み文は、ガイドと好みと前の数日の見出しを含み、ガイドの「使い方」は含まない。"""
    monkeypatch.setattr(tsukuru, "HERE", str(tmp_path))
    (tmp_path / "SKILL.md").write_text("---\nname: news\n---\n# ガイド\n守ること\n\n## 使い方\nCLI の話", encoding="utf-8")
    (tmp_path / "好み.adoc").write_text("= 好み\n分野は規格", encoding="utf-8")
    (tmp_path / "2026-11-10.adoc").write_text("= x\n:日付: 2026-11-10\n\n== 昨日の件\n", encoding="utf-8")
    p = tsukuru.request(datetime.date(2026, 11, 11))
    assert "news/2026-11-11.adoc" in p and "守ること" in p and "分野は規格" in p and "2026-11-10 昨日の件" in p
    assert "CLI の話" not in p and "name: news" not in p


def test_check_accepts_a_good_day_and_names_each_problem(tmp_path):
    """形の確かめは、正しい日を通し、:分野:、:種別:、出典の URL と確かめた日の欠けを件ごとに挙げる。"""
    f = tmp_path / "d.adoc"
    f.write_text(GOOD, encoding="utf-8")
    assert tsukuru.check(str(f)) == []
    bad = GOOD.replace(":種別: 発表", ":種別: 噂").replace(":分野: 事件と弱点\n", "").replace(
        "、https://example.jp/n 、2026-11-11 に確かめました", "")
    f.write_text(bad, encoding="utf-8")
    problems = tsukuru.check(str(f))
    assert any("種別" in p and "噂" in p for p in problems)
    assert any("分野" in p for p in problems)
    assert any("URL か確かめた日" in p for p in problems)
    assert tsukuru.check(str(tmp_path / "無い.adoc")) == ["ファイルがありません"]


def test_the_claude_cli_runs_with_web_tools_and_no_api_key(monkeypatch):
    """Claude Code は Web の道具とファイルの道具だけを許して動かし、API キーは渡さない(その PC のログインを使う)。"""
    seen = {}

    def fake_run(cmd, cwd, env, text, capture_output):
        seen.update(cmd=cmd, env=env)
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(tsukuru.subprocess, "run", fake_run)
    monkeypatch.setenv("CLAUDECODE", "1")
    tsukuru.run("claude", "頼み文")
    assert seen["cmd"][:3] == ["claude", "-p", "頼み文"] and "WebFetch" in seen["cmd"] and "Bash" not in seen["cmd"]
    assert "CLAUDECODE" not in seen["env"]


def test_build_reads_entries_attrs_body_and_sources():
    """一日の adoc を、見出し、属性、本文の段落、出典の行に分けて読む。"""
    title, date, entries = build_news.read_day(GOOD)
    assert date == "2026-11-11" and len(entries) == 2
    e = entries[0]
    assert e["attrs"] == {"分野": "規格", "国・地域": "米国", "種別": "発表"}
    assert e["body"] == ["CSA は認証製品の一覧に錠を足しました(CSA の発表です)。 二行目の文です。"]
    assert len(e["sources"]) == 2 and not e["sources"][0].endswith("+")


def test_build_escapes_text_and_links_only_urls():
    """組んだ HTML は見出しと本文を逃がし、出典の URL だけをリンクにする。"""
    _, date, entries = build_news.read_day(GOOD)
    html = build_news.entry_html(date, 2, entries[1])
    assert "<script>" not in html and "&lt;script&gt;" in html
    src = build_news.link_source("記事(2026-11-09)、https://example.com/a?b=1 、2026-11-11 に確かめました")
    assert '<a href="https://example.com/a?b=1" rel="noopener">' in src and src.endswith("、2026-11-11 に確かめました")
    assert 'id="2026-11-11-2"' in html


def test_build_skips_the_guide_and_tastes(tmp_path):
    """news/ の日付の名前の adoc だけを、新しい日から読む。ガイドと好みと、件の無い日は読まない。"""
    (tmp_path / "SKILL.md").write_text("# x", encoding="utf-8")
    (tmp_path / "好み.adoc").write_text("= 好み\n\n== 分野\n", encoding="utf-8")
    (tmp_path / "2026-11-10.adoc").write_text(GOOD.replace("2026-11-11", "2026-11-10"), encoding="utf-8")
    (tmp_path / "2026-11-11.adoc").write_text(GOOD, encoding="utf-8")
    (tmp_path / "2026-11-12.adoc").write_text("= 空\n:日付: 2026-11-12\n", encoding="utf-8")
    assert [d for d, _ in build_news.load_days(tmp_path)] == ["2026-11-11", "2026-11-10"]
