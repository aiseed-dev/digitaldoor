"""ssh で、失敗が続いた後に同じ所から成功した(当たった)入りを探す(Python の標準ライブラリだけ)。

使い方(Linux。journald の記録を JSON の行で渡す):
    journalctl _COMM=sshd --since -1d -o json --no-pager | python3 fail_then_ok.py --fails 5 --minutes 10

- 同じ送り元の番地から、--minutes 分の中に --fails 回以上の失敗(「Failed password」「Invalid user」)があり、
  その後に「Accepted」が来た時に、一行出します。
- 回数と分は例です。決めるのは会社です(04 の決まり 3)。
- 出すのは、時刻、送り元の番地、アカウントの名、失敗の回数だけです。読むだけで、何も変えません。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict, deque

FAIL = re.compile(r"(?:Failed \S+ for (?:invalid user )?(?P<u1>\S+)|Invalid user (?P<u2>\S+)) from (?P<ip>\S+)")
OK = re.compile(r"Accepted \S+ for (?P<u>\S+) from (?P<ip>\S+)")


def scan(lines, fails: int, minutes: int):
    window = minutes * 60 * 1_000_000  # journald の時刻はマイクロ秒
    seen: dict[str, deque] = defaultdict(deque)
    for raw in lines:
        try:
            e = json.loads(raw)
        except ValueError:
            continue
        msg = e.get("MESSAGE")
        if not isinstance(msg, str):
            continue
        ts = int(e.get("__REALTIME_TIMESTAMP", 0))
        m = FAIL.search(msg)
        if m:
            q = seen[m.group("ip")]
            q.append(ts)
            while q and ts - q[0] > window:
                q.popleft()
            continue
        m = OK.search(msg)
        if m:
            q = seen.get(m.group("ip"))
            while q and ts - q[0] > window:
                q.popleft()
            if q and len(q) >= fails:
                yield ts, m.group("ip"), m.group("u"), len(q)
            seen.pop(m.group("ip"), None)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="失敗が続いた後の成功を探す")
    p.add_argument("--fails", type=int, default=5, help="失敗の回数(例。決めるのは会社)")
    p.add_argument("--minutes", type=int, default=10, help="数える分(例。決めるのは会社)")
    a = p.parse_args(argv)
    import datetime as dt

    hit = 0
    for ts, ip, user, n in scan(sys.stdin, a.fails, a.minutes):
        when = dt.datetime.fromtimestamp(ts / 1_000_000).isoformat(timespec="seconds")
        print(f"{when}\t{ip}\t{user}\t失敗 {n} 回の後に成功")
        hit += 1
    return 1 if hit else 0


if __name__ == "__main__":
    sys.exit(main())
