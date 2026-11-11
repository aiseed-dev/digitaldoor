---
name: door-minipc
description: 実演と扉の門番に使う miniPC に、Debian 13(trixie)を最小構成で入れ、digitaldoor の扉のコントローラ、Sesame の錠のドライバ、Matter の盤(digitalkey door-serve、digitalkey panel)を動かせるように設定する手引き。netinst と tasksel での最小インストール、SSH の鍵、入りも出も既定は拒否のファイアウォール、自動更新、Bluetooth(BLE)のアダプタ、avahi(mDNS)、Python の環境、鍵のファイルの置き場、持ち運ぶ機械としての注意を扱う。miniPC や小さな PC に Debian を入れて錠や扉を動かしたい、実演の機械を準備したい、門番の機械を立てたい、という依頼では、digitaldoor の名前が出ていなくても使うこと。Use this skill whenever someone sets up a small Debian machine to run a door controller, a smart-lock driver over Bluetooth, or a Matter bridge for a lock.
---

# 扉の miniPC に Debian を入れて設定する

miniPC は、錠のそばで Sesame と Bluetooth で話し、扉のコントローラとして判断と記録を持ち、Matter の電子錠としてスマートフォンのホームアプリに見せる機械です。Sesame の 32 桁を持つので、鍵を持つ機械として扱います。

一般の作法(最小インストール、SSH、守り、サービス、バックアップ)は、連載「Claudeと一緒に育てるDebian サーバー」(https://aiseed.dev/debian-server/ )に沿います。このスキルは、その上に扉の miniPC で違う所だけを足します。

## 守ること

- **root の作業は本人が行う。** あなた(AI)は、打つ命令と、その命令が何をするかを書きます。`sudo` の命令は本人が打ちます。cron と systemd の常駐の設定も、本人が入れます
- **鍵を会話に入れない。** Sesame の 32 桁、SSH の秘密鍵、Wi-Fi のパスワード、扉の管理者の鍵と発行者の鍵は、会話、リポジトリ、コミット、ログに入れません。出力を貼ってもらうときは、鍵が混ざっていないかを本人に確かめてもらいます
- **開発の道具を入れない。** コンパイラー、Rust、Node.js などは入れません。足りない物が出たら、入れずに、ほかの道(wheel のある版、miniforge)を探します。実行してよい物を絞るためです(R-RS-11)
- **締め出されない順に進める。** SSH を締めるとき、ファイアウォールを有効にするときは、もう一つのセッションを開いたまま、許可を先に書いてから反映します
- **持ち運ぶ機械には、本物の扉の鍵を入れない。** 実演の miniPC に入れるのは、実演用の Sesame の鍵だけです。なくしたら、実演用の Sesame をリセットすれば済む状態に保ちます。本物の扉の門番は、持ち出さない別の機械にします

## 1. この miniPC の役と、選ぶこと

| 役 | 実演の miniPC | 家や事務所の門番 |
|---|---|---|
| 置き場 | 持ち運ぶ | 扉の近く(Bluetooth が届く所)に置いたまま |
| 入る鍵 | 実演用の Sesame の鍵だけ | 本物の扉の鍵 |
| 網 | 会場ごとに変わる | 決まった網。外からは入れない |
| 止めてよいか | 使うときだけ動かす | 止めない。停電のときの扱いを決める |

持ち主が選ぶこと(選んだことは、この miniPC の覚え書き `my-minipc.md` に書き、リポジトリには入れません):

- **画面:** 画面を付けない(盤は、同じ網のノート PC かスマートフォンのブラウザで見る)か、最小の画面を付けるか
- **網のつなぎ方:** 有線、Wi-Fi、持ち込みのルーター、テザリング。会場ごとに Wi-Fi を替えるなら、NetworkManager(`nmcli`)を入れると替えやすい(`sudo apt install network-manager`、`sudo nmcli device wifi connect "<SSID>" --ask`。パスワードは伏せた入力で打つ)。網が要るのは Matter の所だけで、実演の道具と Sesame のアプリは Bluetooth だけで動く
  - 例: スマートフォンが二台あれば、一台がテザリングを出し、miniPC ともう一台のスマートフォン(ホームアプリを使う側)がそこにつなぐ。会場の Wi-Fi に頼らずに済む。来客用の Wi-Fi は、つないだ機器どうしが見えない設定のことが多く、Matter のペアリングが通らない
  - ホームアプリが Matter の機器を足すのにハブを求めるか、テザリングの網で通るかは、家で前もって試す
  - 住所を固定したいときは、miniPC の側では固定せず、家のルーターで MAC アドレスに予約を付ける。持ち出した先でも、その場の網から住所をもらえる
- **ディスクの暗号化:** インストーラの「暗号化した LVM」を使うか。使うと、起動のたびにパスフレーズを打つキーボードが要る
- **サービスの起動:** 実演では手で起動するか、systemd のサービスにするか

## 2. 最小インストール

連載の第 3 章どおりです。

1. Debian 13 の netinst のイメージで入れる(USB メモリに書いて起動する)
2. 「ソフトウェアの選択」(tasksel)では、「SSH server」と「standard system utilities」の二つだけにチェックする。デスクトップ環境と web server は外す
3. root のパスワードは空欄にし、一般のユーザーに sudo を与える
4. ホスト名は役が分かる名前にする(例: `door-demo`)。会社名や人の名前は使わない
5. ロケールは英語(`C.UTF-8` か `en_US.UTF-8`)。ログとエラーを調べやすくするため

入れたら、最初にこれを行います。

```bash
ip a                    # この miniPC の住所を控える
sudo apt update && sudo apt upgrade
hostnamectl
```

住所は、ルーターの DHCP 予約で固定するのが楽です。会場の網では固定できないことが多いので、そのときは `ip a` で毎回確かめます。

## 3. SSH

連載の第 4 章どおりです。操作する PC で鍵を作り(`ssh-keygen -t ed25519`、パスフレーズを付ける)、`ssh-copy-id` で置き、`/etc/ssh/sshd_config.d/99-hardening.conf` に次を書きます。

```text
PasswordAuthentication no
PermitRootLogin no
```

`sudo sshd -t` で確かめ、`sudo systemctl reload ssh` で反映し、別の窓から鍵で入れることを確かめてから、最初の窓を閉じます。退路は、miniPC にモニタとキーボードを付けて入る物理の操作です。

## 4. 扉の miniPC に要る物

```bash
sudo apt install bluez avahi-daemon avahi-utils python3-venv git ufw unattended-upgrades needrestart
```

| 物 | 何のためか |
|---|---|
| `bluez` | Sesame と Bluetooth(BLE)で話す。`bleak` はこれを D-Bus で使う |
| `avahi-daemon`、`avahi-utils` | Matter の機器であることを網に知らせる(mDNS)。盤は `avahi-publish-service` を使う |
| `python3-venv`、`git` | digitaldoor を取ってきて、閉じた環境で動かす |
| `ufw` | ファイアウォール(6 章) |
| `unattended-upgrades`、`needrestart` | セキュリティの更新を自動で当てる。再起動が要る物を知らせる(連載の第 5 章) |

Bluetooth のアダプタ(USB の物でよい)を挿し、見えることを確かめます。

```bash
bluetoothctl show       # Controller の行と Powered: yes が出ること
systemctl status bluetooth avahi-daemon
```

SSH で入ったユーザーから Bluetooth を使えないときは、そのユーザーを `bluetooth` のグループに入れ、入り直します(`sudo usermod -aG bluetooth <ユーザー名>`)。

## 5. digitaldoor を入れる

```bash
git clone https://github.com/aiseed-dev/digitaldoor
cd digitaldoor
python3 -m venv .venv
.venv/bin/pip install -e ./controller -e ".[panel]" -e "drivers/digitalkey-sesame[app]"
.venv/bin/digitalkey sesame --fake scan     # 疑似の Sesame で動くこと
.venv/bin/digitalkey sesame scan            # 実機の Sesame が見えること
```

- Python は Debian の `python3`(3.13)と venv です。開発機の `.venv` と同じ版にそろえています。入れる物は全部 wheel で入り、コンパイラーは要りません。wheel の無い物が出たら、コンパイラーを入れずに、miniforge の環境で揃える道を選びます
- 取ってくるのは公開のリポジトリです。手元の変更を miniPC で使うときは、開発機で push してから `git pull` します

鍵は、スマートフォンのアプリで書き出して、miniPC で取り込みます(`skills/sesame-app/SKILL.md` の「鍵を PC に写す」)。

```bash
.venv/bin/digitalkey sesame import <UUID> --name 玄関
ls -l ~/.config/digitalkey/sesame-keys.json   # -rw------- であること
```

## 6. ファイアウォール

連載の第 5 章の「入りも出も、既定は拒否」に、扉の miniPC で要る物を足します。`allow ssh` を先に書いてから `enable` します。

| 向き | 相手 | 何のためか |
|---|---|---|
| 入り | SSH(22/tcp)、同じ網から | 操作する PC から入る |
| 入り | Matter(5541/udp)、同じ網から | ホームアプリが盤につなぐ |
| 入り、出 | mDNS(5353/udp) | Matter の機器を見つけてもらう |
| 入り | 盤の画面(8798/tcp)、同じ網から | 画面を付けないとき、ノート PC かスマートフォンのブラウザで盤を見る。盤がどの住所で待つかは `ss -tlnp \| grep 8798` で確かめる(9 章) |
| 出 | DNS(53)、NTP(123/udp)、HTTP と HTTPS(80、443/tcp) | 名前の解決、時刻、更新、`git pull` |

```bash
sudo ufw default deny incoming
sudo ufw default deny outgoing
sudo ufw allow ssh
sudo ufw allow out 53
sudo ufw allow out 123/udp
sudo ufw allow out 80,443/tcp
sudo ufw allow 5353/udp
sudo ufw allow out 5353/udp
sudo ufw allow from <同じ網>/24 to any port 5541 proto udp
sudo ufw allow from <同じ網>/24 to any port 8798 proto tcp   # 盤を外から見るときだけ
sudo ufw enable
sudo ufw status verbose
```

- 扉の HTTP の口(8797/tcp)は開けません。`digitalkey door-serve` は既定で `127.0.0.1` だけで待ち、同じ機械の盤だけが使います
- Matter は IPv6 も使います。ufw の IPv6 が有効(`/etc/default/ufw` の `IPV6=yes`)なことを確かめます
- 会場の網は毎回変わるので、`<同じ網>` はその場の `ip a` で確かめて書き直します。何かが黙って止まったら、`sudo journalctl -k --grep UFW` で止められた通信を見ます

## 7. 動かす

手順は `docs/使い方.md` の「実演」の「実機の Sesame を miniPC の Matter の電子錠として見せる」にあります。流れは次のとおりです。

```bash
.venv/bin/python tools/jitsuen.py check --lock sesame:<UUID>        # 錠に届くこと
.venv/bin/digitalkey door-serve --lock sesame:<UUID> --name genkan --dir ~/door-vault
DIGITALKEY_DOOR_DIR=~/door-vault .venv/bin/digitalkey panel          # 別の窓で
```

終わったら、盤と扉を Ctrl-C で止めます。残った物があれば、`pkill -f avahi-publish-service` と `fuser -k 5541/udp 8797/tcp 8798/tcp` で片付けます。

実演の前の日には、次を確かめます。

- `sudo apt update && sudo apt upgrade` と、`/var/run/reboot-required` が無いこと
- `bluetoothctl show`、`systemctl status avahi-daemon`
- `tools/jitsuen.py check` で錠に届くこと
- スマートフォンのホームアプリで盤の QR を読めること。ホームアプリが Matter の機器を足すのに、ハブ(Apple TV、HomePod、Google の Nest など)を求めるかどうかは、使うスマートフォンとアプリで確かめておきます

## 8. 鍵を持つ機械として

| 物 | 置き場 | なくしたとき、漏れたとき |
|---|---|---|
| Sesame の 32 桁 | `~/.config/digitalkey/sesame-keys.json`(権限 600) | 実演の miniPC なら、実演用の Sesame をリセットして登録し直す |
| 扉の記録と記録の鍵 | `--dir` の置き場(`audit.ed25519` など) | 記録を外へ出しておけば(`export`)、伝票は残る |
| SSH の秘密鍵 | 操作する PC の側。miniPC には公開鍵だけ | `authorized_keys` から外す |
| Wi-Fi のパスワード | miniPC の網の設定 | 会場の網の持ち主に知らせる |

どの鍵がどこにあるかは、権限の目録に載せます(鍵そのものは書かず、置き場だけを書く)。鍵の階層は `docs/扉コントローラ仕様書.md` の 5.2b にあります。

## 9. まだ無い物

- 盤(`digitalkey panel`)が待つ住所を選ぶ設定。今は Flet の環境変数 `FLET_SERVER_IP` で選べます(例: `FLET_SERVER_IP=<miniPC の住所>`)。盤の命令の引数として持つかどうかは、miniPC で `ss -tlnp | grep 8798` を見て、既定の待ち方を確かめてから決める
- 扉と盤を systemd のサービスにする雛形(砂場の設定と、外へ出さない `IPAddressDeny=any` を含む)
- この手順を流せる形にした設定のスクリプト(連載の第 11 章「構成をコード化する」)
