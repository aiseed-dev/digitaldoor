"""記事のビルド(tools/build/markdown.py)が、AsciiDoc の表を部品に渡す前に整える所の試験。

最初の行のセルの数を列の数とし、以後のセルを列の数ごとに一行へまとめ直す。
"""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

from build.markdown import normalize_adoc_tables, render_body  # noqa: E402

pytest.importorskip("pyasciidoc")


def rows(html):
    """組んだ HTML の表を、行ごとのセルの字の並びにする。"""
    out = []
    for tr in re.findall(r"<tr>(.*?)</tr>", html, flags=re.S):
        out.append([re.sub(r"<[^>]+>", "", c).strip()
                    for c in re.findall(r"<t[hd]>(.*?)</t[hd]>", tr, flags=re.S)])
    return out


def render(src, tmp_path):
    return render_body(src, tmp_path / "001-x" / "ja.adoc")


def test_one_cell_per_line(tmp_path):
    """一行に一つずつ書いたセルが、最初の行の列の数ごとに一行になる。"""
    src = "|===\n| 枠 | できること | 返事\n\n| A\n| 一\n| 2 日\n\n| B\n| 二\n| 3 日\n|===\n"
    assert rows(render(src, tmp_path)) == [
        ["枠", "できること", "返事"], ["A", "一", "2 日"], ["B", "二", "3 日"]]


def test_all_cells_on_one_line_unchanged():
    """一行に全部のセルを書いた表は、手を入れずにそのまま渡す。"""
    src = "前の段落\n\n|===\n| a | b | c\n\n| 1 | 2 | 3\n|  | 5 | \n|===\n\n後の段落"
    assert normalize_adoc_tables(src) == src


def test_continuation_line_joins_previous_cell(tmp_path):
    """`|` で始まらない行は直前のセルの続き(行の最後のセルなら、続きの行をそのまま残す)。"""
    src = "|===\n| a | b\n\n| 1\n| 二の\n続き\n| 三\nの続き\n| 4\n| x\ny | z\n| 6\n|===\n"
    out = normalize_adoc_tables(src)
    assert out.split("\n") == [
        "|===", "| a | b", "| 1 | 二の", "続き", "| 三の続き | 4", "| x y \\| z | 6", "|===", ""]
    # 部品は全角どうしの間の改行を消し、ほかは空白に見せる。行の途中のセルも同じ見え方になる
    assert rows(render(src, tmp_path)) == [
        ["a", "b"], ["1", "二の続き"], ["三の続き", "4"], ["x y | z", "6"]]


def test_escaped_pipe_kept(tmp_path):
    """セルの中の `\\|` はパイプそのものとして残る。"""
    src = "|===\n| 記号 | 意味\n\n| a \\| b\n| または\n|===\n"
    assert rows(render(src, tmp_path)) == [["記号", "意味"], ["a | b", "または"]]


def test_tables_in_listing_and_comment_untouched():
    """コードの囲み(----、```)とコメント(////)の中の `|===` は表として扱わない。"""
    src = ("----\n|===\n| a | b\n| 1\n| 2\n|===\n----\n\n"
           "```\n|===\n| a | b\n| 1\n|===\n```\n\n"
           "////\n|===\n| a | b\n| 1\n|===\n////\n")
    assert normalize_adoc_tables(src) == src


def test_uneven_cells_padded_with_warning(tmp_path, capsys):
    """セルの数が列の数で割り切れない表は、最後の行を空のセルで埋め、警告を一行出す。"""
    src = "|===\n| a | b | c\n\n| 1\n| 2\n| 3\n| 4\n|===\n"
    html = render(src, tmp_path)
    assert rows(html) == [["a", "b", "c"], ["1", "2", "3"], ["4", "", ""]]
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1
    assert "001-x/ja.adoc" in err[0] and "1 番目の表" in err[0]
