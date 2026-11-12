"""サーバーの表（machines.toml）と、サーバーのコピー（facts）を突き合わせる。設計書 §3.4。

Linux のサーバーを「見る」側。入れる側は install.py。K-05、K-07、K-11、K-12、K-13、K-14。

- サーバーの表は人が書く: サーバー、役、入れてよい人、管理者、入れてよい鍵（公開鍵の指紋）、ホストの鍵の指紋。
- コピーは、読むだけの決まった sh の文（SCRIPT）をサーバーで走らせた出力。何も変えず、秘密の値を出さない。
  人が走らせてファイルで渡すか、`collect` が人の ssh で走らせる（鍵でだけ、ホストの鍵は確かめ済みの物だけ）。
- kagi はサーバーを変えない。直す手は sh の文として出すだけで、走らせるのは人（sudo は人の手）。
- 鍵は公開鍵と指紋だけを読む。秘密鍵のファイルは読まずに拒む。
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import hashlib
import json
import re
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from kagi import ports
from kagi.inventory import Refused

FILENAME = "machines.toml"
FACTS_DIR = "facts"
HEAD = "# kagi-facts 1"
OLD_FACTS_DAYS = 7
OLD_LISTS_DAYS = 7
DISK_FULL = 85  # %
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")  # サーバーの名、ssh の名（先頭に - を許さない）
USER = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
# 入口の守り方。public: 誰でも（認証無しが正しい）。app: 裏のアプリが確かめる（招待コード、ログイン）。
# gate: 前の Caddy が確かめる（basic_auth、forward_auth、Bearer の照合、mTLS）。lan: ネットワークで絞る（remote_ip）。
DOOR_AUTH = ("public", "app", "gate", "lan")
PATH = re.compile(r"^/[A-Za-z0-9._/@+-]{1,200}$")  # データの置き場。ssh の向こうの sh に渡すので字を絞る
UNIT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9@._:-]{0,127}$")
FP = re.compile(r"^SHA256:[A-Za-z0-9+/]{43}$")
KEY_TYPES = (
    "ssh-ed25519",
    "ssh-rsa",
    "ssh-dss",
    "ecdsa-sha2-nistp256",
    "ecdsa-sha2-nistp384",
    "ecdsa-sha2-nistp521",
    "sk-ssh-ed25519@openssh.com",
    "sk-ecdsa-sha2-nistp256@openssh.com",
)
FORBIDDEN_FIELDS = ("value", "private", "password", "passphrase")  # 表に書かせない欄（K-13）

# サーバーで走らせる文。読むだけ。POSIX sh。root でなくても走り、root なら見える物が増える。
SCRIPT = r"""#!/bin/sh
# kagi のサーバーのコピー（kagi/docs/設計書.md §3.4）。読むだけで、何も変えない。秘密の値は出さない。
LC_ALL=C
export LC_ALL
sec() { printf '\n## kagi %s\n' "$1"; }
printf '# kagi-facts 1\n'

sec collected
date +%Y-%m-%dT%H:%M:%S%z
echo "uid=$(id -u)"

sec os
grep -E '^(ID|VERSION_ID|VERSION_CODENAME|PRETTY_NAME)=' /etc/os-release 2>/dev/null
echo "KERNEL=$(uname -r)"
echo "HOSTNAME=$(hostname)"

sec reboot
if [ -f /var/run/reboot-required ]; then echo required; else echo no; fi

sec updates
if command -v apt >/dev/null 2>&1; then
  echo "manager=apt"
  echo "now=$(date +%s)"
  for f in /var/lib/apt/periodic/update-success-stamp /var/lib/apt/lists; do
    if [ -e "$f" ]; then echo "lists=$(stat -c %Y "$f")"; break; fi
  done
  apt list --upgradable 2>/dev/null | awk -F'[/ ]' '/\// {print "pkg", $1, $2}'
else
  echo "manager=none"
fi

sec auto
dpkg-query -W -f='status=${Status}\n' unattended-upgrades 2>/dev/null
grep -hE '^[[:space:]]*APT::Periodic::(Update-Package-Lists|Unattended-Upgrade)' /etc/apt/apt.conf.d/* 2>/dev/null

sec sshd
KEYS='passwordauthentication|permitrootlogin|pubkeyauthentication|kbdinteractiveauthentication|allowusers|allowgroups|authenticationmethods'
if [ "$(id -u)" = 0 ] && sshd -T >/dev/null 2>&1; then
  echo "effective=1"
  sshd -T 2>/dev/null | grep -Ei "^($KEYS) "
else
  echo "effective=0"
  for f in /etc/ssh/sshd_config.d/*.conf /etc/ssh/sshd_config; do
    [ -e "$f" ] || continue
    if [ -r "$f" ]; then grep -Ei "^[[:space:]]*($KEYS)[[:space:]]" "$f"; else echo "unreadable $f"; fi
  done
fi

sec hostkeys
for f in /etc/ssh/ssh_host_*_key.pub; do
  [ -r "$f" ] && ssh-keygen -lf "$f" 2>/dev/null | awk '{print $1, $2, $NF}'
done

sec authorized
getent passwd | awk -F: '($3>=1000 && $3<65534) || $3==0 {print $1, $6}' | while read -r u h; do
  for f in "$h/.ssh/authorized_keys" "$h/.ssh/authorized_keys2"; do
    [ -r "$f" ] && ssh-keygen -lf "$f" 2>/dev/null | awk -v u="$u" '{print u, $1, $2, $NF}'
  done
done

sec users
getent passwd | awk -F: '$3>=1000 && $3<65534 {print $1, $7}'

sec admins
getent group sudo wheel admin 2>/dev/null | awk -F: '{print $1, $4}'

sec accounts
if [ "$(id -u)" = 0 ]; then passwd -S -a 2>/dev/null | awk '{print $1, $2}'; fi

sec sudoers
if [ "$(id -u)" = 0 ]; then
  echo "nopasswd=$(cat /etc/sudoers /etc/sudoers.d/* 2>/dev/null | grep -v '^[[:space:]]*#' | grep -c NOPASSWD)"
fi

sec firewall
for s in ufw nftables firewalld; do echo "$s=$(systemctl is-active "$s" 2>/dev/null)"; done
[ -r /etc/ufw/ufw.conf ] && grep -E '^ENABLED=' /etc/ufw/ufw.conf
[ -r /etc/default/ufw ] && grep -E '^DEFAULT_(INPUT|OUTPUT)_POLICY=' /etc/default/ufw
echo "fail2ban=$(systemctl is-active fail2ban 2>/dev/null)"

sec timers
systemctl list-timers --no-legend --plain 2>/dev/null | awk '{ for (i = 1; i <= NF; i++) if ($i ~ /\.timer$/) print $i }'

sec errors
echo "err7d=$(journalctl -p err --since -7d -q --no-pager 2>/dev/null | wc -l)"
if [ "$(id -u)" = 0 ]; then
  echo "sshfail7d=$(journalctl -u ssh --since -7d -q --no-pager 2>/dev/null | grep -ciE 'failed|invalid user')"
fi

sec listen
ss -ltnp 2>/dev/null

sec time
timedatectl show -p NTP -p NTPSynchronized -p Timezone 2>/dev/null

sec failed
systemctl --failed --no-legend --plain 2>/dev/null | awk '{print $1}'

sec services
systemctl list-units --type=service --state=running --no-legend --plain 2>/dev/null | awk '{print $1}'

sec user-services
systemctl --user list-units --type=service --state=running --no-legend --plain 2>/dev/null | awk '{print $1}'

sec disk
df -P -x tmpfs -x devtmpfs -x overlay -x squashfs -x efivarfs 2>/dev/null | awk 'NR>1 {print $6, $5}'

sec crypt
echo "crypt=$(lsblk -rno TYPE 2>/dev/null | grep -c crypt)"

sec data
for p in "$@"; do
  if [ -e "$p" ]; then
    echo "path $p $(du -skx "$p" 2>/dev/null | awk '{print $1}') $(df -P "$p" 2>/dev/null | awk 'NR==2 {print $5, $4}')"
  else
    echo "missing $p"
  fi
done

sec caddy
if [ -r /etc/caddy/Caddyfile ]; then
  awk '
    /^[[:space:]]*(#|$)/ { next }
    /^[^[:space:]}]/ { sub(/[[:space:]]*\{[[:space:]]*$/, ""); print "site " $0; next }
    $1 == "reverse_proxy" { for (i = 2; i <= NF; i++) if ($i ~ /^[A-Za-z0-9.:_\[\]-]+$/) print "  reverse_proxy " $i; next }
    $1 == "import" || $1 == "admin" || $1 == "root" { print "  " $1 " " $NF; next }
    $1 == "respond" { for (i = 2; i <= NF; i++) if ($i ~ /^[0-9][0-9][0-9]$/) print "  respond " $i; next }
    $1 ~ /^@/ { print "  matcher " $2; next }
    $1 == "not" && $2 ~ /^(remote_ip|header|expression)$/ { print "  " $2; next }
    $1 ~ /^(basic_auth|basicauth|forward_auth|client_auth|remote_ip|file_server|header|expression)$/ { print "  " $1 }
  ' /etc/caddy/Caddyfile
elif [ -e /etc/caddy/Caddyfile ]; then
  echo "unreadable"
fi

sec load
echo "since=$(uptime -s 2>/dev/null)"
echo "loadavg=$(cut -d' ' -f1-3 /proc/loadavg 2>/dev/null)"
awk '/^(MemTotal|MemAvailable):/ {sub(":", "", $1); print $1 "=" $2}' /proc/meminfo 2>/dev/null
"""


# ---- 鍵（公開鍵と指紋だけ） ----


@dataclass(frozen=True)
class KeyInfo:
    type: str
    fingerprint: str
    bits: int | None  # RSA だけ


def _string(buf: bytes, off: int) -> tuple[bytes, int]:
    n = int.from_bytes(buf[off : off + 4], "big")
    return buf[off + 4 : off + 4 + n], off + 4 + n


def key_info(line: str) -> KeyInfo:
    """公開鍵の一行（`ssh-ed25519 AAAA… comment`。authorized_keys の前置きの options も可）→ 種類と指紋。"""
    if "PRIVATE KEY" in line:
        raise Refused("秘密鍵は読まない（K-13）。公開鍵（.pub）を渡す")
    parts = line.split()
    for i, t in enumerate(parts[:-1]):
        if t in KEY_TYPES:
            try:
                blob = base64.b64decode(parts[i + 1], validate=True)
            except (binascii.Error, ValueError):
                break
            name, off = _string(blob, 0)
            if name.decode("ascii", "replace") != t:
                break
            bits = None
            if t == "ssh-rsa":
                _e, off = _string(blob, off)
                n, off = _string(blob, off)
                bits = int.from_bytes(n, "big").bit_length()
            fp = "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")
            return KeyInfo(t, fp, bits)
    raise Refused("公開鍵として読めない")


def read_pub(path: Path) -> list[tuple[str, KeyInfo]]:
    """公開鍵のファイル（.pub）→ （行、種類と指紋）。.pub でない物と秘密鍵は読まずに拒む。"""
    path = Path(path).expanduser()
    if path.suffix != ".pub":
        raise Refused(f"公開鍵のファイル（.pub）だけを読む: {path.name}")
    try:
        text = path.read_text("utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise Refused(f"公開鍵を読めない: {path} ({type(e).__name__})") from None
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append((line, key_info(line)))
    if not out:
        raise Refused(f"公開鍵が無い: {path}")
    return out


# ---- サーバーの表 ----


@dataclass
class Table:
    machines: list[dict] = field(default_factory=list)
    keys: list[dict] = field(default_factory=list)
    doors: list[dict] = field(default_factory=list)  # 入口の表: どの入口に、誰が、何で守られて入るか

    def machine(self, host: str) -> dict:
        for m in self.machines:
            if m["host"] == host:
                return m
        raise Refused(f"サーバーの表に {host} が無い（{FILENAME} の [[machine]]）")

    def key(self, kid: str) -> dict | None:
        return next((k for k in self.keys if k.get("id") == kid), None)

    def hosts(self) -> list[str]:
        return [m["host"] for m in self.machines]

    def machines_of(self, kid: str) -> list[str]:
        return [m["host"] for m in self.machines if kid in m.get("keys", [])]


def _names(row: dict, key: str, what: str) -> list[str]:
    v = row.get(key, [])
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise Refused(f"サーバーの表の {what} の {key} は名の並びで書く")
    return v


def parse_table(text: str, where: str = FILENAME) -> Table:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        m = re.search(r"line \d+, column \d+", str(e))
        raise Refused(f"サーバーの表が TOML として読めない: {where} ({m.group(0) if m else 'TOML'})") from None
    machines, keys, doors = data.get("machine", []), data.get("key", []), data.get("door", [])
    for rows, name in ((machines, "machine"), (keys, "key"), (doors, "door")):
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise Refused(f"サーバーの表の {name} は [[{name}]] の並びで書く")
        for r in rows:
            bad = [k for k in r if str(k).lower() in FORBIDDEN_FIELDS]
            if bad:
                raise Refused(f"サーバーの表に {bad[0]} の欄がある。値は書かない（K-13）。欄を消してから走らせる")
    seen: set[str] = set()
    for i, m in enumerate(machines, 1):
        host = m.get("host")
        if not isinstance(host, str) or not NAME.match(host):
            raise Refused(f"サーバーの表の {i} 件目の host が名として読めない（英数字と . _ -）")
        if host in seen:
            raise Refused(f"サーバーの表の host が重複: {host}")
        seen.add(host)
        if not NAME.match(str(m.get("ssh", host))):
            raise Refused(f"サーバーの表の {host} の ssh が名として読めない（~/.ssh/config の名を書く）")
        for k in ("users", "admins"):
            for u in _names(m, k, host):
                if not USER.match(u):
                    raise Refused(f"サーバーの表の {host} の {k} に、アカウントの名として読めない物がある")
        _names(m, "keys", host)
        for path in _names(m, "data", host):
            if not PATH.match(path) or ".." in path:
                raise Refused(f"サーバーの表の {host} の data は / から始まる道で書く（英数字と . _ / @ + -）")
        for k in ("services", "user_services", "timers"):
            for u in _names(m, k, host):
                if not UNIT.match(u):
                    raise Refused(f"サーバーの表の {host} の {k} に、サービスの名として読めない物がある")
        if "hostkey" in m and not (isinstance(m["hostkey"], str) and FP.match(m["hostkey"])):
            raise Refused(f"サーバーの表の {host} の hostkey は指紋（SHA256:…）で書く")
    seen = set()
    for i, k in enumerate(keys, 1):
        kid = k.get("id")
        if not isinstance(kid, str) or not kid:
            raise Refused(f"サーバーの表の鍵の {i} 件目に id が無い")
        if kid in seen:
            raise Refused(f"サーバーの表の鍵の id が重複: {kid}")
        seen.add(kid)
        if "fingerprint" in k and not (isinstance(k["fingerprint"], str) and FP.match(k["fingerprint"])):
            raise Refused(f"サーバーの表の鍵 {kid} の fingerprint は指紋（SHA256:…）で書く")
        if "fingerprint" not in k and "pub" not in k:
            raise Refused(f"サーバーの表の鍵 {kid} に fingerprint も pub（公開鍵のファイル）も無い")
    for i, d in enumerate(doors, 1):
        if not isinstance(d.get("host"), str) or not isinstance(d.get("site"), str) or not d.get("site"):
            raise Refused(f"入口の表の {i} 件目に host か site が無い")
        if d.get("auth") not in DOOR_AUTH:
            raise Refused(f"入口の表の {d['site']} の auth は {' | '.join(DOOR_AUTH)} のどれか")
    return Table(machines, keys, doors)


def load(path: Path) -> Table:
    try:
        text = Path(path).read_text("utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise Refused(f"サーバーの表を読めない: {path} ({type(e).__name__})。無ければ kagi init") from None
    return parse_table(text, str(path))


def key_fingerprints(table: Table) -> tuple[dict[str, str], list[str]]:
    """鍵の id → 指紋。pub があればそこから計算し、fingerprint と違えば problems に。"""
    fps: dict[str, str] = {}
    problems: list[str] = []
    for k in table.keys:
        kid, fp = k["id"], k.get("fingerprint")
        if "pub" in k:
            try:
                infos = read_pub(Path(str(k["pub"])))
            except Refused as e:
                if fp is None:
                    problems.append(f"{kid}: {e}")
            else:
                got = infos[0][1].fingerprint
                if fp is not None and fp != got:
                    problems.append(f"{kid}: 表の fingerprint と pub のファイルの指紋が違う")
                fp = fp or got
        if fp:
            fps[kid] = fp
    return fps, problems


# ---- コピー ----


@dataclass
class Facts:
    sections: dict[str, list[str]] = field(default_factory=dict)

    def lines(self, name: str) -> list[str]:
        return self.sections.get(name, [])

    def pairs(self, name: str) -> dict[str, str]:
        """`KEY=value` の行。値の前後の " は外す。"""
        out = {}
        for line in self.lines(name):
            k, sep, v = line.partition("=")
            if sep:
                out[k.strip()] = v.strip().strip('"')
        return out

    @property
    def root(self) -> bool:
        return self.pairs("collected").get("uid") == "0"

    @property
    def collected(self) -> dt.datetime | None:
        for line in self.lines("collected"):
            try:
                return dt.datetime.strptime(line.strip(), "%Y-%m-%dT%H:%M:%S%z")
            except ValueError:
                continue
        return None


def parse_facts(text: str) -> Facts:
    if not text.lstrip().startswith(HEAD):
        raise Refused("サーバーのコピーとして読めない（kagi machine script の出力を渡す）")
    facts = Facts()
    cur: list[str] | None = None
    for line in text.splitlines():
        if line.startswith("## kagi "):
            cur = facts.sections.setdefault(line[len("## kagi ") :].strip(), [])
        elif cur is not None and line.strip():
            cur.append(line.rstrip())
    return facts


def load_facts(path: Path) -> Facts:
    try:
        return parse_facts(Path(path).read_text("utf-8", errors="replace"))
    except OSError as e:
        raise Refused(f"サーバーのコピーを読めない: {path} ({type(e).__name__})") from None


def latest_facts(d: Path, host: str) -> Path | None:
    files = sorted((Path(d) / FACTS_DIR).glob(f"{host}-*.txt"))
    return files[-1] if files else None


def ssh_command(alias: str, paths: list[str] | None = None) -> list[str]:
    """人の ssh で、鍵でだけ、確かめ済みのサーバーにだけ入る。尋ねられる形（パスワード、初めてのサーバー）は全部落ちる。"""
    if not NAME.match(alias):
        raise Refused("ssh の名として読めない")
    return [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=yes",
        "-o", "PasswordAuthentication=no",
        "-o", "KbdInteractiveAuthentication=no",
        "-o", "ConnectTimeout=10",
        "--", alias, "sh", "-s", *_args(paths),
    ]  # fmt: skip


def _args(paths: list[str] | None) -> list[str]:
    """SCRIPT に渡すデータの置き場（文は変えない。大きさを測る道だけを引数で）。"""
    paths = list(paths or [])
    for path in paths:
        if not PATH.match(path) or ".." in path:
            raise Refused("データの置き場の道として読めない")
    return ["--", *paths] if paths else []


def collect(alias: str | None, runner=None, timeout: int = 90, paths: list[str] | None = None) -> str:
    """コピーを取る。alias が None ならこの PC で。サーバーには SCRIPT を標準入力で渡すだけ（置かない、変えない）。

    paths は大きさを測るデータの置き場（サーバーの表の data）。測るのに時間が掛かるので待つ時間を延ばす。
    """
    cmd = ["sh", "-s", *_args(paths)] if alias is None else ssh_command(alias, paths)
    if paths:
        timeout = max(timeout, 600)
    try:
        r = (runner or subprocess.run)(cmd, input=SCRIPT, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise Refused(f"{cmd[0]} が無い") from None
    except subprocess.TimeoutExpired:
        raise Refused(f"{timeout} 秒で返らなかった") from None
    if not r.stdout.lstrip().startswith(HEAD):
        why = (r.stderr or "").strip().splitlines()[-1:] or ["出力が無い"]
        hint = (
            "。鍵で入れる事と、ホストの鍵を自分で確かめて known_hosts に入れた事を見る（skills/kagi/04-サーバーを見る.md）"
            if alias is not None
            else ""
        )
        raise Refused(f"コピーが取れなかった（{why[0][:200]}）{hint}")
    return r.stdout


def save_facts(d: Path, host: str, text: str, now: dt.datetime | None = None) -> Path:
    now = now or dt.datetime.now()
    out = Path(d) / FACTS_DIR
    out.mkdir(parents=True, exist_ok=True)
    p = out / f"{host}-{now.strftime('%Y%m%d-%H%M%S')}.txt"
    p.write_text(text, "utf-8")
    p.chmod(0o600)  # アカウントの名とポートが入る
    return p


# ---- 突き合わせ ----


@dataclass
class Finding:
    fix: bool  # 直す物か、見るだけか
    what: str
    result: str
    how: list[str] = field(default_factory=list)  # 直す手（sh の行。kagi は走らせない）
    confirm: bool = False  # 連載 2-02 の八つに当たる手。人が一行ずつ読んで確かめてから打つ
    later: list[str] = field(
        default_factory=list
    )  # 二段目の手（外へ出る通信を閉じる）。fix の終わりに分けて出す

    def all_how(self) -> list[str]:
        """一段目と二段目の手を、二段目の見出しを挟んで一続きに（画面で一つの物ごとに見せる時）。"""
        return self.how + ([*STAGE2_HEAD, *self.later] if self.later else [])


# 連載 2-02 の「重要な仕事は人が確認する」八つに当たる直す物の見出し（認証、鍵、権限、ポートを開ける・閉じる）。
# 当たらない物: 更新（自動の更新を入れるのは新しいソフトだが、apt の unattended-upgrades で連載 第 5 章が勧める
# 形そのものなので、こちら）、時計、サービス、ディスク、控え、データ、鍵の表（机の側の表を書く物）。
CONFIRM_WHAT = frozenset(
    {"認証（ssh）", "ホストの鍵", "入っている鍵", "アカウント", "管理者", "防火壁", "ポート", "入口"}
)


def _needs_confirm(f: Finding) -> bool:
    if not f.fix or f.what not in CONFIRM_WHAT:
        return False
    # 表にあって開いていないポートは、サービスを起こす側の物（ポートを開ける手ではない）
    return not (f.what == "ポート" and f.result.startswith("表にあって開いていない"))


# 防火壁で出る向きを閉じる時の手。手元のポート（32768 以上）から出ている繋がりの、相手のポートを数える
OUT_PORTS_NOW = (
    'ss -Htn state established | awk \'{n=split($3,l,":"); m=split($4,r,":"); if (l[n]+0>=32768) print r[m]}\''
    " | sort -n | uniq -c | sort -rn"
)
UFW_ALLOW_NOTE = (
    "# 2. 要る通信を許可する。DNS(53)、時刻合わせ(123)、Web(80,443)。"
    "1 で出たポートのうち要る物も足す（ssh と git の push は 22: sudo ufw allow out 22/tcp）"
)
UFW_UNDO_TIMER = "sudo systemd-run --unit=ufw-modosu --on-active=10min /usr/sbin/ufw default allow outgoing"
UFW_UNDO_CANCEL = "sudo systemctl stop ufw-modosu.timer"
# 外へ出る通信を閉じる手。閉じると更新や名前解決が止まりうるので、入る向きの後に、要る外への道を確かめてから
OUT_CLOSE = [
    "# 1. 先に、このサーバーが今、外部のどのポートへ通信しているかを数える（sudo は要らない）",
    OUT_PORTS_NOW,
    UFW_ALLOW_NOTE,
    "# 67/udp はネットワークの番地をもらい直す要求（DHCP）。無いと番地が切れた時にネットワークから落ちる",
    "sudo ufw allow out 53 && sudo ufw allow out 123/udp && sudo ufw allow out 80,443/tcp && sudo ufw allow out 67/udp",
    "# 家のネットワークの機器（NAS、データベースのサーバー）へ出るなら: sudo ufw allow out to <LAN>",
    "# 3. 10 分後に自動で外向き許可に戻すタイマーを仕掛ける（許可し忘れで何かが止まっても、待てば戻る）",
    UFW_UNDO_TIMER,
    "# 4. 外向きの通信を原則禁止にする",
    "sudo ufw default deny outgoing",
    "# 5. 確かめる。止められた通信は: sudo journalctl -k --grep 'UFW BLOCK'",
    "sudo apt update",
    "# 6. 問題が無ければ、3 のタイマーを取り消す（取り消さないと 10 分後に許可に戻る）",
    UFW_UNDO_CANCEL,
]
STAGE2_HEAD = [
    "# ---- 二段目: 外へ出る通信を閉じる",
    "# 一段目を打ち、別の窓から ssh で入れるのを確かめた後に。要る外への道を 1 で確かめてから、一行ずつ打つ。",
]

SSHD_DROPIN = "/etc/ssh/sshd_config.d/10-kagi.conf"
HOW_SSHD = [
    "# 先に、別の窓から鍵で入れる事を確かめる（入れなくなるのを防ぐ）",
    (
        "printf '%s\\n' 'PasswordAuthentication no' 'KbdInteractiveAuthentication no' 'PermitRootLogin no' "
        f"'PubkeyAuthentication yes' | sudo tee {SSHD_DROPIN} >/dev/null"
    ),
    "sudo sshd -t && sudo systemctl reload ssh",
]
NOLOGIN = ("nologin", "/false", "/sync")


def _sshd(facts: Facts) -> tuple[dict[str, str], bool, list[str]]:
    """sshd の設定（最初に出た値が効く）。root のコピーなら実際に効いている値。"""
    vals: dict[str, str] = {}
    effective, unreadable = False, []
    for line in facts.lines("sshd"):
        s = line.strip()
        if s == "effective=1":
            effective = True
        elif s.startswith("unreadable "):
            unreadable.append(s.split(" ", 1)[1])
        elif not s.startswith("effective="):
            k, _, v = s.partition(" ")
            vals.setdefault(k.lower(), v.strip().lower())
    return vals, effective, unreadable


def _check_sshd(facts: Facts, allowed: set[str], out: list[Finding]) -> None:
    vals, effective, unreadable = _sshd(facts)
    if not facts.lines("sshd"):
        out.append(Finding(False, "認証（ssh）", "sshd の設定がコピーに無い"))
        return
    src = "実際に効いている値" if effective else "設定のファイルから（Match の中は見ていない）"
    for f in unreadable:
        out.append(Finding(False, "認証（ssh）", f"読めなかった設定: {f}（root のコピーで見る）"))
    pa = vals.get("passwordauthentication")
    if pa != "no":
        out.append(
            Finding(
                True,
                "認証（ssh）",
                f"パスワードで入れる（PasswordAuthentication {pa or '書いていない。既定は yes'}）。{src}",
                HOW_SSHD,
            )
        )
    kbd = vals.get("kbdinteractiveauthentication")
    if kbd == "yes":
        out.append(
            Finding(
                True, "認証（ssh）", "尋ねて答える形で入れる（KbdInteractiveAuthentication yes）", HOW_SSHD
            )
        )
    elif kbd is None:
        out.append(
            Finding(
                False,
                "認証（ssh）",
                "KbdInteractiveAuthentication を書いていない（root のコピーで効いている値を見る）",
            )
        )
    prl = vals.get("permitrootlogin")
    if prl == "yes":
        out.append(Finding(True, "認証（ssh）", "root がパスワードで入れる（PermitRootLogin yes）", HOW_SSHD))
    elif prl != "no":
        out.append(
            Finding(
                False,
                "認証（ssh）",
                f"root が鍵で入れる（PermitRootLogin {prl or '書いていない。既定は prohibit-password'}）",
            )
        )
    if vals.get("pubkeyauthentication") == "no":
        out.append(Finding(True, "認証（ssh）", "鍵で入れない（PubkeyAuthentication no）", HOW_SSHD))
    au = vals.get("allowusers")
    if au is None and vals.get("allowgroups") is None:
        out.append(
            Finding(False, "認証（ssh）", "入れる人を絞っていない（AllowUsers も AllowGroups も無い）")
        )
    elif au is not None and allowed:
        extra = sorted({u.split("@")[0] for u in au.split()} - allowed)
        if extra:
            out.append(Finding(True, "認証（ssh）", f"AllowUsers に表に無い人: {', '.join(extra)}"))
    if pa == "no" and kbd in ("no", None) and prl in ("no", None, "prohibit-password", "without-password"):
        out.append(Finding(False, "認証（ssh）", f"鍵でだけ入る形。{src}"))


def _check_keys(
    table: Table, m: dict, facts: Facts, inventory_ids: set[str] | None, out: list[Finding]
) -> None:
    host = m["host"]
    fps, problems = key_fingerprints(table)
    for p in problems:
        out.append(Finding(True, "鍵の表", p))
    want: dict[str, str] = {}  # 指紋 → id
    for kid in m.get("keys", []):
        if table.key(kid) is None:
            out.append(Finding(True, "鍵の表", f"サーバーの keys の {kid} が [[key]] に無い"))
        elif kid in fps:
            want[fps[kid]] = kid
        if inventory_ids is not None and kid not in inventory_ids:
            out.append(
                Finding(
                    True,
                    "鍵の表",
                    f"鍵 {kid} が在庫表に無い（K-14。inventory.toml に同じ id で一件）",
                    [
                        f'# inventory.toml に [[secret]] を一件: id = "{kid}"、kind = "key"、where、holder、role、expires、rotate_days'
                    ],
                )
            )

    # ホストの鍵（このサーバーが本物かを確かめる指紋）
    ed = [ln.split() for ln in facts.lines("hostkeys") if "ED25519" in ln.upper()]
    got = ed[0][1] if ed and len(ed[0]) >= 2 else None
    if got is None:
        out.append(Finding(False, "ホストの鍵", "ed25519 のホストの鍵がコピーに無い"))
    elif "hostkey" not in m:
        out.append(
            Finding(False, "ホストの鍵", f'表に指紋が無い。サーバーの画面で確かめてから書く: hostkey = "{got}"')
        )
    elif m["hostkey"] != got:
        out.append(
            Finding(
                True,
                "ホストの鍵",
                f"表と違う（表 {m['hostkey']}、サーバー {got}）。入れ直したのでなければ、別のサーバーか、間に誰か居る",
                [
                    f"# {host} の画面で: ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub  を見て、表かサーバーのどちらが正しいか決める"
                ],
            )
        )

    # 入っている鍵
    seen: set[str] = set()
    for line in facts.lines("authorized"):
        cols = line.split()
        if len(cols) < 4:
            continue
        user, bits, fp, typ = cols[0], cols[1], cols[2], cols[3].strip("()").upper()
        seen.add(fp)
        if fp not in want:
            out.append(
                Finding(
                    True,
                    "入っている鍵",
                    f"表に無い鍵が {user} に入っている（{typ} {fp}）",
                    [
                        f"# {user} の ~/.ssh/authorized_keys から、指紋 {fp} の行を消す（表に足すなら [[key]] と keys に）",
                        f"#   指紋の見方: ssh-keygen -lf ~{user}/.ssh/authorized_keys",
                    ],
                )
            )
        if typ == "DSA" or (typ == "RSA" and bits.isdigit() and int(bits) < 2048):
            out.append(
                Finding(True, "入っている鍵", f"弱い鍵が {user} に入っている（{typ} {bits} bit {fp}）")
            )
        elif typ == "RSA" and bits.isdigit() and int(bits) < 3072:
            out.append(
                Finding(
                    False, "入っている鍵", f"{user} に RSA {bits} bit（ed25519 に替える日を決める。{fp}）"
                )
            )
    for fp, kid in want.items():
        if fp not in seen:
            who = "" if facts.root else "（root でないコピーなので、他の人の分は見えていない）"
            out.append(Finding(False, "入っている鍵", f"表の鍵 {kid} がサーバーに見当たらない{who}"))
    if not facts.root:
        out.append(
            Finding(False, "入っている鍵", "root でないコピー。見えたのはコピーを取った人の authorized_keys だけ")
        )


def _check_people(m: dict, facts: Facts, out: list[Finding]) -> None:
    users, admins = set(m.get("users", [])), set(m.get("admins", []))
    allowed = users | admins
    login = [
        c[0]
        for c in (ln.split() for ln in facts.lines("users"))
        if c and not (len(c) > 1 and c[1].endswith(NOLOGIN))
    ]
    if not allowed:
        if login:
            out.append(
                Finding(
                    False,
                    "アカウント",
                    f"表に users も admins も無いので比べていない（入れる人 {len(login)}: {', '.join(login)}）",
                )
            )
    else:
        for u in sorted(set(login) - allowed):
            out.append(
                Finding(
                    True,
                    "アカウント",
                    f"表に無いアカウント: {u}",
                    [f"sudo usermod -L -e 1 {u}   # 止める（消すのは中身を見てから: sudo deluser {u}）"],
                )
            )
        for u in sorted(allowed - set(login)):
            out.append(Finding(False, "アカウント", f"表にあってサーバーに無い: {u}"))
    members: set[str] = set()
    for line in facts.lines("admins"):
        cols = line.split()
        if len(cols) > 1:
            members |= {x for x in cols[1].split(",") if x}
    if admins or not members:
        for u in sorted(members - admins):
            out.append(
                Finding(True, "管理者", f"表に無い管理者（sudo の組）: {u}", [f"sudo gpasswd -d {u} sudo"])
            )
        if admins:
            # 誰が sudo を持つかを、表と合っている時も必ず一行（sudo の要る手を誰が打つかが分かるように）
            names = "、".join(sorted(members)) or "無し"
            if members == admins:
                tail = "表の通り"
            elif members - admins:
                tail = "表に無い人が居る"
            else:
                tail = f"表の admins に居て持たない: {'、'.join(sorted(admins - members))}"
            out.append(Finding(False, "管理者", f"sudo を持つ人 {len(members)}: {names}（{tail}）"))
    else:
        out.append(
            Finding(
                False,
                "管理者",
                f"表に admins が無いので比べていない（sudo の組 {len(members)}: {', '.join(sorted(members))}）",
            )
        )
    for line in facts.lines("accounts"):
        cols = line.split()
        if len(cols) == 2 and cols[1] == "NP" and cols[0] in login:
            out.append(
                Finding(True, "アカウント", f"パスワードが空: {cols[0]}", [f"sudo passwd -l {cols[0]}"])
            )
    np = facts.pairs("sudoers").get("nopasswd")
    if np and np.isdigit() and int(np) > 0:
        out.append(Finding(True, "管理者", f"パスワード無しの sudo が {np} 行（/etc/sudoers と sudoers.d）"))


@dataclass
class Site:
    name: str
    upstreams: list[str] = field(default_factory=list)
    static: bool = False
    gate: list[str] = field(default_factory=list)  # Caddy の側で確かめている形
    lan: bool = False

    def what(self) -> str:
        to = "、".join(self.upstreams) or ("置いてあるファイル" if self.static else "通す先が見えない")
        guard = "、".join(dict.fromkeys(self.gate)) or (
            "ネットワークで絞る行がある" if self.lan else "Caddy の側の認証は無い"
        )
        return f"→ {to}。{guard}"


def parse_caddy(lines: list[str]) -> tuple[list[Site], bool]:
    """コピーの caddy の節 → 入口の一覧。（snippet）と全体の設定（名の無い { }）は入口でない。"""
    sites: list[Site] = []
    group: list[Site] = []  # 「a, b {」は同じ中身の入口
    matcher_auth = responded = unreadable = False

    def close() -> None:
        if matcher_auth and responded:
            for st in group:
                st.gate.append("Authorization の照合")

    for line in lines:
        s = line.strip()
        if s == "unreadable":
            unreadable = True
        elif line.startswith("site "):
            close()
            name = line[5:].strip()
            matcher_auth = responded = False
            group = []
            if name and not name.startswith("(") and name != "{":
                group = [Site(n.strip()) for n in name.split(",") if n.strip()]
                sites += group
        else:
            d, _, arg = s.partition(" ")
            for st in group:
                if d == "reverse_proxy" and arg:
                    st.upstreams.append(arg)
                elif d == "file_server":
                    st.static = True
                elif d in ("basic_auth", "basicauth", "forward_auth", "client_auth"):
                    st.gate.append(d)
                elif d == "remote_ip" or (d == "matcher" and arg == "remote_ip"):
                    st.lan = True
            if d == "matcher" and arg in ("header", "expression"):
                matcher_auth = True
            elif d == "respond" and arg in ("401", "403"):
                responded = True
    close()
    return sites, unreadable


def _check_doors(table: Table, host: str, facts: Facts, out: list[Finding]) -> None:
    sites, unreadable = parse_caddy(facts.lines("caddy"))
    doors = [d for d in table.doors if d["host"] == host]
    if unreadable:
        out.append(Finding(False, "入口", "Caddy の設定が読めなかった（root のコピーで見る）"))
    if not sites:
        for d in doors:
            out.append(Finding(False, "入口", f"表にあって、Caddy の設定に見当たらない: {d['site']}"))
        return
    if not doors:
        names = "、".join(st.name for st in sites)
        out.append(Finding(False, "入口", f"入口の表にこのサーバーが無い（入口 {len(sites)}: {names}）"))
        return
    by = {d["site"]: d for d in doors}
    for st in sites:
        d = by.get(st.name)
        if d is None:
            out.append(
                Finding(
                    True,
                    "入口",
                    f"表に無い入口: {st.name}（{st.what()}）",
                    [
                        (
                            "# machines.toml に [[door]] を一件: host、site、who（誰が入れるか）、"
                            "auth（public | app | gate | lan）。要らない入口なら Caddyfile から外す"
                        )
                    ],
                )
            )
            continue
        who = f"入れるのは: {d.get('who', '書いていない')}"
        if d["auth"] == "gate" and not st.gate:
            out.append(
                Finding(
                    True,
                    "入口",
                    f"門が無い: {st.name}（表は gate。Caddy の設定に認証の行が無く、誰でも通る。{who}）",
                    [
                        "# Caddyfile のこの入口に、確かめる行を足す（basic_auth、forward_auth、Bearer の照合。見本は seed のリポジトリ https://github.com/aiseed-dev/seed の deploy/ にある）"
                    ],
                )
            )
        elif d["auth"] == "lan" and not st.lan:
            out.append(
                Finding(
                    True,
                    "入口",
                    f"ネットワークで絞っていない: {st.name}（表は lan。Caddy の設定に remote_ip が無い。{who}）",
                    ["# Caddyfile のこの入口に @inside remote_ip <LAN> を書き、それ以外は respond 403"],
                )
            )
        elif d["auth"] == "public" and (st.gate or st.lan):
            out.append(
                Finding(
                    False, "入口", f"{st.name}: 表は誰でも（public）だが、設定は絞っている（{st.what()}）"
                )
            )
        elif d["auth"] == "app":
            out.append(
                Finding(
                    False,
                    "入口",
                    f"{st.name}: 裏のアプリが確かめる（{st.what()}。アプリの中は見ていない。{who}）",
                )
            )
        else:
            label = {"public": "誰でも", "gate": "Caddy が確かめる", "lan": "ネットワークで絞る"}[d["auth"]]
            out.append(Finding(False, "入口", f"{st.name}: {label}（{st.what()}。{who}）"))
    seen = {st.name for st in sites}
    for d in doors:
        if d["site"] not in seen:
            out.append(
                Finding(
                    True,
                    "入口",
                    f"表にあって、Caddy の設定に無い: {d['site']}",
                    [
                        "# まだ置いていない入口なら、置く（配備の手順）。もう要らない入口なら machines.toml から消す"
                    ],
                )
            )


def check(
    table: Table,
    host: str,
    facts: Facts,
    ports_table: list[dict] | None = None,
    inventory_ids: set[str] | None = None,
    now: dt.datetime | None = None,
) -> list[Finding]:
    """サーバーのコピーを、サーバーの表・ポートの表・在庫表と突き合わせる。"""
    m = table.machine(host)
    out: list[Finding] = []
    now = now or dt.datetime.now(dt.UTC)

    when = facts.collected
    if when is None:
        out.append(Finding(False, "コピー", "取った時が読めない"))
    elif (now - when).days >= OLD_FACTS_DAYS:
        out.append(Finding(False, "コピー", f"{(now - when).days} 日前のコピー（集め直す）"))
    if not facts.root:
        out.append(
            Finding(
                False,
                "コピー",
                "root でないコピー。見えない物: 実際に効いている sshd の値、他の人の authorized_keys、他の人のポートのプロセスの名、パスワードの状態、sudoers",
            )
        )

    os_ = facts.pairs("os")
    out.append(Finding(False, "OS", f"{os_.get('PRETTY_NAME', '読めない')}、kernel {os_.get('KERNEL', '?')}"))
    if "os" in m and os_.get("ID"):
        got = f"{os_['ID']}-{os_.get('VERSION_ID', '')}".rstrip("-")
        if got != m["os"]:
            out.append(Finding(False, "OS", f"表は {m['os']}、サーバーは {got}"))
    if os_.get("HOSTNAME") and os_["HOSTNAME"].split(".")[0] != host:
        out.append(Finding(False, "OS", f"サーバーの名が表と違う（サーバーは {os_['HOSTNAME']}）"))

    ld = facts.pairs("load")
    if ld:
        mem = ""
        if ld.get("MemTotal", "").isdigit() and ld.get("MemAvailable", "").isdigit():
            tot, av = int(ld["MemTotal"]) // 1048576, int(ld["MemAvailable"]) // 1048576  # kB → GB
            mem = f"、メモリ {tot - av}/{tot} GB"
        out.append(
            Finding(False, "動き", f"起動 {ld.get('since') or '?'}、load {ld.get('loadavg') or '?'}{mem}")
        )

    if "required" in facts.lines("reboot"):
        out.append(Finding(True, "更新", "再起動が要る", ["sudo systemctl reboot   # 時を選んで"]))
    up = facts.pairs("updates")
    if up.get("manager") == "apt":
        pkgs = [ln.split() for ln in facts.lines("updates") if ln.startswith("pkg ")]
        sec = sum(1 for p in pkgs if len(p) > 2 and "security" in p[2])
        if pkgs:
            out.append(
                Finding(
                    True,
                    "更新",
                    f"更新が {len(pkgs)} 件（うち security {sec}）",
                    ["sudo apt update && sudo apt full-upgrade"],
                )
            )
        if up.get("lists", "").isdigit() and up.get("now", "").isdigit():
            days = (int(up["now"]) - int(up["lists"])) // 86400
            if days >= OLD_LISTS_DAYS:
                out.append(
                    Finding(
                        True,
                        "更新",
                        f"更新の一覧が {days} 日古い（件数は当てにならない）",
                        ["sudo apt update"],
                    )
                )
        auto = facts.lines("auto")
        installed = any(ln.strip() == "status=install ok installed" for ln in auto)
        on = any("Unattended-Upgrade" in ln and '"1"' in ln for ln in auto)
        if not (installed and on):
            out.append(
                Finding(
                    True,
                    "更新",
                    "自動の更新が無い（unattended-upgrades）",
                    [
                        "sudo apt install -y unattended-upgrades",
                        (
                            "printf '%s\\n' 'APT::Periodic::Update-Package-Lists \"1\";' 'APT::Periodic::Unattended-Upgrade \"1\";' "
                            "| sudo tee /etc/apt/apt.conf.d/20auto-upgrades >/dev/null"
                        ),
                    ],
                )
            )
    else:
        out.append(Finding(False, "更新", "apt が無いサーバー。更新は見ていない"))

    _check_sshd(facts, set(m.get("users", [])) | set(m.get("admins", [])), out)
    _check_keys(table, m, facts, inventory_ids, out)
    _check_people(m, facts, out)

    fw = facts.pairs("firewall")
    if fw.get("ufw") == "active" and fw.get("ENABLED") == "yes":
        if fw.get("DEFAULT_INPUT_POLICY", "").upper() == "ACCEPT":
            out.append(
                Finding(
                    True, "防火壁", "入る向きを既定で通している（ufw）", ["sudo ufw default deny incoming"]
                )
            )
        if fw.get("DEFAULT_OUTPUT_POLICY", "").upper() == "ACCEPT":
            out.append(
                Finding(
                    True,
                    "防火壁",
                    "出る向きを既定で通している（連載 第 5 章の形は、入りも出も既定は拒否）",
                    later=OUT_CLOSE,
                )
            )
    elif fw.get("firewalld") == "active":
        pass
    elif fw.get("nftables") == "active":
        out.append(Finding(False, "防火壁", "nftables が動いている（規則の中身は見ていない）"))
    else:
        out.append(
            Finding(
                True,
                "防火壁",
                "動いていない（ufw も firewalld も nftables も）",
                [
                    "# 一段目: 入ってくる通信を閉じる。ssh を先に通してから有効にする",
                    "sudo apt install -y ufw",
                    "sudo ufw default deny incoming",
                    "sudo ufw limit 22/tcp   # LAN だけにするなら: sudo ufw allow from <LAN> to any port 22 proto tcp",
                    "sudo ufw enable",
                    "# 確かめる: 別の窓から ssh で入れる。sudo ufw status verbose に入る向きの既定の拒否",
                ],
                later=OUT_CLOSE,
            )
        )
    if fw.get("fail2ban") and fw["fail2ban"] != "active":
        out.append(
            Finding(
                False, "防火壁", "fail2ban が動いていない（連載 第 5 章。補助の番犬で、主役は鍵でだけ入る形）"
            )
        )

    listens = ports.parse_ss("\n".join(facts.lines("listen")))
    rows = [p for p in (ports_table or []) if p.get("host") == host]
    if rows:
        diff = ports.compare(rows, listens, host)
        for l in diff.unlisted:
            out.append(
                Finding(
                    True,
                    "ポート",
                    f"表に無いポート: {l.port}（{l.bind} {l.process}）".replace(" ）", "）"),
                    [
                        f"sudo ss -ltnp | grep ':{l.port} '   # 何が開けているかを見る",
                        "# 要るポートなら ports.toml に足す。要らなければ、そのサービスを止める",
                    ],
                )
            )
        for p in diff.closed:
            out.append(
                Finding(
                    True,
                    "ポート",
                    f"表にあって開いていない: {p['port']}（{p.get('service', '')}）",
                    ["# そのサービスが動いているかを見る。もう要らないポートなら ports.toml から消す"],
                )
            )
        for l, want in diff.outward:
            out.append(
                Finding(
                    True,
                    "ポート",
                    f"外向きで表と違う: {l.port}（サーバーは {l.bind}、表は {want}）",
                    [
                        "# そのサービスの設定で 127.0.0.1 に縛る。外から使うなら、前に門（token を確かめるインターフェース）を置く"
                    ],
                )
            )
    else:
        nums = sorted({l.port for l in listens})
        out.append(
            Finding(
                False,
                "ポート",
                f"ポートの表にこのサーバーが無い（開いているポート {len(nums)}: {', '.join(map(str, nums))}）",
            )
        )

    _check_doors(table, host, facts, out)

    t = facts.pairs("time")
    if t and t.get("NTPSynchronized") != "yes":
        out.append(
            Finding(True, "時計", "時計が合わせられていない（K-11）", ["sudo timedatectl set-ntp true"])
        )
    for unit in facts.lines("failed"):
        out.append(
            Finding(
                True,
                "サービス",
                f"落ちている: {unit.strip()}",
                [f"systemctl status {unit.strip()}   # 理由を見る"],
            )
        )
    for key, sec, ctl in (
        ("services", "services", "sudo systemctl"),
        ("user_services", "user-services", "systemctl --user"),
    ):
        want = m.get(key, [])
        if not want:
            continue
        running = {ln.strip().removesuffix(".service") for ln in facts.lines(sec)}
        down = [u for u in want if u.removesuffix(".service") not in running]
        for u in down:
            see = ctl.replace("sudo ", "")
            out.append(
                Finding(
                    True,
                    "サービス",
                    f"動いていない: {u}" + ("（その人の systemd）" if key == "user_services" else ""),
                    [f"{see} status {u} --no-pager | head -20   # 理由を見る", f"{ctl} restart {u}"],
                )
            )
        if not down:
            out.append(Finding(False, "サービス", f"表の {len(want)} 件が動いている（{', '.join(want)}）"))
    for line in facts.lines("disk"):
        cols = line.split()
        if len(cols) == 2 and cols[1].rstrip("%").isdigit() and int(cols[1].rstrip("%")) >= DISK_FULL:
            out.append(Finding(True, "ディスク", f"{cols[0]} が {cols[1]}"))
    active = {ln.strip().removesuffix(".timer") for ln in facts.lines("timers")}
    want_t = m.get("timers", [])
    for u in want_t:
        if u.removesuffix(".timer") not in active:
            name = u.removesuffix(".timer")
            out.append(
                Finding(
                    True,
                    "控え",
                    f"timer が動いていない: {name}",
                    [
                        f"systemctl list-timers --all | grep {name}   # 在るかを見る",
                        f"sudo systemctl enable --now {name}.timer",
                    ],
                )
            )
    if want_t and all(u.removesuffix(".timer") in active for u in want_t):
        out.append(Finding(False, "控え", f"表の timer が動いている（{', '.join(want_t)}）"))
    elif not want_t and facts.lines("timers"):
        if "backup" in active:
            out.append(
                Finding(False, "控え", "backup.timer が動いている（表の timers に書くと、止まった時に出る）")
            )
        else:
            out.append(
                Finding(
                    False, "控え", "控えの timer を表に書いていない（連載 第 10 章の backup.timer。K-08）"
                )
            )
    er = facts.pairs("errors")
    if er.get("err7d", "").isdigit():
        tail = f"、ssh の失敗 {er['sshfail7d']}" if er.get("sshfail7d", "").isdigit() else ""
        who = "" if facts.root else "（root でないコピーで読めた分）"
        out.append(Finding(False, "記録", f"この 7 日のエラーの行 {er['err7d']}{tail}{who}"))
    for line in facts.lines("data"):
        cols = line.split()
        if cols[0] == "missing" and len(cols) == 2:
            out.append(
                Finding(
                    True,
                    "データ",
                    f"置き場が無い: {cols[1]}",
                    ["# ディスクが繋がっているか、道が合っているかを見る（mount、ls）"],
                )
            )
        elif cols[0] == "path" and len(cols) >= 3 and cols[2].isdigit():
            size = _size(int(cols[2]))
            rest = (
                f"（そのディスクは {cols[3]} 使用、残り {_size(int(cols[4]))}）"
                if len(cols) == 5 and cols[4].isdigit()
                else ""
            )
            out.append(Finding(False, "データ", f"{cols[1]}: {size}{rest}"))
    disks = [ln.split() for ln in facts.lines("disk")]
    if disks:
        out.append(Finding(False, "ディスク", "、".join(f"{c[0]} {c[1]}" for c in disks if len(c) == 2)))
    crypt = facts.pairs("crypt").get("crypt")
    if crypt == "0" and m.get("encrypt") is True:
        out.append(
            Finding(
                True,
                "ディスク",
                "表は暗号化するサーバー（encrypt = true）だが、暗号化されていない",
                [
                    "# 後からは難しい。入れ直す時に暗号化 LVM を選ぶ（kagi machine install の手順）。それまで守る物を置かない"
                ],
            )
        )
    elif crypt == "0":
        out.append(Finding(False, "ディスク", "暗号化されていない（持ち出すコンピューターなら入れ直す時に。K-08）"))
    elif crypt and crypt.isdigit() and m.get("encrypt") is True:
        out.append(Finding(False, "ディスク", "暗号化されている（表の通り）"))
    for f in out:
        f.confirm = f.confirm or _needs_confirm(f)
    return out


def _size(kb: int) -> str:
    for unit, n in (("TB", 1 << 30), ("GB", 1 << 20), ("MB", 1 << 10)):
        if kb >= n:
            return f"{kb / n:.1f} {unit}"
    return f"{kb} kB"


def changes(before: list[Finding], after: list[Finding]) -> tuple[list[Finding], list[Finding]]:
    """前のコピーから（増えた直す物、消えた直す物）。連載 第 11 章の「先週の出力と比べる」。"""
    b = {(f.what, f.result) for f in before if f.fix}
    a = {(f.what, f.result) for f in after if f.fix}
    return [f for f in after if f.fix and (f.what, f.result) not in b], [
        f for f in before if f.fix and (f.what, f.result) not in a
    ]


def previous_facts(d: Path, host: str) -> Path | None:
    files = sorted((Path(d) / FACTS_DIR).glob(f"{host}-*.txt"))
    return files[-2] if len(files) >= 2 else None


def fixes(findings: list[Finding]) -> int:
    return sum(1 for f in findings if f.fix)


def head(host: str, facts: Facts, findings: list[Finding]) -> str:
    when = facts.collected
    stamp = when.strftime("%Y-%m-%d %H:%M") if when else "時が読めない"
    who = "root のコピー" if facts.root else "root でないコピー"
    n = fixes(findings)
    return f"サーバー {host}（{stamp}、{who}）: " + (f"直す物 {n}。" if n else "直す物は無い。")


def table(host: str, facts: Facts, findings: list[Finding]) -> str:
    lines = [head(host, facts, findings), "", "| | 何 | 見た物 |", "|---|---|---|"]
    for fix in (True, False):
        for f in findings:
            if f.fix == fix:
                lines.append(f"| {'直す' if fix else '見るだけ'} | {f.what} | {f.result} |")
    return "\n".join(lines)


def fix_script(host: str, facts: Facts, findings: list[Finding]) -> str:
    """直す手を sh の文にする。kagi は走らせない。人が読んで、サーバーで、一つずつ。"""
    out = [
        "#!/bin/sh",
        f"# {head(host, facts, findings)}",
        "# kagi はこれを走らせない。人がサーバーの端末で一つずつ打つ。",
        "# 印のある手は、一行ずつ読んで確かめてから。印の無い手は打って、コピーを取り直して確かめる。",
        "",
    ]
    done: set[tuple[str, ...]] = set()
    later: list[str] = []
    for f in findings:
        if not f.fix:
            continue
        mark = "【読んで確かめてから】 " if f.confirm else ""
        out.append(f"# {mark}{f.what}: {f.result}")
        key = tuple(f.how)
        if f.later and tuple(f.later) not in done:
            later += [f"# {mark}{f.what}: {f.result}", *f.later, ""]
            done.add(tuple(f.later))
        if not f.how:
            out.append("#   手は終わりの二段目" if f.later else "#   決まった手は無い。見て決める")
        elif key in done:
            out.append("#   上と同じ手")
        else:
            out += f.how
            done.add(key)
        out.append("")
    if later:
        out += [*STAGE2_HEAD, "", *later]
    return "\n".join(out)


def _toml(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, list):
        return "[" + ", ".join(json.dumps(str(x), ensure_ascii=False) for x in v) + "]"
    return json.dumps(str(v), ensure_ascii=False)


def append_row(path: Path, kind: str, row: dict) -> None:
    """サーバーの表に [[machine]] か [[key]] を一件足す。足した形を読めるか確かめてから書く。空の欄は書かない。"""
    if kind not in ("machine", "key", "door"):
        raise Refused("足せるのは machine か key か door")
    path = Path(path)
    body = "".join(f"{k} = {_toml(v)}\n" for k, v in row.items() if v not in ("", [], None, False))
    try:
        old = path.read_text("utf-8") if path.exists() else ""
    except (OSError, UnicodeDecodeError) as e:
        raise Refused(f"サーバーの表を読めない: {path} ({type(e).__name__})") from None
    new = old.rstrip("\n") + ("\n\n" if old.strip() else "") + f"[[{kind}]]\n{body}"
    parse_table(new, str(path))
    path.write_text(new, "utf-8")


def key_rows(table: Table, inventory: list[dict] | None = None) -> list[list[str]]:
    """鍵ごとの一行: id、持つ人、指紋、入れてよいサーバー、在庫表。"""
    fps, _problems = key_fingerprints(table)
    inv = {str(s.get("id")): s for s in (inventory or [])}
    rows = []
    for k in table.keys:
        kid = k["id"]
        if inventory is None:
            stock = "見ていない"
        elif kid not in inv:
            stock = "無い（K-14）"
        else:
            stock = f"期限 {inv[kid]['expires']}" if inv[kid].get("expires") else "期限が無い（K-05）"
        hosts = ", ".join(table.machines_of(kid)) or "どこにも無い"
        rows.append([kid, str(k.get("holder", "書いていない")), fps.get(kid, "読めない"), hosts, stock])
    return rows


def key_table(table: Table, inventory: list[dict] | None = None) -> str:
    """鍵の一覧: 持つ人、指紋、入れてよいサーバー、在庫表の期限。値は無い（指紋は公開の物）。"""
    _fps, problems = key_fingerprints(table)
    lines = [
        f"鍵 {len(table.keys)} 件。",
        "",
        "| id | 持つ人 | 指紋 | 入れてよいサーバー | 在庫表 |",
        "|---|---|---|---|---|",
    ]
    lines += ["| " + " | ".join(r) + " |" for r in key_rows(table, inventory)]
    lines += [f"- 直す: {p}" for p in problems]
    return "\n".join(lines)
