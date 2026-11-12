# SPDX-License-Identifier: AGPL-3.0-or-later
"""窓口(お問い合わせと照会)へ、照会用のコピーを置きに行く道具 `digitalkey madoguchi push`(窓口仕様書 8 章)。

鍵の管理ソフトの命令 `digitalkey kanri portal export --out` が出したコピーのファイルを読み、窓口の PocketBase の
コレクション `shoukai` を、ファイルと同じ中身にそろえます(作る、直す、ファイルに無い人のコピーを消す)。

閉じるコンピューターから外へ向かう HTTPS の要求だけを出し、待ち受けるポートを開きません(R-AC-13)。kanri の台帳は読まず、
ファイルだけを読みます。ネットワークを使うのは、この道具だけで、`digitalkey kanri` の命令はネットワークを使いません(R-AC-19)。
入る値(窓口の URL、システムのアカウント kikai の識別とパスワード)は、権限 600 の env のファイルか環境変数で渡し、
引数では受けません。Python の標準ライブラリだけで書いています。使い方は `digitalkey madoguchi push --help`。
"""
