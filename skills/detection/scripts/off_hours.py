"""決めた時刻の外の SSH のログインの成功を探す(Python の標準ライブラリだけ)。

使い方(Linux。journald の記録を JSON の行で渡す):
    journalctl _COMM=sshd --since -1d -o json --no-pager | python3 off_hours.py --start 8 --end 20 --users aoki,suzuki

- --start 時から --end 時の前までを「普段」とし、その外の「Accepted」を一行ずつ出します。
- --users を付けると、その名のアカウント(管理者など)だけを見ます。付けなければ全部を見ます。
- --weekend を付けると、土曜と日曜のログインも出します。
- 時刻と曜日は例です。決めるのは会社です(03 の普段の形、04 の決まり 1)。
- 時刻は、このコンピューターの時間帯で読みます。読むだけで、何も変えません。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys

OK = re.compile(r"Accepted (?P<how>\S+) for (?P<u>\S+) from (?P<ip>\S+)")


def scan(lines, start: int, end: int, users: set[str], weekend: bool):
    for raw in lines:
        try:
            e = json.loads(raw)
        except ValueError:
            continue
        msg = e.get("MESSAGE")
        if not isinstance(msg, str):
            continue
        m = OK.search(msg)
        if not m or (users and m.group("u") not in users):
            continue
        when = dt.datetime.fromtimestamp(int(e.get("__REALTIME_TIMESTAMP", 0)) / 1_000_000)
        off = not (start <= when.hour < end) or (weekend and when.weekday() >= 5)
        if off:
            yield when, m.group("u"), m.group("ip"), m.group("how")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="決めた時刻の外のログインを探す")
    p.add_argument("--start", type=int, default=8, help="普段の始まりの時(例。決めるのは会社)")
    p.add_argument("--end", type=int, default=20, help="普段の終わりの時(例。決めるのは会社)")
    p.add_argument("--users", default="", help="見るアカウントの名(カンマ区切り)")
    p.add_argument("--weekend", action="store_true", help="土曜と日曜も外とする")
    a = p.parse_args(argv)
    users = {u for u in a.users.split(",") if u}
    hit = 0
    for when, user, ip, how in scan(sys.stdin, a.start, a.end, users, a.weekend):
        print(f"{when.isoformat(timespec='seconds')}\t{user}\t{ip}\t{how}")
        hit += 1
    return 1 if hit else 0


if __name__ == "__main__":
    sys.exit(main())
