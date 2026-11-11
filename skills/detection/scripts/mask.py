"""記録を AI に渡す前に、名前とアドレスを伏せる(Python の標準ライブラリだけ)。

使い方:
    python3 mask.py --names names.txt < 元の記録.txt > 伏せた記録.txt
    python3 mask.py --names names.txt --table 対応表.tsv < 元の記録.txt > 伏せた記録.txt

- IPv4 と IPv6 の番地、メールアドレス、names.txt に一行ずつ書いた名前(人、アカウント、機械)を、
  同じ物には同じ印(IP-1、MAIL-1、NAME-1 …)に置き換えます。同じ番地は同じ印になるので、
  「同じ所から何度も」は伏せた後でも読めます。
- 鍵やトークンらしい長い文字列(英数字と記号の 32 字以上の並び)は KEY に置き換えます。
- --table を付けると、印と元の値の対応を書き出します。対応表は手元だけに置き、AI に渡しません。
- 伏せ方は完全ではありません。渡す前に、伏せた物を人が目で読みます。
"""

from __future__ import annotations

import argparse
import ipaddress
import re
import sys

IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
IPV6 = re.compile(r"(?<![\w:])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![\w:])")
MAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
LONG = re.compile(r"(?<![A-Za-z0-9_\-+/])[A-Za-z0-9_\-+/]{32,}={0,2}")


class Masker:
    def __init__(self, names: list[str]):
        self.table: dict[str, str] = {}
        self.count: dict[str, int] = {}
        # 長い名前から先に置き換える(「suzuki」より「suzuki-pc」を先に)
        self.names = sorted({n for n in names if n}, key=len, reverse=True)

    def _mark(self, kind: str, value: str) -> str:
        key = f"{kind}\0{value}"
        if key not in self.table:
            self.count[kind] = self.count.get(kind, 0) + 1
            self.table[key] = f"{kind}-{self.count[kind]}"
        return self.table[key]

    def _ip(self, m: re.Match) -> str:
        text = m.group(0)
        try:
            ipaddress.ip_address(text)
        except ValueError:
            return text  # 時刻(12:34:56)など、番地でない物はそのまま
        return self._mark("IP", text)

    def line(self, s: str) -> str:
        s = MAIL.sub(lambda m: self._mark("MAIL", m.group(0)), s)
        s = IPV4.sub(self._ip, s)
        s = IPV6.sub(self._ip, s)
        for n in self.names:
            s = re.sub(rf"(?<![\w.-]){re.escape(n)}(?![\w-])", lambda m, n=n: self._mark("NAME", n), s)
        s = LONG.sub("KEY", s)
        return s


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="記録の名前とアドレスを伏せる")
    p.add_argument("--names", help="伏せる名前を一行ずつ書いたファイル(人、アカウント、機械)")
    p.add_argument("--table", help="印と元の値の対応を書き出すファイル(手元だけに置く)")
    a = p.parse_args(argv)
    names: list[str] = []
    if a.names:
        with open(a.names, encoding="utf-8") as f:
            names = [x.strip() for x in f if x.strip() and not x.startswith("#")]
    m = Masker(names)
    for raw in sys.stdin:
        sys.stdout.write(m.line(raw))
    if a.table:
        with open(a.table, "w", encoding="utf-8") as f:
            for key, mark in m.table.items():
                _, value = key.split("\0", 1)
                f.write(f"{mark}\t{value}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
