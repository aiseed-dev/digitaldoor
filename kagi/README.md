# digitaldoor-kagi — 秘密の在庫、ポート、記録、サーバーの形を表と突き合わせる道具

**kagi は扉の鍵(digitalkey)ではなく、サーバーと業務システムの秘密の在庫・ポート・記録を見る道具です。**

[digitaldoor](https://door.aiseed.dev) の目的の一つは、会社の人が自分で Claude を使って自社のシステムを守れるようにすることです。kagi はその道具の一つで、人が書いた表と、サーバーの今の形を突き合わせ、違いを出します。鍵の値を一度も読まず、サーバーを変えません。直す手は sh の文で出し、打つのは人です。

## 何をする道具か

| 命令 | する事 |
|---|---|
| `kagi init` | 作業の場所に、在庫表(`inventory.toml`)、ポートの表(`ports.toml`)、サーバーの表(`machines.toml`)、記録(`audit.log`)の雛形を作ります |
| `kagi check inventory` | 秘密の在庫表の、期限切れ、期限の近い物、欄の抜けを出します。値の欄があれば読まずに止まります |
| `kagi check ports` | `ss -ltnp` の出力とポートの表を比べ、表に無いポート、開いていないポート、外向きで表と違うポートを出します |
| `kagi check smb` | Samba の `smb.conf` の、SMB1、署名、ゲスト、待ち受けるインターフェース、個人での権限を見ます |
| `kagi check tokens` | アプリが相手ごとに出した token の台帳(ハッシュだけ)の期限を見ます |
| `kagi scan` | 秘密らしい文字列の在り処を `path:line  種類` だけで出します。値は出しません |
| `kagi log add` / `verify` | 追記だけの記録。連番、前の行のハッシュの連鎖、HMAC の署名で、消した・変えた行が分かります |
| `kagi machine collect` / `check` / `fix` | Linux のサーバーの今の形(ssh の認証、入っている鍵、アカウント、sudo、更新、防火壁、ポート、時計、サービス、ディスク、Caddy の入口)のコピーを取り、サーバーの表と比べ、直す手を文で出します |
| `kagi machine install` | Debian 13 を入れる答えのファイル(パスワードとディスクは入れません)と手順を作ります |
| `kagi key list` / `fingerprint` | 公開鍵と指紋だけで、どの鍵がどのサーバーに入ってよいかの台帳を見ます |
| `kagi weekly` | 全部のサーバーのコピーを取り直し、週の報告を一枚にします |
| `kagi report` | 一枚の報告(Markdown) |
| `kagi app` | 係の PC の窓(Flet の PC 版。ネットワークには出しません) |

形と決まりは [`docs/設計書.md`](docs/設計書.md)、使い方の手順は Skill の形で [`../skills/kagi/`](../skills/kagi/SKILL.md) にあります。監視の仕組み全体の中で kagi をどう使うかは [`../skills/detection/`](../skills/detection/SKILL.md) にあります。

## 入れ方

本体は Python 3.11 以上の標準ライブラリだけで動きます。

```bash
# digitaldoor のリポジトリの最上位で
python3 -m venv .venv
.venv/bin/pip install -e kagi            # 本体
.venv/bin/pip install -e 'kagi[app]'     # 机の画面も使う時(flet が入ります)
.venv/bin/kagi --help
```

入れずに試す時は、`kagi/` の中で `python3 -m kagi --help` と打ちます。リポジトリの最上位で `python3 -m kagi` と打つと、最上位の `kagi/`(このディレクトリ)が先に見つかり動かないので、`kagi` の命令を使うか、`kagi/` の中で打ちます。

作業の場所(`--dir`、既定は `./kagi-data`)には、サーバーの名、番地、指紋、アカウントの名が入ります。公開するリポジトリの外に置いてください。

## テストの回し方

```bash
# digitaldoor のリポジトリの最上位で
.venv/bin/python -m pytest -q kagi
```

`tests/conftest.py` が `kagi/` を import の道の先頭に置くので、`pip install -e kagi` で入れていなくても回ります。テストのサーバーの名、人の名、鍵の形は架空の物です(公開鍵の見本は、対になる秘密鍵の無い偽物です)。

## seed のサーバーを前提にしている部分

kagi は、同じ作者の畑のアプリ seed のリポジトリで作られました。次の部分は seed のサーバーを前提にしています。消さずにコピーしてあり、切り離すかは運営者が決めます。ほかの部分は seed が無くても動きます。

| 場所 | 前提にしている物 |
|---|---|
| `kagi/admin.py`(全部) | seed のサーバーの管理のインターフェース(`/api/admin/*`、要求の頭 `x-seed-invite`)、作業の場所の `seed-admin.json`、操作の記録の `seed.*` の action、Claude と Pl@ntNet の鍵の突き合わせ |
| `kagi/app.py` の「人の出入り」の画面と「サーバーの外の鍵」の札 | 上の `admin.py`。色(`SEED_COLOR`)と字体は seed の theme に合わせてあります |
| `kagi/tokens.py` | token の台帳の形と既定の場所(`~/.config/aiseed/tokens.json`)を seed の `seed token` に合わせています。形が同じなら、ほかのアプリの台帳も読めます |
| `kagi/machine.py` の入口の直す手の注釈 | Caddy の門の見本が seed のリポジトリの `deploy/` にある、と書いています |
| `tests/test_kagi_admin.py`、`tests/test_kagi_app.py` の一部 | 上の部分のテストです |
| 決まりの番号(K-01〜15)と「鍵の管理 §…」 | seed のリポジトリの `docs/鍵の管理の仕組み.md` を指しています |

## seed からコピーした事

2026-11-11 に、seed のリポジトリ(https://github.com/aiseed-dev/seed )から次をコピーしました。seed のリポジトリは変えていません。

| 元の場所(seed) | コピーした先(digitaldoor) |
|---|---|
| `src/kagi/` | `kagi/kagi/` |
| `tests/kagi/` | `kagi/tests/` |
| `docs/kagi 設計書.md` | `kagi/docs/設計書.md` |
| `skill-kagi/` | `skills/kagi/` |
| `pyproject.toml` の `kagi = "kagi.cli:main"` | `kagi/pyproject.toml` |

コピーする時に直した事:

- 例とテストのサーバーの名、人の名、アカウントの名を架空の物(`web1`、`db1`、`suzuki`、`aoki`)に替えました。
- コードの中の文書への道(`docs/kagi 手引き.md` など)を、digitaldoor の中の道(`skills/kagi/02-記録.md`、`kagi/docs/設計書.md`)に替えました。
- 設計書の経緯の発言の引用を、「選んでいる物」として短く直しました。Skill は です・ます調にしました。
- `tests/conftest.py` を足しました(import の道の準備だけです)。

計画書、配線図、決まりの番号の本文、seed を動かす人の守りのガイド(`skill-keeper/`)はコピーしていません。seed のリポジトリにあります。

## ライセンス

GNU Affero General Public License v3.0 or later(`LICENSE`)です。リポジトリの最上位の `digitalkey/` と同じで、`controller/`(Apache-2.0)とは別です。kagi のコードを `controller/` にコピーしないでください。
