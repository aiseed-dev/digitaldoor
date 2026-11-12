"""サーバーを入れる時のファイルを作る（Debian 13）。設計書 §3.4。K-03、K-07、K-12、K-13。

サーバーの表（machines.toml）の一件から三つを書く。kagi はサーバーに触らない。

- preseed.cfg: Debian のインストーラーに渡す答え。**パスワードとディスクは書かない**（インストーラーが尋ね、人が画面で打つ・選ぶ）。
  root は止め、最初の管理者の authorized_keys に表の公開鍵を入れ、sshd を「鍵でだけ」にする。
- firstboot.sh: 入れた後に人が sudo で一度走らせる。防火壁、自動の更新、時計、sshd の確かめ。何度走らせても同じ形になる。
  今あるサーバーに形を当てる時にも使える（鍵が入っていなければ sshd は変えない）。
- 手順.md: 人がやる事、kagi がやる事、人が確かめる事。

公開鍵だけを読む。秘密鍵のファイルは読まずに拒む（machine.read_pub）。
"""

from __future__ import annotations

import base64
import datetime as dt
import ipaddress
import re
from pathlib import Path

from kagi import machine
from kagi.inventory import Refused

SUPPORTED = ("debian-13",)
PROFILES = {
    "server": "standard, ssh-server",
    "desktop": "standard, ssh-server, desktop, gnome-desktop",
}
_LOCALE = re.compile(r"^[A-Za-z]{2,3}_[A-Z]{2}\.UTF-8$")
_KEYMAP = re.compile(r"^[a-z]{2,8}$")
_ZONE = re.compile(r"^[A-Za-z_]+(/[A-Za-z0-9_+-]+){1,2}$")
_MIRROR = re.compile(r"^[a-z0-9.-]+$")


def _sshd_conf(allow: list[str]) -> str:
    return (
        "# kagi (kagi/docs/設計書.md §3.4): 鍵でだけ入る\n"
        "PasswordAuthentication no\n"
        "KbdInteractiveAuthentication no\n"
        "PermitRootLogin no\n"
        "PubkeyAuthentication yes\n"
        f"AllowUsers {' '.join(allow)}\n"
    )


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def plan(table: machine.Table, host: str) -> dict:
    """サーバーの表の一件を、入れる時の値に直す。足りない物・読めない物は拒む（半端な形で入れない）。"""
    m = table.machine(host)
    os_ = m.get("os", "")
    if os_ not in SUPPORTED:
        raise Refused(
            f"入れるファイルを作れるのは今 {', '.join(SUPPORTED)} だけ（{host} の os は {os_ or '書いていない'}）"
        )
    admins = m.get("admins", [])
    if not admins:
        raise Refused(f"{host} の admins が無い。最初の管理者のアカウントが要る")
    kids = m.get("keys", [])
    if not kids:
        raise Refused(f"{host} の keys が無い。鍵でだけ入る形にするので、入れてよい公開鍵が要る")
    pubs: list[str] = []
    fps: list[tuple[str, str]] = []
    for kid in kids:
        k = table.key(kid)
        if k is None:
            raise Refused(f"{host} の keys の {kid} が [[key]] に無い")
        if "pub" not in k:
            raise Refused(f"鍵 {kid} に pub（公開鍵のファイル）が無い。入れる時は公開鍵そのものが要る")
        for line, info in machine.read_pub(Path(str(k["pub"]))):
            if info.type == "ssh-dss" or (info.bits is not None and info.bits < 2048):
                raise Refused(f"鍵 {kid} は弱い（{info.type} {info.bits or ''}）。ed25519 を作り直す")
            if "fingerprint" in k and k["fingerprint"] != info.fingerprint:
                raise Refused(f"鍵 {kid} の表の fingerprint と pub のファイルの指紋が違う")
            # 公開鍵の後ろの注釈（メールやコンピューターの名）は入れない。代わりに表の id を付ける
            typ, blob = (
                [t for t in line.split() if t][:2] if line.split()[0] in machine.KEY_TYPES else (None, None)
            )
            if typ is None:
                raise Refused(f"鍵 {kid} の pub は「種類 中身」の一行で（前置きの options は入れない）")
            pubs.append(f"{typ} {blob} {kid}")
            fps.append((kid, info.fingerprint))
    profile = m.get("profile", "server")
    if profile not in PROFILES:
        raise Refused(f"{host} の profile は {' | '.join(PROFILES)} のどれか")
    locale, keymap, zone = (
        m.get("locale", "ja_JP.UTF-8"),
        m.get("keymap", "jp"),
        m.get("timezone", "Asia/Tokyo"),
    )
    mirror = m.get("mirror", "deb.debian.org")
    for v, rx, name in (
        (locale, _LOCALE, "locale"),
        (keymap, _KEYMAP, "keymap"),
        (zone, _ZONE, "timezone"),
        (mirror, _MIRROR, "mirror"),
    ):
        if not isinstance(v, str) or not rx.match(v):
            raise Refused(f"{host} の {name} が読めない")
    lan = m.get("lan")
    if lan is not None:
        try:
            lan = str(ipaddress.ip_network(str(lan), strict=True))
        except ValueError:
            raise Refused(f"{host} の lan はネットワーク（例 192.168.1.0/24）で書く") from None
    allow = list(dict.fromkeys([*admins, *m.get("users", [])]))
    return {
        "host": host,
        "admin": admins[0],
        "allow": allow,
        "pubs": pubs,
        "fingerprints": fps,
        "tasks": PROFILES[profile],
        "profile": profile,
        "locale": locale,
        "keymap": keymap,
        "zone": zone,
        "mirror": mirror,
        "lan": lan,
        "role": str(m.get("role", "")),
        "encrypt": bool(m.get("encrypt", False)),
    }


def preseed(p: dict) -> str:
    u = p["admin"]
    late = (
        f"install -d -m 700 -o {u} -g {u} /home/{u}/.ssh; "
        f"echo {_b64(chr(10).join(p['pubs']) + chr(10))} | base64 -d > /home/{u}/.ssh/authorized_keys; "
        f"chown {u}:{u} /home/{u}/.ssh/authorized_keys; chmod 600 /home/{u}/.ssh/authorized_keys; "
        f"install -d -m 755 /etc/ssh/sshd_config.d; "
        f"echo {_b64(_sshd_conf(p['allow']))} | base64 -d > {machine.SSHD_DROPIN}; chmod 644 {machine.SSHD_DROPIN}"
    )
    return f"""\
#_preseed_V1
# kagi machine install --host {p["host"]} (kagi/docs/設計書.md 3.4). Debian 13 (trixie).
# No password and no disk choice in this file: the installer asks, a person answers on the screen.

d-i debian-installer/locale string {p["locale"]}
d-i keyboard-configuration/xkb-keymap select {p["keymap"]}

d-i netcfg/choose_interface select auto
d-i netcfg/get_hostname string {p["host"]}
d-i netcfg/hostname string {p["host"]}
d-i netcfg/get_domain string

d-i mirror/country string manual
d-i mirror/http/hostname string {p["mirror"]}
d-i mirror/http/directory string /debian
d-i mirror/http/proxy string
d-i apt-setup/non-free-firmware boolean true
d-i apt-setup/services-select multiselect security, updates

# root is locked; the first admin gets sudo. The password is typed at the installer.
d-i passwd/root-login boolean false
d-i passwd/user-fullname string {u}
d-i passwd/username string {u}

d-i clock-setup/utc boolean true
d-i time/zone string {p["zone"]}
d-i clock-setup/ntp boolean true

tasksel tasksel/first multiselect {p["tasks"]}
d-i pkgsel/include string sudo ufw unattended-upgrades openssh-server
d-i pkgsel/upgrade select full-upgrade
popularity-contest popularity-contest/participate boolean false

d-i grub-installer/only_debian boolean true
d-i grub-installer/bootdev string default

# Keys only: authorized_keys for {u} (public keys from machines.toml) and the sshd drop-in.
d-i preseed/late_command string in-target sh -c '{late}'

d-i finish-install/reboot_in_progress note
"""


def firstboot(p: dict) -> str:
    u = p["admin"]
    ssh_rule = f"ufw allow from {p['lan']} to any port 22 proto tcp" if p["lan"] else "ufw limit 22/tcp"
    lan_out = f"ufw allow out to {p['lan']}\n" if p["lan"] else ""
    return f"""\
#!/bin/sh
# kagi machine install --host {p["host"]}（kagi/docs/設計書.md §3.4）。入れた後に一度: sudo sh firstboot.sh
# 何度走らせても同じ形になる。鍵の値は書いていない（公開鍵だけ）。
set -eu
[ "$(id -u)" = 0 ] || {{ echo "sudo sh firstboot.sh で走らせる" >&2; exit 2; }}

echo "== 1 鍵でだけ入る形（sshd）"
AK=/home/{u}/.ssh/authorized_keys
if [ ! -s "$AK" ]; then
  install -d -m 700 -o {u} -g {u} /home/{u}/.ssh
  echo {_b64(chr(10).join(p["pubs"]) + chr(10))} | base64 -d > "$AK"
  chown {u}:{u} "$AK"; chmod 600 "$AK"
  echo "   {u} の authorized_keys に表の公開鍵を入れた"
fi
if [ -s "$AK" ]; then
  install -d -m 755 /etc/ssh/sshd_config.d
  echo {_b64(_sshd_conf(p["allow"]))} | base64 -d > {machine.SSHD_DROPIN}
  chmod 644 {machine.SSHD_DROPIN}
  sshd -t
  systemctl reload ssh
else
  echo "   鍵が入っていないので sshd は変えていない" >&2
fi

echo "== 2 防火壁（入りも出も既定は拒否。連載 第 5 章の形）"
apt-get install -y ufw unattended-upgrades fail2ban >/dev/null
ufw default deny incoming
ufw default deny outgoing
{ssh_rule}
ufw allow out 53
ufw allow out 123/udp
ufw allow out 80,443/tcp
# 67/udp はネットワークの番地をもらい直す要求（DHCP）。無いと番地が切れた時にネットワークから落ちる
ufw allow out 67/udp
{lan_out}ufw --force enable

echo "== 3 自動の更新"
printf '%s\\n' 'APT::Periodic::Update-Package-Lists "1";' 'APT::Periodic::Unattended-Upgrade "1";' \\
  > /etc/apt/apt.conf.d/20auto-upgrades

echo "== 4 時計と番犬"
timedatectl set-ntp true || true
if [ ! -e /etc/fail2ban/jail.local ]; then
  printf '%s\\n' '[DEFAULT]' 'bantime = 10m' 'findtime = 10m' 'maxretry = 5' '' '[sshd]' 'enabled = true' \\
    > /etc/fail2ban/jail.local
fi
systemctl restart fail2ban || true

echo "== 5 見る（この三つを手順の通りに確かめる）"
ufw status verbose
ss -ltnp
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
"""


def steps(p: dict, today: dt.date) -> str:
    u, h = p["admin"], p["host"]
    keys = "\n".join(f"  - `{kid}`  {fp}" for kid, fp in p["fingerprints"])
    disk = (
        "「ガイド - ディスク全体を使い、暗号化 LVM をセットアップ」を選び、ディスクのパスフレーズを打つ（表の `encrypt = true`）"
        if p["encrypt"]
        else "ディスクを選び、消してよいディスクかを画面で確かめる（持ち出すコンピューターなら暗号化 LVM を選ぶ。K-08）"
    )
    return f"""\
# {h} を入れる手順（{today.isoformat()}。`kagi machine install --host {h}` が書いた）

役: {p["role"] or "（表に書いていない）"}。OS: Debian 13、形: {p["profile"]}。最初の管理者: `{u}`。
入れてよい鍵（公開鍵の指紋。値ではない）:
{keys}

**このファイルはインストーラーの実機ではまだ確かめていない**（kagi/docs/設計書.md §3.4 の「確かめていない物」）。
初めて使う時は、消してよいコンピューターか仮想マシンで一度通す。

| 歩 | 人がやる | kagi がやった・やる | 人が確かめる |
|---|---|---|---|
| 1 | Debian 13 の netinst の ISO を取り、`SHA512SUMS` と署名で確かめ、USB に書く | — | チェックサムが一致 |
| 2 | この場所で `python3 -m http.server 8000 --bind <この PC の LAN の番地>` を開く（入れ終わったら Ctrl-C で閉じる） | `preseed.cfg` を書いた。パスワードもディスクも入っていない | `kagi scan` がこの場所で何も出さない |
| 3 | USB から起動 →「Advanced options」→「Automated install」→ 尋ねられたら `http://<この PC の LAN の番地>:8000/preseed.cfg` | — | 言語と名前を尋ねられずに進む |
| 4 | `{u}` のパスワードを画面で打つ（紙か、覚える。どこにも書き写さない） | — | — |
| 5 | {disk} | — | 消すディスクの名が、思っているディスク |
| 6 | 再起動を待つ。**サーバーの画面で** `{u}` で入り `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` | — | 出た指紋を `machines.toml` の `hostkey = "SHA256:…"` に書く |
| 7 | 自分の PC から `ssh {u}@{h}`。初めてのサーバーだと尋ねられる | — | **尋ねられた指紋が 6 の指紋と同じ**なら yes。違えば止める |
| 8 | `scp firstboot.sh {u}@{h}:` → `ssh -t {u}@{h} sudo sh firstboot.sh` | `firstboot.sh` を書いた | 最後に出る三つ: 防火壁が active、ポートが 22 だけ、ホストの鍵の指紋が 6 と同じ |
| 9 | 別の窓で `ssh -o PubkeyAuthentication=no {u}@{h}` | — | **入れない**（Permission denied (publickey)）。鍵でだけ入る形 |
| 10 | — | `kagi machine collect --host {h}` → `kagi machine check --host {h}` | 「直す物は無い」まで |
| 11 | `inventory.toml` に足す: `{u}` のパスワード（置き場は紙/記憶）、ホストの鍵、{"ディスクのパスフレーズ、" if p["encrypt"] else ""}入れた鍵。`ports.toml` に 22 | `kagi check inventory`、`kagi key list` | 抜けが無い |
| 12 | `kagi log add --actor <自分> --action machine.install --detail {h}` | 一行足す | `kagi log verify` が ok |

止まった時: 3 で preseed が読めなければ、2 の番地と防火壁（この PC の 8000）を見る。8 の途中で切れても、もう一度走らせてよい。
9 で入れてしまったら、`{machine.SSHD_DROPIN}` があるか、`sudo sshd -T | grep -i passwordauth` を見る。
"""


def build(table: machine.Table, host: str, today: dt.date | None = None) -> dict[str, str]:
    p = plan(table, host)
    return {
        "preseed.cfg": preseed(p),
        "firstboot.sh": firstboot(p),
        "手順.md": steps(p, today or dt.date.today()),
    }


def write(
    d: Path, table: machine.Table, host: str, out: Path | None = None, today: dt.date | None = None
) -> list[Path]:
    files = build(table, host, today)
    out = Path(out) if out else Path(d) / "install" / host
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, text in files.items():
        p = out / name
        p.write_text(text, "utf-8")
        paths.append(p)
    return paths
