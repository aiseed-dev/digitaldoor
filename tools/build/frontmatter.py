"""フロントマターの解析 —— **外部依存ゼロ**の最小モジュール。

`--- … ---` の平坦な `key: value` を読むだけ。markdown.py(レンダリング)
から独立させてあるのは、**構文の解析だけを使いたい側**が重い依存
(markdown-it / jinja2 / Pillow)を引きずらずに済むようにするため。

series.py(シリーズファイルの構文)と、外部ツール(aiseed-builder などの
管理アプリ)がこれを直接 import する。レンダリングを伴う処理は
markdown.py 側にある。
"""


def parse_frontmatter(text):
    """Parse YAML-like front matter between --- delimiters."""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    meta = {}
    for line in parts[1].strip().split("\n"):
        line = line.strip()
        if ":" in line:
            key, val = line.split(":", 1)
            val = val.strip()
            if val.startswith('"') and val.endswith('"'):
                val = val[1:-1]
            meta[key.strip()] = val
    body = parts[2].strip()
    return meta, body
