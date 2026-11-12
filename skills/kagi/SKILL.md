---
name: kagi
description: サーバーと業務システムを守る係が、kagi(秘密の在庫表、ポートの表、追記だけの記録、Samba の設定、Linux のサーバーの今の形、鍵の台帳、週の定例、一枚の報告、机の画面)を使う時に読むガイド。kagi は扉の鍵ではなく、サーバーと業務システムの秘密の在庫・ポート・記録を見る道具です。鍵の値を一度も読まず、サーバーを変えません。直す手は文で出て、打つのは人です。「サーバーの鍵やパスワードの在り処を整理したい」「開いているポートを確かめたい」「Linux のサーバーの設定を表と比べたい」という依頼で使うこと。Use this skill when someone wants to inventory secrets, compare open ports and Linux machine state against written tables, or keep a tamper-evident log with the kagi tool.
---

# kagi — 鍵とポートとサーバーを、表と突き合わせる

あなたは、サーバーと業務システムを守る係が kagi を使うのを手伝います。係のする事は三つです。在庫表とポートの表とサーバーの表を書く、kagi を走らせる、結果を読んで「合っている」と言う。kagi は鍵の値を一度も読まないので、このガイドのどこにも値を書く所はありません。

kagi は扉の鍵(digitalkey)ではなく、サーバーと業務システムの秘密の在庫・ポート・記録を見る道具です。コードは `kagi/`(配布の名 `digitaldoor-kagi`、AGPL-3.0-or-later)、設計は `kagi/docs/設計書.md` にあります。

## 守ること

1. **鍵、token、パスワードの値を見ません。書きません。会話に出しません。** kagi に渡すのは `.pub` だけです。秘密鍵のファイルを渡しても、kagi は読まずに止まります。値が会話に貼られたら、写さず、使わず、その値を取り消して作り直す手(06)を出します。走査の出力に値が出る事は無い作りです。もし出たら kagi の外れなので、止めて直します。
2. **サーバーを変えるのは人です。** `kagi machine fix` は sh の文を出すだけで、走らせません。人が読んで、サーバーの端末で一つずつ打ちます。ssh の形を変える行は、先に別の端末で入れる事を確かめてから打ちます。文を読まずに丸ごと走らせません。
3. **鍵を作る、回す、外すのも人です。** kagi に鍵を作らせません。kagi は、どの鍵がどのサーバーに入ってよいかの台帳と、表とサーバーの違いを出すだけです。
4. **記録(`audit.log`)を手で直しません。** 直す必要があったら、直した事を新しい行として足します(`kagi log add`)。記録は一行ずつ署名され、`kagi log verify` で確かめられます。
5. **読むだけの命令は、一言断ってからあなたが打ってよい物です。** `kagi report`、`kagi check …`、`kagi scan`、`kagi key list`、`kagi machine list` / `script` / `collect` / `check` / `fix`(文を出すだけ)。`collect` は係の ssh と係の鍵でサーバーに入り、読むだけの文を走らせます。sudo の要る命令と、表やサーバーを変える物は人が打ちます。
6. **届く・届かない、動く・動かないは、測ってから書きます。** `kagi machine install` は、まだ本物のインストーラーで通していません(05)。
7. **サーバーの名、番地、指紋、アカウントの名は、作業の場所(`--dir`)に置きます。** 作業の場所はリポジトリの外に置き、公開するリポジトリには入れません。

## 章

| 章 | 何をする | 主な命令 |
|---|---|---|
| [00 始める](00-始める.md) | kagi を入れ、作業の場所を作る | `pip install -e kagi`、`kagi init` |
| [01 在庫表](01-在庫表.md) | 今ある秘密を一件ずつ書く(値は書かない)。走査で探す | `kagi scan`、`kagi check inventory` |
| [02 記録](02-記録.md) | 作った、回した、外した事を一行ずつ残す | `kagi log add`、`kagi log verify` |
| [03 ポートを突き合わせる](03-ポートを突き合わせる.md) | サーバーのポートの一覧をポートの表と比べる。Samba の形を見る | `kagi check ports`、`kagi check smb` |
| [04 サーバーを見る](04-サーバーを見る.md) | Linux のサーバーの今の形をコピーし、表と比べ、直す文を出す | `kagi machine collect` / `check` / `fix` |
| [05 サーバーを入れる](05-サーバーを入れる.md) | Debian 13 を入れる答えのファイルを作る | `kagi machine install` |
| [06 鍵](06-鍵.md) | 鍵を足す、回す、外す。token の台帳 | `kagi key list`、`kagi check tokens` |
| [07 週の定例](07-週の定例.md) | 週に一度、全部を取り直して差を読む | `kagi weekly` |
| [08 報告と机の画面](08-報告と机の画面.md) | 一枚の報告。係の PC の窓 | `kagi report`、`kagi app` |

監視の仕組み全体(何を監視し、何を異常と見て、誰に知らせるか)は `skills/detection/` にあります。kagi の差を「検知」として扱う使い方は、その 07 章です。

## 読む物

- 形と決まり: `kagi/docs/設計書.md`
- コード: `kagi/kagi/`。テスト: `kagi/tests/`(リポジトリの最上位で `.venv/bin/python -m pytest -q kagi`)
- 良い形の出どころ: 連載「Claude と一緒に育てる Debian サーバー」(https://aiseed.dev/debian-server/ )
- 決まりの番号(K-01〜15)の本文、計画書、配線図、seed を自分のサーバーで動かす人の守りのガイド(`skill-keeper/`)は、seed のリポジトリ(https://github.com/aiseed-dev/seed )にあります。

## 出どころ

このガイドは、seed のリポジトリの `skill-kagi/` を 2026-11-11 にコピーし、digitaldoor の書き方(です・ます調)に直した物です。章の分け方と中身は同じです。
