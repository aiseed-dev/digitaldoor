"""kagi のテストの共通の準備。

リポジトリの最上位から `.venv/bin/python -m pytest -q kagi` と打つと、最上位の `kagi/`(このパッケージの置き場で、
`__init__.py` を持たない)が名前空間のパッケージとして先に見つかることがあります。ここで `kagi/kagi/` の親を
import の道の先頭に置き、本体のパッケージを読ませます。`pip install -e kagi` で入れていても、入れていなくても同じに動きます。
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
