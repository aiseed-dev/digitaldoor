"""シリーズファイル展開: articles/<series>.adoc → .build/articles/ ツリー。

記事群は「シリーズ＝1つの .adoc」(articles/blog.adoc 等)で管理する。各記事は

    // ===== article: 021-software-three-transitions =====
    ---
    slug: software-three-transitions
    date: 2026.05.22
    title.ja: 日本語タイトル
    title.en: English title
    ---
    ifdef::lang-ja[]
    = 日本語タイトル
    (日本語本文)
    endif::[]
    ifdef::lang-en[]
    = English title
    (英語本文)
    endif::[]

という節の並びで書く。フロントマターは従来と同じ平坦な `key: value` で、
日英で値が異なるキーだけ `key.ja` / `key.en` と接尾辞を付ける(裸のキーは
両言語共通)。`lang` と prev/next 連鎖は書かない——展開時に合成・導出する。

expand_all() はこれを既存ビルダーがそのまま読める従来型ツリー

    .build/articles/blog/021-…/ja.adoc, en.adoc, <資産へのsymlink>

に展開し、config の各シリーズディレクトリを .build 側へ付け替える。
下流(build_article.py, images.py, template_vars.py)は無変更で動く。

資産は articles/assets/<シリーズ>/<記事ID>/ に、ai-native-ways の
example-N/ コードは articles/examples/<シリーズ>/<章ID>/ に置き、
展開時に記事ディレクトリへ symlink する。
"""

import re
import shutil
import sys
from pathlib import Path

from .frontmatter import parse_frontmatter

# シリーズファイル名 → .build/articles/ 配下の展開先サブディレクトリ。
# サブシリーズ(server/software)は章番号が1から振り直され、URLも別系統
# なので独立したファイルにする(展開先は親シリーズの下の従来位置)。
SERIES_MAP = {
    "insights.adoc": "insights",
    "blog.adoc": "blog",
    "claude-debian.adoc": "claude-debian",
    "claude-debian-server.adoc": "claude-debian/server",
    "ai-native-ways.adoc": "ai-native-ways",
    "ai-native-ways-software.adoc": "ai-native-ways/software",
    "phosphorus-and-farming.adoc": "phosphorus-and-farming",
    "fable.adoc": "fable",
}

# 展開先サブディレクトリの先頭要素 → config の付け替え対象属性
_DIR_ATTRS = {
    "insights": "INSIGHTS_DIR",
    "blog": "BLOG_DIR",
    "claude-debian": "BOOK_DIR",
    "ai-native-ways": "AIWAYS_DIR",
    "phosphorus-and-farming": "FARMING_DIR",
    "fable": "FABLE_DIR",
}

def site_series_defs(site_root):
    """site.json の `builder.series` を返す(無ければ空)。**依存ゼロ**で読む
    ——このモジュールは管理アプリからも import されるため。"""
    import json
    f = Path(site_root) / "site.json"
    if not f.exists():
        return []
    try:
        cfg = json.loads(f.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return cfg.get("builder", {}).get("series", []) or []


def custom_series_defs(site_root):
    """組み込みでない(サイト独自の)シリーズ定義。

    site.json で `{"file": "pages.adoc", "label": "固定ページ",
    "url_base": "/pages"}` のように宣言すると、そのサイト固有のシリーズに
    なる。組み込み6シリーズ(SERIES_MAP)を持たないサイトでも、自分の
    シリーズを定義して記事を置ける。"""
    out = []
    for s in site_series_defs(site_root):
        f = s.get("file", "")
        if f and f not in SERIES_MAP and (Path(site_root) / "articles" / f).exists():
            d = dict(s)
            d.setdefault("url_base", "/" + f[: -len(".adoc")])
            d.setdefault("label", f[: -len(".adoc")])
            out.append(d)
    return out


def series_map(site_root):
    """組み込みの SERIES_MAP に、サイト独自のシリーズを重ねたもの。
    値は .build/articles/ 配下の展開先サブディレクトリ。"""
    m = dict(SERIES_MAP)
    for d in custom_series_defs(site_root):
        m[d["file"]] = d["url_base"].strip("/")
    return m


_SENTINEL_RE = re.compile(r"^//\s*=====\s*article:\s*(\S+)\s*=====\s*$")
_IFDEF_RE = re.compile(r"^ifdef::lang-(ja|en)\[\]\s*$")
_ENDIF_RE = re.compile(r"^endif::\[\]\s*$")


class SeriesError(SystemExit):
    """シリーズファイルの構文エラー。file:line 付きで即座に落とす——
    書き損じ(ifdef閉じ忘れ等)を黙って飲み込むと1ファイル=1シリーズの
    構成では被害が全章に及ぶため。"""

    def __init__(self, path, line_no, message):
        super().__init__(f"{path}:{line_no}: {message}")


class ArticleUnit:
    """シリーズファイル中の1記事分。"""

    def __init__(self, article_id, line_no):
        self.article_id = article_id  # 例 "021-slug" / "part-1-collapse/03-slug"
        self.line_no = line_no        # シリーズファイル内の開始行(1始まり)
        self.meta = {}                # 統合フロントマター(key.ja/key.en 含む)
        self.bodies = {}              # {"ja": str, "en": str} 存在する言語のみ

    def langs(self):
        return [l for l in ("ja", "en") if l in self.bodies]


def parse_series_file(path):
    """シリーズファイルを ArticleUnit の列(文書順)に読み下す。"""
    path = Path(path)
    lines = path.read_text(encoding="utf-8").split("\n")

    units = []
    unit = None
    seen_ids = set()
    # 現在の記事の中の状態
    fm_lines = None      # フロントマター収集中なら行リスト
    fm_done = False
    cur_lang = None      # ifdef ブロック内なら "ja"/"en"
    ifdef_line = 0
    body = {"ja": [], "en": []}
    explicit = set()     # 明示的な ifdef があった言語

    def flush(end_line_no):
        if unit is None:
            return
        if fm_lines is not None and not fm_done:
            raise SeriesError(path, unit.line_no, "フロントマターの --- が閉じていません")
        if cur_lang is not None:
            raise SeriesError(path, ifdef_line, f"ifdef::lang-{cur_lang}[] が endif::[] で閉じていません")
        if not unit.meta.get("slug"):
            raise SeriesError(path, unit.line_no, f"article {unit.article_id}: slug がありません")
        # ifdef が1つも無い記事は日本語のみとして扱う(このサイトの既定言語。
        # ifdef 無しの本文を両言語に出すと、日本語の本文が /en/ にそのまま
        # 出てしまう事故になるため)
        langs = explicit or {"ja"}
        for lang in langs:
            text = "\n".join(body[lang]).strip()
            if text:
                unit.bodies[lang] = text
        if not unit.bodies:
            raise SeriesError(path, unit.line_no, f"article {unit.article_id}: 本文がありません")
        units.append(unit)

    for i, line in enumerate(lines, start=1):
        m = _SENTINEL_RE.match(line)
        if m:
            flush(i)
            article_id = m.group(1)
            if article_id in seen_ids:
                raise SeriesError(path, i, f"article id が重複しています: {article_id}")
            seen_ids.add(article_id)
            unit = ArticleUnit(article_id, i)
            fm_lines = None
            fm_done = False
            cur_lang = None
            body = {"ja": [], "en": []}
            explicit = set()
            continue

        if unit is None:
            # 最初の区切りより前はコメント・空行のみ許す(タイトル行等は
            # 書き損じの可能性が高いので落とす)
            if line.strip() and not line.strip().startswith("//"):
                raise SeriesError(
                    path, i,
                    "最初の『// ===== article: <id> =====』より前に本文があります",
                )
            continue

        # フロントマター(記事内の最初の --- ブロック)
        if fm_lines is None and not fm_done:
            if line.strip() == "---":
                fm_lines = []
                continue
            if line.strip() == "":
                continue
            raise SeriesError(path, i, "区切りコメントの直後はフロントマター(---)が必要です")
        if fm_lines is not None and not fm_done:
            if line.strip() == "---":
                meta, _ = parse_frontmatter("---\n" + "\n".join(fm_lines) + "\n---\n")
                unit.meta = meta
                fm_done = True
                continue
            fm_lines.append(line)
            continue

        # 本文: ifdef による言語分岐
        im = _IFDEF_RE.match(line)
        if im:
            if cur_lang is not None:
                raise SeriesError(path, i, "ifdef の入れ子はできません")
            cur_lang = im.group(1)
            explicit.add(cur_lang)
            ifdef_line = i
            continue
        if _ENDIF_RE.match(line):
            if cur_lang is None:
                raise SeriesError(path, i, "対応する ifdef のない endif::[] です")
            cur_lang = None
            continue
        if line.startswith("ifdef::") or line.startswith("ifndef::"):
            raise SeriesError(path, i, f"未対応の条件ディレクティブです: {line.strip()}")

        if cur_lang is not None:
            body[cur_lang].append(line)
        else:
            # ifdef の外 = 両言語共通
            body["ja"].append(line)
            body["en"].append(line)

    flush(len(lines))
    return units


def select_lang_meta(meta, lang):
    """統合フロントマターから1言語分を取り出す。`key.<lang>` が裸の `key`
    より優先。もう一方の言語のキーは落とす。`lang` を合成して入れる。"""
    out = {}
    for key, val in meta.items():
        if key.endswith((".ja", ".en")):
            base, suffix = key[:-3], key[-2:]
            if suffix == lang:
                out[base] = val
            elif base not in out:
                # 逆言語しか無いキーの穴は空けておく(後で同言語が来れば上書き)
                pass
        else:
            if key not in out:
                out[key] = val
    # 接尾辞付きが裸より後に並んでいても勝つように、もう1周だけ上書きする
    for key, val in meta.items():
        if key.endswith(f".{lang}"):
            out[key[:-3]] = val
    out["lang"] = lang
    return out


def derive_nav(units, lang):
    """文書順から prev/next 連鎖を導出する。対象言語の本文がある記事だけを
    並べ、その並びで隣を決める(EN が無い章は EN 連鎖から抜ける——従来の
    手書き連鎖と同じ振る舞い)。`number:` を持つ記事だけが対象(ブログは
    ビルダー自身が日付順から導出するので何もしない)。

    返り値: {article_id: {"prev_slug":…, "prev_title":…, "next_slug":…, "next_title":…}}
    """
    chain = [
        u for u in units
        if lang in u.bodies and select_lang_meta(u.meta, lang).get("number")
    ]
    nav = {}
    for i, u in enumerate(chain):
        entry = {}
        if i > 0:
            prev_meta = select_lang_meta(chain[i - 1].meta, lang)
            entry["prev_slug"] = prev_meta.get("slug", "")
            entry["prev_title"] = prev_meta.get("title", "")
        if i + 1 < len(chain):
            next_meta = select_lang_meta(chain[i + 1].meta, lang)
            entry["next_slug"] = next_meta.get("slug", "")
            entry["next_title"] = next_meta.get("title", "")
        nav[u.article_id] = entry
    return nav


def _write_expanded(unit, lang, nav, out_dir):
    meta = select_lang_meta(unit.meta, lang)
    # 導出した prev/next は明示キーが無い場合のみ入れる(prev_title 等を
    # 意図的に短縮している記事はその表記が勝つ)
    for k, v in nav.get(unit.article_id, {}).items():
        meta.setdefault(k, v)
    fm = "\n".join(f"{k}: {v}" for k, v in meta.items() if str(v).strip() != "")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{lang}.adoc").write_text(
        f"---\n{fm}\n---\n\n{unit.bodies[lang]}\n", encoding="utf-8"
    )


def _link_assets(site_root, stem, unit, out_dir):
    """記事の資産(articles/assets/<stem>/<記事ID>/*)と ai-native-ways の
    example コード(articles/examples/<stem>/<章ID>/example-N/)を展開先へ
    symlink する。ファイル単位・ディレクトリ単位の symlink はそれぞれ
    copy_images / collect_aiways_examples がそのまま辿れる。"""
    asset_dir = site_root / "articles" / "assets" / stem / unit.article_id
    if asset_dir.is_dir():
        for f in asset_dir.iterdir():
            if f.is_file():
                (out_dir / f.name).symlink_to(f)
    example_root = site_root / "articles" / "examples" / stem / unit.article_id
    if example_root.is_dir():
        for d in sorted(example_root.iterdir()):
            if d.is_dir():
                (out_dir / d.name).symlink_to(d, target_is_directory=True)


def _is_draft(unit):
    """`draft: true` の記事は下書き(記事エディタの「公開」で解除される)。"""
    return str(unit.meta.get("draft", "")).strip().lower() in ("true", "yes", "1")


def series_files(site_root):
    """存在するシリーズファイルの一覧 [(path, stem, subdir), …]。
    組み込み + site.json で宣言されたサイト独自のシリーズ。"""
    found = []
    for name, subdir in series_map(site_root).items():
        p = site_root / "articles" / name
        if p.exists():
            found.append((p, p.name[: -len(".adoc")], subdir))
    return found


def expand_all(site_root=None):
    """全シリーズファイルを .build/articles/ に展開し、config の各シリーズ
    ディレクトリを展開先へ付け替える。シリーズファイルが1つも無ければ
    何もしない(従来のフォルダ構成のまま動く)。"""
    from . import config  # 遅延 import: パースだけ使う側に
                          # jinja2 等の依存を持ち込まないため
    site_root = Path(site_root) if site_root else config.SITE_ROOT
    found = series_files(site_root)
    if not found:
        return False

    build_root = site_root / ".build" / "articles"
    if build_root.exists():
        shutil.rmtree(build_root)

    for path, stem, subdir in found:
        # draft: true の記事(下書き)はビルドから除外——サイトに出ず、
        # prev/next 連鎖・索引・sitemap からも消える
        units = [u for u in parse_series_file(path) if not _is_draft(u)]
        out_base = build_root / subdir
        nav = {"ja": derive_nav(units, "ja"), "en": derive_nav(units, "en")}
        for unit in units:
            out_dir = out_base / unit.article_id
            for lang in unit.langs():
                _write_expanded(unit, lang, nav[lang], out_dir)
            _link_assets(site_root, stem, unit, out_dir)
        # シリーズルート直下のビルド入力(template-example.html 等)
        root_assets = site_root / "articles" / "assets" / stem / "_root"
        if root_assets.is_dir():
            out_base.mkdir(parents=True, exist_ok=True)
            for f in root_assets.iterdir():
                if f.is_file():
                    (out_base / f.name).symlink_to(f)

    # config のシリーズディレクトリを展開先へ付け替える。親シリーズの
    # ファイルがある場合のみ(サブシリーズ単独では付け替えない——親の
    # 章が消えてしまう)。
    # 付け替えるのは組み込みシリーズだけ。サイト独自のシリーズ
    # (site.json で宣言したもの)は config に対応する属性を持たず、
    # ビルダーが .build/articles/<url_base> を直接見る。
    roots_present = {subdir.split("/")[0] for _, _, subdir in found}
    for root_name in roots_present:
        if root_name not in _DIR_ATTRS:
            continue
        parent_file = [n for n, s in SERIES_MAP.items() if s == root_name]
        if parent_file and not (site_root / "articles" / parent_file[0]).exists():
            print(
                f"警告: {root_name} はサブシリーズのファイルだけがあります。"
                f"親シリーズ({parent_file[0]})が無いため付け替えをスキップします",
                file=sys.stderr,
            )
            continue
        attr = _DIR_ATTRS[root_name]
        setattr(config, attr, build_root / root_name)
    return True
